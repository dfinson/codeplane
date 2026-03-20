"""Tests for the repository pattern persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.models.db import Base
from backend.models.domain import Artifact
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.tests.unit.conftest import make_job


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory SQLite database and yield an async session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    await engine.dispose()


# --- JobRepository tests ---


@pytest.mark.asyncio
async def test_job_create_and_get(session: AsyncSession) -> None:
    repo = JobRepository(session)
    job = make_job(worktree_path="/repos/test")
    await repo.create(job)
    await session.commit()

    result = await repo.get("job-1")
    assert result is not None
    assert result.id == "job-1"
    assert result.repo == "/repos/test"
    assert result.prompt == "Fix the bug"
    assert result.state == "running"


@pytest.mark.asyncio
async def test_job_get_returns_none_for_missing(session: AsyncSession) -> None:
    repo = JobRepository(session)
    result = await repo.get("job-999")
    assert result is None


@pytest.mark.asyncio
async def test_job_list_all(session: AsyncSession) -> None:
    repo = JobRepository(session)
    await repo.create(make_job(id="job-1", state="running", worktree_path="/repos/test"))
    await repo.create(make_job(id="job-2", state="succeeded", worktree_path="/repos/test"))
    await repo.create(make_job(id="job-3", state="failed", worktree_path="/repos/test"))
    await session.commit()

    jobs = await repo.list()
    assert len(jobs) == 3


@pytest.mark.asyncio
async def test_job_list_filter_by_state(session: AsyncSession) -> None:
    repo = JobRepository(session)
    await repo.create(make_job(id="job-1", state="running", worktree_path="/repos/test"))
    await repo.create(make_job(id="job-2", state="succeeded", worktree_path="/repos/test"))
    await repo.create(make_job(id="job-3", state="failed", worktree_path="/repos/test"))
    await session.commit()

    jobs = await repo.list(state="running")
    assert len(jobs) == 1
    assert jobs[0].id == "job-1"


@pytest.mark.asyncio
async def test_job_list_filter_multi_state(session: AsyncSession) -> None:
    repo = JobRepository(session)
    await repo.create(make_job(id="job-1", state="running", worktree_path="/repos/test"))
    await repo.create(make_job(id="job-2", state="succeeded", worktree_path="/repos/test"))
    await repo.create(make_job(id="job-3", state="failed", worktree_path="/repos/test"))
    await session.commit()

    jobs = await repo.list(state="succeeded,failed")
    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_job_list_cursor_pagination(session: AsyncSession) -> None:
    repo = JobRepository(session)
    for i in range(5):
        await repo.create(make_job(id=f"job-{i}", state="running", worktree_path="/repos/test"))
    await session.commit()

    # First page
    page1 = await repo.list(limit=2)
    assert len(page1) == 2

    # Second page using last item's ID as cursor
    page2 = await repo.list(limit=2, cursor=page1[-1].id)
    assert len(page2) == 2

    # Third page — only 1 remaining
    page3 = await repo.list(limit=2, cursor=page2[-1].id)
    assert len(page3) == 1

    # No duplicates across pages
    all_ids = [j.id for j in page1 + page2 + page3]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.asyncio
async def test_job_update_state(session: AsyncSession) -> None:
    repo = JobRepository(session)
    await repo.create(make_job(id="job-1", state="running", worktree_path="/repos/test"))
    await session.commit()

    now = datetime.now(UTC)
    await repo.update_state("job-1", "succeeded", now, completed_at=now)
    await session.commit()

    job = await repo.get("job-1")
    assert job is not None
    assert job.state == "succeeded"
    assert job.completed_at is not None


# --- EventRepository tests ---


@pytest.mark.asyncio
async def test_event_append_and_list(session: AsyncSession) -> None:
    # Create a job first (FK constraint)
    job_repo = JobRepository(session)
    await job_repo.create(make_job(id="job-1", worktree_path="/repos/test"))
    await session.commit()

    event_repo = EventRepository(session)
    now = datetime.now(UTC)
    event = DomainEvent(
        event_id="evt-1",
        job_id="job-1",
        timestamp=now,
        kind=DomainEventKind.job_created,
        payload={"repo": "/repos/test"},
    )
    await event_repo.append(event)
    await session.commit()

    events = await event_repo.list_after(0)
    assert len(events) == 1
    assert events[0].event_id == "evt-1"
    assert events[0].kind == DomainEventKind.job_created
    assert events[0].payload == {"repo": "/repos/test"}


@pytest.mark.asyncio
async def test_event_list_after_filters_by_id(session: AsyncSession) -> None:
    job_repo = JobRepository(session)
    await job_repo.create(make_job(id="job-1", worktree_path="/repos/test"))
    await session.commit()

    event_repo = EventRepository(session)
    now = datetime.now(UTC)
    for i in range(5):
        await event_repo.append(
            DomainEvent(
                event_id=f"evt-{i}",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": i},
            )
        )
    await session.commit()

    # Events have auto-increment IDs 1-5; list after ID 3
    events = await event_repo.list_after(3)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_event_list_after_scoped_to_job(session: AsyncSession) -> None:
    job_repo = JobRepository(session)
    await job_repo.create(make_job(id="job-1", worktree_path="/repos/test"))
    await job_repo.create(make_job(id="job-2", worktree_path="/repos/test"))
    await session.commit()

    event_repo = EventRepository(session)
    now = datetime.now(UTC)
    await event_repo.append(
        DomainEvent(event_id="evt-1", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload={})
    )
    await event_repo.append(
        DomainEvent(event_id="evt-2", job_id="job-2", timestamp=now, kind=DomainEventKind.job_created, payload={})
    )
    await session.commit()

    events = await event_repo.list_after(0, job_id="job-1")
    assert len(events) == 1
    assert events[0].job_id == "job-1"


# --- ArtifactRepository tests ---


@pytest.mark.asyncio
async def test_artifact_create_and_get(session: AsyncSession) -> None:
    job_repo = JobRepository(session)
    await job_repo.create(make_job(id="job-1", worktree_path="/repos/test"))
    await session.commit()

    artifact_repo = ArtifactRepository(session)
    now = datetime.now(UTC)
    artifact = Artifact(
        id="art-1",
        job_id="job-1",
        name="final.diff",
        type="diff_snapshot",
        mime_type="text/plain",
        size_bytes=1024,
        disk_path="/home/test/.codeplane/artifacts/job-1/art-1-final.diff",
        phase="finalization",
        created_at=now,
    )
    await artifact_repo.create(artifact)
    await session.commit()

    result = await artifact_repo.get("art-1")
    assert result is not None
    assert result.name == "final.diff"
    assert result.size_bytes == 1024


@pytest.mark.asyncio
async def test_artifact_get_returns_none_for_missing(session: AsyncSession) -> None:
    artifact_repo = ArtifactRepository(session)
    result = await artifact_repo.get("art-999")
    assert result is None


@pytest.mark.asyncio
async def test_artifact_list_for_job(session: AsyncSession) -> None:
    job_repo = JobRepository(session)
    await job_repo.create(make_job(id="job-1", worktree_path="/repos/test"))
    await job_repo.create(make_job(id="job-2", worktree_path="/repos/test"))
    await session.commit()

    artifact_repo = ArtifactRepository(session)
    now = datetime.now(UTC)
    await artifact_repo.create(
        Artifact(
            id="art-1",
            job_id="job-1",
            name="a.diff",
            type="diff_snapshot",
            mime_type="text/plain",
            size_bytes=100,
            disk_path="/p/a",
            phase="finalization",
            created_at=now,
        )
    )
    await artifact_repo.create(
        Artifact(
            id="art-2",
            job_id="job-2",
            name="b.diff",
            type="diff_snapshot",
            mime_type="text/plain",
            size_bytes=200,
            disk_path="/p/b",
            phase="finalization",
            created_at=now,
        )
    )
    await session.commit()

    arts = await artifact_repo.list_for_job("job-1")
    assert len(arts) == 1
    assert arts[0].id == "art-1"
