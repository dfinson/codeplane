"""Tests for the /api/models endpoint — verifies it serves from startup cache."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.jobs import router as jobs_router


def _make_app(cached_models: list[dict[str, object]]) -> FastAPI:
    """Minimal FastAPI app with cached_models_by_sdk wired into app.state."""
    app = FastAPI()
    app.include_router(jobs_router, prefix="/api")
    app.state.cached_models_by_sdk = {"copilot": cached_models}
    return app


@pytest.mark.asyncio
async def test_models_returns_cached_list() -> None:
    """GET /api/models returns the list cached at startup — no SDK call."""
    models = [{"id": "claude-3-5-sonnet", "name": "Claude 3.5 Sonnet"}]
    app = _make_app(models)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == models


@pytest.mark.asyncio
async def test_models_returns_empty_when_cache_is_empty() -> None:
    """If the startup cache is empty (SDK unavailable), endpoint returns []."""
    app = _make_app([])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == []
