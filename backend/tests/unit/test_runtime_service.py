"""Tests for RuntimeService — capacity, queueing, heartbeat, MCP discovery, event translation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

import structlog

from backend.config import (
    CPLConfig,
    build_session_config,
    discover_mcp_servers,
    resolve_protected_paths,
)
from backend.models.db import Base
from backend.models.domain import (
    Job,
    JobSpec,
    JobState,
    Preset,
    Resolution,
    SessionConfig,
    StateConflictError,
)
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, new_event
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.agent_adapter import AgentAdapterInterface, CompletionResult
from backend.services.events.event_bus import EventBus
from backend.services.runtime import (
    AgentSession,
    EventAction,
    RuntimeService,
)

log = structlog.get_logger()


class TestRuntimeContract:
    async def test_cancel_stops_scout_start_task(self, runtime):
        waiting = asyncio.create_task(asyncio.Event().wait())
        runtime._starting_tasks = {"job-1": waiting}
        try:
            await runtime.cancel("job-1")
            await asyncio.sleep(0)
            assert waiting.cancelled(), "Cancellation must stop preflight before primary launch"
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    async def test_followup_is_the_controllable_live_session(self, monkeypatch):
        from backend.services.runtime.verify import run_followup_turn

        observed = []
        host = SimpleNamespace(
            _agent_sessions={},
            _echo_suppress={},
            _session_ids={},
            _resolve_adapter=lambda sdk: object(),
            _event_bus=EventBus(),
            _event_processor=None,
        )

        async def execute(session, config, adapter):
            yield new_event("job-1", EventKind.message_assistant, {"content": "verified"})

        async def process(job_id, event, session, rejection):
            observed.append(host._agent_sessions.get(job_id) is session)
            return EventAction.skip, None

        host._process_agent_event = process
        monkeypatch.setattr(AgentSession, "execute", execute)
        await run_followup_turn(host, "job-1", "verify", SessionConfig(".", "fixture"), None, None, None)
        assert observed == [True]


def _make_runtime(**kwargs: Any) -> RuntimeService:
    """Build a RuntimeService with a stub governance decider wired.

    Since the P5 governance cutover, ``_setup_action_policy`` hard-requires a
    governance decider (wired via ``set_governance()`` at startup). These
    runtime-lifecycle tests don't exercise the TraceForge decision path, so a
    stub decider + subscriber satisfy the mandatory guard.
    """
    service = RuntimeService(**kwargs)
    service.set_governance(MagicMock(), MagicMock())  # type: ignore[arg-type]
    return service


# ---------------------------------------------------------------------------
# Test double — lives here, not in production code
# ---------------------------------------------------------------------------


class FakeAgentAdapter(AgentAdapterInterface):
    """Test double that emits scripted events with realistic event sequences."""

    def __init__(self, delay: float = 0.1) -> None:
        self._delay = delay
        self._aborted: set[str] = set()
        self._interrupted: set[str] = set()
        self._messages: list[tuple[str, str]] = []  # (session_id, message)
        self._paused: set[str] = set()

    async def create_session(self, config: SessionConfig) -> str:
        import uuid

        return f"fake-{uuid.uuid4().hex[:8]}"

    async def stream_events(self, session_id: str) -> AsyncGenerator[SessionEvent, None]:
        scripted: list[SessionEvent] = [
            new_event(
                session_id=session_id,
                kind=EventKind.log_line_emitted,
                payload={"level": "info", "message": "Agent session started"},
            ),
            new_event(
                session_id=session_id,
                kind=EventKind.message_assistant,
                payload={"content": "I'll analyze the codebase and implement the requested changes."},
            ),
            new_event(session_id=session_id, kind=EventKind.file_edited, payload={"path": "src/example.py"}),
            new_event(
                session_id=session_id,
                kind=EventKind.message_assistant,
                payload={"content": "Changes complete. All tests pass."},
            ),
        ]
        for event in scripted:
            if session_id in self._aborted:
                return
            await asyncio.sleep(self._delay)
            yield event

    async def send_message(self, session_id: str, message: str) -> None:
        self._messages.append((session_id, message))
        log.debug("fake_adapter_message", session_id=session_id, message=message)

    async def interrupt_session(self, session_id: str) -> None:
        self._interrupted.add(session_id)

    def pause_tools(self, session_id: str) -> None:
        self._paused.add(session_id)

    def resume_tools(self, session_id: str) -> None:
        self._paused.discard(session_id)

    async def abort_session(self, session_id: str) -> None:
        self._aborted.add(session_id)

    async def complete(self, prompt: str) -> CompletionResult:
        return CompletionResult(text="{}")

    def set_policy_router(self, router: object, policy: object, job_id: str, cwd: str) -> None:
        pass

    def update_repo_policy(self, job_id: str, policy: object) -> None:
        pass

    def set_job_id(self, session_id: str, job_id: str) -> None:
        pass


class FakeAdapterRegistry(AdapterRegistry):
    """Test registry that returns a pre-built adapter for any SDK."""

    def __init__(self, adapter: AgentAdapterInterface) -> None:
        super().__init__()
        self._fake = adapter

    def get_adapter(self, sdk=None) -> AgentAdapterInterface:  # noqa: ANN001
        return self._fake


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
def config(tmp_path: Path) -> CPLConfig:
    return CPLConfig(repos=[str(tmp_path)])


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def adapter() -> FakeAgentAdapter:
    return FakeAgentAdapter(delay=0.0)


@pytest.fixture
async def runtime(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    adapter: FakeAgentAdapter,
    config: CPLConfig,
) -> AsyncGenerator[RuntimeService, None]:
    service = _make_runtime(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter_registry=FakeAdapterRegistry(adapter),
        config=config,
    )
    yield service
    # Fence dequeueing before cancellation so cleanup cannot start another job.
    await service.shutdown()
    # Allow aiosqlite background threads to drain so they don't
    # encounter a closed event-loop after the engine is disposed.
    await asyncio.sleep(0.05)


def _make_job(
    *,
    job_id: str = "job-1",
    repo: str = "/repos/test",
    state: str = JobState.queued,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        project_id="proj-1",
        repo=repo,
        prompt="Fix the bug",
        state=state,
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
    from backend.persistence.project_repo import ProjectRepository

    async with session_factory() as session:
        project_repo = ProjectRepository(session)
        existing = await project_repo.get(job.project_id)
        if existing is None:
            await project_repo.create(job.project_id, "Test Project", [job.execution_repo])
        row = JobRow(
            id=job.id,
            repo=job.repo,
            project_id=job.project_id,
            prompt=job.prompt,
            state=job.state,
            base_ref=job.base_ref,
            branch=job.branch,
            worktree_path=job.worktree_path,
            session_id=job.session_id,
            title=job.title,
            worktree_name=job.worktree_name,
            preset=job.preset,
            session_count=job.session_count,
            sdk_session_id=job.sdk_session_id,
            model=job.model,
            resolution=job.resolution,
            archived_at=job.archived_at,
            failure_reason=job.failure_reason,
            sdk=job.sdk,
            verify=job.verify,
            self_review=job.self_review,
            max_turns=job.max_turns,
            verify_prompt=job.verify_prompt,
            self_review_prompt=job.self_review_prompt,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            pr_url=job.pr_url,
            merge_status=job.merge_status,
        )
        session.add(row)
        await session.commit()


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 15.0,
    interval: float = 0.05,
    msg: str = "",
) -> None:
    """Poll *predicate* every *interval* seconds; raise if *timeout* expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(msg or f"Condition not met within {timeout}s")
        await asyncio.sleep(interval)


async def _prepare_scout_job(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
) -> Job:
    from pathlib import Path

    job = _make_job(repo=config.repos[0])
    job.worktree_path = str(Path(config.repos[0]) / "job-worktree")
    Path(job.worktree_path).mkdir(exist_ok=True)
    job.model = "primary-heavy-model"
    await _create_db_job(session_factory, job)
    runtime._setup_action_policy = AsyncMock()  # type: ignore[method-assign]
    # Capture the configuration at the primary execution boundary; leave Scout
    # orchestration, terminal-state checks and persistence on their real paths.
    runtime._run_job_guarded = AsyncMock()  # type: ignore[method-assign]
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize(("utility_sdk", "primary_sdk"), [("copilot", "claude"), ("claude", "copilot")])
async def test_scout_uses_job_policy_when_utility_and_primary_sdks_differ(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    utility_sdk: str,
    primary_sdk: str,
) -> None:
    from backend.services.adapters.base_adapter import BaseAgentAdapter, PermissionDecision
    from backend.services.auth.permission_policy import PermissionRequest
    from backend.services.tools.preflight_curator import PreflightCurator

    class PolicyAdapter(BaseAgentAdapter, FakeAgentAdapter):
        pass

    job = await _prepare_scout_job(runtime, session_factory, config)
    primary = PolicyAdapter()
    utility = PolicyAdapter()
    config.runtime.default_sdk = utility_sdk
    runtime._adapter_registry = MagicMock()
    runtime._adapter_registry.get_adapter.side_effect = lambda sdk: {
        utility_sdk: utility,
        primary_sdk: primary,
    }[sdk]
    runtime._git_service = MagicMock()
    runtime.set_preflight_curator(PreflightCurator(utility, model="utility-model"))
    await RuntimeService._setup_action_policy(
        runtime,
        job.id,
        SessionConfig(workspace_path=job.worktree_path, prompt=job.prompt, sdk=primary_sdk),
        job.worktree_path,
        job_preset="locked",
    )
    router = runtime._policy_routers[job.id]
    router.route = AsyncMock(return_value=SimpleNamespace(proceed=True, classification=None))
    result = await utility._evaluate_permission(
        "scout",
        job.id,
        PermissionRequest(kind="read", workspace_path=job.worktree_path, path="src.py"),
        tool_name="view",
    )
    assert result == PermissionDecision.allow
    router.route.assert_awaited_once()
    assert primary._policy_router[job.id] is utility._policy_router[job.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["configuring_policy", "starting_agent"])
@pytest.mark.parametrize("action", ["cancel", "shutdown"])
async def test_scout_startup_cannot_dispatch_after_cancel_or_shutdown(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    event_bus: EventBus,
    stage: str,
    action: str,
) -> None:
    from backend.services.tools.preflight_curator import PreflightReport

    job = await _prepare_scout_job(runtime, session_factory, config)
    runtime.set_preflight_curator(MagicMock(curate=AsyncMock(return_value=PreflightReport(brief="Context"))))
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def pause_start(event: SessionEvent) -> None:
        if event.kind == EventKind.job_setup_progress and event.payload["step"] == stage:
            blocked.set()
            await release.wait()

    event_bus.subscribe(pause_start)
    startup = asyncio.create_task(runtime._start_job(job))
    await asyncio.wait_for(blocked.wait(), timeout=2)
    if action == "cancel":
        await runtime.cancel(job.id)
    else:
        await runtime.shutdown()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await startup
    runtime._run_job_guarded.assert_not_awaited()
    assert job.id not in runtime._tasks
    assert job.id not in runtime._agent_sessions


@pytest.mark.asyncio
async def test_cancellation_after_startup_completion_still_stops_primary(runtime: RuntimeService) -> None:
    startup = asyncio.create_task(asyncio.sleep(0))
    await startup
    primary = asyncio.create_task(asyncio.sleep(60))
    runtime._starting_tasks["job-1"] = startup
    runtime._tasks["job-1"] = primary
    await runtime.cancel("job-1")
    with pytest.raises(asyncio.CancelledError):
        await primary


@pytest.mark.asyncio
async def test_scout_brief_reaches_primary_without_structural_service(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    event_bus: EventBus,
) -> None:
    from backend.persistence.secondary_session_repo import SecondarySessionRepository
    from backend.services.tools.preflight_curator import PreflightReport, PreflightToolCall

    job = await _prepare_scout_job(runtime, session_factory, config)
    brief = "  src.py: target() uses the shared parser.\nRisk: callers expect None.\n"
    tool_call = PreflightToolCall(
        tool_name="view", tool_args='{"path":"src.py"}', result_text="def target(): pass", duration_ms=12.0
    )
    curator = MagicMock()

    async def curate(**kwargs: Any) -> PreflightReport:
        await kwargs["on_reasoning"]("Inspecting the task's entry point")
        await kwargs["on_tool_call"](tool_call)
        return PreflightReport(brief=brief, tool_calls=[tool_call])

    curator.curate = AsyncMock(side_effect=curate)
    runtime.set_preflight_curator(curator)
    published: list[SessionEvent] = []

    async def capture(event: SessionEvent) -> None:
        published.append(event)

    event_bus.subscribe(capture)
    await runtime._start_job(job)
    await runtime._tasks[job.id]

    primary = runtime._run_job_guarded.call_args.args[2]
    assert primary.memory_context == brief, "An absent structural service must not suppress the Scout handoff"
    assert primary.model == job.model
    assert primary.workspace_path == job.worktree_path
    curator.curate.assert_awaited_once()
    assert curator.curate.call_args.kwargs["repo"] == job.repo
    assert curator.curate.call_args.kwargs["worktree"] == job.worktree_path
    handoffs = [e for e in published if e.kind == EventKind.context_handoff]
    assert len(handoffs) == 1
    assert handoffs[0].payload["content"] == brief
    assert handoffs[0].payload["source"] == "preflight"
    assert any(e.kind == EventKind.secondary_session_started for e in published)
    assert any(e.kind == EventKind.job_setup_progress and e.payload["step"] == "exploring_codebase" for e in published)

    repository = SecondarySessionRepository(session_factory)
    sessions = await repository.get_by_job(job.id)
    assert len(sessions) == 1
    assert sessions[0].kind == "preflight"
    assert sessions[0].status == "completed"
    assert sessions[0].output == brief
    assert handoffs[0].payload["source_session_id"] == sessions[0].id
    entries = await repository.get_entries(sessions[0].id)
    assert [entry.seq for entry in entries] == [1, 2]
    assert entries[0].content == "Inspecting the task's entry point"
    assert entries[1].tool_name == "view"
    assert entries[1].tool_args == tool_call.tool_args
    assert entries[1].content == tool_call.result_text
    assert entries[1].duration_ms == 12.0
    assert entries[1].tool_success is True
    streamed_entries = [e.payload["entry"] for e in published if e.kind == EventKind.secondary_session_entry]
    assert [entry["seq"] for entry in streamed_entries] == [1, 2]
    assert streamed_entries[1]["tool_name"] == entries[1].tool_name


@pytest.mark.asyncio
async def test_scout_missing_worktree_fails_visibly_without_repo_fallback(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    event_bus: EventBus,
) -> None:
    from backend.persistence.secondary_session_repo import SecondarySessionRepository

    job = await _prepare_scout_job(runtime, session_factory, config)
    job.worktree_path = None
    curator = MagicMock()
    curator.curate = AsyncMock()
    runtime.set_preflight_curator(curator)
    published: list[SessionEvent] = []

    async def capture(event: SessionEvent) -> None:
        published.append(event)

    event_bus.subscribe(capture)
    session_config = SessionConfig(workspace_path=job.repo, prompt="Inspect the task", job_id=job.id)
    result = await runtime._run_preflight_curator(job, session_config)

    assert result is session_config
    curator.curate.assert_not_awaited()
    sessions = await SecondarySessionRepository(session_factory).get_by_job(job.id)
    assert len(sessions) == 1
    assert sessions[0].status == "failed"
    assert sessions[0].output is None
    assert any(
        event.kind == EventKind.secondary_session_completed and event.payload["status"] == "failed"
        for event in published
    )
    assert not any(event.kind == EventKind.context_handoff for event in published)


@pytest.mark.asyncio
async def test_scout_accounting_is_persisted_without_changing_primary_model(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
) -> None:
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.secondary_session_repo import SecondarySessionRepository

    job = await _prepare_scout_job(runtime, session_factory, config)
    report = SimpleNamespace(
        brief="src.py contains the shared parser.",
        tool_calls=[],
        elapsed_ms=15.0,
        input_tokens=200,
        output_tokens=50,
        cost_usd=0.002,
        model="utility-observed",
    )
    curator = MagicMock()
    curator.curate = AsyncMock(return_value=report)
    runtime.set_preflight_curator(curator)

    await runtime._start_job(job)
    await runtime._tasks[job.id]

    sessions = await SecondarySessionRepository(session_factory).get_by_job(job.id)
    assert len(sessions) == 1
    assert sessions[0].input_tokens == report.input_tokens
    assert sessions[0].output_tokens == report.output_tokens
    assert sessions[0].cost_usd == pytest.approx(report.cost_usd)
    assert json.loads(sessions[0].metadata_json)["model"] == report.model
    assert sessions[0].status == "completed"
    assert sessions[0].output == report.brief
    primary = runtime._run_job_guarded.call_args.args[2]
    assert primary.model == job.model
    assert primary.memory_context == report.brief
    async with session_factory() as session:
        persisted_job = await JobRepository(session).get(job.id)
    assert persisted_job is not None
    assert persisted_job.model == job.model


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("Scout failed"), TimeoutError("Scout timed out")])
async def test_scout_failure_remains_visible_without_fabricated_handoff(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    event_bus: EventBus,
    error: Exception,
) -> None:
    from backend.persistence.secondary_session_repo import SecondarySessionRepository

    job = await _prepare_scout_job(runtime, session_factory, config)
    curator = MagicMock()
    curator.curate = AsyncMock(side_effect=error)
    runtime.set_preflight_curator(curator)
    published: list[SessionEvent] = []

    async def capture(event: SessionEvent) -> None:
        published.append(event)

    event_bus.subscribe(capture)
    await runtime._start_job(job)
    await runtime._tasks[job.id]

    primary = runtime._run_job_guarded.call_args.args[2]
    assert not primary.memory_context
    assert primary.model == job.model
    assert not any(e.kind == EventKind.context_handoff for e in published)
    sessions = await SecondarySessionRepository(session_factory).get_by_job(job.id)
    assert len(sessions) == 1
    assert sessions[0].status in {"failed", "timeout"}
    assert sessions[0].output is None
    completed = [e for e in published if e.kind == EventKind.secondary_session_completed]
    assert len(completed) == 1
    assert completed[0].payload["status"] == sessions[0].status


@pytest.mark.asyncio
async def test_empty_scout_result_does_not_emit_handoff(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    event_bus: EventBus,
) -> None:
    from backend.persistence.secondary_session_repo import SecondarySessionRepository
    from backend.services.tools.preflight_curator import PreflightReport

    job = await _prepare_scout_job(runtime, session_factory, config)
    curator = MagicMock()
    curator.curate = AsyncMock(return_value=PreflightReport())
    runtime.set_preflight_curator(curator)
    published: list[SessionEvent] = []

    async def capture(event: SessionEvent) -> None:
        published.append(event)

    event_bus.subscribe(capture)
    await runtime._start_job(job)
    await runtime._tasks[job.id]

    assert not runtime._run_job_guarded.call_args.args[2].memory_context
    assert not any(e.kind == EventKind.context_handoff for e in published)
    sessions = await SecondarySessionRepository(session_factory).get_by_job(job.id)
    assert len(sessions) == 1
    assert sessions[0].status == "completed"
    assert sessions[0].output is None


@pytest.mark.asyncio
async def test_cancel_during_scout_stops_scout_and_never_launches_primary(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    event_bus: EventBus,
) -> None:
    from backend.persistence.job_repo import JobRepository
    from backend.services.tools.preflight_curator import PreflightReport

    job = await _prepare_scout_job(runtime, session_factory, config)
    entered = asyncio.Event()
    stopped = asyncio.Event()
    curator = MagicMock()

    async def curate(**kwargs: Any) -> PreflightReport:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return PreflightReport()

    curator.curate = AsyncMock(side_effect=curate)
    runtime.set_preflight_curator(curator)
    published: list[SessionEvent] = []

    async def capture(event: SessionEvent) -> None:
        published.append(event)

    event_bus.subscribe(capture)
    start = asyncio.create_task(runtime._start_job(job))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with session_factory() as session:
            await JobRepository(session).update_state(job.id, JobState.canceled, datetime.now(UTC))
            await session.commit()
        await runtime.cancel(job.id)
        # A bounded yield distinguishes a real interruption from merely waiting
        # for the Scout's normal (potentially two-minute) deadline.
        for _ in range(20):
            if stopped.is_set():
                break
            await asyncio.sleep(0.01)
        assert stopped.is_set(), "Canceling a job must interrupt its active Scout"
        await asyncio.gather(start, return_exceptions=True)
        runtime._run_job_guarded.assert_not_awaited()
        assert not any(e.kind == EventKind.context_handoff for e in published)
        assert not any(
            e.kind == EventKind.job_setup_progress and e.payload["step"] == "starting_agent" for e in published
        )
    finally:
        start.cancel()
        await asyncio.gather(start, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_cancel_order_preserves_scout_terminal_history(
    runtime: RuntimeService, config: CPLConfig, tmp_path: Path
) -> None:
    from contextlib import asynccontextmanager

    from backend.api.jobs import cancel_job
    from backend.di import RequestProvider
    from backend.persistence.secondary_session_repo import SecondarySessionRepository

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def configure(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=1000")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    runtime._session_factory = factory
    job = await _prepare_scout_job(runtime, factory, config)
    entered = asyncio.Event()
    curator = MagicMock()

    async def curate(**kwargs: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    curator.curate = AsyncMock(side_effect=curate)
    runtime.set_preflight_curator(curator)
    start = asyncio.create_task(runtime._start_job(job))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        request_scope = asynccontextmanager(RequestProvider().session)
        async with request_scope(factory) as request_session:
            response = await cancel_job(job.id, runtime._make_job_service(request_session), runtime, AsyncMock())
            assert response.state == "canceled"
            # Response transmission can yield before the real provider commits.
            await asyncio.sleep(0.05)
        await asyncio.gather(start, return_exceptions=True)
        sessions = await SecondarySessionRepository(factory).get_by_job(job.id)
        assert len(sessions) == 1
        assert sessions[0].status == "failed"
        assert sessions[0].completed_at is not None
        runtime._run_job_guarded.assert_not_awaited()
    finally:
        start.cancel()
        await asyncio.gather(start, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_scout_timeout_retains_observed_accounting(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.persistence.secondary_session_repo import SecondarySessionRepository
    from backend.services.tools import preflight_curator

    job = await _prepare_scout_job(runtime, session_factory, config)
    adapter = AsyncMock()
    adapter.create_session.return_value = "timed-scout"

    async def stream(session_id: str):
        yield new_event(
            job.id,
            EventKind.telemetry_usage,
            {"model": "utility-observed", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.003},
        )
        await asyncio.Event().wait()

    adapter.stream_events = MagicMock(side_effect=stream)
    monkeypatch.setattr(preflight_curator, "_SESSION_TIMEOUT_S", 0.02)
    runtime.set_preflight_curator(preflight_curator.PreflightCurator(adapter, model=config.runtime.utility_model))
    await runtime._start_job(job)
    await runtime._tasks[job.id]
    sessions = await SecondarySessionRepository(session_factory).get_by_job(job.id)
    assert len(sessions) == 1
    assert sessions[0].status == "timeout"
    assert sessions[0].output is None
    assert sessions[0].input_tokens == 100
    assert sessions[0].output_tokens == 20
    assert sessions[0].cost_usd == pytest.approx(0.003)
    assert json.loads(sessions[0].metadata_json)["model"] == "utility-observed"
    assert not runtime._run_job_guarded.call_args.args[2].memory_context


@pytest.mark.asyncio
async def test_shutdown_stops_scout_before_primary_launch(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    config: CPLConfig,
) -> None:
    job = await _prepare_scout_job(runtime, session_factory, config)
    entered = asyncio.Event()
    stopped = asyncio.Event()
    curator = MagicMock()

    async def curate(**kwargs: Any) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    curator.curate = AsyncMock(side_effect=curate)
    runtime.set_preflight_curator(curator)
    start = asyncio.create_task(runtime._start_job(job))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await runtime.shutdown()
        await asyncio.gather(start, return_exceptions=True)
        assert stopped.is_set()
        runtime._run_job_guarded.assert_not_awaited()
    finally:
        start.cancel()
        await asyncio.gather(start, return_exceptions=True)


@pytest.mark.asyncio
async def test_create_followup_job_uses_parent_handoff_context(runtime: RuntimeService) -> None:
    parent = _make_job(job_id="parent", state=JobState.review)
    parent.preset = Preset.locked
    parent.model = "gpt-5.4"
    parent.sdk = "claude"
    parent.verify = True
    parent.self_review = True
    parent.max_turns = 4
    parent.verify_prompt = "Verify this carefully"
    parent.self_review_prompt = "Review your work"

    child = _make_job(job_id="child", state=JobState.queued)
    child.prompt = "Add regression coverage"

    fake_service = SimpleNamespace(
        get_job=AsyncMock(side_effect=[parent, child]),
        create_job=AsyncMock(return_value=child),
    )
    runtime._make_job_service = lambda session: fake_service  # type: ignore[assignment, return-value]
    runtime._build_followup_handoff_prompt_for_job = AsyncMock(return_value="FOLLOWUP HANDOFF")  # type: ignore[method-assign]
    runtime.start_or_enqueue = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await runtime.create_followup_job("parent", "  Add regression coverage  ")

    assert result is child
    assert fake_service.get_job.await_args_list[0].args == ("parent",)
    fake_service.create_job.assert_awaited_once_with(
        JobSpec(
            repo=parent.repo,
            prompt="Add regression coverage",
            base_ref=parent.base_ref,
            preset=Preset.locked,
            model="gpt-5.4",
            sdk="claude",
            verify=True,
            self_review=True,
            max_turns=4,
            verify_prompt="Verify this carefully",
            self_review_prompt="Review your work",
            parent_job_id="parent",
            parent_job_context="This is a follow-up task continuing work from 'parent' (parent job: parent).",
        )
    )
    runtime.start_or_enqueue.assert_awaited_once_with(child, override_prompt="FOLLOWUP HANDOFF")
    assert fake_service.get_job.await_args_list[1].args == ("child",)


class ResumeFallbackAdapter(AgentAdapterInterface):
    def __init__(self, *, first_attempt_progress: bool = False) -> None:
        self.configs: list[SessionConfig] = []
        self._first_attempt_progress = first_attempt_progress

    async def create_session(self, config: SessionConfig) -> str:
        self.configs.append(config)
        return f"resume-{len(self.configs)}"

    async def stream_events(self, session_id: str) -> AsyncGenerator[SessionEvent, None]:
        attempt = int(session_id.rsplit("-", 1)[1])
        if attempt == 1:
            if self._first_attempt_progress:
                yield new_event(
                    session_id=session_id,
                    kind=EventKind.message_assistant,
                    payload={"content": "I resumed and started working."},
                )
            yield new_event(
                session_id=session_id,
                kind=EventKind.job_failed,
                payload={"message": "Execution failed: CAPIError: 400 400 Bad Request\n"},
            )
            return

        yield new_event(
            session_id=session_id,
            kind=EventKind.message_assistant,
            payload={"content": "Recovered via opaque handoff."},
        )

    async def send_message(self, session_id: str, message: str) -> None:
        return None

    async def abort_session(self, session_id: str) -> None:
        return None

    async def complete(self, prompt: str) -> CompletionResult:
        return CompletionResult(text="{}")

    def set_policy_router(self, router: object, policy: object, job_id: str, cwd: str) -> None:
        pass

    def update_repo_policy(self, job_id: str, policy: object) -> None:
        pass

    def set_job_id(self, session_id: str, job_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# RuntimeService — capacity & queueing
# ---------------------------------------------------------------------------


class TestCapacityAndQueueing:
    async def test_start_job_within_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        # The FakeAgentAdapter finishes fast (delay=0), so the job should complete
        await _wait_until(lambda: runtime.running_count == 0, msg="job did not complete")

    async def test_enqueue_when_at_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """When max_concurrent_jobs is reached, new jobs get enqueued."""
        # Create a slow adapter so jobs stay in-flight
        slow_adapter = FakeAgentAdapter(delay=2.0)
        runtime._adapter_registry._fake = slow_adapter

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

    async def test_running_count_property(self, runtime: RuntimeService) -> None:
        assert runtime.running_count == 0
        assert runtime.max_concurrent == 2  # default


# ---------------------------------------------------------------------------
# RuntimeService — TF-native managed pre-step
# ---------------------------------------------------------------------------


class TestProcessAgentEvent:
    async def test_log_event_publishes(self, runtime: RuntimeService) -> None:
        event = new_event("job-1", EventKind.log_line_emitted, {"level": "info", "message": "test"})
        action, error = await runtime._process_agent_event("job-1", event, None, "rejected")
        assert action == EventAction.publish
        assert error is None

    async def test_transcript_event_publishes(self, runtime: RuntimeService) -> None:
        event = new_event("job-1", EventKind.message_assistant, {"content": "hello"})
        action, error = await runtime._process_agent_event("job-1", event, None, "rejected")
        assert action == EventAction.publish
        assert error is None

    async def test_file_edited_publishes_to_processor_funnel(self, runtime: RuntimeService) -> None:
        event = new_event("job-1", EventKind.file_edited, {"path": "foo.py"})
        action, error = await runtime._process_agent_event("job-1", event, None, "rejected")
        assert action == EventAction.publish
        assert error is None

    async def test_tool_events_track_active_tool(self, runtime: RuntimeService) -> None:
        started = new_event("job-1", EventKind.tool_call_started, {"tool_name": "bash", "arguments": {"command": "ls"}})
        action, error = await runtime._process_agent_event("job-1", started, None, "rejected")
        assert action == EventAction.publish
        assert error is None
        assert runtime._active_tool["job-1"][0] == "bash"
        assert runtime._active_tool["job-1"][2] == '{"command": "ls"}'

        completed = new_event("job-1", EventKind.tool_call_completed, {"tool_name": "bash"})
        action, error = await runtime._process_agent_event("job-1", completed, None, "rejected")
        assert action == EventAction.publish
        assert error is None
        assert "job-1" not in runtime._active_tool

    async def test_error_event_reports_error_reason(self, runtime: RuntimeService) -> None:
        event = new_event("job-1", EventKind.job_failed, {"message": "boom"})
        action, error = await runtime._process_agent_event("job-1", event, None, "rejected")
        assert action == EventAction.publish
        assert error == "boom"


# ---------------------------------------------------------------------------
# RuntimeService — job lifecycle
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    async def test_successful_job_publishes_events(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        published: list[SessionEvent] = []

        async def _collect(e: SessionEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(
            lambda: any(e.kind == EventKind.job_review for e in published),
            msg="job_review event not published",
        )

        # Should have log, transcript events + job_review
        # (diff_updated events now come from DiffService which requires a real git worktree)
        kinds = [e.kind for e in published]
        assert EventKind.log_line_emitted in kinds
        assert any(k in TRANSCRIPT_KINDS for k in kinds)
        assert EventKind.job_review in kinds

    async def test_cancel_running_job(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        # Use slow adapter to keep job running
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(lambda: runtime.running_count == 1, msg="job did not start")
        await asyncio.sleep(0.2)  # let task progress past setup

        await runtime.cancel(job.id)
        await _wait_until(lambda: runtime.running_count == 0, msg="job did not stop after cancel")

    async def test_cancel_queued_job(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """cancel() for a non-running job is a no-op (state change is the API layer's job)."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

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

    async def test_send_message_delegates_to_session(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)

        # Should not raise
        await runtime.send_message(job.id, "hello")
        # Non-existent job should also not raise
        await runtime.send_message("no-such-job", "hello")

    async def test_send_message_auto_resumes_terminal_job(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """send_message on a job with no live session but a terminal DB state auto-resumes it."""
        published: list[SessionEvent] = []

        async def _collect(e: SessionEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        # Insert a review job with no in-memory session
        job = _make_job(repo=config.repos[0], state=JobState.review)
        await _create_db_job(session_factory, job)

        result = await runtime.send_message(job.id, "please continue")
        assert result is True

        # Give the resumed task time to run to completion
        await asyncio.sleep(0.5)

        # The auto-resume should have published a session_resumed event
        kinds = [e.kind for e in published]
        assert EventKind.session_resumed in kinds

        # The operator message should appear as a transcript entry
        transcript_events = [e for e in published if e.kind in TRANSCRIPT_KINDS]
        operator_msgs = [e for e in transcript_events if e.kind == EventKind.message_user]
        assert any("please continue" in e.payload.get("content", "") for e in operator_msgs)

    async def test_send_message_auto_resumes_orphaned_running_job(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """send_message on a job in 'running' state with no live session recovers it in place."""
        published: list[SessionEvent] = []

        async def _collect(e: SessionEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        # Simulate an orphaned running job (e.g. server was killed mid-run)
        job = _make_job(repo=config.repos[0], state=JobState.running)
        await _create_db_job(session_factory, job)

        result = await runtime.send_message(job.id, "retry please")
        assert result is True

        # Wait for the resumed task to finish
        await _wait_until(
            lambda: any(e.kind == EventKind.session_resumed for e in published),
            msg="session_resumed event not published",
        )
        await _wait_until(lambda: runtime.running_count == 0, msg="resumed job did not finish")

        kinds = [e.kind for e in published]
        assert EventKind.job_failed not in kinds
        assert EventKind.session_resumed in kinds

        # DB should end in review/failed/canceled after the resumed run
        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state in (JobState.review, JobState.failed, JobState.canceled)

    async def test_send_message_returns_false_for_nonexistent_job(
        self,
        runtime: RuntimeService,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """send_message returns False (not an error) for a job that doesn't exist."""
        result = await runtime.send_message("nonexistent-job", "hello")
        assert result is False

    async def test_pause_job_no_session_returns_false(
        self,
        runtime: RuntimeService,
    ) -> None:
        """pause_job returns False when no agent session is running."""
        result = await runtime.pause_job("nonexistent-job")
        assert result is False

    async def test_pause_job_blocks_tools_and_interrupts(
        self,
        runtime: RuntimeService,
        adapter: FakeAgentAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """pause_job blocks tools, interrupts the session, and sends the pause message."""
        slow_adapter = FakeAgentAdapter(delay=2.0)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(lambda: runtime.running_count == 1, msg="job did not start")
        await asyncio.sleep(0.5)  # let task progress past setup

        result = await runtime.pause_job(job.id)
        assert result is True

        # Adapter should have been interrupted
        assert len(slow_adapter._interrupted) == 1
        # Adapter should have been told to pause tools
        assert len(slow_adapter._paused) == 1
        # A pause message should have been sent
        assert any("stop what you are doing" in msg for _, msg in slow_adapter._messages)

    async def test_send_message_clears_pause(
        self,
        runtime: RuntimeService,
        adapter: FakeAgentAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """Sending a follow-up message after pause lifts the tool block."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(lambda: runtime.running_count == 1, msg="job did not start")
        await asyncio.sleep(0.2)  # let task progress past setup

        await runtime.pause_job(job.id)
        assert len(slow_adapter._paused) == 1

        await runtime.send_message(job.id, "continue working")
        # Tool block should be cleared
        assert len(slow_adapter._paused) == 0


class TestResumeFallback:
    async def test_conflict_resume_auto_merges_back_after_agent_success(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        merge_service = AsyncMock()

        async def _resolve_job(*args: object, **kwargs: object):
            from types import SimpleNamespace

            from backend.persistence.job_repo import JobRepository

            async with session_factory() as session:
                repo = JobRepository(session)
                await repo.update_merge_status("job-1", Resolution.merged)
                await session.commit()

            return SimpleNamespace(status="merged", pr_url=None, conflict_files=None, error=None)

        merge_service.resolve_job.side_effect = _resolve_job

        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter(delay=0.0)),
            config=config,
            merge_service=merge_service,
        )

        published: list[SessionEvent] = []

        async def _collect(event: SessionEvent) -> None:
            published.append(event)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0], state=JobState.review)
        job.branch = "cpl/job-1"
        job.completed_at = datetime.now(UTC)
        job.resolution = Resolution.conflict
        job.merge_status = Resolution.conflict
        await _create_db_job(session_factory, job)

        resumed = await runtime.resume_job(job.id, "resolve the merge conflict")
        assert resumed.state == JobState.running

        await _wait_until(
            lambda: any(e.kind == EventKind.job_completed for e in published),
            msg="job_completed event not published",
        )

        merge_service.resolve_job.assert_awaited_once()

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.completed
            assert row.resolution == Resolution.merged
            assert row.merge_status == Resolution.merged

        completed_events = [event for event in published if event.kind == EventKind.job_completed]
        assert completed_events
        assert completed_events[-1].payload["resolution"] == Resolution.merged
        assert completed_events[-1].payload["merge_status"] == Resolution.merged

        await runtime.shutdown()

    async def test_resume_restores_terminal_state_when_startup_fails(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
        from backend.persistence.job_repo import JobRepository

        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )

        published: list[SessionEvent] = []

        async def _collect(event: SessionEvent) -> None:
            published.append(event)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0], state=JobState.review)
        job.completed_at = datetime.now(UTC)
        job.session_count = 4
        job.resolution = Resolution.unresolved
        await _create_db_job(session_factory, job)
        async with session_factory() as session:
            initial = await JobRepository(session).declare_initial_legacy_admission(job.id)
            await session.commit()

        async def _boom(*args: object, **kwargs: object) -> None:
            async with session_factory() as session:
                current = await ExecutionAdmissionRepository(session).current(job.id)
                assert current is not None and current.id != initial.id
                assert current.previous_admission_id == initial.id
            raise RuntimeError("startup failed")

        monkeypatch.setattr(runtime, "start_or_enqueue", _boom)

        with pytest.raises(RuntimeError, match="startup failed"):
            await runtime.resume_job(job.id, "continue")

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.review
            assert row.session_count == 4
            assert row.resolution == Resolution.unresolved
            assert row.completed_at is not None
            assert await ExecutionAdmissionRepository(session).current(job.id) == initial

        assert [event.kind for event in published] == []

        await runtime.shutdown()

    async def test_resume_uses_default_instruction_when_none_is_provided(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        adapter = ResumeFallbackAdapter()
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
        )

        job = _make_job(repo=config.repos[0], state=JobState.failed)
        job.prompt = "Fix session pool scaling for naming service"
        job.session_count = 2
        job.sdk_session_id = "stale-sdk-session"
        job.completed_at = datetime.now(UTC)
        await _create_db_job(session_factory, job)

        await runtime.resume_job(job.id, None)
        await _wait_until(lambda: len(adapter.configs) >= 2, msg="adapter did not receive 2 configs")

        assert len(adapter.configs) == 2
        assert adapter.configs[0].prompt == "Continue the current task from where you left off and finish it."
        assert "Continue the current task from where you left off and finish it." in adapter.configs[1].prompt

        await runtime.shutdown()

    async def test_resume_rejects_when_missing_worktree_cannot_be_restored(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from pathlib import Path

        from backend.services.git.git_service import GitError, GitService

        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )

        job = _make_job(repo=config.repos[0], state=JobState.failed)
        job.completed_at = datetime.now(UTC)
        job.worktree_path = str(Path(config.repos[0]) / ".codeplane-worktrees" / job.id)
        job.branch = "cpl/job-1"
        await _create_db_job(session_factory, job)

        async def _fail_reattach(self: GitService, repo_path: str, job_id: str, branch: str) -> str:
            raise GitError("branch no longer exists", stderr="fatal: invalid reference")

        monkeypatch.setattr(GitService, "reattach_worktree", _fail_reattach)

        with pytest.raises(StateConflictError, match="worktree could not be restored"):
            await runtime.resume_job(job.id, "continue")

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.failed
            assert row.session_count == job.session_count

        await runtime.shutdown()

    async def test_resume_falls_back_to_handoff_when_native_resume_errors_immediately(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        from backend.persistence.event_repo import EventRepository

        adapter = ResumeFallbackAdapter()
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
        )

        job = _make_job(repo=config.repos[0], state=JobState.failed)
        job.prompt = "Fix session pool scaling for naming service"
        job.session_count = 7
        job.sdk_session_id = "stale-sdk-session"
        job.completed_at = datetime.now(UTC)
        await _create_db_job(session_factory, job)

        resumed = await runtime.resume_job(job.id, "continue")
        assert resumed.state == JobState.running

        await _wait_until(lambda: len(adapter.configs) >= 2, msg="adapter did not receive 2 configs")

        assert len(adapter.configs) == 2
        assert adapter.configs[0].resume_sdk_session_id == "stale-sdk-session"
        assert adapter.configs[0].prompt == "continue"
        assert adapter.configs[1].resume_sdk_session_id is None
        assert "[RESUMED SESSION" in adapter.configs[1].prompt
        assert "Fix session pool scaling for naming service" in adapter.configs[1].prompt
        assert "continue" in adapter.configs[1].prompt

        async def _check_review() -> bool:
            async with session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                repo = JobRepository(session)
                row = await repo.get(job.id)
                return row is not None and row.state == JobState.review

        await _wait_until(_check_review, msg="job state not review in DB")

        async def _check_sdk_session_id() -> bool:
            async with session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                repo = JobRepository(session)
                row = await repo.get(job.id)
                return row is not None and row.sdk_session_id == "resume-2"

        await _wait_until(_check_sdk_session_id, msg="sdk_session_id not set to resume-2")

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.sdk_session_id == "resume-2"
            declarations = [
                item.event
                for item in await EventRepository(session).list_after(0, job_id=job.id)
                if item.event.kind == "execution.admission.declared"
            ]
            assert len(declarations) == 1, "Native fallback must retain the accepted admission"

        await runtime.shutdown()

    async def test_resume_does_not_fallback_after_progress_has_started(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        adapter = ResumeFallbackAdapter(first_attempt_progress=True)
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
        )

        job = _make_job(repo=config.repos[0], state=JobState.failed)
        job.session_count = 3
        job.sdk_session_id = "resume-me"
        job.completed_at = datetime.now(UTC)
        await _create_db_job(session_factory, job)

        await runtime.resume_job(job.id, "resume")
        await _wait_until(lambda: len(adapter.configs) >= 1, msg="adapter did not receive config")
        await _wait_until(lambda: runtime.running_count == 0, msg="job did not finish")

        assert len(adapter.configs) == 1

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.failed

        await runtime.shutdown()

    async def test_resume_allows_resolved_merged_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """Resolved/merged jobs can still be resumed when the worktree exists."""
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )

        job = _make_job(repo=config.repos[0], state=JobState.completed)
        job.completed_at = datetime.now(UTC)
        job.resolution = Resolution.merged
        await _create_db_job(session_factory, job)

        # Resume should succeed — the backend no longer blocks on resolution
        result = await runtime.resume_job(job.id, "keep going")
        assert result.state == JobState.running

        await runtime.shutdown()

    async def test_resume_allows_archived_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """Archived jobs can still be resumed when the worktree exists."""
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )

        job = _make_job(repo=config.repos[0], state=JobState.failed)
        job.completed_at = datetime.now(UTC)
        job.archived_at = datetime.now(UTC)
        await _create_db_job(session_factory, job)

        # Resume should succeed — the backend no longer blocks on archived_at
        result = await runtime.resume_job(job.id, "try again")
        assert result.state == JobState.running

        await runtime.shutdown()


# ---------------------------------------------------------------------------
# RuntimeService — recovery on startup
# ---------------------------------------------------------------------------


class TestRecovery:
    async def test_recover_restarts_orphaned_running_jobs(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        from backend.persistence.job_repo import JobRepository

        published: list[SessionEvent] = []

        async def _collect(e: SessionEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0], state=JobState.running)
        await _create_db_job(session_factory, job)
        async with session_factory() as session:
            initial = await JobRepository(session).declare_initial_legacy_admission(job.id)
            await session.commit()

        await runtime.recover_on_startup()
        await _wait_until(
            lambda: any(e.kind == EventKind.job_review for e in published),
            msg="job_review event not published after recovery",
        )

        async with session_factory() as session:
            from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.review
            assert await ExecutionAdmissionRepository(session).current(job.id) == initial

        resumed_events = [e for e in published if e.kind == EventKind.session_resumed]
        assert len(resumed_events) == 1
        assert resumed_events[0].payload["reason"] == "server_restart"

    async def test_recover_commits_running_state_before_publishing_resume_event(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        observed_states: list[str] = []

        async def _assert_running_state_visible(event: SessionEvent) -> None:
            if event.kind != EventKind.session_resumed:
                return
            async with session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                repo = JobRepository(session)
                row = await repo.get("job-1")
                assert row is not None
                observed_states.append(row.state)

        event_bus.subscribe(_assert_running_state_visible)

        job = _make_job(job_id="job-1", repo=config.repos[0], state=JobState.running)
        await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()

        assert observed_states == [JobState.running]

    async def test_recover_restarts_queued_jobs(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        job = _make_job(repo=config.repos[0], state=JobState.queued)
        await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()
        await _wait_until(lambda: runtime.running_count == 0, msg="recovered job did not finish")

        # The queued job should have been started and completed (fast adapter)
        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.review


# ---------------------------------------------------------------------------
# RuntimeService — shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_shutdown_cancels_all_tasks(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        slow_adapter = FakeAgentAdapter(delay=10.0)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(lambda: runtime.running_count == 1, msg="job did not start")
        await asyncio.sleep(0.2)  # let task progress past setup

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

        assert len(events) == 4
        assert events[0].kind == EventKind.log_line_emitted
        assert events[1].kind == EventKind.message_assistant
        assert events[2].kind == EventKind.file_edited
        assert events[3].kind == EventKind.message_assistant

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
# _AgentSession
# ---------------------------------------------------------------------------


class TestAgentSession:
    async def test_execute_yields_events(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        events = []
        async for event in session.execute(config, adapter):
            events.append(event)

        assert len(events) == 4
        assert events[-1].kind == EventKind.message_assistant

    async def test_send_message_after_execute(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        # Must run execute first so session_id is set
        async for _ in session.execute(config, adapter):
            pass

        # Should not raise
        await session.send_message("test message")

    async def test_abort(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        async for _ in session.execute(config, adapter):
            break  # start but don't finish

        await session.abort()

    async def test_pause_and_resume_tools(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        async for _ in session.execute(config, adapter):
            pass

        session.pause_tools()
        assert session.session_id in adapter._paused

        session.resume_tools()
        assert session.session_id not in adapter._paused


# ---------------------------------------------------------------------------
# MCP Server Discovery
# ---------------------------------------------------------------------------


class TestMCPDiscovery:
    def test_empty_when_no_files_exist(self, config: CPLConfig) -> None:
        result = discover_mcp_servers("/nonexistent/repo", config)
        assert result == {}

    def test_reads_vscode_mcp_json(self, tmp_path: Path, config: CPLConfig) -> None:
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

        result = discover_mcp_servers(str(tmp_path), config)
        assert "myserver" in result
        assert result["myserver"].command == "npx"
        assert result["myserver"].args == ["-y", "@myserver/mcp"]
        assert result["myserver"].env == {"TOKEN": "abc"}

    def test_codeplane_yml_disables_servers(self, tmp_path: Path, config: CPLConfig) -> None:
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

        # Disable 'remove' via .codeplane.yml
        codeplane_yml = {"tools": {"mcp": {"disabled": ["remove"]}}}
        (tmp_path / ".codeplane.yml").write_text(yaml.dump(codeplane_yml))

        result = discover_mcp_servers(str(tmp_path), config)
        assert "keep" in result
        assert "remove" not in result

    def test_malformed_mcp_json_handled(self, tmp_path: Path, config: CPLConfig) -> None:
        mcp_dir = tmp_path / ".vscode"
        mcp_dir.mkdir()
        (mcp_dir / "mcp.json").write_text("{invalid json")

        # Should not raise
        result = discover_mcp_servers(str(tmp_path), config)
        assert result == {}


# ---------------------------------------------------------------------------
# Protected paths resolution
# ---------------------------------------------------------------------------


class TestProtectedPaths:
    def test_no_codeplane_yml(self) -> None:
        result = resolve_protected_paths("/nonexistent")
        assert result == []

    def test_reads_protected_paths(self, tmp_path: Path) -> None:
        codeplane_yml = {"protected_paths": ["src/config.py", "secrets/"]}
        (tmp_path / ".codeplane.yml").write_text(yaml.dump(codeplane_yml))

        result = resolve_protected_paths(str(tmp_path))
        assert result == ["src/config.py", "secrets/"]

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".codeplane.yml").write_text(":::invalid:::")
        result = resolve_protected_paths(str(tmp_path))
        assert result == []


# ---------------------------------------------------------------------------
# Session config builder
# ---------------------------------------------------------------------------


class TestBuildSessionConfig:
    def test_uses_worktree_path_when_set(self, config: CPLConfig) -> None:
        job = _make_job()
        job.worktree_path = "/some/worktree"
        result = build_session_config(job, config)
        assert result.workspace_path == "/some/worktree"
        assert result.prompt == job.prompt

    def test_falls_back_to_repo(self, config: CPLConfig) -> None:
        job = _make_job()
        job.worktree_path = None
        result = build_session_config(job, config)
        assert result.workspace_path == job.repo


# ---------------------------------------------------------------------------
# _make_event_id
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Concurrency guards
# ---------------------------------------------------------------------------


class TestConcurrencyGuards:
    @pytest.fixture(name="engine")
    async def disk_engine(self, tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
        from backend.persistence.database import create_engine

        # Concurrent ORM sessions need separate connections, not one shared
        # in-memory transaction that another session can roll back.
        engine = create_engine(tmp_path / "runtime-concurrency.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            yield engine
        finally:
            await engine.dispose()

    async def test_managed_cleanup_continues_when_artifact_finalization_raises(
        self,
        runtime: RuntimeService,
    ) -> None:
        job_id = "job-finalizer-failure"
        task = asyncio.current_task()
        assert task is not None
        runtime._tasks[job_id] = task
        runtime._last_activity[job_id] = 1.0
        runtime._ensure_terminal_state = AsyncMock()  # type: ignore[method-assign]
        runtime._artifact_finalizer.finalize = AsyncMock(side_effect=RuntimeError("database unavailable"))
        runtime._dequeue_next = AsyncMock()  # type: ignore[method-assign]

        await runtime._cleanup_job_state(job_id)

        runtime._artifact_finalizer.finalize.assert_awaited_once_with(job_id)
        runtime._dequeue_next.assert_awaited_once_with()
        assert job_id not in runtime._tasks
        assert job_id not in runtime._last_activity

    async def test_external_cleanup_continues_when_artifact_finalization_raises(
        self,
        runtime: RuntimeService,
    ) -> None:
        job_id = "external-finalizer-failure"
        runtime._last_activity[job_id] = 1.0
        runtime._active_tool[job_id] = ("powershell", "now", "{}")
        runtime._artifact_finalizer.finalize = AsyncMock(side_effect=RuntimeError("event bus unavailable"))
        runtime._finalize_naming = AsyncMock()  # type: ignore[method-assign]

        await runtime.finalize_external_session(job_id)

        runtime._artifact_finalizer.finalize.assert_awaited_once_with(job_id)
        runtime._finalize_naming.assert_awaited_once_with(job_id)
        assert job_id not in runtime._last_activity
        assert job_id not in runtime._active_tool

    async def test_double_start_guard(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """_start_job should no-op if a task for the job already exists."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)
        assert runtime.running_count == 1

        # Attempt to start the same job again (simulating a race)
        await runtime._start_job(job)
        # Should still have only one running task
        assert runtime.running_count == 1

    async def test_dequeue_respects_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """_dequeue_next under the lock should not exceed max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

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

    async def test_cancel_already_canceled_is_idempotent(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """CancelledError handler should not fail if job is already canceled."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(lambda: runtime.running_count == 1, msg="job did not start")
        await asyncio.sleep(0.2)  # let task progress past setup

        # Transition to canceled via DB directly (simulating route handler)
        async with session_factory() as session:
            svc = runtime._make_job_service(session)
            await svc.transition_state(job.id, JobState.canceled)
            await session.commit()

        # Now cancel via runtime — the CancelledError handler should see
        # that the job is already canceled and skip the transition
        await runtime.cancel(job.id)
        await _wait_until(lambda: runtime.running_count == 0, msg="job did not stop after cancel")

        # Should complete without errors
        assert runtime.running_count == 0


class TestRecoveryCapacity:
    async def test_recover_respects_capacity(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """recover_on_startup uses start_or_enqueue, respecting max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        # Create 3 queued jobs
        for i in range(3):
            job = _make_job(job_id=f"job-{i}", repo=config.repos[0], state=JobState.queued)
            await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()
        await asyncio.sleep(0.1)

        # Should only start max_concurrent (2), not all 3
        assert runtime.running_count == runtime.max_concurrent

    async def test_recover_preserves_resume_context_for_queued_active_jobs(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """Active jobs recovered under capacity pressure should keep their resume context while waiting for capacity."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime._adapter_registry._fake = slow_adapter

        for job_id in ("active-1", "active-2", "active-3"):
            job = _make_job(job_id=job_id, repo=config.repos[0], state=JobState.running)
            job.sdk_session_id = f"sdk-{job_id}"
            await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()
        await asyncio.sleep(0.1)

        pending_job_id, pending_entry = next(iter(runtime._pending_starts.items()))

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(pending_job_id)
            assert row is not None
            assert row.state == JobState.running
            assert row.session_count == 2

        assert pending_entry[0] is not None
        assert pending_entry[0].startswith("The CodePlane server restarted while this job was in progress.")
        assert pending_entry[1] == f"sdk-{pending_job_id}"


class TestJobStateChangedEvent:
    async def test_state_change_publishes_correct_event_kind(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """_publish_state_event should use job_state_changed, not job_created."""
        published: list[SessionEvent] = []

        async def _collect(e: SessionEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.5)

        state_change_events = [e for e in published if e.kind == EventKind.job_state_changed]
        assert len(state_change_events) >= 1
        # Should NOT have published any job_created events
        created_events = [e for e in published if e.kind == EventKind.job_created]
        assert len(created_events) == 0


# ---------------------------------------------------------------------------
# Red-team: error events should cause job failure, not succeeded
# ---------------------------------------------------------------------------


class ErrorAdapter(AgentAdapterInterface):
    """Adapter that emits an error event before completing."""

    async def create_session(self, config: SessionConfig) -> str:
        return "err-session"

    async def stream_events(self, session_id: str) -> AsyncGenerator[SessionEvent, None]:
        yield new_event(
            session_id=session_id,
            kind=EventKind.log_line_emitted,
            payload={"level": "info", "message": "Starting"},
        )
        yield new_event(
            session_id=session_id,
            kind=EventKind.job_failed,
            payload={"message": "Something went wrong"},
        )

    async def send_message(self, session_id: str, message: str) -> None:
        pass

    async def abort_session(self, session_id: str) -> None:
        pass

    async def complete(self, prompt: str) -> CompletionResult:
        return CompletionResult(text="{}")


class TestErrorEventCausesFailure:
    async def test_error_event_fails_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """A job whose adapter emits an error event should end as failed, not succeeded."""
        error_adapter = ErrorAdapter()
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(error_adapter),
            config=config,
        )

        published: list[SessionEvent] = []

        async def _collect(e: SessionEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await _wait_until(
            lambda: any(e.kind == EventKind.job_failed for e in published),
            msg="job_failed event not published",
        )

        # Should have a job_failed event, NOT job_review
        kinds = [e.kind for e in published]
        assert EventKind.job_failed in kinds
        assert EventKind.job_review not in kinds

        # DB state should be failed
        from backend.models.db import JobRow

        async def _check_failed() -> bool:
            async with session_factory() as session:
                from sqlalchemy import select

                row = (await session.execute(select(JobRow).where(JobRow.id == job.id))).scalar_one()
                return row.state == "failed"

        await _wait_until(_check_failed, msg="job state not failed in DB")

        await runtime.shutdown()


# ---------------------------------------------------------------------------
# Red-team: start_or_enqueue uses dequeue lock
# ---------------------------------------------------------------------------


class TestStartOrEnqueueCapacitySafety:
    async def test_concurrent_start_or_enqueue_respects_capacity(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """Multiple concurrent start_or_enqueue calls should not exceed max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=0.3)
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(slow_adapter),
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


# ---------------------------------------------------------------------------
# Stall detection — arbiter reliability
# ---------------------------------------------------------------------------


class FakeArbiterSidecar:
    """Minimal sidecar double exposing only the ``complete()`` surface used by
    ``RuntimeService._check_stall``."""

    def __init__(self, responder: Callable[[], str]) -> None:
        self._responder = responder

    async def complete(self, prompt: str, timeout: float = 15.0) -> str:
        return self._responder()


class FakeSidecarSessionManager:
    """Minimal sidecar-session-manager double: always hands back the same
    fake sidecar for any (job_id, name) pair."""

    def __init__(self, sidecar: FakeArbiterSidecar) -> None:
        self._sidecar = sidecar

    def get(self, job_id: str, name: str) -> FakeArbiterSidecar:
        return self._sidecar


def _active_tool_far_in_past(seconds: float = 200.0) -> tuple[str, str, str]:
    started = datetime.now(UTC).timestamp() - seconds
    started_iso = datetime.fromtimestamp(started, tz=UTC).isoformat()
    return ("powershell", started_iso, '{"command": "mvn test"}')


class TestStallDetectionArbiterReliability:
    """A broken/unreliable arbiter must not let a job hang forever with zero
    operator-visible signal — see the bug this guards against: a live job sat
    in `running` for ~2 hours with the arbiter repeatedly failing/timing out
    and `_check_stall` silently doing nothing each time."""

    async def test_single_arbiter_failure_does_not_escalate(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter: FakeAgentAdapter,
        config: CPLConfig,
    ) -> None:
        def _raise() -> str:
            raise TimeoutError("arbiter timed out")

        sidecar = FakeArbiterSidecar(_raise)
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
            sidecar_sessions=FakeSidecarSessionManager(sidecar),
        )
        runtime._handle_stall_interrupt = AsyncMock()  # type: ignore[method-assign]
        job_id = "job-stall-1"
        runtime._active_tool[job_id] = _active_tool_far_in_past()

        await runtime._check_stall(job_id)

        runtime._handle_stall_interrupt.assert_not_called()
        assert runtime._stall_check_consecutive_failures[job_id] == 1

    async def test_repeated_arbiter_failures_escalate_to_interrupt(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter: FakeAgentAdapter,
        config: CPLConfig,
    ) -> None:
        def _raise() -> str:
            raise TimeoutError("arbiter timed out")

        sidecar = FakeArbiterSidecar(_raise)
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
            sidecar_sessions=FakeSidecarSessionManager(sidecar),
        )
        runtime._handle_stall_interrupt = AsyncMock()  # type: ignore[method-assign]
        job_id = "job-stall-2"
        runtime._active_tool[job_id] = _active_tool_far_in_past()

        # Consecutive exceptions never update _last_stall_check, so each call
        # proceeds immediately without needing to fast-forward the recheck
        # interval.
        for _ in range(3):
            await runtime._check_stall(job_id)

        runtime._handle_stall_interrupt.assert_called_once()
        call_kwargs = runtime._handle_stall_interrupt.call_args
        assert call_kwargs.args[0] == job_id
        assert "3 times in a row" in call_kwargs.kwargs["reason"]
        # Escalation resets the counter so it doesn't fire on every subsequent tick.
        assert job_id not in runtime._stall_check_consecutive_failures

    async def test_unparseable_responses_also_escalate(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter: FakeAgentAdapter,
        config: CPLConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import backend.services.runtime.service as runtime_service_module

        monkeypatch.setattr(runtime_service_module, "_STALL_RECHECK_INTERVAL_S", 0)

        sidecar = FakeArbiterSidecar(lambda: "not json")
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
            sidecar_sessions=FakeSidecarSessionManager(sidecar),
        )
        runtime._handle_stall_interrupt = AsyncMock()  # type: ignore[method-assign]
        job_id = "job-stall-3"
        runtime._active_tool[job_id] = _active_tool_far_in_past()

        for _ in range(3):
            await runtime._check_stall(job_id)

        runtime._handle_stall_interrupt.assert_called_once()

    async def test_successful_verdict_resets_failure_counter(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter: FakeAgentAdapter,
        config: CPLConfig,
    ) -> None:
        calls = {"n": 0}

        def _flaky() -> str:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise TimeoutError("arbiter timed out")
            return json.dumps({"action": "wait", "reason": "legitimately slow build"})

        sidecar = FakeArbiterSidecar(_flaky)
        runtime = _make_runtime(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(adapter),
            config=config,
            sidecar_sessions=FakeSidecarSessionManager(sidecar),
        )
        runtime._handle_stall_interrupt = AsyncMock()  # type: ignore[method-assign]
        job_id = "job-stall-4"
        runtime._active_tool[job_id] = _active_tool_far_in_past()

        # Two failures (short of the threshold), then one healthy response.
        for _ in range(3):
            await runtime._check_stall(job_id)

        runtime._handle_stall_interrupt.assert_not_called()
        assert job_id not in runtime._stall_check_consecutive_failures
