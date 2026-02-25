"""Structured logging with request correlation and multi-output support.

Supports:
- Console suppression during Rich live displays (spinners, progress bars)
- Separate console vs file log levels
- Request correlation IDs
- Session-based log format: "Agent Session <session-id>: tool -> summary"
- Log file path tracking for exception pointers
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

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Track the current log file path for exception pointers
_log_file_path: Path | None = None


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str | None = None) -> str:
    """Set or generate request correlation ID."""
    rid = request_id or uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def clear_request_id() -> None:
    _request_id.set(None)


def get_log_file_path() -> Path | None:
    """Get the current log file path, if any.

    Used for exception log pointers to direct users to detailed logs.
    """
    return _log_file_path


def _set_log_file_path(path: Path | None) -> None:
    """Set the current log file path.

    Called internally during logging configuration.
    """
    global _log_file_path
    _log_file_path = path


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


class ConsoleSuppressingFilter(logging.Filter):
    """Filter that blocks structlog console output.

    This filter ensures NO structlog messages appear on the console.
    All user-facing output should go through Rich console.print() calls
    in progress.py and middleware.py, which are controlled and formatted.

    Structlog messages (including errors) go to the log file only.
    This prevents stacktraces and raw error messages from leaking
    to the terminal.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: ARG002
        # Import here to avoid circular dependency
        from codeplane.core.progress import is_console_suppressed

        # Block all logs during Live displays (spinners, progress bars)
        if is_console_suppressed():
            return False

        # Block ALL structlog output from console - use Rich console.print() instead
        # This prevents error/stacktrace leakage to terminal
        return False


def configure_logging(
    *,
    config: LoggingConfig | None = None,
    json_format: bool = False,
    level: str = "INFO",
) -> None:
    """Configure structlog. Pass config for multi-output, or use simple params.

    Args:
        config: Logging configuration with outputs
        json_format: Use JSON format for simple setup
        level: Default log level
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
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", key="timestamp"),
        _add_request_id,  # type: ignore[list-item]
    ]

    # Always use stdlib logging for proper resource management
    _configure_stdlib_logging(config, shared_processors, default_level)


def _create_handler(destination: str, is_console: bool = False) -> logging.Handler:
    """Create handler for stderr, stdout, or file path.

    Args:
        destination: "stderr", "stdout", or a file path
        is_console: If True, add console suppression filter
    """
    handler: logging.Handler
    if destination == "stderr":
        handler = logging.StreamHandler(sys.stderr)
    elif destination == "stdout":
        handler = logging.StreamHandler(sys.stdout)
    else:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="a")

    # Add suppression filter only to console handlers
    if is_console:
        handler.addFilter(ConsoleSuppressingFilter())

    return handler


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

    # Silence noisy upstream MCP SDK loggers (low-value per-request spam)
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)

    # Silence FastMCP's "Sending INFO/DEBUG to client" spam on every notification
    logging.getLogger("fastmcp.server.context.to_client").setLevel(logging.WARNING)

    # Silence watchfiles debug logging (triggers on every filtered change,
    # creates feedback loop with log file inside watched directory)
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

    # Track first file destination for exception log pointers
    _set_log_file_path(None)

    for output in config.outputs:
        output_level = _LEVEL_MAP.get((output.level or config.level).upper(), default_level)
        is_console = output.destination in ("stderr", "stdout")

        # Track the first file destination for exception pointers
        if not is_console and _log_file_path is None:
            _set_log_file_path(Path(output.destination))

        if output.format == "json":
            formatter = structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(),
                foreign_pre_chain=shared_processors,
            )
        else:
            formatter = structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(
                    colors=is_console and sys.stderr.isatty(),
                    pad_event_to=0,
                    pad_level=False,
                ),
                foreign_pre_chain=shared_processors,
            )

        handler = _create_handler(output.destination, is_console=is_console)
        handler.setLevel(output_level)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger()
    if name:
        logger = logger.bind(logger=name)
    return logger  # type: ignore[no-any-return]
