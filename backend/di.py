"""Dishka dependency-injection providers for CodePlane.

Defines the DI container wiring that replaces the previous hand-rolled
``app.state`` approach.  APP-scoped services are created once at startup
(via ``from_context``); REQUEST-scoped services are created per HTTP request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, NewType

from dishka import Provider, Scope, from_context, provide
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import CPLConfig
from backend.services.approval_service import ApprovalService
from backend.services.event_bus import EventBus
from backend.services.job_service import JobService
from backend.services.merge_service import MergeService
from backend.services.naming_service import NamingService
from backend.services.platform_adapter import PlatformRegistry
from backend.services.runtime_service import RuntimeService
from backend.services.sse_manager import SSEManager
from backend.services.sister_session import SisterSessionManager
from backend.services.voice_service import VoiceService

# NewType wrappers for plain values that need unique DI keys
CachedModelsBySdk = NewType("CachedModelsBySdk", dict[str, Any])
VoiceMaxBytes = NewType("VoiceMaxBytes", int)


class AppProvider(Provider):
    """APP-scoped services — created once during startup, live for the
    duration of the process.  Values are injected via the container's
    ``context`` dict at creation time."""

    scope = Scope.APP

    config = from_context(provides=CPLConfig)
    session_factory = from_context(provides=async_sessionmaker)
    event_bus = from_context(provides=EventBus)
    sse_manager = from_context(provides=SSEManager)
    approval_service = from_context(provides=ApprovalService)
    runtime_service = from_context(provides=RuntimeService)
    merge_service = from_context(provides=MergeService)
    platform_registry = from_context(provides=PlatformRegistry)
    sister_sessions = from_context(provides=SisterSessionManager)
    voice_service = from_context(provides=VoiceService)
    cached_models = from_context(provides=CachedModelsBySdk)
    voice_max_bytes = from_context(provides=VoiceMaxBytes)


class RequestProvider(Provider):
    """REQUEST-scoped dependencies — created fresh per HTTP request."""

    scope = Scope.REQUEST

    @provide
    async def session(
        self,
        sf: async_sessionmaker,  # type: ignore[type-arg]
    ) -> AsyncIterator[AsyncSession]:
        async with sf() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @provide
    def job_service(
        self,
        session: AsyncSession,
        config: CPLConfig,
        sister_sessions: SisterSessionManager,
    ) -> JobService:
        naming = NamingService(sister_sessions)
        return JobService.from_session(
            session,
            config,
            naming_service=naming,
        )
