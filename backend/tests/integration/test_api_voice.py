"""API integration tests for Voice transcription endpoint.

Tests exercise the POST /api/voice/transcribe route including
content-type validation, size limits, and error handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx import AsyncClient


# ── Helpers ──────────────────────────────────────────────────────────


def _audio_file(
    data: bytes = b"\x00" * 128,
    content_type: str = "audio/webm",
    filename: str = "clip.webm",
) -> dict:
    """Return kwargs suitable for ``client.post(files=...)``."""
    return {"audio": (filename, data, content_type)}


# ── Transcribe ───────────────────────────────────────────────────────


class TestTranscribe:
    async def test_success(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/voice/transcribe",
            files=_audio_file(),
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "hello world"

    async def test_invalid_content_type(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/voice/transcribe",
            files=_audio_file(content_type="text/plain"),
        )
        assert resp.status_code == 415

    async def test_no_voice_service(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        original = app.state.voice_service
        app.state.voice_service = None
        try:
            resp = await client.post(
                "/api/voice/transcribe",
                files=_audio_file(),
            )
            assert resp.status_code == 501
        finally:
            app.state.voice_service = original

    async def test_file_too_large(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        original = app.state.voice_max_bytes
        app.state.voice_max_bytes = 64  # very small
        try:
            resp = await client.post(
                "/api/voice/transcribe",
                files=_audio_file(data=b"\x00" * 256),
            )
            assert resp.status_code == 413
        finally:
            app.state.voice_max_bytes = original

    async def test_empty_file(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/voice/transcribe",
            files=_audio_file(data=b""),
        )
        assert resp.status_code == 400

    async def test_transcribe_exception(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        svc = Mock()
        svc.transcribe = Mock(side_effect=RuntimeError("model crashed"))
        original = app.state.voice_service
        app.state.voice_service = svc
        try:
            # ASGITransport re-raises app exceptions by default
            with pytest.raises(RuntimeError, match="model crashed"):
                await client.post(
                    "/api/voice/transcribe",
                    files=_audio_file(),
                )
        finally:
            app.state.voice_service = original
