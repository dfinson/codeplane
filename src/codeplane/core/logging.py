"""Structured logging with request correlation and multi-output support."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from codeplane.config.models import LoggingConfig

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str | None = None) -> str:
    """Set or generate request correlation ID."""
    rid = request_id or uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def clear_request_id() -> None:
    _request_id.set(None)


def _add_request_id(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    if rid := get_request_id():
        event_dict["request_id"] = rid
    return event_dict


_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def configure_logging(
    *,
    config: LoggingConfig | None = None,
    json_format: bool = False,
    level: str = "INFO",
) -> None:
    """Configure structlog. Pass config for multi-output, or use simple params."""
    from codeplane.config.models import LoggingConfig, LogOutputConfig

    if config is None:
        config = LoggingConfig(
            level=level,
            outputs=[LogOutputConfig(format="json" if json_format else "console")],
        )

    default_level = _LEVEL_MAP.get(config.level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", key="timestamp"),
        _add_request_id,  # type: ignore[list-item]
    ]

    # Always use stdlib logging for proper resource management
    _configure_stdlib_logging(config, shared_processors, default_level)


def _create_handler(destination: str) -> logging.Handler:
    """Create handler for stderr, stdout, or file path."""
    if destination == "stderr":
        return logging.StreamHandler(sys.stderr)
    if destination == "stdout":
        return logging.StreamHandler(sys.stdout)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    return logging.FileHandler(path, mode="a")


def _configure_stdlib_logging(
    config: LoggingConfig,
    shared_processors: list[structlog.types.Processor],
    default_level: int,
) -> None:
    """Configure logging via stdlib (proper file handle management)."""
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(default_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Don't cache - allows reconfiguration and respects level changes
        cache_logger_on_first_use=False,
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(default_level)

    for output in config.outputs:
        output_level = _LEVEL_MAP.get((output.level or config.level).upper(), default_level)
        if output.format == "json":
            formatter = structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(),
                foreign_pre_chain=shared_processors,
            )
        else:
            formatter = structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(
                    colors=output.destination == "stderr" and sys.stderr.isatty(),
                    pad_event_to=0,
                    pad_level=False,
                ),
                foreign_pre_chain=shared_processors,
            )

        handler = _create_handler(output.destination)
        handler.setLevel(output_level)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger()
    if name:
        logger = logger.bind(logger=name)
    return logger  # type: ignore[no-any-return]
