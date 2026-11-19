"""Integration tests for the plan mode execution flow.

Tests the full lifecycle: plan → approval → implementation, as well as
the rejection re-plan loop, the max-iteration guard, and the cancel race.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import Base, JobRow
from backend.models.domain import (
    ApprovalResolution,
    Job,
    JobMode,
    JobState,
    SessionConfig,
)
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, new_event
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.agent_adapter import AgentAdapterInterface, CompletionResult
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.services.trail import TrailService
from backend.services.trail.plan_manager import PlanManager

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class PlanProducingAdapter(AgentAdapterInterface):
    """Fake adapter that simulates an agent producing plan steps via manage_todo_list."""

    def __init__(self) -> None:
        self._sessions_created: list[str] = []
        self._session_count = 0
        self._session_to_job: dict[str, str] = {}

    async def create_session(self, config: SessionConfig) -> str:
        self._session_count += 1
        sid = f"plan-session-{self._session_count}"
        self._sessions_created.append(sid)
        self._session_to_job[sid] = config.job_id or sid
        return sid

    async def stream_events(self, session_id: str) -> AsyncGenerator[SessionEvent, None]:
        job_id = self._session_to_job.get(session_id, session_id)
        # Emit a transcript event simulating a manage_todo_list tool call
        yield new_event(
            session_id=job_id,
            kind=EventKind.tool_call_completed,
            payload={
                "tool_name": "manage_todo_list",
                "arguments": {
                    "todoList": [
                        {"id": 1, "title": "Step 1: Read codebase", "status": "not-started"},
                        {"id": 2, "title": "Step 2: Implement changes", "status": "not-started"},
                        {"id": 3, "title": "Step 3: Write tests", "status": "not-started"},
                    ]
                },
                "result": "Planning the implementation...",
                "success": True,
            },
        )
        yield new_event(
            session_id=job_id,
            kind=EventKind.message_assistant,
            payload={"content": "Here is my plan."},
        )

    async def send_message(self, session_id: str, message: str) -> None:
        pass

    async def interrupt_session(self, session_id: str) -> None:
        pass

    def pause_tools(self, session_id: str) -> None:
        pass

    def resume_tools(self, session_id: str) -> None:
        pass

    async def abort_session(self, session_id: str) -> None:
        pass

    async def complete(self, prompt: str) -> CompletionResult:
        return CompletionResult(text="{}")

    def set_policy_router(self, router: object, policy: object, job_id: str, cwd: str) -> None:
        pass

    def update_repo_policy(self, job_id: str, policy: object) -> None:
        pass

    def set_job_id(self, session_id: str, job_id: str) -> None:
        pass


class FakeAdapterRegistry(AdapterRegistry):
    def __init__(self, adapter: AgentAdapterInterface) -> None:
        super().__init__()
        self._fake = adapter

    def get_adapter(self, sdk=None) -> AgentAdapterInterface:  # noqa: ANN001
        return self._fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'data.db'}")
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
def adapter() -> PlanProducingAdapter:
    return PlanProducingAdapter()


@pytest.fixture
def approval_service(session_factory: async_sessionmaker[AsyncSession]) -> ApprovalService:
    return ApprovalService(session_factory)


@pytest.fixture
async def runtime(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    adapter: PlanProducingAdapter,
    config: CPLConfig,
    approval_service: ApprovalService,
) -> AsyncGenerator[RuntimeService, None]:
    from unittest.mock import MagicMock

    from backend.services.trail.models import TrailJobState as _TrailJobState

    service = RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter_registry=FakeAdapterRegistry(adapter),
        config=config,
        approval_service=approval_service,
    )
    service._setup_action_policy = AsyncMock()  # type: ignore[method-assign]
    # Create a minimal TrailService mock that captures plan steps from events
    trail_state: dict[str, _TrailJobState] = {}
    trail_svc = TrailService.__new__(TrailService)
    trail_svc._session_factory = session_factory
    trail_svc._event_bus = event_bus
    trail_svc._sidecar_sessions = None
    trail_svc._config = config
    trail_svc._repo = None
    trail_svc._job_state = trail_state
    trail_svc._plan_tracking_disabled = set()
    trail_svc._plan_manager = PlanManager(event_bus=event_bus, job_state=trail_state)
    # Mock node_builder to avoid DB calls
    trail_svc._node_builder = MagicMock()
    trail_svc._node_builder.handle_event = AsyncMock(return_value=None)
    service._trail_service = trail_svc

    # Subscribe trail service's transcript handler to the event bus
    # so manage_todo_list calls get captured as plan steps.
    async def _trail_event_handler(event: SessionEvent) -> None:
        if event.kind in TRANSCRIPT_KINDS:
            await trail_svc._on_transcript_event(event)
        # Auto-create job state on state change to running
        if event.kind == EventKind.job_state_changed:
            payload = event.payload or {}
            if payload.get("new_state") == "running" and event.session_id and event.session_id not in trail_state:
                trail_state[event.session_id] = _TrailJobState()

    event_bus.subscribe(_trail_event_handler)

    yield service
    for task in list(service._tasks.values()):
        task.cancel()
    for task in list(service._heartbeat_tasks.values()):
        task.cancel()
    all_tasks = list(service._tasks.values()) + list(service._heartbeat_tasks.values())
    if all_tasks:
        await asyncio.gather(*all_tasks, return_exceptions=True)
    await asyncio.sleep(0.05)


def _make_plan_job(
    *,
    job_id: str = "plan-job-1",
    repo: str = "/repos/test",
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        project_id="proj-1",
        repo=repo,
        prompt="Implement feature X",
        state=JobState.queued,
        base_ref="main",
        branch="feat/plan-test",
        worktree_path=repo,
        session_id=None,
        created_at=now,
        updated_at=now,
        mode=JobMode.plan,
    )


async def _create_db_job(
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
) -> None:
    async with session_factory() as session:
        from backend.persistence.project_repo import ProjectRepository

        existing = await ProjectRepository(session).get(job.project_id)
        if existing is None:
            await ProjectRepository(session).create(job.project_id, "Test Project", [job.execution_repo])
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
            mode=job.mode,
        )
        session.add(row)
        await session.commit()


async def _wait_for_state(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
    state: JobState,
    *,
    timeout: float = 10.0,
) -> None:
    """Poll until job reaches the given state."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with session_factory() as session:
            repo = JobRepository(session)
            job = await repo.get(job_id)
        if job is not None and job.state == state:
            return
        if asyncio.get_event_loop().time() >= deadline:
            actual = job.state if job else "None"
            raise AssertionError(f"Job {job_id} did not reach {state} within {timeout}s (actual: {actual})")
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_mode_reaches_approval_gate(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PlanProducingAdapter,
    event_bus: EventBus,
) -> None:
    """A plan-mode job should produce plan steps and reach waiting_for_approval."""
    job = _make_plan_job()
    await _create_db_job(session_factory, job)

    # Collect events
    events: list[SessionEvent] = []

    async def _capture(e: SessionEvent) -> None:
        events.append(e)

    event_bus.subscribe(_capture)

    await runtime.start_or_enqueue(job)
    await _wait_for_state(session_factory, job.id, JobState.waiting_for_approval)

    # Verify approval_requested event was emitted
    approval_events = [e for e in events if e.kind == EventKind.approval_requested]
    assert len(approval_events) >= 1
    assert approval_events[0].payload["proposed_action"] == "execute_plan"


@pytest.mark.asyncio
async def test_plan_mode_approval_transitions_to_implementing(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PlanProducingAdapter,
    event_bus: EventBus,
) -> None:
    """Approving the plan should transition mode to plan_implementing and emit mode_changed."""
    job = _make_plan_job()
    await _create_db_job(session_factory, job)

    events: list[SessionEvent] = []

    async def _capture(e: SessionEvent) -> None:
        events.append(e)

    event_bus.subscribe(_capture)

    await runtime.start_or_enqueue(job)
    await _wait_for_state(session_factory, job.id, JobState.waiting_for_approval)

    # Approve the plan
    approval_events = [e for e in events if e.kind == EventKind.approval_requested]
    approval_id = approval_events[0].payload["approval_id"]
    await runtime._approval_service.resolve(approval_id, ApprovalResolution.approved)

    # Wait for job to reach review (implementation completes → review state)
    await _wait_for_state(session_factory, job.id, JobState.review, timeout=15.0)

    # Verify mode_changed event
    mode_events = [e for e in events if e.kind == EventKind.job_mode_changed]
    assert len(mode_events) == 1
    assert mode_events[0].payload["previous_mode"] == "plan"
    assert mode_events[0].payload["new_mode"] == "plan_implementing"

    # Verify DB mode
    async with session_factory() as session:
        repo = JobRepository(session)
        final = await repo.get(job.id)
    assert final is not None
    assert final.mode == JobMode.plan_implementing


@pytest.mark.asyncio
async def test_plan_mode_rejection_triggers_replan(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PlanProducingAdapter,
    event_bus: EventBus,
) -> None:
    """Rejecting the plan should trigger a re-plan session and raise a new approval."""
    job = _make_plan_job()
    await _create_db_job(session_factory, job)

    events: list[SessionEvent] = []

    async def _capture(e: SessionEvent) -> None:
        events.append(e)

    event_bus.subscribe(_capture)

    await runtime.start_or_enqueue(job)
    await _wait_for_state(session_factory, job.id, JobState.waiting_for_approval)

    # Reject the plan
    approval_events = [e for e in events if e.kind == EventKind.approval_requested]
    approval_id = approval_events[0].payload["approval_id"]
    await runtime._approval_service.resolve(approval_id, ApprovalResolution.rejected, notes="Needs more detail")

    # Give the runtime task a chance to process rejection and issue second approval.
    # The re-plan session is instant (fake adapter), so we just need to yield.
    await asyncio.sleep(0.2)

    # A second approval_requested event should have been emitted
    approval_events_after = [e for e in events if e.kind == EventKind.approval_requested]
    assert len(approval_events_after) >= 2

    # Now approve the second plan
    second_approval_id = approval_events_after[1].payload["approval_id"]
    await runtime._approval_service.resolve(second_approval_id, ApprovalResolution.approved)
    await _wait_for_state(session_factory, job.id, JobState.review, timeout=15.0)


@pytest.mark.asyncio
async def test_plan_mode_max_rejections_fails_job(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PlanProducingAdapter,
    event_bus: EventBus,
) -> None:
    """Rejecting the plan more than _MAX_REPLAN_ITERATIONS times should fail the job."""
    job = _make_plan_job()
    await _create_db_job(session_factory, job)

    events: list[SessionEvent] = []

    async def _capture(e: SessionEvent) -> None:
        events.append(e)

    event_bus.subscribe(_capture)

    await runtime.start_or_enqueue(job)

    # Reject 6 times (limit is 5)
    for i in range(6):
        await _wait_for_state(session_factory, job.id, JobState.waiting_for_approval, timeout=10.0)
        approval_events = [e for e in events if e.kind == EventKind.approval_requested]
        latest_approval_id = approval_events[-1].payload["approval_id"]
        await runtime._approval_service.resolve(latest_approval_id, ApprovalResolution.rejected)

        # Check if job failed (should happen after 5th rejection)
        await asyncio.sleep(0.2)
        async with session_factory() as session:
            repo = JobRepository(session)
            current = await repo.get(job.id)
        if current is not None and current.state == JobState.failed:
            assert i >= 5, f"Job failed too early after {i + 1} rejections"
            assert "rejected 5 times" in (current.failure_reason or "")
            return

    # If we get here, the job should have failed
    async with session_factory() as session:
        repo = JobRepository(session)
        final = await repo.get(job.id)
    assert final is not None
    assert final.state == JobState.failed


@pytest.mark.asyncio
async def test_plan_mode_recovery_fails_gracefully_when_awaiting_approval(
    runtime: RuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Recovery of a plan-mode job in waiting_for_approval should fail it cleanly."""
    from backend.services.runtime.resume import recover_active_job

    job = _make_plan_job(job_id="recover-plan-1")
    job.state = JobState.waiting_for_approval
    await _create_db_job(session_factory, job)

    # Simulate server restart recovery
    recovered = await recover_active_job(runtime, job.id)
    assert recovered.state == JobState.failed
    assert "Server restarted" in (recovered.failure_reason or "")
