"""Structured logging with request correlation.

Supports multiple simultaneous outputs with independent formats and levels.
All entries within a request share a correlation ID.
"""

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

# Context variable for request correlation
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Get current request correlation ID."""
    return _request_id.get()


def set_request_id(request_id: str | None = None) -> str:
    """Set request correlation ID. Generates one if not provided."""
    rid = request_id or uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def clear_request_id() -> None:
    """Clear request correlation ID."""
    _request_id.set(None)


def _add_request_id(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Processor that adds request_id to log entries."""
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
    """Configure structlog for CodePlane.

    For simple cases, use json_format and level directly.
    For multi-output or advanced config, pass a LoggingConfig object.

    Args:
        config: Full logging configuration. If provided, other args are ignored.
        json_format: Output JSON lines (True) or console format (False).
        level: Log level (DEBUG, INFO, WARN, ERROR).
    """
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
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _add_request_id,  # type: ignore[list-item]
    ]

    # For single output, use simple configuration
    if len(config.outputs) == 1:
        output = config.outputs[0]
        output_level = _LEVEL_MAP.get((output.level or config.level).upper(), default_level)

        if output.format == "json":
            processors: list[structlog.types.Processor] = [
                *shared_processors,
                structlog.processors.JSONRenderer(),
            ]
        else:
            processors = [
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
            ]

        file = _get_output_file(output.destination)
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(output_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=file),
            cache_logger_on_first_use=True,
        )
    else:
        # Multi-output: configure stdlib logging with multiple handlers
        _configure_multi_output(config, shared_processors, default_level)


def _get_output_file(destination: str) -> Any:
    """Get file object for destination."""
    if destination == "stderr":
        return sys.stderr
    if destination == "stdout":
        return sys.stdout
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a")  # noqa: SIM115


def _configure_multi_output(
    config: LoggingConfig,
    shared_processors: list[structlog.types.Processor],
    default_level: int,
) -> None:
    """Configure logging with multiple outputs via stdlib logging."""
    # Use structlog's stdlib integration for multi-output
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(default_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
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
                    colors=output.destination == "stderr" and sys.stderr.isatty()
                ),
                foreign_pre_chain=shared_processors,
            )

        handler = _create_handler(output.destination)
        handler.setLevel(output_level)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)


def _create_handler(destination: str) -> logging.Handler:
    """Create appropriate handler for destination."""
    if destination == "stderr":
        return logging.StreamHandler(sys.stderr)
    if destination == "stdout":
        return logging.StreamHandler(sys.stdout)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    return logging.FileHandler(path, mode="a")


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger instance, optionally bound to a name."""
    logger = structlog.get_logger()
    if name:
        logger = logger.bind(logger=name)
    return logger  # type: ignore[no-any-return]
