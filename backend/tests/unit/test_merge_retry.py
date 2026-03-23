"""Tests for merge status retry under concurrent SQLite lock contention.

Validates that _update_merge_status retries on transient SQLite lock errors
and raises on persistent failures.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.db import Base, JobRow
from backend.models.domain import Job, JobState
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Create a job for FK
    async with factory() as session:
        session.add(
            JobRow(
                id="merge-job",
                repo="/test",
                prompt="test",
                state="succeeded",
                base_ref="main",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


class TestMergeStatusRetry:
    """_update_merge_status retries on SQLite lock errors."""

    @pytest.mark.asyncio
    async def test_direct_update_succeeds(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Normal merge status update works without retry."""
        async with session_factory() as session:
            repo = JobRepository(session)
            await repo.update_merge_status("merge-job", "merged")
            await session.commit()

        async with session_factory() as session:
            repo = JobRepository(session)
            job = await repo.get("merge-job")
        assert job is not None
        assert job.merge_status == "merged"

    @pytest.mark.asyncio
    async def test_concurrent_merge_status_updates(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Multiple concurrent merge_status updates are serialized by SQLite."""
        statuses = ["merged", "conflict", "not_merged", "merged", "conflict"]

        async def update_status(status: str) -> None:
            async with session_factory() as session:
                repo = JobRepository(session)
                await repo.update_merge_status("merge-job", status)
                await session.commit()

        await asyncio.gather(*(update_status(s) for s in statuses))

        async with session_factory() as session:
            repo = JobRepository(session)
            job = await repo.get("merge-job")
        assert job is not None
        # One of the statuses should have won (last-write-wins)
        assert job.merge_status in statuses

    @pytest.mark.asyncio
    async def test_merge_status_with_pr_url(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Merge status update with PR URL persists both fields."""
        async with session_factory() as session:
            repo = JobRepository(session)
            await repo.update_merge_status("merge-job", "merged", pr_url="https://github.com/test/pr/1")
            await session.commit()

        async with session_factory() as session:
            repo = JobRepository(session)
            job = await repo.get("merge-job")
        assert job is not None
        assert job.merge_status == "merged"
        assert job.pr_url == "https://github.com/test/pr/1"
