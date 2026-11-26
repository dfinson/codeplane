"""Tests for persistence repos.

Covers CostAttributionRepository, FileAccessRepository,
ObservationsRepository, StepRepository.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.models.db import Base, ProjectRow, StepRow
from backend.persistence.cost_attribution_repo import CostAttributionRepository
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.file_access_repo import FileAccessRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.observations_repo import ObservationsRepository
from backend.persistence.step_repo import StepRepository
from backend.tests.unit.conftest import make_job


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    await engine.dispose()


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory for StepRepository (uses its own sessions)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Seed a job for FK constraints
    async with factory() as sess:
        sess.add(
            ProjectRow(
                id="proj-1",
                name="Test Project",
                repo_paths='["/repos/test"]',
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await sess.commit()
        job_repo = JobRepository(sess)
        await job_repo.create(make_job(id="job-1", worktree_path="/repos/test"))
        await sess.commit()

    yield factory
    await engine.dispose()


async def _seed_job(session: AsyncSession) -> None:
    """Insert a job so FK constraints are satisfied."""
    session.add(
        ProjectRow(
            id="proj-1",
            name="Test Project",
            repo_paths='["/repos/test"]',
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    await session.commit()
    repo = JobRepository(session)
    await repo.create(make_job(id="job-1", worktree_path="/repos/test"))
    await session.commit()


async def _seed_telemetry(session: AsyncSession, job_id: str = "job-1") -> None:
    """Insert a job_telemetry_summary row (needed for aggregate JOIN queries)."""
    now = datetime.now(UTC).isoformat()
    await session.execute(
        text("""
            INSERT INTO job_telemetry_summary
                (job_id, session_kind, sdk, model, repo, branch, status, created_at, updated_at,
                 duration_ms, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                 total_cost_usd, premium_requests, llm_call_count, total_llm_duration_ms,
                 tool_call_count, tool_failure_count, total_tool_duration_ms, compactions,
                 tokens_compacted, approval_count, approval_wait_ms, agent_messages,
                 operator_messages, context_window_size, current_context_tokens,
                 total_turns, retry_count, retry_cost_usd, file_read_count,
                 file_write_count, unique_files_read, file_reread_count)
            VALUES
                (:job_id, 'job', 'copilot', '', '', '', 'running', :now, :now,
                 0, 0, 0, 0, 0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                 0, 0, 0.0, 0, 0, 0, 0)
        """),
        {"job_id": job_id, "now": now},
    )
    await session.commit()


# ---- CostAttributionRepository ----


class TestCostAttributionRepository:
    @pytest.mark.asyncio
    async def test_insert_and_for_job(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = CostAttributionRepository(session)
        await repo.insert(
            job_id="job-1",
            dimension="activity",
            bucket="code_reading",
            cost_usd=1.5,
            input_tokens=100,
            output_tokens=50,
            call_count=3,
        )
        await session.commit()

        rows = await repo.for_job("job-1")
        assert len(rows) == 1
        assert rows[0]["bucket"] == "code_reading"
        assert rows[0]["cost_usd"] is None
        assert rows[0]["input_tokens"] == 100
        assert rows[0]["output_tokens"] == 50
        assert rows[0]["call_count"] == 3

    @pytest.mark.asyncio
    async def test_delete_for_job(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = CostAttributionRepository(session)
        await repo.insert(job_id="job-1", dimension="activity", bucket="a")
        await session.commit()

        await repo.delete_for_job("job-1")
        await session.commit()

        rows = await repo.for_job("job-1")
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_insert_batch_replaces(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = CostAttributionRepository(session)
        await repo.insert(job_id="job-1", dimension="old", bucket="stale")
        await session.commit()

        await repo.insert_batch(
            job_id="job-1",
            rows=[
                {"dimension": "activity", "bucket": "code_reading", "cost_usd": 1.0},
                {"dimension": "activity", "bucket": "reasoning", "cost_usd": 2.0},
            ],
        )
        await session.commit()

        rows = await repo.for_job("job-1")
        assert len(rows) == 2
        buckets = {r["bucket"] for r in rows}
        assert buckets == {"code_reading", "reasoning"}

    @pytest.mark.asyncio
    async def test_insert_batch_empty_rows(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = CostAttributionRepository(session)
        await repo.insert(job_id="job-1", dimension="x", bucket="y")
        await session.commit()
        # Empty batch still deletes existing rows
        await repo.insert_batch(job_id="job-1", rows=[])
        await session.commit()
        rows = await repo.for_job("job-1")
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_by_dimension(self, session: AsyncSession) -> None:
        await _seed_job(session)
        await _seed_telemetry(session)
        await session.execute(
            text("UPDATE job_telemetry_summary SET derived_currency_qualified = 1 WHERE job_id = 'job-1'")
        )
        repo = CostAttributionRepository(session)
        await repo.insert_batch(
            job_id="job-1",
            currency_qualified=True,
            rows=[
                {"dimension": "activity", "bucket": "reasoning", "cost_usd": 3.0, "call_count": 10},
                {"dimension": "activity", "bucket": "code_reading", "cost_usd": 1.0, "call_count": 5},
            ],
        )
        await session.commit()

        agg = await repo.by_dimension("activity")
        assert len(agg) == 2
        assert agg[0]["bucket"] == "reasoning"  # highest cost first

    @pytest.mark.asyncio
    async def test_fleet_summary(self, session: AsyncSession) -> None:
        await _seed_job(session)
        await _seed_telemetry(session)
        repo = CostAttributionRepository(session)
        await repo.insert(job_id="job-1", dimension="activity", bucket="reasoning", cost_usd=5.0)
        await session.commit()

        summary = await repo.fleet_summary()
        assert len(summary) >= 1
        assert summary[0]["dimension"] == "activity"

    @pytest.mark.asyncio
    async def test_for_job_empty(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = CostAttributionRepository(session)
        rows = await repo.for_job("job-1")
        assert rows == []


# ---- FileAccessRepository ----


class TestFileAccessRepository:
    @pytest.mark.asyncio
    async def test_record_and_reread_stats(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = FileAccessRepository(session)
        # Read same file twice, write once
        await repo.record(job_id="job-1", file_path="/a.py", access_type="read")
        await repo.record(job_id="job-1", file_path="/a.py", access_type="read")
        await repo.record(job_id="job-1", file_path="/b.py", access_type="write")
        await session.commit()

        stats = await repo.reread_stats("job-1")
        assert stats["total_accesses"] == 3
        assert stats["unique_files"] == 2
        assert stats["total_reads"] == 2
        assert stats["total_writes"] == 1
        assert stats["reread_count"] == 1  # /a.py read twice → 1 reread

    @pytest.mark.asyncio
    async def test_record_batch(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = FileAccessRepository(session)
        await repo.record_batch(
            job_id="job-1",
            entries=[
                {"file_path": "/x.py", "access_type": "read"},
                {"file_path": "/y.py", "access_type": "write"},
            ],
        )
        await session.commit()

        stats = await repo.reread_stats("job-1")
        assert stats["total_accesses"] == 2

    @pytest.mark.asyncio
    async def test_record_batch_empty(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = FileAccessRepository(session)
        await repo.record_batch(job_id="job-1", entries=[])
        # Should not raise

    @pytest.mark.asyncio
    async def test_most_accessed_files_for_job(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = FileAccessRepository(session)
        for _ in range(5):
            await repo.record(job_id="job-1", file_path="/hot.py", access_type="read")
        await repo.record(job_id="job-1", file_path="/cold.py", access_type="read")
        await session.commit()

        top = await repo.most_accessed_files(job_id="job-1")
        assert len(top) == 2
        assert top[0]["file_path"] == "/hot.py"
        assert top[0]["access_count"] == 5

    @pytest.mark.asyncio
    async def test_most_accessed_files_cross_job(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = FileAccessRepository(session)
        await repo.record(job_id="job-1", file_path="/a.py", access_type="read")
        await session.commit()

        # Cross-job (no job_id filter) — uses period_days filter
        top = await repo.most_accessed_files()
        assert len(top) >= 1

    @pytest.mark.asyncio
    async def test_reread_stats_empty_job(self, session: AsyncSession) -> None:
        await _seed_job(session)
        repo = FileAccessRepository(session)
        stats = await repo.reread_stats("job-1")
        assert stats["total_accesses"] == 0
        assert stats["reread_count"] == 0


# ---- ObservationsRepository ----


class TestObservationsRepository:
    @pytest.mark.asyncio
    async def test_upsert_insert(self, session: AsyncSession) -> None:
        repo = ObservationsRepository(session)
        await repo.upsert(
            category="file_rereads",
            severity="warning",
            title="Too many rereads",
            detail="Files are being reread excessively",
            evidence={"files": ["/a.py"]},
            job_count=3,
            total_waste_usd=0.5,
        )
        await session.commit()

        rows = await repo.list_active()
        assert len(rows) == 1
        assert rows[0]["category"] == "file_rereads"
        assert rows[0]["severity"] == "warning"
        assert rows[0]["evidence"] == {"files": ["/a.py"]}

    @pytest.mark.asyncio
    async def test_upsert_update(self, session: AsyncSession) -> None:
        repo = ObservationsRepository(session)
        await repo.upsert(
            category="file_rereads",
            severity="warning",
            title="Too many rereads",
            detail="v1",
            evidence={},
        )
        await session.commit()

        # Same category+title → update
        await repo.upsert(
            category="file_rereads",
            severity="critical",
            title="Too many rereads",
            detail="v2",
            evidence={"updated": True},
            total_waste_usd=1.0,
        )
        await session.commit()

        rows = await repo.list_active()
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"
        assert rows[0]["detail"] == "v2"

    @pytest.mark.asyncio
    async def test_list_active_filters_dismissed(self, session: AsyncSession) -> None:
        repo = ObservationsRepository(session)
        await repo.upsert(category="a", severity="warning", title="t1", detail="d", evidence={})
        await repo.upsert(category="b", severity="info", title="t2", detail="d", evidence={})
        await session.commit()

        rows = await repo.list_active()
        assert len(rows) == 2

        # Dismiss the first one
        await repo.dismiss(rows[0]["id"])
        await session.commit()

        rows = await repo.list_active()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_list_active_filter_by_category(self, session: AsyncSession) -> None:
        repo = ObservationsRepository(session)
        await repo.upsert(category="file_rereads", severity="warning", title="t1", detail="d", evidence={})
        await repo.upsert(category="tool_failures", severity="warning", title="t2", detail="d", evidence={})
        await session.commit()

        rows = await repo.list_active(category="file_rereads")
        assert len(rows) == 1
        assert rows[0]["category"] == "file_rereads"

    @pytest.mark.asyncio
    async def test_list_active_filter_by_severity(self, session: AsyncSession) -> None:
        repo = ObservationsRepository(session)
        await repo.upsert(category="a", severity="critical", title="t1", detail="d", evidence={})
        await repo.upsert(category="b", severity="warning", title="t2", detail="d", evidence={})
        await session.commit()

        rows = await repo.list_active(severity="critical")
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_list_active_severity_ordering(self, session: AsyncSession) -> None:
        repo = ObservationsRepository(session)
        await repo.upsert(category="a", severity="info", title="info-obs", detail="d", evidence={})
        await repo.upsert(category="b", severity="critical", title="crit-obs", detail="d", evidence={})
        await repo.upsert(category="c", severity="warning", title="warn-obs", detail="d", evidence={})
        await session.commit()

        rows = await repo.list_active()
        assert rows[0]["severity"] == "critical"
        assert rows[1]["severity"] == "warning"


# ---- StepRepository ----


class TestStepRepository:
    @pytest.mark.asyncio
    async def test_create_and_get(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        repo = StepRepository(session_factory)
        now = datetime.now(UTC)
        step = StepRow(
            id="step-1",
            job_id="job-1",
            step_number=1,
            intent="Edit files",
            status="running",
            trigger="tool_call",
            started_at=now,
        )
        await repo.create(step)

        result = await repo.get("step-1")
        assert result is not None
        assert result.id == "step-1"
        assert result.intent == "Edit files"
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_complete(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        repo = StepRepository(session_factory)
        now = datetime.now(UTC)
        step = StepRow(
            id="step-2",
            job_id="job-1",
            step_number=1,
            intent="Run tests",
            status="running",
            trigger="tool_call",
            started_at=now,
        )
        await repo.create(step)

        await repo.complete(
            step_id="step-2",
            status="done",
            tool_count=5,
            completed_at=now,
            duration_ms=1234,
        )

        result = await repo.get("step-2")
        assert result is not None
        assert result.status == "done"
        assert result.tool_count == 5
        assert result.duration_ms == 1234

    @pytest.mark.asyncio
    async def test_set_title(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        repo = StepRepository(session_factory)
        now = datetime.now(UTC)
        step = StepRow(
            id="step-3",
            job_id="job-1",
            step_number=1,
            intent="x",
            status="running",
            trigger="tool_call",
            started_at=now,
        )
        await repo.create(step)
        await repo.set_title("step-3", "Updated Title")

        result = await repo.get("step-3")
        assert result is not None
        assert result.title == "Updated Title"

    @pytest.mark.asyncio
    async def test_get_missing(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        repo = StepRepository(session_factory)
        result = await repo.get("no-such-step")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_job(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        repo = StepRepository(session_factory)
        now = datetime.now(UTC)
        for i in range(3):
            step = StepRow(
                id=f"step-{i}",
                job_id="job-1",
                step_number=i + 1,
                intent=f"Step {i}",
                status="done",
                trigger="tool_call",
                started_at=now,
            )
            await repo.create(step)

        steps = await repo.get_by_job("job-1")
        assert len(steps) == 3
        assert [s.step_number for s in steps] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_get_by_job_empty(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        repo = StepRepository(session_factory)
        steps = await repo.get_by_job("job-1")
        assert steps == []
