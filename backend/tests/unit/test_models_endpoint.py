"""Tests for the /api/models endpoint — verifies it serves from startup cache."""

from __future__ import annotations

import sys

import pytest
from dishka import Provider, Scope, from_context, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.jobs import router as jobs_router
from backend.di import CachedModelsBySdk


class _ModelsProvider(Provider):
    """Provide dependencies for ambient model discovery."""

    scope = Scope.APP
    models = from_context(provides=CachedModelsBySdk)
    session_factory = from_context(provides=async_sessionmaker[AsyncSession])


async def _make_app(cached_models: list[dict[str, object]]) -> FastAPI:
    """Minimal FastAPI app with cached_models_by_sdk wired via dishka."""
    app = FastAPI()
    app.include_router(jobs_router, prefix="/api")
    container = make_async_container(
        _ModelsProvider(),
        context={
            CachedModelsBySdk: CachedModelsBySdk({"copilot": cached_models}),
            async_sessionmaker[AsyncSession]: async_sessionmaker(),
        },
    )
    setup_dishka(container, app)
    return app


@pytest.mark.asyncio
async def test_models_returns_cached_list() -> None:
    """GET /api/models returns the list cached at startup — no SDK call."""
    models = [{"id": "claude-3-5-sonnet", "name": "Claude 3.5 Sonnet"}]
    app = await _make_app(models)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == {"items": models}


@pytest.mark.asyncio
async def test_models_returns_empty_when_cache_is_empty() -> None:
    """If the startup cache is empty (SDK unavailable), endpoint returns []."""
    app = await _make_app([])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.MonkeyPatch.context() as mp:
            # Block the live-fetch fallback so the endpoint returns the empty cache as-is.
            mp.setitem(sys.modules, "copilot", None)
            resp = await client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}
