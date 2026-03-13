"""Integration tests — approval flow, concurrent jobs, SSE replay, config operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import TowerConfig
from backend.models.db import Base
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.services.approval_service import ApprovalService
from backend.services.event_bus import EventBus
from backend.services.git_service import GitService
from backend.services.job_service import JobService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Create job rows for FK constraints
    from backend.models.db import JobRow

    async with factory() as session:
        for jid in ["job-1", "job-2"]:
            session.add(
                JobRow(
                    id=jid,
                    repo="/test",
                    prompt="test",
                    state="running",
                    strategy="single_agent",
                    base_ref="main",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
def config() -> TowerConfig:
    cfg = TowerConfig()
    cfg.repos = ["/test/repo"]
    return cfg


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


# --- Approval Flow Integration ---


class TestApprovalFlowIntegration:
    @pytest.mark.asyncio
    async def test_create_wait_resolve_cycle(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Full approval lifecycle: create → wait → resolve."""
        svc = ApprovalService(session_factory)

        # Create approval
        approval = await svc.create_request("job-1", "Deploy?")
        assert approval.id
        assert approval.resolution is None

        # Resolve in background
        async def resolve_later():
            await asyncio.sleep(0.05)
            await svc.resolve(approval.id, "approved")

        task = asyncio.create_task(resolve_later())
        # Wait blocks until resolved
        resolution = await svc.wait_for_resolution(approval.id)
        assert resolution == "approved"
        await task

    @pytest.mark.asyncio
    async def test_cleanup_unblocks_waiting_code(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Cleanup cancels pending futures, which should raise CancelledError."""
        svc = ApprovalService(session_factory)
        approval = await svc.create_request("job-1", "Check?")

        async def wait_and_catch():
            try:
                await svc.wait_for_resolution(approval.id)
                return "resolved"
            except asyncio.CancelledError:
                return "cancelled"

        task = asyncio.create_task(wait_and_catch())
        await asyncio.sleep(0.01)
        svc.cleanup_job("job-1")
        result = await task
        assert result == "cancelled"

    @pytest.mark.asyncio
    async def test_multiple_approvals_per_job(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        svc = ApprovalService(session_factory)
        a1 = await svc.create_request("job-1", "First check?")
        a2 = await svc.create_request("job-1", "Second check?")

        await svc.resolve(a1.id, "approved")
        await svc.resolve(a2.id, "rejected")

        all_approvals = await svc.list_for_job("job-1")
        assert len(all_approvals) == 2
        resolutions = {a.id: a.resolution for a in all_approvals}
        assert resolutions[a1.id] == "approved"
        assert resolutions[a2.id] == "rejected"


# --- Event Bus Integration ---


class TestEventBusIntegration:
    @pytest.mark.asyncio
    async def test_concurrent_subscribers(self, event_bus: EventBus) -> None:
        """Multiple subscribers receive the same event."""
        from backend.models.events import DomainEvent, DomainEventKind

        received: list[str] = []

        async def sub1(event: DomainEvent) -> None:
            received.append(f"sub1:{event.kind.value}")

        async def sub2(event: DomainEvent) -> None:
            received.append(f"sub2:{event.kind.value}")

        event_bus.subscribe(sub1)
        event_bus.subscribe(sub2)

        event = DomainEvent(
            event_id="evt-1",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.job_state_changed,
            payload={"old_state": "queued", "new_state": "running"},
        )
        await event_bus.publish(event)

        assert "sub1:JobStateChanged" in received
        assert "sub2:JobStateChanged" in received

    @pytest.mark.asyncio
    async def test_subscriber_error_does_not_crash_bus(self, event_bus: EventBus) -> None:
        from backend.models.events import DomainEvent, DomainEventKind

        received: list[str] = []

        async def bad_sub(event: DomainEvent) -> None:
            raise ValueError("subscriber failed")

        async def good_sub(event: DomainEvent) -> None:
            received.append("ok")

        event_bus.subscribe(bad_sub)
        event_bus.subscribe(good_sub)

        event = DomainEvent(
            event_id="evt-1",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.job_state_changed,
            payload={},
        )
        await event_bus.publish(event)
        assert "ok" in received


# --- Config Registration Integration ---


class TestConfigIntegration:
    def test_register_unregister_repo(self) -> None:
        from backend.config import TowerConfig, register_repo, unregister_repo

        config = TowerConfig()
        assert config.repos == []

        register_repo(config, "/test/repo")
        assert "/test/repo" in config.repos

        # Duplicate registration is idempotent
        register_repo(config, "/test/repo")
        assert config.repos.count("/test/repo") == 1

        unregister_repo(config, "/test/repo")
        assert "/test/repo" not in config.repos

    def test_unregister_missing_raises(self) -> None:
        from backend.config import TowerConfig, unregister_repo

        config = TowerConfig()
        with pytest.raises(ValueError):
            unregister_repo(config, "/nonexistent")


# --- Job State Machine Integration ---


class TestJobStateMachineIntegration:
    @pytest.mark.asyncio
    async def test_job_create_list_get(
        self, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """Create a job and retrieve it."""
        async with session_factory() as session:
            from unittest.mock import AsyncMock

            git = AsyncMock(spec=GitService)
            git.validate_repo = AsyncMock(return_value=True)
            git.get_default_branch = AsyncMock(return_value="main")
            git.create_worktree = AsyncMock(return_value=("/test/worktree", "fix/branch"))

            svc = JobService(job_repo=JobRepository(session), git_service=git, config=config)
            job = await svc.create_job(repo="/test/repo", prompt="Fix bug")
            await session.commit()

            # List
            jobs, cursor, has_more = await svc.list_jobs()
            assert len(jobs) >= 1
            assert any(j.id == job.id for j in jobs)

            # Get
            fetched = await svc.get_job(job.id)
            assert fetched.prompt == "Fix bug"

    @pytest.mark.asyncio
    async def test_cancel_job(self, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig) -> None:
        async with session_factory() as session:
            from unittest.mock import AsyncMock

            git = AsyncMock(spec=GitService)
            git.validate_repo = AsyncMock(return_value=True)
            git.get_default_branch = AsyncMock(return_value="main")
            git.create_worktree = AsyncMock(return_value=("/test/worktree", "fix/b"))

            svc = JobService(job_repo=JobRepository(session), git_service=git, config=config)
            job = await svc.create_job(repo="/test/repo", prompt="Fix bug")
            await session.commit()

            cancelled = await svc.cancel_job(job.id)
            assert cancelled.state == "canceled"
