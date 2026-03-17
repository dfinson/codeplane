"""Tests for ApprovalService — create, resolve, wait, cleanup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.db import Base, JobRow
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.approval_service import (
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    ApprovalService,
)

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
        for jid in ["job-1", "job-2"]:
            session.add(
                JobRow(
                    id=jid,
                    repo="/test",
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
        svc.cleanup_job("job-1")
        assert svc._pending_futures.get(a1.id) is None
        assert svc._pending_futures.get(a2.id) is None
        # job-2 should be unaffected
        assert a3.id in svc._pending_futures
        assert not svc._pending_futures[a3.id].done()
