"""Fixtures for API integration tests.

Provides a fully-wired FastAPI test app backed by an in-memory SQLite
database and mock services where needed.  A dishka container provides
dependency injection exactly as the real lifespan does, but without
starting real subprocesses, loading ML models, or touching disk.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock
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

from backend.api import settings as _settings  # noqa: F401 - bind route types before per-test Git factory patches
from backend.config import CPLConfig
from backend.di import AppProvider, CachedModelsBySdk, RequestProvider, VoiceMaxBytes
from backend.models.db import Base, JobRow
from backend.models.domain import JobNotFoundError
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.adapters.platform_adapter import PlatformRegistry
from backend.services.completers.voice_service import VoiceService
from backend.services.events.event_bus import EventBus
from backend.services.events.ingest_service import IngestService
from backend.services.events.sse_manager import SSEManager
from backend.services.git.git_service import GitService
from backend.services.ingest.body_limit import ObservationBodyLimit
from backend.services.job.approval_service import ApprovalService
from backend.services.merge_service import MergeService
from backend.services.runtime import RuntimeService
from backend.services.sharing.share_service import ShareService
from backend.services.sidecar.session import SidecarSessionManager
from backend.services.task_execution_service import TaskExecutionService
from backend.services.terminal.terminal_service import TerminalService
from backend.services.tracker_sync_service import TrackerSyncService
from backend.services.tracker_write_service import TrackerWriteService

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
    svc.list_worktree_names = MagicMock(return_value=set())
    svc.has_active_worktree = MagicMock(return_value=False)
    svc.get_active_worktree_count = MagicMock(return_value=0)
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
    svc = AsyncMock(spec=SidecarSessionManager)
    # Return valid naming JSON so NamingService succeeds in tests
    svc.complete.return_value = '{"title": "Test Task", "branch_name": "fix/test-task", "worktree_name": "task-test"}'
    return svc


@pytest.fixture
def mock_terminal_service() -> Mock:
    return Mock()


@pytest.fixture
def mock_tracker_sync_service() -> AsyncMock:
    return AsyncMock(spec=TrackerSyncService)


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
    mock_terminal_service: Mock,
    mock_tracker_sync_service: AsyncMock,
    voice_max_bytes_value: int,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[FastAPI, None]:
    from backend.api import (
        approvals,
        artifacts,
        chats,
        credentials,
        events,
        health,
        integrations,
        job_artifacts,
        job_telemetry,
        jobs,
        projects,
        recipe_runs,
        recipes,
        reviews,
        session_coordination,
        settings,
        share,
        terminal,
        tracker_links,
        voice,
        workspace,
    )
    from backend.app_factory import _register_domain_exception_handlers

    application = FastAPI(title="CodePlane", version="0.1.0")
    application.add_middleware(ObservationBodyLimit)
    _register_domain_exception_handlers(application)

    # -- routers (same order as create_app) --------------------------------
    application.include_router(health.router, prefix="/api")
    application.include_router(jobs.router, prefix="/api")
    application.include_router(session_coordination.router, prefix="/api")
    application.include_router(job_artifacts.router, prefix="/api")
    application.include_router(job_telemetry.router, prefix="/api")
    application.include_router(events.router, prefix="/api")
    application.include_router(approvals.router, prefix="/api")
    application.include_router(artifacts.router, prefix="/api")
    application.include_router(workspace.router, prefix="/api")
    application.include_router(voice.router, prefix="/api")
    application.include_router(settings.router, prefix="/api")
    application.include_router(credentials.router, prefix="/api")
    application.include_router(projects.router, prefix="/api")
    application.include_router(share.router, prefix="/api")
    application.include_router(terminal.router, prefix="/api")
    application.include_router(chats.router, prefix="/api")
    application.include_router(tracker_links.router, prefix="/api")
    application.include_router(recipes.router, prefix="/api")
    application.include_router(integrations.router, prefix="/api")
    application.include_router(recipe_runs.router, prefix="/api")
    application.include_router(reviews.router, prefix="/api")

    config = _test_config()
    tracker_write_service = TrackerWriteService(
        approval_service,
        session_factory=session_factory,
        event_bus=event_bus,
    )
    task_execution_service = TaskExecutionService(
        session_factory,
        config,
        git_service=mock_git_service,
        approval_service=approval_service,
        tracker_write_service=tracker_write_service,
    )
    monkeypatch.setattr(
        "backend.services.tracker_resolution.dispatch_tracker_write",
        AsyncMock(side_effect=RuntimeError("Tracker dispatch must be configured by the test")),
    )

    # -- dishka DI container (replaces app.state) --------------------------
    container = make_async_container(
        AppProvider(),
        RequestProvider(),
        context={
            CPLConfig: config,
            async_sessionmaker: session_factory,
            EventBus: event_bus,
            SSEManager: sse_manager,
            ApprovalService: approval_service,
            RuntimeService: mock_runtime_service,
            ShareService: ShareService(session_factory),
            MergeService: mock_merge_service,
            GitService: mock_git_service,
            PlatformRegistry: mock_platform_registry,
            SidecarSessionManager: mock_utility_session,
            VoiceService: mock_voice_service,
            CachedModelsBySdk: CachedModelsBySdk({}),
            VoiceMaxBytes: VoiceMaxBytes(voice_max_bytes_value),
            TerminalService: mock_terminal_service,
            IngestService: AsyncMock(spec=IngestService),
            TrackerSyncService: mock_tracker_sync_service,
            TrackerWriteService: tracker_write_service,
            TaskExecutionService: task_execution_service,
        },
    )
    setup_dishka(container, application)

    # -- config overrides (for non-dishka load_config calls) ----
    monkeypatch.setattr("backend.config.load_config", _test_config)

    try:
        yield application
    finally:
        await asyncio.wait_for(tracker_write_service.close(), timeout=5)
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
    """Return an async factory that inserts a job row and returns its ID.

    A Job can never exist without an owning Project (AD-5) — this seeds (or
    reuses) a default Project owning the job's repo unless the caller passes
    an explicit ``project_id`` override.
    """

    async def _seed(
        state: str = "running",
        job_id: str | None = None,
        **overrides: Any,
    ) -> str:
        job_id = job_id or f"job-{uuid4().hex[:8]}"
        repo = overrides.pop("repo", "/test/repo")
        project_id = overrides.pop("project_id", None)
        async with session_factory() as session:
            if project_id is None:
                from backend.persistence.project_repo import ProjectRepository

                project_repo = ProjectRepository(session)
                project_id = "proj-default"
                existing = await project_repo.get(project_id)
                if existing is None:
                    await project_repo.create(project_id, "Default", [repo])
            session.add(
                JobRow(
                    id=job_id,
                    repo=repo,
                    project_id=project_id,
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
