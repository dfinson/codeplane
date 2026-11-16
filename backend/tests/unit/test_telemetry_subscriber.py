"""Tests for TraceForge-native telemetry subscriber persistence."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from traceforge.types import EventMetadata, ToolMotivation

from backend.models.api_schemas import ExecutionPhase
from backend.models.db import Base, JobRow, ProjectRow
from backend.models.domain import JobState
from backend.models.events import EventKind, new_event
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.telemetry_subscriber import TelemetrySubscriber

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine


class _Harness:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        model_pricing: Any = None,
    ) -> None:
        self.pending: list[Coroutine[Any, Any, None]] = []
        self.subscriber = TelemetrySubscriber(
            session_factory=factory,
            schedule_write=self.pending.append,
            model_pricing=model_pricing,
        )

    async def handle_and_drain(self, event: Any) -> None:
        await self.subscriber.handle_event(event)
        while self.pending:
            coro = self.pending.pop(0)
            await coro


class _MetricRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, Any]]] = []

    def add(self, value: float, attrs: dict[str, Any]) -> None:
        self.calls.append((value, attrs))

    def record(self, value: float, attrs: dict[str, Any]) -> None:
        self.calls.append((value, attrs))


class _FakePricing:
    """Stub ModelPricingService: records calls and returns a fixed cost."""

    def __init__(self, cost: float) -> None:
        self.cost = cost
        self.calls: list[tuple[str, int, int, int, int]] = []

    def compute_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> float:
        self.calls.append((model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens))
        return self.cost


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        now = datetime.now(UTC)
        sess.add(
            ProjectRow(
                id="proj-1",
                name="Test Project",
                repo_paths='["/repos/test"]',
                created_at=now,
                updated_at=now,
            )
        )
        await sess.flush()
        sess.add(
            JobRow(
                id="job-1",
                repo="/repos/test",
                project_id="proj-1",
                prompt="Fix the bug",
                state=JobState.running,
                base_ref="main",
                permission_mode="full_auto",
                preset="autonomous",
                sdk="copilot",
                created_at=now,
                updated_at=now,
            )
        )
        await sess.commit()

    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_quota_observations_merge_resources_and_keep_fractional_balances(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from backend.models.api_schemas import TelemetryQuotaSnapshot

    async with session_factory() as session:
        repo = TelemetrySummaryRepository(session)
        await repo.init_job("job-1", sdk="copilot")
        await repo.set_quota("job-1", json.dumps({"credits": {"used_requests": 1.25, "overage": 0.5}}))
        await repo.set_quota("job-1", json.dumps({"requests": {"used_requests": 2}}))
        await repo.set_quota("job-1", json.dumps({"credits": {"used_requests": 3.75, "overage": 0.25}}))
        stored = await session.scalar(text("SELECT quota_json FROM job_telemetry_summary WHERE job_id = 'job-1'"))
    resources = json.loads(stored)
    assert resources["requests"]["used_requests"] == 2
    snapshot = TelemetryQuotaSnapshot.model_validate(resources["credits"])
    assert snapshot.used_requests == 3.75
    assert snapshot.overage == 0.25


@pytest.mark.asyncio
async def test_subscriber_persists_usage_tools_files_and_messages(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services.analytics import telemetry as tel

    harness = _Harness(session_factory)
    harness.subscriber.set_job_start_time("job-1", 100.0)
    message_metrics = _MetricRecorder()
    tool_metrics = _MetricRecorder()
    monkeypatch.setattr(tel, "messages_counter", message_metrics)
    monkeypatch.setattr(tel, "tool_duration", tool_metrics)

    events = [
        new_event(
            "job-1",
            EventKind.message_user,
            {"content": "please update app"},
            metadata=EventMetadata(source_framework="copilot"),
        ),
        new_event(
            "job-1",
            EventKind.message_assistant,
            {"content": "I'll edit it."},
            metadata=EventMetadata(source_framework="copilot"),
        ),
        new_event(
            "job-1",
            EventKind.telemetry_usage,
            {
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
                "cost_usd": 0.25,
                "duration_ms": 1234.9,
                "premium_requests": 2,
                "advance_turn": True,
            },
            metadata=EventMetadata(source_framework="copilot"),
        ),
        new_event(
            "job-1",
            EventKind.tool_call_completed,
            {
                "tool_name": "write_file",
                "arguments": {"path": "src/app.py"},
                "result": "wrote file",
                "success": True,
                "tool_call_id": "tool-1",
                "turn_id": "turn-1",
            },
            metadata=EventMetadata(
                source_framework="copilot",
                duration_ms=20.8,
                motivation=ToolMotivation(intent="Update app", reasoning="Need to change behavior"),
            ),
        ),
        new_event(
            "job-1",
            EventKind.tool_call_completed,
            {
                "tool_name": "bash",
                "arguments": {"command": "bad"},
                "result": "error: command not found",
                "success": False,
                "tool_call_id": "tool-2",
                "turn_id": "turn-1",
            },
            metadata=EventMetadata(source_framework="claude", duration_ms=30.2),
        ),
    ]

    for event in events:
        await harness.handle_and_drain(event)

    async with session_factory() as session:
        summary = await TelemetrySummaryRepository(session).get("job-1")
        assert summary is not None
        assert summary["model"] == "gpt-4o"
        assert summary["input_tokens"] == 100
        assert summary["output_tokens"] == 50
        assert summary["cache_read_tokens"] == 10
        assert summary["cache_write_tokens"] == 5
        assert summary["total_cost_usd"] == pytest.approx(0.25)
        assert summary["premium_requests"] == pytest.approx(2)
        assert summary["llm_call_count"] == 1
        assert summary["total_llm_duration_ms"] == 1234
        assert summary["total_turns"] == 1
        assert summary["tool_call_count"] == 2
        assert summary["tool_failure_count"] == 1
        assert summary["total_tool_duration_ms"] == 50
        assert summary["file_write_count"] == 1
        assert summary["agent_messages"] == 1
        assert summary["operator_messages"] == 1

        spans = await TelemetrySpansRepository(session).list_for_job("job-1")
        assert [span["span_type"] for span in spans] == ["llm", "tool", "tool"]

        llm_span = spans[0]
        assert llm_span["name"] == "gpt-4o"
        assert float(llm_span["duration_ms"]) == pytest.approx(1234.9)
        assert llm_span["turn_number"] == 1
        assert llm_span["execution_phase"] == "agent_reasoning"
        assert llm_span["input_tokens"] == 100
        assert llm_span["output_tokens"] == 50
        assert llm_span["cache_read_tokens"] == 10
        assert llm_span["cache_write_tokens"] == 5
        assert llm_span["cost_usd"] == pytest.approx(0.25)
        assert llm_span["attrs"]["cost"] == pytest.approx(0.25)

        write_span = spans[1]
        assert write_span["name"] == "write_file"
        assert write_span["tool_category"] == "file_write"
        assert write_span["tool_target"] == "src/app.py"
        assert float(write_span["duration_ms"]) == pytest.approx(20.8)
        assert write_span["turn_number"] == 1
        assert write_span["tool_args_json"] == json.dumps({"path": "src/app.py"})
        assert write_span["result_size_bytes"] == len("wrote file")
        assert write_span["attrs"] == {"success": True}
        assert write_span["turn_id"] == "turn-1"
        assert write_span["motivation_summary"] == "Update app\nNeed to change behavior"
        assert write_span["preceding_context"] is not None
        preceding = json.loads(write_span["preceding_context"])
        assert preceding == [
            {"role": "operator", "content": "please update app"},
            {"role": "agent", "content": "I'll edit it."},
            {
                "role": "tool_call",
                "tool_name": "write_file",
                "tool_args": json.dumps({"path": "src/app.py"}),
                "tool_result": "wrote file",
            },
        ]

        failed_span = spans[2]
        assert failed_span["name"] == "bash"
        assert failed_span["tool_category"] == "shell"
        assert failed_span["tool_target"] == "bad"
        assert failed_span["attrs"] == {"success": False, "error_snippet": "error: command not found"}
        assert failed_span["result_size_bytes"] == len("error: command not found")
        assert failed_span["turn_id"] == "turn-1"

        access_rows = (
            (
                await session.execute(
                    text(
                        "SELECT job_id, file_path, access_type, turn_number "
                        "FROM job_file_access_log WHERE job_id = :job_id"
                    ),
                    {"job_id": "job-1"},
                )
            )
            .mappings()
            .all()
        )
        assert [dict(row) for row in access_rows] == [
            {
                "job_id": "job-1",
                "file_path": "src/app.py",
                "access_type": "write",
                "turn_number": 1,
            }
        ]
    assert [attrs["sdk"] for _, attrs in message_metrics.calls] == ["copilot", "copilot"]
    assert [attrs["sdk"] for _, attrs in tool_metrics.calls] == ["copilot", "claude"]


@pytest.mark.asyncio
async def test_usage_cost_prefers_sdk_then_falls_back_to_model_pricing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Both cost paths: SDK-provided cost_usd is authoritative (managed); when it
    is absent (imported), cost is derived via ModelPricingService from tokens."""
    pricing = _FakePricing(0.42)
    harness = _Harness(session_factory, model_pricing=pricing)
    harness.subscriber.set_job_start_time("sdk-job", 100.0)
    harness.subscriber.set_job_start_time("imported-job", 100.0)

    async with session_factory() as seed:
        now = datetime.now(UTC)
        for jid, sdk in (("sdk-job", "copilot"), ("imported-job", "claude")):
            seed.add(
                JobRow(
                    id=jid,
                    repo="/repos/test",
                    project_id="proj-1",
                    prompt="x",
                    state=JobState.running,
                    base_ref="main",
                    permission_mode="full_auto",
                    preset="autonomous",
                    sdk=sdk,
                    created_at=now,
                    updated_at=now,
                )
            )
        await seed.commit()

    # Path A — managed: SDK supplies cost_usd; pricing must NOT be consulted.
    await harness.handle_and_drain(
        new_event(
            "sdk-job",
            EventKind.telemetry_usage,
            {
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.25,
                "duration_ms": 10.0,
                "advance_turn": True,
            },
            metadata=EventMetadata(source_framework="copilot"),
        )
    )
    assert pricing.calls == []

    # Path B — imported: no cost_usd; pricing derives cost from token counts.
    await harness.handle_and_drain(
        new_event(
            "imported-job",
            EventKind.telemetry_usage,
            {
                "model": "claude-sonnet-4",
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
                "duration_ms": 10.0,
                "advance_turn": True,
            },
            metadata=EventMetadata(source_framework="claude"),
        )
    )
    assert pricing.calls == [("claude-sonnet-4", 200, 80, 10, 5)]

    async with session_factory() as session:
        sdk_summary = await TelemetrySummaryRepository(session).get("sdk-job")
        imported_summary = await TelemetrySummaryRepository(session).get("imported-job")
        assert sdk_summary is not None
        assert imported_summary is not None
        assert sdk_summary["total_cost_usd"] == pytest.approx(0.25)
        assert imported_summary["total_cost_usd"] == pytest.approx(0.42)

        sdk_spans = await TelemetrySpansRepository(session).list_for_job("sdk-job")
        imported_spans = await TelemetrySpansRepository(session).list_for_job("imported-job")
        assert sdk_spans[0]["cost_usd"] == pytest.approx(0.25)
        assert imported_spans[0]["cost_usd"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_subscribe_attaches_to_event_bus(session_factory: async_sessionmaker[AsyncSession]) -> None:
    harness = _Harness(session_factory)
    bus = EventBus()
    harness.subscriber.subscribe(bus)

    await bus.publish(
        new_event(
            "job-1",
            EventKind.message_user,
            {"content": "hello"},
            metadata=EventMetadata(source_framework="claude"),
        )
    )
    while harness.pending:
        await harness.pending.pop(0)

    async with session_factory() as session:
        summary = await TelemetrySummaryRepository(session).get("job-1")
        assert summary is not None
        assert summary["operator_messages"] == 1


@pytest.mark.asyncio
async def test_execution_phase_changed_feeds_span_phase(session_factory: async_sessionmaker[AsyncSession]) -> None:
    harness = _Harness(session_factory)
    bus = EventBus()
    harness.subscriber.subscribe(bus)

    await bus.publish(
        new_event(
            "job-1",
            EventKind.execution_phase_changed,
            {"phase": ExecutionPhase.verification},
            metadata=EventMetadata(source_framework="copilot"),
        )
    )
    await bus.publish(
        new_event(
            "job-1",
            EventKind.tool_call_completed,
            {
                "tool_name": "bash",
                "arguments": {"command": "uv run pytest"},
                "result": "passed",
                "success": True,
                "tool_call_id": "tool-phase",
                "turn_id": "turn-phase",
            },
            metadata=EventMetadata(source_framework="copilot", duration_ms=42.0),
        )
    )
    while harness.pending:
        await harness.pending.pop(0)

    async with session_factory() as session:
        spans = await TelemetrySpansRepository(session).list_for_job("job-1")
        assert len(spans) == 1
        assert spans[0]["execution_phase"] == "verification"


@pytest.mark.asyncio
async def test_cleanup_removes_per_job_state(session_factory: async_sessionmaker[AsyncSession]) -> None:
    harness = _Harness(session_factory)
    await harness.handle_and_drain(
        new_event(
            "job-1",
            EventKind.telemetry_usage,
            {"advance_turn": True},
            metadata=EventMetadata(source_framework="copilot"),
        )
    )
    assert harness.subscriber.get_turn("job-1") == 1

    harness.subscriber.cleanup("job-1")

    assert harness.subscriber.get_turn("job-1") == 0


def test_scheduler_can_create_tasks(session_factory: async_sessionmaker[AsyncSession]) -> None:
    pending: list[asyncio.Task[None]] = []
    subscriber = TelemetrySubscriber(
        session_factory=session_factory,
        schedule_write=lambda coro: pending.append(asyncio.create_task(coro)),
    )
    assert subscriber.get_turn("job-1") == 0
