"""Structured logging with request correlation.

Supports JSON (machine) and console (human) output formats.
All entries within a request share a correlation ID.
"""

import sys
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import structlog

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


def configure_logging(
    *,
    json_format: bool = False,
    level: str = "INFO",
) -> None:
    """Configure structlog for CodePlane.

    Args:
        json_format: True for JSON lines, False for console format.
        level: Log level (DEBUG, INFO, WARN, ERROR).
    """
    import logging

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    log_level = level_map.get(level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _add_request_id,  # type: ignore[list-item]
    ]

    if json_format:
        processors: list[structlog.types.Processor] = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger instance, optionally bound to a name."""
    logger = structlog.get_logger()
    if name:
        logger = logger.bind(logger=name)
    return logger  # type: ignore[no-any-return]
