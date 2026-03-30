"""Tests for OTEL telemetry persistence (summary + spans repos)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.models.db import Base, JobRow
from backend.models.domain import JobState, PermissionMode
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        now = datetime.now(UTC)
        sess.add(
            JobRow(
                id="job-1",
                repo="/repos/test",
                prompt="Fix the bug",
                state=JobState.running,
                base_ref="main",
                permission_mode=PermissionMode.full_auto,
                sdk="copilot",
                created_at=now,
                updated_at=now,
            )
        )
        await sess.commit()
        yield sess

    await engine.dispose()


# ---------------------------------------------------------------------------
# TelemetrySummaryRepo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_init_and_get(session: AsyncSession) -> None:
    repo = TelemetrySummaryRepo(session)
    await repo.init_job("job-1", sdk="copilot", model="gpt-4o")
    await session.commit()

    row = await repo.get("job-1")
    assert row is not None
    assert row["sdk"] == "copilot"
    assert row["model"] == "gpt-4o"
    assert row["status"] == "running"
    assert row["input_tokens"] == 0


@pytest.mark.asyncio
async def test_summary_increment(session: AsyncSession) -> None:
    repo = TelemetrySummaryRepo(session)
    await repo.init_job("job-1", sdk="claude", model="sonnet")
    await session.commit()

    await repo.increment("job-1", input_tokens=500, output_tokens=200, total_cost_usd=0.01)
    await session.commit()

    row = await repo.get("job-1")
    assert row is not None
    assert row["input_tokens"] == 500
    assert row["output_tokens"] == 200

    # Second increment accumulates
    await repo.increment("job-1", input_tokens=100, tool_call_count=3)
    await session.commit()

    row = await repo.get("job-1")
    assert row is not None
    assert row["input_tokens"] == 600
    assert row["tool_call_count"] == 3


@pytest.mark.asyncio
async def test_summary_finalize(session: AsyncSession) -> None:
    repo = TelemetrySummaryRepo(session)
    await repo.init_job("job-1", sdk="copilot")
    await session.commit()

    await repo.finalize("job-1", status="review", duration_ms=12345)
    await session.commit()

    row = await repo.get("job-1")
    assert row is not None
    assert row["status"] == "review"
    assert row["duration_ms"] == 12345
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_summary_get_missing_returns_none(session: AsyncSession) -> None:
    repo = TelemetrySummaryRepo(session)
    assert await repo.get("no-such-job") is None


@pytest.mark.asyncio
async def test_summary_set_model(session: AsyncSession) -> None:
    repo = TelemetrySummaryRepo(session)
    await repo.init_job("job-1", sdk="claude")
    await session.commit()

    await repo.set_model("job-1", "claude-opus-4")
    await session.commit()

    row = await repo.get("job-1")
    assert row is not None
    assert row["model"] == "claude-opus-4"


@pytest.mark.asyncio
async def test_summary_aggregate(session: AsyncSession) -> None:
    repo = TelemetrySummaryRepo(session)
    await repo.init_job("job-1", sdk="copilot", model="gpt-4o")
    await repo.increment("job-1", input_tokens=1000, output_tokens=500)
    await repo.finalize("job-1", status="completed", duration_ms=5000)
    await session.commit()

    agg = await repo.aggregate(period_days=7)
    assert agg["total_jobs"] == 1
    assert agg["completed"] == 1


# ---------------------------------------------------------------------------
# TelemetrySpansRepo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spans_insert_and_list(session: AsyncSession) -> None:
    repo = TelemetrySpansRepo(session)
    await repo.insert(
        job_id="job-1",
        span_type="tool",
        name="read_file",
        started_at=0.0,
        duration_ms=50.0,
        attrs={"success": True},
        tool_category="file_read",
        tool_target="src/app.py",
        turn_number=2,
        execution_phase="agent_reasoning",
        is_retry=True,
        retries_span_id=7,
    )
    await repo.insert(
        job_id="job-1",
        span_type="llm",
        name="gpt-4o",
        started_at=0.1,
        duration_ms=1200.0,
        attrs={"input_tokens": 300, "output_tokens": 150},
        turn_number=3,
        execution_phase="verification",
        input_tokens=300,
        output_tokens=150,
        cache_read_tokens=25,
        cache_write_tokens=10,
        cost_usd=0.42,
    )
    await session.commit()

    spans = await repo.list_for_job("job-1")
    assert len(spans) == 2
    assert spans[0]["name"] == "read_file"
    assert spans[0]["attrs"]["success"] is True
    assert spans[0]["tool_category"] == "file_read"
    assert spans[0]["tool_target"] == "src/app.py"
    assert spans[0]["turn_number"] == 2
    assert spans[0]["execution_phase"] == "agent_reasoning"
    assert spans[0]["is_retry"] is True
    assert spans[0]["retries_span_id"] == 7
    assert spans[1]["name"] == "gpt-4o"
    assert spans[1]["turn_number"] == 3
    assert spans[1]["execution_phase"] == "verification"
    assert spans[1]["input_tokens"] == 300
    assert spans[1]["output_tokens"] == 150
    assert spans[1]["cache_read_tokens"] == 25
    assert spans[1]["cache_write_tokens"] == 10
    assert spans[1]["cost_usd"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_spans_tool_stats(session: AsyncSession) -> None:
    repo = TelemetrySpansRepo(session)
    for i in range(5):
        await repo.insert(
            job_id="job-1",
            span_type="tool",
            name="write_file",
            started_at=float(i),
            duration_ms=100.0 + i * 10,
            attrs={"success": i != 2},  # one failure
        )
    await session.commit()

    stats = await repo.tool_stats(period_days=30)
    assert len(stats) == 1
    assert stats[0]["name"] == "write_file"
    assert stats[0]["count"] == 5
    assert stats[0]["failure_count"] == 1
