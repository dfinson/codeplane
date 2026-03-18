from __future__ import annotations

import logging

from backend.main import _ConsoleNoiseFilter


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