"""Integration tests for the SSE events endpoint (GET /api/events)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from backend.tests.integration.conftest import SeedJobFn


# ---------------------------------------------------------------------------
# ASGI-level SSE helper
# ---------------------------------------------------------------------------


async def _raw_asgi_sse(
    app: FastAPI,
    *,
    query_params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Send a raw ASGI request to the SSE endpoint and capture the response.

    Returns dict with ``status``, ``headers`` (decoded), and ``body`` (bytes
    collected before the timeout fires).  Bypasses httpx entirely so we
    can cleanly cancel the infinite server-side generator.
    """
    qs = urlencode(query_params) if query_params else ""
    raw_headers: list[tuple[bytes, bytes]] = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode(), v.encode()))

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/events",
        "query_string": qs.encode(),
        "root_path": "",
        "headers": raw_headers,
        "server": ("test", 80),
    }

    result: dict[str, Any] = {}
    body_chunks: list[bytes] = []
    response_started = asyncio.Event()
    disconnect = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            result["status"] = message["status"]
            result["headers"] = {k.decode(): v.decode() for k, v in message.get("headers", [])}
            response_started.set()
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if chunk:
                body_chunks.append(chunk)

    task = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(response_started.wait(), timeout=timeout)
        # Give the generator a moment to yield the first frame
        await asyncio.sleep(0.1)
    except TimeoutError:
        pass
    finally:
        disconnect.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    result["body"] = b"".join(body_chunks)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSSEConnection:
    """Basic SSE connection and streaming behaviour."""

    @pytest.mark.asyncio
    async def test_connect_returns_200_event_stream(self, app: FastAPI) -> None:
        """GET /api/events returns 200 with text/event-stream content type."""
        r = await _raw_asgi_sse(app)
        assert r["status"] == 200
        assert "text/event-stream" in r["headers"]["content-type"]

    @pytest.mark.asyncio
    async def test_first_event_is_session_heartbeat(self, app: FastAPI) -> None:
        """First SSE frame should be a session_heartbeat event."""
        r = await _raw_asgi_sse(app)
        assert b"event: session_heartbeat" in r["body"]
        assert b"data: {}" in r["body"]

    @pytest.mark.asyncio
    async def test_no_cache_headers(self, app: FastAPI) -> None:
        """SSE response includes cache-control and keep-alive headers."""
        r = await _raw_asgi_sse(app)
        assert "no-cache" in r["headers"].get("cache-control", "")
        assert r["headers"].get("x-accel-buffering") == "no"


class TestSSEFiltering:
    """SSE connection with query parameters."""

    @pytest.mark.asyncio
    async def test_connect_with_job_id_filter(self, app: FastAPI, seed_job: SeedJobFn) -> None:
        """Connecting with ?job_id= still returns 200 and heartbeat."""
        job_id = await seed_job(state="running")
        r = await _raw_asgi_sse(app, query_params={"job_id": job_id})
        assert r["status"] == 200
        assert b"event: session_heartbeat" in r["body"]

    @pytest.mark.asyncio
    async def test_connect_with_nonexistent_job_id(self, app: FastAPI) -> None:
        """SSE endpoint does not validate job_id — still connects successfully."""
        r = await _raw_asgi_sse(app, query_params={"job_id": "nonexistent"})
        assert r["status"] == 200

    @pytest.mark.asyncio
    async def test_connect_with_last_event_id_query(self, app: FastAPI) -> None:
        """Last-Event-ID as query param is accepted without error."""
        r = await _raw_asgi_sse(app, query_params={"Last-Event-ID": "0"})
        assert r["status"] == 200
        assert b"event: session_heartbeat" in r["body"]

    @pytest.mark.asyncio
    async def test_connect_with_last_event_id_header(self, app: FastAPI) -> None:
        """Last-Event-ID as request header is accepted without error."""
        r = await _raw_asgi_sse(app, headers={"Last-Event-ID": "0"})
        assert r["status"] == 200


class TestSSEInfrastructureUnavailable:
    """SSE returns 503 when infrastructure is not ready."""

    @pytest.mark.asyncio
    async def test_missing_sse_manager_returns_503(self, app: FastAPI) -> None:
        """When sse_manager is missing from app.state, return 503."""
        from httpx import ASGITransport
        from httpx import AsyncClient as _AsyncClient

        original = app.state.sse_manager
        del app.state.sse_manager
        try:
            transport = ASGITransport(app=app)
            async with _AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/api/events")
                assert resp.status_code == 503
                assert "SSE infrastructure" in resp.json()["detail"]
        finally:
            app.state.sse_manager = original
