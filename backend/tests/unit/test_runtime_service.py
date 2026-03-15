"""Tests for RuntimeService — capacity, queueing, heartbeat, MCP discovery, event translation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

import structlog

from backend.config import TowerConfig
from backend.models.db import Base
from backend.models.domain import (
    Job,
    JobState,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.agent_adapter import AgentAdapterInterface
from backend.services.event_bus import EventBus
from backend.services.execution_strategy import STRATEGY_REGISTRY, SingleAgentExecutor
from backend.services.runtime_service import (
    RuntimeService,
    _build_session_config,
    _discover_mcp_servers,
    _make_event_id,
    _resolve_protected_paths,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Test double — lives here, not in production code
# ---------------------------------------------------------------------------


class FakeAgentAdapter(AgentAdapterInterface):
    """Test double that emits scripted events with realistic event sequences."""

    def __init__(self, delay: float = 0.1) -> None:
        self._delay = delay
        self._aborted: set[str] = set()

    async def create_session(self, config: SessionConfig) -> str:
        import uuid

        return f"fake-{uuid.uuid4().hex[:8]}"

    async def stream_events(self, session_id: str) -> AsyncGenerator[SessionEvent, None]:
        scripted: list[SessionEvent] = [
            SessionEvent(
                kind=SessionEventKind.log,
                payload={"level": "info", "message": "Agent session started"},
            ),
            SessionEvent(
                kind=SessionEventKind.transcript,
                payload={
                    "role": "agent",
                    "content": "I'll analyze the codebase and implement the requested changes.",
                },
            ),
            SessionEvent(
                kind=SessionEventKind.file_changed,
                payload={"path": "src/example.py", "action": "modified"},
            ),
            SessionEvent(
                kind=SessionEventKind.transcript,
                payload={"role": "agent", "content": "Changes complete. All tests pass."},
            ),
            SessionEvent(kind=SessionEventKind.done, payload={}),
        ]
        for event in scripted:
            if session_id in self._aborted:
                return
            await asyncio.sleep(self._delay)
            yield event

    async def send_message(self, session_id: str, message: str) -> None:
        log.debug("fake_adapter_message", session_id=session_id, message=message)

    async def abort_session(self, session_id: str) -> None:
        self._aborted.add(session_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(eng.sync_engine, "connect", _set_sqlite_pragmas)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def config(tmp_path: Path) -> TowerConfig:
    return TowerConfig(repos=[str(tmp_path)])


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def adapter() -> FakeAgentAdapter:
    return FakeAgentAdapter(delay=0.0)


@pytest.fixture
def runtime(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    adapter: FakeAgentAdapter,
    config: TowerConfig,
) -> RuntimeService:
    return RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter=adapter,
        config=config,
    )


def _make_job(
    *,
    job_id: str = "job-1",
    repo: str = "/repos/test",
    state: str = JobState.queued,
    strategy: str = "single_agent",
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        repo=repo,
        prompt="Fix the bug",
        state=state,
        strategy=strategy,
        base_ref="main",
        branch=None,
        worktree_path=None,
        session_id=None,
        created_at=now,
        updated_at=now,
    )


async def _create_db_job(
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
) -> None:
    """Insert a minimal job row so state transitions work."""
    from backend.models.db import JobRow

    async with session_factory() as session:
        row = JobRow(
            id=job.id,
            repo=job.repo,
            prompt=job.prompt,
            state=job.state,
            strategy=job.strategy,
            base_ref=job.base_ref,
            branch=job.branch,
            worktree_path=job.worktree_path,
            session_id=job.session_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        session.add(row)
        await session.commit()


# ---------------------------------------------------------------------------
# RuntimeService — capacity & queueing
# ---------------------------------------------------------------------------


class TestCapacityAndQueueing:
    async def test_start_job_within_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        # Give the asyncio task time to start and finish
        await asyncio.sleep(0.3)
        # The FakeAgentAdapter finishes fast (delay=0), so the job should complete
        assert runtime.running_count == 0  # completed

    async def test_enqueue_when_at_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """When max_concurrent_jobs is reached, new jobs get enqueued."""
        # Create a slow adapter so jobs stay in-flight
        slow_adapter = FakeAgentAdapter(delay=2.0)
        runtime._adapter = slow_adapter

        jobs = []
        for i in range(3):
            job = _make_job(job_id=f"job-{i}", repo=config.repos[0])
            await _create_db_job(session_factory, job)
            jobs.append(job)

        # Start first two (within default capacity of 2)
        await runtime.start_or_enqueue(jobs[0])
        await runtime.start_or_enqueue(jobs[1])
        assert runtime.running_count == 2

        # Third should be enqueued
        await runtime.start_or_enqueue(jobs[2])
        # running_count shouldn't increase (still 2 running tasks)
        assert runtime.running_count == 2

        # Verify job-2 was transitioned to queued state in DB
        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get("job-2")
            assert row is not None
            assert row.state == JobState.queued

        # Cleanup
        await runtime.shutdown()

    async def test_running_count_property(self, runtime: RuntimeService) -> None:
        assert runtime.running_count == 0
        assert runtime.max_concurrent == 2  # default


# ---------------------------------------------------------------------------
# RuntimeService — event translation
# ---------------------------------------------------------------------------


class TestEventTranslation:
    def test_log_event_translated(self, runtime: RuntimeService) -> None:
        event = SessionEvent(
            kind=SessionEventKind.log,
            payload={"level": "info", "message": "test"},
        )
        result = runtime._translate_event("job-1", event)
        assert result is not None
        assert result.kind == DomainEventKind.log_line_emitted
        assert result.job_id == "job-1"

    def test_transcript_event_translated(self, runtime: RuntimeService) -> None:
        event = SessionEvent(
            kind=SessionEventKind.transcript,
            payload={"role": "agent", "content": "hello"},
        )
        result = runtime._translate_event("job-1", event)
        assert result is not None
        assert result.kind == DomainEventKind.transcript_updated

    def test_file_changed_not_translated(self, runtime: RuntimeService) -> None:
        """file_changed events are handled by DiffService, not _translate_event."""
        event = SessionEvent(
            kind=SessionEventKind.file_changed,
            payload={"path": "foo.py", "action": "modified"},
        )
        result = runtime._translate_event("job-1", event)
        assert result is None

    def test_approval_request_translated(self, runtime: RuntimeService) -> None:
        event = SessionEvent(
            kind=SessionEventKind.approval_request,
            payload={"description": "need approval"},
        )
        result = runtime._translate_event("job-1", event)
        assert result is not None
        assert result.kind == DomainEventKind.approval_requested

    def test_error_event_translated(self, runtime: RuntimeService) -> None:
        event = SessionEvent(
            kind=SessionEventKind.error,
            payload={"message": "boom"},
        )
        result = runtime._translate_event("job-1", event)
        assert result is not None
        assert result.kind == DomainEventKind.job_failed

    def test_done_event_returns_none(self, runtime: RuntimeService) -> None:
        event = SessionEvent(kind=SessionEventKind.done, payload={})
        result = runtime._translate_event("job-1", event)
        assert result is None


# ---------------------------------------------------------------------------
# RuntimeService — job lifecycle
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    async def test_successful_job_publishes_events(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: TowerConfig,
    ) -> None:
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.5)

        # Should have log, transcript events + job_succeeded
        # (diff_updated events now come from DiffService which requires a real git worktree)
        kinds = [e.kind for e in published]
        assert DomainEventKind.log_line_emitted in kinds
        assert DomainEventKind.transcript_updated in kinds
        assert DomainEventKind.job_succeeded in kinds

    async def test_cancel_running_job(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        # Use slow adapter to keep job running
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)
        assert runtime.running_count == 1

        await runtime.cancel(job.id)
        await asyncio.sleep(0.3)
        assert runtime.running_count == 0

    async def test_cancel_queued_job(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """cancel() for a non-running job is a no-op (state change is the API layer's job)."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        # Fill capacity
        j1 = _make_job(job_id="j1", repo=config.repos[0])
        j2 = _make_job(job_id="j2", repo=config.repos[0])
        j3 = _make_job(job_id="j3", repo=config.repos[0])
        await _create_db_job(session_factory, j1)
        await _create_db_job(session_factory, j2)
        await _create_db_job(session_factory, j3)

        await runtime.start_or_enqueue(j1)
        await runtime.start_or_enqueue(j2)
        await runtime.start_or_enqueue(j3)  # queued
        await asyncio.sleep(0.1)

        # cancel() should be a no-op for queued jobs (no task to cancel)
        await runtime.cancel("j3")
        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get("j3")
            assert row is not None
            # State remains queued — API layer handles the transition
            assert row.state == JobState.queued

        await runtime.shutdown()

    async def test_send_message_delegates_to_strategy(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)

        # Should not raise
        await runtime.send_message(job.id, "hello")
        # Non-existent job should also not raise
        await runtime.send_message("no-such-job", "hello")

        await runtime.shutdown()


# ---------------------------------------------------------------------------
# RuntimeService — recovery on startup
# ---------------------------------------------------------------------------


class TestRecovery:
    async def test_recover_fails_orphaned_running_jobs(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: TowerConfig,
    ) -> None:
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0], state=JobState.running)
        await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.failed

        # Should have published a job_failed event
        failed_events = [e for e in published if e.kind == DomainEventKind.job_failed]
        assert len(failed_events) == 1
        assert failed_events[0].payload["reason"] == "process_restarted"

    async def test_recover_restarts_queued_jobs(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        job = _make_job(repo=config.repos[0], state=JobState.queued)
        await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()
        await asyncio.sleep(0.5)

        # The queued job should have been started and completed (fast adapter)
        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.succeeded


# ---------------------------------------------------------------------------
# RuntimeService — shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_shutdown_cancels_all_tasks(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        slow_adapter = FakeAgentAdapter(delay=10.0)
        runtime._adapter = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)
        assert runtime.running_count == 1

        await runtime.shutdown()
        # After shutdown, tasks should be cleaned up
        assert runtime.running_count == 0


# ---------------------------------------------------------------------------
# FakeAgentAdapter
# ---------------------------------------------------------------------------


class TestFakeAgentAdapter:
    async def test_create_session_returns_fake_id(self) -> None:
        adapter = FakeAgentAdapter()
        config = SessionConfig(workspace_path="/tmp", prompt="test")
        session_id = await adapter.create_session(config)
        assert session_id.startswith("fake-")

    async def test_stream_events_produces_scripted_events(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        config = SessionConfig(workspace_path="/tmp", prompt="test")
        session_id = await adapter.create_session(config)

        events = []
        async for event in adapter.stream_events(session_id):
            events.append(event)

        assert len(events) == 5
        assert events[0].kind == SessionEventKind.log
        assert events[1].kind == SessionEventKind.transcript
        assert events[2].kind == SessionEventKind.file_changed
        assert events[3].kind == SessionEventKind.transcript
        assert events[4].kind == SessionEventKind.done

    async def test_abort_stops_stream(self) -> None:
        adapter = FakeAgentAdapter(delay=0.1)
        config = SessionConfig(workspace_path="/tmp", prompt="test")
        session_id = await adapter.create_session(config)

        await adapter.abort_session(session_id)

        events = []
        async for event in adapter.stream_events(session_id):
            events.append(event)

        # Aborted session returns immediately, yields no events
        assert len(events) == 0

    async def test_send_message_noop(self) -> None:
        adapter = FakeAgentAdapter()
        # Should not raise
        await adapter.send_message("fake-123", "hello")


# ---------------------------------------------------------------------------
# SingleAgentExecutor
# ---------------------------------------------------------------------------


class TestSingleAgentExecutor:
    async def test_execute_yields_events(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        executor = SingleAgentExecutor()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        events = []
        async for event in executor.execute(config, adapter):
            events.append(event)

        assert len(events) == 5
        assert events[-1].kind == SessionEventKind.done

    async def test_send_message_after_execute(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        executor = SingleAgentExecutor()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        # Must run execute first so session_id is set
        async for _ in executor.execute(config, adapter):
            pass

        # Should not raise
        await executor.send_message("test message")

    async def test_abort(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        executor = SingleAgentExecutor()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        async for _ in executor.execute(config, adapter):
            break  # start but don't finish

        await executor.abort()


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------


class TestStrategyRegistry:
    def test_single_agent_in_registry(self) -> None:
        from backend.models.api_schemas import StrategyKind

        assert StrategyKind.single_agent in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY[StrategyKind.single_agent] is SingleAgentExecutor


# ---------------------------------------------------------------------------
# MCP Server Discovery
# ---------------------------------------------------------------------------


class TestMCPDiscovery:
    def test_empty_when_no_files_exist(self, config: TowerConfig) -> None:
        result = _discover_mcp_servers("/nonexistent/repo", config)
        assert result == {}

    def test_reads_vscode_mcp_json(self, tmp_path: Path, config: TowerConfig) -> None:
        mcp_dir = tmp_path / ".vscode"
        mcp_dir.mkdir()
        mcp_json = {
            "servers": {
                "myserver": {
                    "command": "npx",
                    "args": ["-y", "@myserver/mcp"],
                    "env": {"TOKEN": "abc"},
                }
            }
        }
        (mcp_dir / "mcp.json").write_text(json.dumps(mcp_json))

        result = _discover_mcp_servers(str(tmp_path), config)
        assert "myserver" in result
        assert result["myserver"].command == "npx"
        assert result["myserver"].args == ["-y", "@myserver/mcp"]
        assert result["myserver"].env == {"TOKEN": "abc"}

    def test_tower_yml_disables_servers(self, tmp_path: Path, config: TowerConfig) -> None:
        # Add a server via .vscode/mcp.json
        mcp_dir = tmp_path / ".vscode"
        mcp_dir.mkdir()
        mcp_json = {
            "servers": {
                "keep": {"command": "keep-cmd", "args": []},
                "remove": {"command": "remove-cmd", "args": []},
            }
        }
        (mcp_dir / "mcp.json").write_text(json.dumps(mcp_json))

        # Disable 'remove' via .tower.yml
        tower_yml = {"tools": {"mcp": {"disabled": ["remove"]}}}
        (tmp_path / ".tower.yml").write_text(yaml.dump(tower_yml))

        result = _discover_mcp_servers(str(tmp_path), config)
        assert "keep" in result
        assert "remove" not in result

    def test_malformed_mcp_json_handled(self, tmp_path: Path, config: TowerConfig) -> None:
        mcp_dir = tmp_path / ".vscode"
        mcp_dir.mkdir()
        (mcp_dir / "mcp.json").write_text("{invalid json")

        # Should not raise
        result = _discover_mcp_servers(str(tmp_path), config)
        assert result == {}


# ---------------------------------------------------------------------------
# Protected paths resolution
# ---------------------------------------------------------------------------


class TestProtectedPaths:
    def test_no_tower_yml(self) -> None:
        result = _resolve_protected_paths("/nonexistent")
        assert result == []

    def test_reads_protected_paths(self, tmp_path: Path) -> None:
        tower_yml = {"protected_paths": ["src/config.py", "secrets/"]}
        (tmp_path / ".tower.yml").write_text(yaml.dump(tower_yml))

        result = _resolve_protected_paths(str(tmp_path))
        assert result == ["src/config.py", "secrets/"]

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".tower.yml").write_text(":::invalid:::")
        result = _resolve_protected_paths(str(tmp_path))
        assert result == []


# ---------------------------------------------------------------------------
# Session config builder
# ---------------------------------------------------------------------------


class TestBuildSessionConfig:
    def test_uses_worktree_path_when_set(self, config: TowerConfig) -> None:
        job = _make_job()
        job.worktree_path = "/some/worktree"
        result = _build_session_config(job, config)
        assert result.workspace_path == "/some/worktree"
        assert result.prompt == job.prompt

    def test_falls_back_to_repo(self, config: TowerConfig) -> None:
        job = _make_job()
        job.worktree_path = None
        result = _build_session_config(job, config)
        assert result.workspace_path == job.repo


# ---------------------------------------------------------------------------
# _make_event_id
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Concurrency guards
# ---------------------------------------------------------------------------


class TestConcurrencyGuards:
    async def test_double_start_guard(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """_start_job should no-op if a task for the job already exists."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)
        assert runtime.running_count == 1

        # Attempt to start the same job again (simulating a race)
        await runtime._start_job(job)
        # Should still have only one running task
        assert runtime.running_count == 1

        await runtime.shutdown()

    async def test_dequeue_respects_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """_dequeue_next under the lock should not exceed max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        # Create 3 queued jobs
        for i in range(3):
            job = _make_job(job_id=f"job-{i}", repo=config.repos[0])
            await _create_db_job(session_factory, job)
            await runtime.start_or_enqueue(job)

        await asyncio.sleep(0.1)
        # 2 running, 1 queued
        assert runtime.running_count == 2

        # Multiple concurrent dequeue calls should not exceed capacity
        await asyncio.gather(
            runtime._dequeue_next(),
            runtime._dequeue_next(),
        )
        # Still at capacity
        assert runtime.running_count <= runtime.max_concurrent

        await runtime.shutdown()

    async def test_cancel_already_canceled_is_idempotent(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """CancelledError handler should not fail if job is already canceled."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)

        # Transition to canceled via DB directly (simulating route handler)
        async with session_factory() as session:
            svc = runtime._make_job_service(session)
            await svc.transition_state(job.id, JobState.canceled)
            await session.commit()

        # Now cancel via runtime — the CancelledError handler should see
        # that the job is already canceled and skip the transition
        await runtime.cancel(job.id)
        await asyncio.sleep(0.3)

        # Should complete without errors
        assert runtime.running_count == 0


class TestRecoveryCapacity:
    async def test_recover_respects_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: TowerConfig
    ) -> None:
        """recover_on_startup uses start_or_enqueue, respecting max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter = slow_adapter

        # Create 3 queued jobs
        for i in range(3):
            job = _make_job(job_id=f"job-{i}", repo=config.repos[0], state=JobState.queued)
            await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()
        await asyncio.sleep(0.1)

        # Should only start max_concurrent (2), not all 3
        assert runtime.running_count == runtime.max_concurrent

        await runtime.shutdown()


class TestJobStateChangedEvent:
    async def test_state_change_publishes_correct_event_kind(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: TowerConfig,
    ) -> None:
        """_publish_state_event should use job_state_changed, not job_created."""
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.5)

        state_change_events = [e for e in published if e.kind == DomainEventKind.job_state_changed]
        assert len(state_change_events) >= 1
        # Should NOT have published any job_created events
        created_events = [e for e in published if e.kind == DomainEventKind.job_created]
        assert len(created_events) == 0


class TestMakeEventId:
    def test_format(self) -> None:
        eid = _make_event_id()
        assert eid.startswith("evt-")
        assert len(eid) == 16  # "evt-" + 12 hex chars

    def test_unique(self) -> None:
        ids = {_make_event_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Red-team: error events should cause job failure, not succeeded
# ---------------------------------------------------------------------------


class ErrorAdapter(AgentAdapterInterface):
    """Adapter that emits an error event before completing."""

    async def create_session(self, config: SessionConfig) -> str:
        return "err-session"

    async def stream_events(self, session_id: str) -> AsyncGenerator[SessionEvent, None]:
        yield SessionEvent(
            kind=SessionEventKind.log,
            payload={"level": "info", "message": "Starting"},
        )
        yield SessionEvent(
            kind=SessionEventKind.error,
            payload={"message": "Something went wrong"},
        )

    async def send_message(self, session_id: str, message: str) -> None:
        pass

    async def abort_session(self, session_id: str) -> None:
        pass


class TestErrorEventCausesFailure:
    async def test_error_event_fails_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: TowerConfig,
    ) -> None:
        """A job whose adapter emits an error event should end as failed, not succeeded."""
        error_adapter = ErrorAdapter()
        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter=error_adapter,
            config=config,
        )

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.5)

        # Should have a job_failed event, NOT job_succeeded
        kinds = [e.kind for e in published]
        assert DomainEventKind.job_failed in kinds
        assert DomainEventKind.job_succeeded not in kinds

        # DB state should be failed
        from backend.models.db import JobRow

        async with session_factory() as session:
            from sqlalchemy import select

            row = (await session.execute(select(JobRow).where(JobRow.id == job.id))).scalar_one()
            assert row.state == JobState.failed


# ---------------------------------------------------------------------------
# Red-team: start_or_enqueue uses dequeue lock
# ---------------------------------------------------------------------------


class TestStartOrEnqueueCapacitySafety:
    async def test_concurrent_start_or_enqueue_respects_capacity(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: TowerConfig,
    ) -> None:
        """Multiple concurrent start_or_enqueue calls should not exceed max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter=slow_adapter,
            config=config,
        )
        config.runtime.max_concurrent_jobs = 1

        jobs = []
        for i in range(3):
            job = _make_job(job_id=f"race-{i}", repo=config.repos[0])
            await _create_db_job(session_factory, job)
            jobs.append(job)

        # Fire all start_or_enqueue calls concurrently
        await asyncio.gather(*(runtime.start_or_enqueue(j) for j in jobs))
        await asyncio.sleep(0.1)

        # Should only have 1 running (max_concurrent=1)
        assert runtime.running_count <= config.runtime.max_concurrent_jobs

        await runtime.shutdown()
