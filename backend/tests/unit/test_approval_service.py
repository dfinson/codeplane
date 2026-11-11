"""Tests for ApprovalService — create, resolve, wait, cleanup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.db import Base, JobRow, ProjectRow
from backend.models.domain import ApprovalAlreadyResolvedError, ApprovalNotFoundError
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.job.approval_service import ApprovalService

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
    async with factory() as session:
        session.add(
            ProjectRow(
                id="proj-1",
                name="Test Project",
                repo_paths='["/test"]',
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        for jid in ["job-1", "job-2"]:
            await session.flush()
            session.add(
                JobRow(
                    id=jid,
                    repo="/test",
                    project_id="proj-1",
                    prompt="test",
                    state="running",
                    base_ref="main",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
def svc(session_factory: async_sessionmaker[AsyncSession]) -> ApprovalService:
    return ApprovalService(session_factory)


class TestCreateRequest:
    @pytest.mark.asyncio
    async def test_creates_approval(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "Deploy changes?")
        assert approval.id
        assert approval.job_id == "job-1"
        assert approval.description == "Deploy changes?"
        assert approval.resolution is None
        assert approval.resolved_at is None

    @pytest.mark.asyncio
    async def test_creates_with_proposed_action(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "OK?", proposed_action="restart")
        assert approval.proposed_action == "restart"

    @pytest.mark.asyncio
    async def test_creates_pending_future(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "Check?")
        assert approval.id in svc._pending_futures
        assert not svc._pending_futures[approval.id].done()


class TestRuntimeContract:
    async def test_resolved_before_wait_is_read_from_database(self, svc):
        approval = await svc.create_request("job-1", "Fixture approval")
        await svc.resolve(approval.id, "approved")
        result = None
        try:
            result = await svc.wait_for_resolution(approval.id)
        except ApprovalNotFoundError:
            pass
        assert result == "approved"

    async def test_cancelled_future_does_not_leave_pending_row(self, svc):
        approval = await svc.create_request("job-1", "Fixture approval")
        svc._pending_futures[approval.id].cancel()
        await svc.cleanup_job("job-1")
        stored = await svc.get(approval.id)
        assert stored.resolution == "rejected"


class TestResolve:
    @pytest.mark.asyncio
    async def test_resolve_approval(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "OK?")
        resolved = await svc.resolve(approval.id, "approved")
        assert resolved.resolution == "approved"
        assert resolved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_resolve_unblocks_future(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "OK?")
        future = svc._pending_futures[approval.id]
        await svc.resolve(approval.id, "rejected")
        assert future.done()
        assert future.result() == "rejected"

    @pytest.mark.asyncio
    async def test_resolve_not_found_raises(self, svc: ApprovalService) -> None:
        with pytest.raises(ApprovalNotFoundError):
            await svc.resolve("nonexistent", "approved")

    @pytest.mark.asyncio
    async def test_double_resolve_raises(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "OK?")
        await svc.resolve(approval.id, "approved")
        with pytest.raises(ApprovalAlreadyResolvedError):
            await svc.resolve(approval.id, "rejected")


class TestWaitForResolution:
    @pytest.mark.asyncio
    async def test_wait_returns_resolution(self, svc: ApprovalService) -> None:
        approval = await svc.create_request("job-1", "Check?")

        async def resolve_later() -> None:
            await asyncio.sleep(0.05)
            await svc.resolve(approval.id, "approved")

        task = asyncio.create_task(resolve_later())
        result = await svc.wait_for_resolution(approval.id)
        assert result == "approved"
        await task

    @pytest.mark.asyncio
    async def test_wait_no_pending_future_raises(self, svc: ApprovalService) -> None:
        with pytest.raises(ApprovalNotFoundError):
            await svc.wait_for_resolution("no-such-id")


class TestListForJob:
    @pytest.mark.asyncio
    async def test_list_for_job_returns_approvals(self, svc: ApprovalService) -> None:
        await svc.create_request("job-1", "First?")
        await svc.create_request("job-1", "Second?")
        await svc.create_request("job-2", "Other?")
        result = await svc.list_for_job("job-1")
        assert len(result) == 2
        assert all(a.job_id == "job-1" for a in result)

    @pytest.mark.asyncio
    async def test_list_pending(self, svc: ApprovalService) -> None:
        a1 = await svc.create_request("job-1", "Pending?")
        a2 = await svc.create_request("job-1", "Also pending?")
        await svc.resolve(a1.id, "approved")
        pending = await svc.list_pending("job-1")
        assert len(pending) == 1
        assert pending[0].id == a2.id


class TestCleanupJob:
    @pytest.mark.asyncio
    async def test_cleanup_cancels_futures(self, svc: ApprovalService) -> None:
        a1 = await svc.create_request("job-1", "Check 1?")
        a2 = await svc.create_request("job-1", "Check 2?")
        a3 = await svc.create_request("job-2", "Other?")
        await svc.cleanup_job("job-1")
        assert svc._pending_futures.get(a1.id) is None
        assert svc._pending_futures.get(a2.id) is None
        # job-2 should be unaffected
        assert a3.id in svc._pending_futures
        assert not svc._pending_futures[a3.id].done()

    @pytest.mark.asyncio
    async def test_cleanup_removes_explicit_approval_ids(self, svc: ApprovalService) -> None:
        a1 = await svc.create_request("job-1", "git reset --hard?", requires_explicit_approval=True)
        assert a1.id in svc._explicit_approval_ids
        await svc.cleanup_job("job-1")
        assert a1.id not in svc._explicit_approval_ids


class TestTrustJob:
    @pytest.mark.asyncio
    async def test_trust_job_resolves_normal_approvals(self, svc: ApprovalService) -> None:
        a1 = await svc.create_request("job-1", "Normal action?")
        resolved = await svc.trust_job("job-1")
        assert resolved == 1
        future = svc._pending_futures.get(a1.id)
        assert future is None  # resolved and removed

    @pytest.mark.asyncio
    async def test_trust_job_skips_explicit_approvals(self, svc: ApprovalService) -> None:
        """git reset --hard (requires_explicit_approval=True) must NOT be auto-resolved by trust."""
        a_normal = await svc.create_request("job-1", "Normal action?")
        a_explicit = await svc.create_request("job-1", "git reset --hard HEAD", requires_explicit_approval=True)
        resolved = await svc.trust_job("job-1")

        # Only the normal approval should have been auto-resolved
        assert resolved == 1
        assert svc._pending_futures.get(a_normal.id) is None  # resolved
        assert a_explicit.id in svc._pending_futures  # still pending
        assert not svc._pending_futures[a_explicit.id].done()

    @pytest.mark.asyncio
    async def test_trust_job_marks_job_as_trusted(self, svc: ApprovalService) -> None:
        await svc.trust_job("job-1")
        assert svc.is_trusted("job-1")

    @pytest.mark.asyncio
    async def test_explicit_approval_stored_on_domain_object(self, svc: ApprovalService) -> None:
        a = await svc.create_request("job-1", "Hard reset", requires_explicit_approval=True)
        assert a.requires_explicit_approval is True

    @pytest.mark.asyncio
    async def test_normal_approval_not_explicit(self, svc: ApprovalService) -> None:
        a = await svc.create_request("job-1", "Regular action?")
        assert a.requires_explicit_approval is False


class TestConcurrentApprovals:
    """Verify the asyncio.Lock prevents race conditions under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_create_and_resolve(self, svc: ApprovalService) -> None:
        """Create and immediately resolve approvals concurrently."""
        a1 = await svc.create_request("job-1", "Action 1")
        a2 = await svc.create_request("job-1", "Action 2")

        async def resolve_one(aid: str) -> str:
            resolved = await svc.resolve(aid, "approved")
            return resolved.resolution  # type: ignore[return-value]

        results = await asyncio.gather(resolve_one(a1.id), resolve_one(a2.id))
        assert set(results) == {"approved"}
        assert len(svc._pending_futures) == 0

    @pytest.mark.asyncio
    async def test_concurrent_create_and_cleanup(self, svc: ApprovalService) -> None:
        """Create approvals while cleanup runs concurrently."""
        _a1 = await svc.create_request("job-1", "Action 1")

        async def create_another() -> str:
            _a = await svc.create_request("job-1", "Action 2")
            return _a.id

        async def cleanup() -> None:
            await svc.cleanup_job("job-1")

        # Run create and cleanup concurrently — neither should raise
        new_id, _ = await asyncio.gather(create_another(), cleanup())
        # The new approval may or may not have been cleaned up depending on
        # scheduling, but the data structures must be consistent
        remaining = {aid for aid, jid in svc._approval_to_job.items() if jid == "job-1"}
        for aid in remaining:
            assert aid in svc._pending_futures

    @pytest.mark.asyncio
    async def test_concurrent_trust_and_create(self, svc: ApprovalService) -> None:
        """Trust a job while new approvals are being created."""
        await svc.create_request("job-1", "Existing action")

        async def create_more() -> None:
            await svc.create_request("job-1", "New action")

        resolved, _ = await asyncio.gather(svc.trust_job("job-1"), create_more())
        # At least the first approval should have been resolved
        assert resolved >= 1
