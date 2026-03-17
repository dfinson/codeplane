"""Tests for ApprovalRepository — CRUD with DB."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.db import Base, JobRow
from backend.models.domain import Approval
from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.database import _set_sqlite_pragmas

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        # Create a job row for FK constraints
        sess.add(
            JobRow(
                id="job-1",
                repo="/test",
                prompt="test",
                state="running",
                base_ref="main",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await sess.flush()
        yield sess
    await engine.dispose()


def _make_approval(approval_id: str = "approval-1", job_id: str = "job-1") -> Approval:
    return Approval(
        id=approval_id,
        job_id=job_id,
        description="Deploy to production?",
        proposed_action="restart service",
        requested_at=datetime.now(UTC),
    )


class TestApprovalRepo:
    @pytest.mark.asyncio
    async def test_create_and_get(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        approval = _make_approval()
        await repo.create(approval)
        await session.flush()

        fetched = await repo.get("approval-1")
        assert fetched is not None
        assert fetched.description == "Deploy to production?"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        assert await repo.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_for_job(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        await repo.create(_make_approval("a-1"))
        await repo.create(_make_approval("a-2"))
        await session.flush()

        result = await repo.list_for_job("job-1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_pending(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        await repo.create(_make_approval("a-pending"))
        a_resolved = _make_approval("a-resolved")
        a_resolved = Approval(**{**a_resolved.__dict__, "resolution": "approved", "resolved_at": datetime.now(UTC)})
        await repo.create(a_resolved)
        await session.flush()

        pending = await repo.list_pending()
        assert len(pending) == 1
        assert pending[0].id == "a-pending"

    @pytest.mark.asyncio
    async def test_resolve_atomically(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        await repo.create(_make_approval("a-to-resolve"))
        await session.flush()

        now = datetime.now(UTC)
        result = await repo.resolve("a-to-resolve", "approved", now)
        assert result is not None
        assert result.resolution == "approved"
        assert result.resolved_at is not None

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_returns_none(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        await repo.create(_make_approval("a-double"))
        await session.flush()

        now = datetime.now(UTC)
        first = await repo.resolve("a-double", "approved", now)
        assert first is not None
        second = await repo.resolve("a-double", "rejected", now)
        assert second is None

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_returns_none(self, session: AsyncSession) -> None:
        repo = ApprovalRepository(session)
        now = datetime.now(UTC)
        result = await repo.resolve("nonexistent", "approved", now)
        assert result is None
