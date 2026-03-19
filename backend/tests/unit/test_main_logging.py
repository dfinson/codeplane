from __future__ import annotations

import logging
from io import StringIO

import structlog

from backend.main import _ConsoleNoiseFilter, setup_logging


def _record(name: str, level: int) -> logging.LogRecord:
    return logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg="msg", args=(), exc_info=None)


def test_console_noise_filter_suppresses_uvicorn_info() -> None:
    filt = _ConsoleNoiseFilter()
    assert filt.filter(_record("uvicorn.error", logging.INFO)) is False


def test_console_noise_filter_allows_uvicorn_warning() -> None:
    filt = _ConsoleNoiseFilter()
    assert filt.filter(_record("uvicorn.error", logging.WARNING)) is True


def test_console_noise_filter_suppresses_sse_info() -> None:
    filt = _ConsoleNoiseFilter()
    assert filt.filter(_record("backend.services.sse_manager", logging.INFO)) is False


def test_console_noise_filter_allows_backend_main_info() -> None:
    filt = _ConsoleNoiseFilter()
    assert filt.filter(_record("backend.main", logging.INFO)) is True


def test_setup_logging_renders_structlog_human_readable(tmp_path) -> None:
    setup_logging(str(tmp_path / "codeplane.log"))

    root_logger = logging.getLogger()
    console_handler = next(
        handler
        for handler in root_logger.handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    )
    stream = StringIO()
    console_handler.setStream(stream)

    structlog.get_logger("backend.test").warning("recovering_orphaned_job", job_id="job-1", state="running")

    output = stream.getvalue()
    assert "recovering_orphaned_job" in output
    assert "job_id=job-1" in output
    assert "state=running" in output
    assert "{'job_id': 'job-1'" not in output
