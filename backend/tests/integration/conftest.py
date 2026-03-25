"""Fixtures for API integration tests.

Provides a fully-wired FastAPI test app backed by an in-memory SQLite
database and mock services where needed.  A dishka container provides
dependency injection exactly as the real lifespan does, but without
starting real subprocesses, loading ML models, or touching disk.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import CPLConfig
from backend.di import AppProvider, CachedModelsBySdk, RequestProvider, VoiceMaxBytes
from backend.models.db import Base, JobRow
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.approval_service import ApprovalService
from backend.services.event_bus import EventBus
from backend.services.git_service import GitService
from backend.services.job_service import JobNotFoundError
from backend.services.merge_service import MergeService
from backend.services.platform_adapter import PlatformRegistry
from backend.services.runtime_service import RuntimeService
from backend.services.sse_manager import SSEManager
from backend.services.utility_session import UtilitySessionService
from backend.services.voice_service import VoiceService

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    sa_event.listen(eng.sync_engine, "connect", _set_sqlite_pragmas)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Real lightweight services
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def sse_manager() -> SSEManager:
    return SSEManager()


@pytest.fixture
def approval_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> ApprovalService:
    return ApprovalService(session_factory=session_factory)


# ---------------------------------------------------------------------------
# Mock services (expensive / side-effecting)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_git_service() -> AsyncMock:
    svc = AsyncMock(spec=GitService)
    svc.validate_repo.return_value = True
    svc.get_default_branch.return_value = "main"
    svc.get_current_branch.return_value = "feature/my-branch"
    svc.create_worktree.return_value = ("/tmp/test-worktree", "fix/test-branch")
    svc.cleanup_worktrees.return_value = 0
    svc.get_origin_url.return_value = "https://github.com/test/repo.git"
    svc.clone_repo.return_value = "/tmp/cloned"
    svc.list_branches.return_value = set()
    svc.list_worktree_names.return_value = set()
    return svc


@pytest.fixture
def mock_runtime_service() -> AsyncMock:
    svc = AsyncMock(spec=RuntimeService)
    svc.start_or_enqueue.return_value = None
    svc.create_followup_job.side_effect = JobNotFoundError("Job ghost does not exist.")
    svc.cancel.return_value = None
    svc.pause_job.return_value = True
    svc.send_message.return_value = True
    return svc


@pytest.fixture
def mock_merge_service() -> AsyncMock:
    return AsyncMock(spec=MergeService)


@pytest.fixture
def mock_voice_service() -> Mock:
    svc = Mock()
    svc.transcribe.return_value = "hello world"
    return svc


@pytest.fixture
def mock_platform_registry() -> Mock:
    return Mock(spec=PlatformRegistry)


@pytest.fixture
def mock_utility_session() -> AsyncMock:
    svc = AsyncMock(spec=UtilitySessionService)
    # Return valid naming JSON so NamingService succeeds in tests
    svc.complete.return_value = '{"title": "Test Task", "branch_name": "fix/test-task", "worktree_name": "task-test"}'
    return svc


@pytest.fixture
def voice_max_bytes_value() -> int:
    """Default voice max bytes — override in specific test classes for smaller limits."""
    return 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _test_config() -> CPLConfig:
    return CPLConfig(repos=["/test/repo"])


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    sse_manager: SSEManager,
    approval_service: ApprovalService,
    mock_runtime_service: AsyncMock,
    mock_merge_service: AsyncMock,
    mock_git_service: AsyncMock,
    mock_voice_service: Mock,
    mock_platform_registry: Mock,
    mock_utility_session: AsyncMock,
    voice_max_bytes_value: int,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[FastAPI, None]:
    from backend.api import (
        approvals,
        artifacts,
        events,
        health,
        jobs,
        settings,
        terminal,
        voice,
        workspace,
    )
    from backend.app_factory import _register_domain_exception_handlers

    application = FastAPI(title="CodePlane", version="0.1.0")
    _register_domain_exception_handlers(application)

    # -- routers (same order as create_app) --------------------------------
    application.include_router(health.router, prefix="/api")
    application.include_router(jobs.router, prefix="/api")
    application.include_router(events.router, prefix="/api")
    application.include_router(approvals.router, prefix="/api")
    application.include_router(artifacts.router, prefix="/api")
    application.include_router(workspace.router, prefix="/api")
    application.include_router(voice.router, prefix="/api")
    application.include_router(settings.router, prefix="/api")
    application.include_router(terminal.router)  # already has /api/terminal prefix

    # -- dishka DI container (replaces app.state) --------------------------
    container = make_async_container(
        AppProvider(),
        RequestProvider(),
        context={
            CPLConfig: _test_config(),
            async_sessionmaker: session_factory,
            EventBus: event_bus,
            SSEManager: sse_manager,
            ApprovalService: approval_service,
            RuntimeService: mock_runtime_service,
            MergeService: mock_merge_service,
            PlatformRegistry: mock_platform_registry,
            UtilitySessionService: mock_utility_session,
            VoiceService: mock_voice_service,
            CachedModelsBySdk: CachedModelsBySdk({}),
            VoiceMaxBytes: VoiceMaxBytes(voice_max_bytes_value),
        },
    )
    setup_dishka(container, application)

    # -- config overrides (for non-dishka Depends still in settings.py) ----
    monkeypatch.setattr("backend.config.load_config", _test_config)
    monkeypatch.setattr("backend.api.settings._get_config", _test_config)

    # -- settings router git-service override ------------------------------
    application.dependency_overrides[settings._get_git_service] = lambda: mock_git_service

    # -- terminal module-level service -------------------------------------
    terminal.set_terminal_service(Mock())

    yield application

    await container.close()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

SeedJobFn = Callable[..., Coroutine[Any, Any, str]]


@pytest.fixture
def seed_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> SeedJobFn:
    """Return an async factory that inserts a job row and returns its ID."""

    async def _seed(
        state: str = "running",
        job_id: str | None = None,
        **overrides: Any,
    ) -> str:
        job_id = job_id or f"job-{uuid4().hex[:8]}"
        async with session_factory() as session:
            session.add(
                JobRow(
                    id=job_id,
                    repo="/test/repo",
                    prompt="Test prompt",
                    state=state,
                    base_ref="main",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    **overrides,
                )
            )
            await session.commit()
        return job_id

    return _seed
