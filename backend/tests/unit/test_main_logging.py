from __future__ import annotations

import logging
from io import StringIO

import structlog

from backend.main import ConsoleNoiseFilter, setup_logging


def _record(name: str, level: int) -> logging.LogRecord:
    return logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg="msg", args=(), exc_info=None)


def test_console_noise_filter_suppresses_uvicorn_info() -> None:
    filt = ConsoleNoiseFilter()
    assert filt.filter(_record("uvicorn.error", logging.INFO)) is False


def test_console_noise_filter_allows_uvicorn_warning() -> None:
    filt = ConsoleNoiseFilter()
    assert filt.filter(_record("uvicorn.error", logging.WARNING)) is True


def test_console_noise_filter_suppresses_invalid_http_request() -> None:
    filt = ConsoleNoiseFilter()
    rec = logging.LogRecord(
        name="uvicorn.error",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Invalid HTTP request received.",
        args=(),
        exc_info=None,
    )
    assert filt.filter(rec) is False


def test_console_noise_filter_suppresses_sse_info() -> None:
    filt = ConsoleNoiseFilter()
    assert filt.filter(_record("backend.services.events.sse_manager", logging.INFO)) is False


def test_console_noise_filter_allows_backend_main_info() -> None:
    filt = ConsoleNoiseFilter()
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


def test_setup_logging_console_traceback_omits_frame_locals(tmp_path) -> None:
    setup_logging(str(tmp_path / "codeplane.log"))
    console_handler = next(
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    )
    stream = StringIO()
    console_handler.setStream(stream)

    def fail(token: str) -> None:
        raise RuntimeError("completion failed")

    sentinel = "ghu_" + "console-local-sentinel"
    try:
        fail(sentinel)
    except RuntimeError:
        structlog.get_logger("backend.test").error("completion_failed", exc_info=True)

    output = stream.getvalue()
    assert "RuntimeError: completion failed" in output
    assert sentinel not in output
