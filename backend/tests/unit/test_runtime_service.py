"""Tests for RuntimeService — capacity, queueing, heartbeat, MCP discovery, event translation."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
import yaml
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
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
    JobState,
    PermissionMode,
    Resolution,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.adapter_registry import AdapterRegistry
from backend.services.agent_adapter import AgentAdapterInterface
from backend.services.event_bus import EventBus
from backend.services.job_service import StateConflictError
from backend.services.progress_tracking_service import (
    _count_similar_trailing_headlines,
    _headlines_are_similar,
)
from backend.services.runtime_service import (
    RuntimeService,
    _AgentSession,
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

    async def complete(self, prompt: str) -> str:
        return "{}"


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
    service = RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter_registry=FakeAdapterRegistry(adapter),
        config=config,
    )
    yield service
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

    async with session_factory() as session:
        row = JobRow(
            id=job.id,
            repo=job.repo,
            prompt=job.prompt,
            state=job.state,
            base_ref=job.base_ref,
            branch=job.branch,
            worktree_path=job.worktree_path,
            session_id=job.session_id,
            title=job.title,
            worktree_name=job.worktree_name,
            permission_mode=job.permission_mode,
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


@pytest.mark.asyncio
async def test_create_followup_job_uses_parent_handoff_context(runtime: RuntimeService) -> None:
    parent = _make_job(job_id="parent", state=JobState.review)
    parent.permission_mode = PermissionMode.read_only
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
        repo=parent.repo,
        prompt="Add regression coverage",
        base_ref=parent.base_ref,
        permission_mode=PermissionMode.read_only,
        model="gpt-5.4",
        sdk="claude",
        verify=True,
        self_review=True,
        max_turns=4,
        verify_prompt="Verify this carefully",
        self_review_prompt="Review your work",
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
                yield SessionEvent(
                    kind=SessionEventKind.transcript,
                    payload={"role": "agent", "content": "I resumed and started working."},
                )
            yield SessionEvent(
                kind=SessionEventKind.error,
                payload={"message": "Execution failed: CAPIError: 400 400 Bad Request\n"},
            )
            return

        yield SessionEvent(
            kind=SessionEventKind.transcript,
            payload={"role": "agent", "content": "Recovered via opaque handoff."},
        )
        yield SessionEvent(kind=SessionEventKind.done, payload={})

    async def send_message(self, session_id: str, message: str) -> None:
        return None

    async def abort_session(self, session_id: str) -> None:
        return None

    async def complete(self, prompt: str) -> str:
        return "{}"


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
        # Give the asyncio task time to start and finish
        await asyncio.sleep(0.3)
        # The FakeAgentAdapter finishes fast (delay=0), so the job should complete
        assert runtime.running_count == 0  # completed

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


class TestHeadlineSimilarity:
    def test_headlines_are_similar_when_subject_is_same(self) -> None:
        assert _headlines_are_similar("Investigating tool error display", "Debugging tool error display")

    def test_headlines_are_not_similar_for_different_work(self) -> None:
        assert not _headlines_are_similar("Implementing auth API", "Writing Playwright tests")

    def test_count_similar_trailing_headlines_counts_contiguous_tail(self) -> None:
        history = [
            "Preparing worktree",
            "Investigating tool error display",
            "Debugging tool error display",
        ]
        assert _count_similar_trailing_headlines(history, "Fixing tool error display") == 2


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
        config: CPLConfig,
    ) -> None:
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.5)

        # Should have log, transcript events + job_review
        # (diff_updated events now come from DiffService which requires a real git worktree)
        kinds = [e.kind for e in published]
        assert DomainEventKind.log_line_emitted in kinds
        assert DomainEventKind.transcript_updated in kinds
        assert DomainEventKind.job_review in kinds

    async def test_cancel_running_job(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        # Use slow adapter to keep job running
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter_registry._fake = slow_adapter

        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        await runtime.start_or_enqueue(job)
        await asyncio.sleep(0.1)
        assert runtime.running_count == 1

        await runtime.cancel(job.id)
        await asyncio.sleep(0.3)
        assert runtime.running_count == 0

    async def test_cancel_queued_job(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """cancel() for a non-running job is a no-op (state change is the API layer's job)."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
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
        slow_adapter = FakeAgentAdapter(delay=5.0)
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
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
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
        assert DomainEventKind.session_resumed in kinds

        # The operator message should appear as a transcript entry
        transcript_events = [e for e in published if e.kind == DomainEventKind.transcript_updated]
        operator_msgs = [e for e in transcript_events if e.payload.get("role") == "operator"]
        assert any("please continue" in e.payload.get("content", "") for e in operator_msgs)

    async def test_send_message_auto_resumes_orphaned_running_job(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        """send_message on a job in 'running' state with no live session recovers it in place."""
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        # Simulate an orphaned running job (e.g. server was killed mid-run)
        job = _make_job(repo=config.repos[0], state=JobState.running)
        await _create_db_job(session_factory, job)

        result = await runtime.send_message(job.id, "retry please")
        assert result is True

        # Give the resumed task time to run
        await asyncio.sleep(0.5)

        kinds = [e.kind for e in published]
        assert DomainEventKind.job_failed not in kinds
        assert DomainEventKind.session_resumed in kinds

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


class TestResumeFallback:
    async def test_conflict_resume_auto_merges_back_after_agent_success(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        merge_service = AsyncMock()

        async def _resolve_job(**kwargs: object):
            from types import SimpleNamespace

            from backend.persistence.job_repo import JobRepository

            async with session_factory() as session:
                repo = JobRepository(session)
                await repo.update_merge_status("job-1", Resolution.merged)
                await session.commit()

            return SimpleNamespace(status="merged", pr_url=None, conflict_files=None, error=None)

        merge_service.resolve_job.side_effect = _resolve_job

        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter(delay=0.0)),
            config=config,
            merge_service=merge_service,
        )

        published: list[DomainEvent] = []

        async def _collect(event: DomainEvent) -> None:
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

        await asyncio.sleep(0.2)

        merge_service.resolve_job.assert_awaited_once()

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.completed
            assert row.resolution == Resolution.merged
            assert row.merge_status == Resolution.merged

        completed_events = [event for event in published if event.kind == DomainEventKind.job_completed]
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
        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )

        published: list[DomainEvent] = []

        async def _collect(event: DomainEvent) -> None:
            published.append(event)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0], state=JobState.review)
        job.completed_at = datetime.now(UTC)
        job.session_count = 4
        job.resolution = Resolution.unresolved
        await _create_db_job(session_factory, job)

        async def _boom(*args: object, **kwargs: object) -> None:
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

        assert [event.kind for event in published] == []

        await runtime.shutdown()

    async def test_resume_uses_default_instruction_when_none_is_provided(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        adapter = ResumeFallbackAdapter()
        runtime = RuntimeService(
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
        await asyncio.sleep(0.2)

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

        from backend.services.git_service import GitError, GitService

        runtime = RuntimeService(
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
        adapter = ResumeFallbackAdapter()
        runtime = RuntimeService(
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

        await asyncio.sleep(0.2)

        assert len(adapter.configs) == 2
        assert adapter.configs[0].resume_sdk_session_id == "stale-sdk-session"
        assert adapter.configs[0].prompt == "continue"
        assert adapter.configs[1].resume_sdk_session_id is None
        assert "[RESUMED SESSION" in adapter.configs[1].prompt
        assert "Fix session pool scaling for naming service" in adapter.configs[1].prompt
        assert "continue" in adapter.configs[1].prompt

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.review
            assert row.sdk_session_id == "resume-2"

        await runtime.shutdown()

    async def test_resume_does_not_fallback_after_progress_has_started(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        adapter = ResumeFallbackAdapter(first_attempt_progress=True)
        runtime = RuntimeService(
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
        await asyncio.sleep(0.2)

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
        runtime = RuntimeService(
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
        runtime = RuntimeService(
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
        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        job = _make_job(repo=config.repos[0], state=JobState.running)
        await _create_db_job(session_factory, job)

        await runtime.recover_on_startup()
        await asyncio.sleep(0.5)

        async with session_factory() as session:
            from backend.persistence.job_repo import JobRepository

            repo = JobRepository(session)
            row = await repo.get(job.id)
            assert row is not None
            assert row.state == JobState.review

        resumed_events = [e for e in published if e.kind == DomainEventKind.session_resumed]
        assert len(resumed_events) == 1
        assert resumed_events[0].payload["reason"] == "process_restarted"

    async def test_recover_commits_running_state_before_publishing_resume_event(
        self,
        runtime: RuntimeService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
    ) -> None:
        observed_states: list[str] = []

        async def _assert_running_state_visible(event: DomainEvent) -> None:
            if event.kind != DomainEventKind.session_resumed:
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
        await asyncio.sleep(0.5)

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
# _AgentSession
# ---------------------------------------------------------------------------


class TestAgentSession:
    async def test_execute_yields_events(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = _AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        events = []
        async for event in session.execute(config, adapter):
            events.append(event)

        assert len(events) == 5
        assert events[-1].kind == SessionEventKind.done

    async def test_send_message_after_execute(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = _AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        # Must run execute first so session_id is set
        async for _ in session.execute(config, adapter):
            pass

        # Should not raise
        await session.send_message("test message")

    async def test_abort(self) -> None:
        adapter = FakeAgentAdapter(delay=0.0)
        session = _AgentSession()
        config = SessionConfig(workspace_path="/tmp", prompt="test")

        async for _ in session.execute(config, adapter):
            break  # start but don't finish

        await session.abort()


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
    async def test_double_start_guard(
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """_start_job should no-op if a task for the job already exists."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
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
        slow_adapter = FakeAgentAdapter(delay=5.0)
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
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime._adapter_registry._fake = slow_adapter

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
        self, runtime: RuntimeService, session_factory: async_sessionmaker[AsyncSession], config: CPLConfig
    ) -> None:
        """recover_on_startup uses start_or_enqueue, respecting max_concurrent."""
        slow_adapter = FakeAgentAdapter(delay=5.0)
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
        slow_adapter = FakeAgentAdapter(delay=5.0)
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
        eid = DomainEvent.make_event_id()
        assert eid.startswith("evt-")
        assert len(eid) == 16  # "evt-" + 12 hex chars

    def test_unique(self) -> None:
        ids = {DomainEvent.make_event_id() for _ in range(100)}
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

    async def complete(self, prompt: str) -> str:
        return "{}"


class TestErrorEventCausesFailure:
    async def test_error_event_fails_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """A job whose adapter emits an error event should end as failed, not succeeded."""
        error_adapter = ErrorAdapter()
        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(error_adapter),
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

        # Should have a job_failed event, NOT job_review
        kinds = [e.kind for e in published]
        assert DomainEventKind.job_failed in kinds
        assert DomainEventKind.job_review not in kinds

        # DB state should be failed
        from backend.models.db import JobRow

        async with session_factory() as session:
            from sqlalchemy import select

            row = (await session.execute(select(JobRow).where(JobRow.id == job.id))).scalar_one()
            assert row.state == JobState.failed

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
        slow_adapter = FakeAgentAdapter(delay=5.0)
        runtime = RuntimeService(
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
# Heartbeat: waiting_for_approval pauses the timeout
# ---------------------------------------------------------------------------


class TestHeartbeatWaitingForApproval:
    async def test_heartbeat_timeout_skipped_while_waiting_for_approval(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """Jobs in waiting_for_approval must not be failed by the heartbeat timeout."""
        import time

        import backend.services.runtime_service as rs_mod

        failed_jobs: list[str] = []

        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )
        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        original_fail = runtime._fail_job

        async def _capture_fail(job_id: str, reason: str) -> None:
            failed_jobs.append(job_id)
            await original_fail(job_id, reason)

        runtime._fail_job = _capture_fail  # type: ignore[method-assign]

        # Simulate the job in waiting_for_approval with a stale activity timestamp
        runtime._waiting_for_approval.add(job.id)
        runtime._last_activity[job.id] = time.monotonic() - (rs_mod._HEARTBEAT_TIMEOUT_S + 10)

        # Run the heartbeat loop with the interval zeroed so it triggers immediately
        original_interval = rs_mod._HEARTBEAT_INTERVAL_S
        rs_mod._HEARTBEAT_INTERVAL_S = 0
        try:
            heartbeat_task = asyncio.create_task(runtime._heartbeat_loop(job.id))
            await asyncio.sleep(0.05)
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        finally:
            rs_mod._HEARTBEAT_INTERVAL_S = original_interval

        # The loop must have looped (not exited early) and must NOT have failed the job
        assert job.id not in failed_jobs

        await runtime.shutdown()

    async def test_heartbeat_timeout_fires_when_not_waiting_for_approval(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config: CPLConfig,
    ) -> None:
        """Jobs NOT in waiting_for_approval are failed when the timeout expires."""
        import time

        import backend.services.runtime_service as rs_mod

        failed_jobs: list[str] = []

        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=event_bus,
            adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
            config=config,
        )
        job = _make_job(repo=config.repos[0])
        await _create_db_job(session_factory, job)

        original_fail = runtime._fail_job

        async def _capture_fail(job_id: str, reason: str) -> None:
            failed_jobs.append(job_id)
            await original_fail(job_id, reason)

        runtime._fail_job = _capture_fail  # type: ignore[method-assign]

        # Seed stale activity — past the timeout threshold — and no approval wait
        runtime._last_activity[job.id] = time.monotonic() - (rs_mod._HEARTBEAT_TIMEOUT_S + 10)
        assert job.id not in runtime._waiting_for_approval

        # Patch interval to 0 so the loop triggers immediately
        original_interval = rs_mod._HEARTBEAT_INTERVAL_S
        rs_mod._HEARTBEAT_INTERVAL_S = 0
        try:
            heartbeat_task = asyncio.create_task(runtime._heartbeat_loop(job.id))
            await asyncio.sleep(0.05)
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        finally:
            rs_mod._HEARTBEAT_INTERVAL_S = original_interval

        assert job.id in failed_jobs

        await runtime.shutdown()
