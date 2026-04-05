"""Test that the health endpoint returns a valid response."""

from __future__ import annotations

from collections.abc import AsyncGenerator  # noqa: TC003 — pytest-asyncio resolves at runtime

import pytest
from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app_factory import _register_domain_exception_handlers
from backend.config import CPLConfig
from backend.di import (
    AppProvider,
    CachedModelsBySdk,
    RequestProvider,
    VoiceMaxBytes,
)
from backend.models.db import Base


def _make_mock_services() -> dict[type, object]:
    from unittest.mock import AsyncMock, Mock

    from backend.services.approval_service import ApprovalService
    from backend.services.event_bus import EventBus
    from backend.services.merge_service import MergeService
    from backend.services.platform_adapter import PlatformRegistry
    from backend.services.runtime_service import RuntimeService
    from backend.services.sse_manager import SSEManager
    from backend.services.sister_session import SisterSessionManager
    from backend.services.voice_service import VoiceService

    return {
        EventBus: EventBus(),
        SSEManager: SSEManager(),
        ApprovalService: AsyncMock(spec=ApprovalService),
        RuntimeService: AsyncMock(spec=RuntimeService),
        MergeService: AsyncMock(spec=MergeService),
        PlatformRegistry: Mock(spec=PlatformRegistry),
        SisterSessionManager: AsyncMock(spec=SisterSessionManager),
        VoiceService: Mock(),
        CachedModelsBySdk: CachedModelsBySdk({}),
        VoiceMaxBytes: VoiceMaxBytes(10 * 1024 * 1024),
    }


@pytest.fixture
async def app() -> AsyncGenerator[FastAPI, None]:
    from backend.api import health

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    application = FastAPI(title="CodePlane-test")
    _register_domain_exception_handlers(application)
    application.include_router(health.router, prefix="/api")

    container = make_async_container(
        AppProvider(),
        RequestProvider(),
        context={
            CPLConfig: CPLConfig(repos=[]),
            async_sessionmaker: session_factory,
            **_make_mock_services(),
        },
    )
    setup_dishka(container, application)

    yield application

    await container.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_health_returns_healthy(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["version"]
    assert "uptimeSeconds" in data
