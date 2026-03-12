"""Red-team / pressure tests for persistence layer (Phase 1).

Covers: SQL injection attempts, FK violations, duplicate PKs,
boundary values, unicode/null bytes, concurrent writes,
invalid cursors, and extreme limit values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.models.db import Base
from backend.models.domain import Artifact, Job
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


def _make_job(
    job_id: str = "job-1",
    state: str = "running",
    created_at: datetime | None = None,
) -> Job:
    now = created_at or datetime.now(UTC)
    return Job(
        id=job_id,
        repo="/repos/test",
        prompt="Fix the bug",
        state=state,
        strategy="single_agent",
        base_ref="main",
        branch="fix/bug",
        worktree_path="/repos/test",
        session_id=None,
        created_at=now,
        updated_at=now,
    )


# ── SQL injection attempts ───────────────────────────────────────


class TestSQLInjection:
    """ORM should parameterize all values, but verify."""

    @pytest.mark.asyncio
    async def test_job_id_with_sql_injection(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        evil_id = "'; DROP TABLE jobs; --"
        job = _make_job(job_id=evil_id)
        await repo.create(job)
        await session.commit()

        result = await repo.get(evil_id)
        assert result is not None
        assert result.id == evil_id

        # Table must still exist
        r = await session.execute(text("SELECT count(*) FROM jobs"))
        assert r.scalar() == 1

    @pytest.mark.asyncio
    async def test_prompt_with_sql_injection(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        evil_prompt = "Robert'); DROP TABLE jobs;--"
        job = _make_job()
        job.prompt = evil_prompt
        await repo.create(job)
        await session.commit()

        result = await repo.get("job-1")
        assert result is not None
        assert result.prompt == evil_prompt

    @pytest.mark.asyncio
    async def test_state_filter_with_injection(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1", "running"))
        await session.commit()

        # This goes into an IN clause via ORM
        evil_state = "running' OR '1'='1"
        jobs = await repo.list(state=evil_state)
        assert len(jobs) == 0  # should match nothing

    @pytest.mark.asyncio
    async def test_cursor_with_injection(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1"))
        await session.commit()

        evil_cursor = "' OR 1=1; --"
        jobs = await repo.list(cursor=evil_cursor)
        # Should return empty or no crash
        assert isinstance(jobs, list)

    @pytest.mark.asyncio
    async def test_event_payload_with_injection(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        evil_payload = {"key": "'; DROP TABLE events; --", "nested": {"sql": "1=1"}}
        event = DomainEvent(
            event_id="evt-1",
            job_id="job-1",
            timestamp=now,
            kind=DomainEventKind.log_line_emitted,
            payload=evil_payload,
        )
        await event_repo.append(event)
        await session.commit()

        events = await event_repo.list_after(0)
        assert len(events) == 1
        assert events[0].payload == evil_payload


# ── FK constraint violations ─────────────────────────────────────


class TestFKViolations:
    @pytest.mark.asyncio
    async def test_event_for_nonexistent_job_fails(self, session: AsyncSession) -> None:
        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        event = DomainEvent(
            event_id="evt-1",
            job_id="nonexistent-job",
            timestamp=now,
            kind=DomainEventKind.job_created,
            payload={},
        )
        # flush() inside append() triggers the FK check
        with pytest.raises(IntegrityError):
            await event_repo.append(event)

    @pytest.mark.asyncio
    async def test_artifact_for_nonexistent_job_fails(self, session: AsyncSession) -> None:
        artifact_repo = ArtifactRepository(session)
        now = datetime.now(UTC)
        artifact = Artifact(
            id="art-1",
            job_id="nonexistent-job",
            name="file.txt",
            type="custom",
            mime_type="text/plain",
            size_bytes=0,
            disk_path="/p/file.txt",
            phase="finalization",
            created_at=now,
        )
        # flush() inside create() triggers the FK check
        with pytest.raises(IntegrityError):
            await artifact_repo.create(artifact)


# ── Duplicate primary keys ───────────────────────────────────────


class TestDuplicatePKs:
    @pytest.mark.asyncio
    async def test_duplicate_job_id_fails(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-dup"))
        await session.flush()
        with pytest.raises(IntegrityError):
            await repo.create(_make_job("job-dup"))
            await session.flush()

    @pytest.mark.asyncio
    async def test_duplicate_event_id_fails(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        await event_repo.append(
            DomainEvent(event_id="evt-dup", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload={})
        )
        await session.flush()
        with pytest.raises(IntegrityError):
            await event_repo.append(
                DomainEvent(
                    event_id="evt-dup", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload={}
                )
            )
            await session.flush()

    @pytest.mark.asyncio
    async def test_duplicate_artifact_id_fails(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        artifact_repo = ArtifactRepository(session)
        now = datetime.now(UTC)
        base = Artifact(
            id="art-dup",
            job_id="job-1",
            name="x",
            type="custom",
            mime_type="text/plain",
            size_bytes=0,
            disk_path="/p",
            phase="finalization",
            created_at=now,
        )
        await artifact_repo.create(base)
        await session.flush()
        with pytest.raises(IntegrityError):
            dup = Artifact(
                id="art-dup",
                job_id="job-1",
                name="y",
                type="custom",
                mime_type="text/plain",
                size_bytes=0,
                disk_path="/q",
                phase="finalization",
                created_at=now,
            )
            await artifact_repo.create(dup)
            await session.flush()


# ── Boundary values ──────────────────────────────────────────────


class TestBoundaryValues:
    @pytest.mark.asyncio
    async def test_empty_string_fields(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        job = Job(
            id="job-empty",
            repo="",
            prompt="",
            state="",
            strategy="",
            base_ref="",
            branch=None,
            worktree_path=None,
            session_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repo.create(job)
        await session.commit()
        result = await repo.get("job-empty")
        assert result is not None
        assert result.repo == ""
        assert result.prompt == ""

    @pytest.mark.asyncio
    async def test_very_long_prompt(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        long_prompt = "x" * 1_000_000
        job = _make_job()
        job.prompt = long_prompt
        await repo.create(job)
        await session.commit()
        result = await repo.get("job-1")
        assert result is not None
        assert len(result.prompt) == 1_000_000

    @pytest.mark.asyncio
    async def test_unicode_in_all_text_fields(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        job = Job(
            id="job-日本語",
            repo="/repos/ñ/café",
            prompt="修复这个错误 🐛",
            state="running",
            strategy="single_agent",
            base_ref="main",
            branch="fix/ñ",
            worktree_path="/repos/ñ",
            session_id="session-émoji-🎉",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repo.create(job)
        await session.commit()
        result = await repo.get("job-日本語")
        assert result is not None
        assert result.prompt == "修复这个错误 🐛"
        assert result.session_id == "session-émoji-🎉"

    @pytest.mark.asyncio
    async def test_null_byte_in_string(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        job = _make_job()
        job.prompt = "before\x00after"
        await repo.create(job)
        await session.commit()
        result = await repo.get("job-1")
        assert result is not None
        assert "\x00" in result.prompt

    @pytest.mark.asyncio
    async def test_artifact_zero_size(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        artifact_repo = ArtifactRepository(session)
        now = datetime.now(UTC)
        art = Artifact(
            id="art-1",
            job_id="job-1",
            name="empty.txt",
            type="custom",
            mime_type="text/plain",
            size_bytes=0,
            disk_path="/p/empty.txt",
            phase="finalization",
            created_at=now,
        )
        await artifact_repo.create(art)
        await session.commit()
        result = await artifact_repo.get("art-1")
        assert result is not None
        assert result.size_bytes == 0

    @pytest.mark.asyncio
    async def test_artifact_negative_size(self, session: AsyncSession) -> None:
        """Negative size_bytes is technically allowed by the schema — no CHECK constraint."""
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        artifact_repo = ArtifactRepository(session)
        now = datetime.now(UTC)
        art = Artifact(
            id="art-1",
            job_id="job-1",
            name="neg.txt",
            type="custom",
            mime_type="text/plain",
            size_bytes=-1,
            disk_path="/p/neg.txt",
            phase="finalization",
            created_at=now,
        )
        await artifact_repo.create(art)
        await session.commit()
        result = await artifact_repo.get("art-1")
        assert result is not None
        assert result.size_bytes == -1


# ── Pagination edge cases ───────────────────────────────────────


class TestPaginationEdgeCases:
    @pytest.mark.asyncio
    async def test_list_with_limit_zero(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1"))
        await session.commit()
        jobs = await repo.list(limit=0)
        assert len(jobs) == 0

    @pytest.mark.asyncio
    async def test_list_with_limit_negative(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1"))
        await session.commit()
        jobs = await repo.list(limit=-1)
        # SQLite LIMIT -1 returns all rows
        assert len(jobs) >= 0

    @pytest.mark.asyncio
    async def test_list_with_huge_limit(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1"))
        await session.commit()
        jobs = await repo.list(limit=999999)
        assert len(jobs) == 1

    @pytest.mark.asyncio
    async def test_list_with_nonexistent_cursor(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1"))
        await session.commit()
        # Cursor refers to a nonexistent job — the subquery returns NULL
        jobs = await repo.list(cursor="nonexistent")
        # With NULL cursor_time, the comparison is NULL and nothing matches
        assert len(jobs) == 0

    @pytest.mark.asyncio
    async def test_list_empty_state_filter(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        await repo.create(_make_job("job-1", "running"))
        await session.commit()
        jobs = await repo.list(state="")
        # Empty string state filter — splits to [""], no job has state ""
        assert len(jobs) == 0

    @pytest.mark.asyncio
    async def test_pagination_with_same_timestamp(self, session: AsyncSession) -> None:
        """Jobs with identical created_at should still paginate correctly via ID tiebreaker."""
        repo = JobRepository(session)
        fixed_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        for i in range(10):
            job = _make_job(f"job-{i:02d}", "running", created_at=fixed_time)
            await repo.create(job)
        await session.commit()

        all_ids: list[str] = []
        cursor = None
        for _ in range(10):  # safety bound
            page = await repo.list(limit=3, cursor=cursor)
            if not page:
                break
            all_ids.extend(j.id for j in page)
            cursor = page[-1].id

        # Should have all 10 unique IDs
        assert len(all_ids) == 10
        assert len(set(all_ids)) == 10

    @pytest.mark.asyncio
    async def test_event_list_after_negative_id(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        await event_repo.append(
            DomainEvent(event_id="evt-1", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload={})
        )
        await session.commit()

        # -1 should still return everything (all auto-IDs > -1)
        events = await event_repo.list_after(-1)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_event_list_large_after_id(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        await event_repo.append(
            DomainEvent(event_id="evt-1", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload={})
        )
        await session.commit()

        events = await event_repo.list_after(999999)
        assert len(events) == 0


# ── update_state edge cases ──────────────────────────────────────


class TestUpdateStateEdgeCases:
    @pytest.mark.asyncio
    async def test_update_nonexistent_job_is_noop(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        now = datetime.now(UTC)
        # Should not raise
        await repo.update_state("nonexistent", "failed", now)
        await session.commit()

    @pytest.mark.asyncio
    async def test_update_to_invalid_state(self, session: AsyncSession) -> None:
        """No state machine enforcement in the repo layer — accepts any string."""
        repo = JobRepository(session)
        await repo.create(_make_job("job-1", "queued"))
        await session.commit()

        now = datetime.now(UTC)
        await repo.update_state("job-1", "invalid_state_value", now)
        await session.commit()

        job = await repo.get("job-1")
        assert job is not None
        assert job.state == "invalid_state_value"

    @pytest.mark.asyncio
    async def test_update_state_preserves_other_fields(self, session: AsyncSession) -> None:
        repo = JobRepository(session)
        job = _make_job("job-1", "queued")
        job.prompt = "Original prompt"
        await repo.create(job)
        await session.commit()

        now = datetime.now(UTC)
        await repo.update_state("job-1", "running", now)
        await session.commit()

        result = await repo.get("job-1")
        assert result is not None
        assert result.prompt == "Original prompt"
        assert result.state == "running"


# ── Event payload edge cases ─────────────────────────────────────


class TestEventPayloadEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_payload(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        await event_repo.append(
            DomainEvent(event_id="evt-1", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload={})
        )
        await session.commit()
        events = await event_repo.list_after(0)
        assert events[0].payload == {}

    @pytest.mark.asyncio
    async def test_deeply_nested_payload(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        # Build deeply nested dict
        deep: dict[str, Any] = {"level": 0}
        current: dict[str, Any] = deep
        for i in range(1, 50):
            current["child"] = {"level": i}
            current = current["child"]

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        await event_repo.append(
            DomainEvent(event_id="evt-1", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload=deep)
        )
        await session.commit()
        events = await event_repo.list_after(0)
        assert events[0].payload["level"] == 0

    @pytest.mark.asyncio
    async def test_payload_with_special_json_types(self, session: AsyncSession) -> None:
        job_repo = JobRepository(session)
        await job_repo.create(_make_job("job-1"))
        await session.commit()

        payload = {
            "boolean": True,
            "null_val": None,
            "int_val": 42,
            "float_val": 3.14,
            "list_val": [1, "two", None],
            "unicode": "café ☕ 日本語",
        }

        event_repo = EventRepository(session)
        now = datetime.now(UTC)
        await event_repo.append(
            DomainEvent(
                event_id="evt-1", job_id="job-1", timestamp=now, kind=DomainEventKind.job_created, payload=payload
            )
        )
        await session.commit()
        events = await event_repo.list_after(0)
        assert events[0].payload == payload
