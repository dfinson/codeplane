"""Logging configuration for CodePlane.

Configures structlog + stdlib logging with rotating file handler and
console handler with noise filtering.

When a ``ConsoleLog`` is supplied to ``setup_logging`` the plain stderr
handler is replaced with a ``ConsoleLogHandler`` that suppresses
everything below ERROR on the console (all levels still reach the log
file) and prints errors as structured lines.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from backend.console_dashboard import ConsoleLog

_LOG_LEVEL_MAP: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_CONSOLE_NOISE_PREFIXES: tuple[str, ...] = (
    "alembic",
    "uvicorn.access",
    "uvicorn.error",
    "mcp.server.streamable_http_manager",
    "backend.services.sse_manager",
    "backend.services.voice_service",
    "backend.services.utility_session",
)

# Warning-level messages that are safe to suppress on the console.
# They still reach the log file.
_SUPPRESSED_WARNINGS: tuple[tuple[str, str], ...] = (
    ("uvicorn.error", "Invalid HTTP request received."),
)


class _ConsoleNoiseFilter(logging.Filter):
    """Keep warnings/errors on console while suppressing chatty info logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            for logger_name, message in _SUPPRESSED_WARNINGS:
                if record.name == logger_name and record.getMessage() == message:
                    return False
            return True
        return not any(record.name.startswith(prefix) for prefix in _CONSOLE_NOISE_PREFIXES)


def setup_logging(
    log_file: str,
    console_level: str = "info",
    max_file_size_mb: int = 50,
    backup_count: int = 3,
    dashboard: ConsoleLog | None = None,
) -> None:
    """Configure structlog + stdlib logging.

    Strategy
    --------
    * **File handler** — always at DEBUG verbosity so every log line is
      persisted.  Uses a rotating handler (``max_file_size_mb`` × ``backup_count``).
    * **Console handler** — two modes:

      - *Plain mode* (``dashboard=None``): respects ``console_level`` from
        config (default info) so the terminal stays readable at runtime.
      - *Console log mode* (``dashboard`` provided): installs a
        ``ConsoleLogHandler`` that prints ERROR records as structured
        lines and suppresses everything else on the console (they remain
        in the file).  Before ``start()`` is called the handler falls
        back to a plain stderr stream so startup messages are not lost.
    * **structlog** — uses the same stdlib handlers so all structured
      context fields are serialised consistently.
    """
    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.KeyValueRenderer(
                key_order=["timestamp", "level", "logger", "event"],
                sort_keys=True,
            ),
        ],
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )

    # File handler: DEBUG, rotating per config
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_file_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Console handler: structured console log or plain stderr
    if dashboard is not None:
        from backend.console_dashboard import ConsoleLogHandler

        console_handler: logging.Handler = ConsoleLogHandler(
            console_log=dashboard,
            fallback_formatter=console_formatter,
            fallback_filter=_ConsoleNoiseFilter(),
        )
    else:
        console_int = _LOG_LEVEL_MAP.get(console_level.lower(), logging.INFO)
        plain = logging.StreamHandler()
        plain.setLevel(console_int)
        plain.setFormatter(console_formatter)
        plain.addFilter(_ConsoleNoiseFilter())
        console_handler = plain

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # let handlers decide what to suppress
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Suppress chatty third-party loggers from polluting the debug file
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
