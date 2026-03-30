"""Long-running job execution manager.

RuntimeService orchestrates the full lifecycle of agent jobs: session creation,
event streaming, heartbeat monitoring, diff tracking, approval flow,
cancellation, and post-job cleanup.

Progress tracking (headline milestones and plan extraction) is delegated to
``ProgressTrackingService`` — see ``backend/services/progress_tracking_service.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import enum
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import structlog

from backend.config import build_session_config
from backend.models.domain import (
    TERMINAL_STATES,
    Job,
    JobState,
    Resolution,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.models.events import DomainEvent, DomainEventKind
from backend.services.progress_tracking_service import ProgressTrackingService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from backend.persistence.job_repo import JobRepository


class _AgentSession:
    """Thin wrapper around the adapter for a single running agent session."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._adapter: AgentAdapterInterface | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def execute(
        self,
        config: SessionConfig,
        adapter: AgentAdapterInterface,
    ) -> AsyncIterator[SessionEvent]:
        self._adapter = adapter
        self._session_id = await adapter.create_session(config)
        async for event in adapter.stream_events(self._session_id):
            yield event

    async def send_message(self, message: str) -> None:
        if self._adapter and self._session_id:
            await self._adapter.send_message(self._session_id, message)

    async def interrupt(self) -> None:
        if self._adapter and self._session_id:
            await self._adapter.interrupt_session(self._session_id)

    def pause_tools(self) -> None:
        if self._adapter and self._session_id:
            self._adapter.pause_tools(self._session_id)

    def resume_tools(self) -> None:
        if self._adapter and self._session_id:
            self._adapter.resume_tools(self._session_id)

    async def abort(self) -> None:
        if self._adapter and self._session_id:
            await self._adapter.abort_session(self._session_id)


class _EventAction(enum.Enum):
    """Action directive returned by ``_process_agent_event``."""

    skip = enum.auto()
    publish = enum.auto()
    abort = enum.auto()


@dataclasses.dataclass(frozen=True, slots=True)
class _SessionAttemptResult:
    """Outcome of a single ``_execute_session_attempt`` call."""

    session_id: str | None = None
    error_reason: str | None = None
    made_progress: bool = False
    downgrade: tuple[str, str] | None = None  # (requested, actual) model names


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.persistence.job_repo import JobRepository
    from backend.services.adapter_registry import AdapterRegistry
    from backend.services.agent_adapter import AgentAdapterInterface
    from backend.services.approval_service import ApprovalService
    from backend.services.diff_service import DiffService
    from backend.services.event_bus import EventBus
    from backend.services.job_service import JobService
    from backend.services.merge_service import MergeService
    from backend.services.platform_adapter import PlatformRegistry
    from backend.services.summarization_service import SummarizationService
    from backend.services.utility_session import UtilitySessionService

log = structlog.get_logger()

_SERVER_RESTART_RECOVERY_INSTRUCTION = (
    "The CodePlane server restarted while this job was in progress. "
    "Resume this existing job in place from the current worktree and prior context. "
    "Do not start over or create a duplicate job."
)

_DEFAULT_RESUME_INSTRUCTION = "Continue the current task from where you left off and finish it."

# Heartbeat configuration
_HEARTBEAT_INTERVAL_S = 30
_HEARTBEAT_WARNING_S = 90
_HEARTBEAT_TIMEOUT_S = 300  # 5 minutes

# Default prompts for post-completion verification and self-review turns
DEFAULT_VERIFY_PROMPT = (
    "You are now running a post-task verification pass. "
    "Start with a single short sentence announcing this — e.g. 'Running lint and tests.' "
    "Then check whether any source files or documentation were modified during this "
    "task (e.g. `git diff --stat HEAD` or compare against the base ref). "
    "If no files were modified, state that there is nothing to verify and stop. "
    "Only if files were changed: identify and run this project's test suite, "
    "linter, and type checker. Stop as soon as everything passes — you do not "
    "need to exhaust the maximum number of allowed turns. If something fails, "
    "fix it and re-run. Assume failures are caused by your changes; do not "
    "dismiss them as pre-existing or flaky. Also check that you haven't made "
    "unrelated changes outside the scope of the original task; revert any that "
    "you find. "
    "Your final message must be a single cohesive summary covering: first, what "
    "was built or changed and why (the main task); then, the verification outcome "
    "as one appended sentence (e.g. 'All checks pass.' or 'Fixed a failing test "
    "in foo.py.'). The checks are a footnote — the task summary is the headline."
)

DEFAULT_SELF_REVIEW_PROMPT = (
    "You are now running a post-task self-review pass. "
    "Start with a single short sentence announcing this — e.g. 'Reviewing my changes.' "
    "Then check whether any source files or documentation were modified during this "
    "task (e.g. `git diff --stat HEAD` or compare against the base ref). "
    "If no files were modified, state that there is nothing to review and stop. "
    "Only if files were changed: look at the full diff and check for missed edge "
    "cases, incomplete implementations, leftover debug code, broken imports, dead "
    "code, backwards-compatibility shims or fallback paths that may no longer be "
    "needed, and inconsistencies with the surrounding codebase. If you find "
    "issues, fix them. "
    "Your final message must be a single cohesive summary covering: first, what "
    "was built or changed and why (the main task); then, the review outcome as one "
    "appended sentence (e.g. 'Self-review clean.' or 'Removed a leftover debug "
    "print.'). The review is a footnote — the task summary is the headline."
)


def _session_event_counts_as_resume_progress(event: SessionEvent) -> bool:
    """Return True once a resumed session has produced real agent work."""
    if event.kind in (
        SessionEventKind.file_changed,
        SessionEventKind.approval_request,
        SessionEventKind.model_downgraded,
    ):
        return True
    if event.kind != SessionEventKind.transcript:
        return False
    role = str(event.payload.get("role", ""))
    return role != "operator"


def _normalize_resume_instruction(instruction: str | None) -> str:
    """Return a default continue instruction when the operator doesn't provide one."""
    normalized = (instruction or "").strip()
    return normalized or _DEFAULT_RESUME_INSTRUCTION


class RuntimeService:
    """Manages active job tasks, capacity enforcement, and queueing."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        adapter_registry: AdapterRegistry,
        config: CPLConfig,
        approval_service: ApprovalService | None = None,
        diff_service: DiffService | None = None,
        merge_service: MergeService | None = None,
        summarization_service: SummarizationService | None = None,
        platform_registry: PlatformRegistry | None = None,
        utility_session: UtilitySessionService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._adapter_registry = adapter_registry
        self._config = config
        self._approval_service = approval_service
        self._diff_service = diff_service
        self._merge_service = merge_service
        self._summarization_service = summarization_service
        self._platform_registry = platform_registry
        self._utility_session = utility_session
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._agent_sessions: dict[str, _AgentSession] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity: dict[str, float] = {}
        self._waiting_for_approval: set[str] = set()
        self._session_ids: dict[str, str] = {}
        self._permission_overrides: dict[str, str] = {}  # job_id → permission_mode
        self._dequeue_lock = asyncio.Lock()
        self._shutting_down = False
        self._snapshot_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_starts: dict[str, tuple[str | None, str | None]] = {}
        self._queued_override_prompts: dict[str, str] = {}
        self._queued_resume_session_ids: dict[str, str] = {}
        # Contents to suppress when the SDK echoes them back (already published locally)
        self._echo_suppress: dict[str, set[str]] = {}
        # Progress tracking (headline milestones + plan extraction)
        self._progress_tracking: ProgressTrackingService | None = None
        if utility_session is not None:
            self._progress_tracking = ProgressTrackingService(
                utility_session=utility_session,
                event_bus=event_bus,
            )

    def _resolve_adapter(self, sdk: str) -> AgentAdapterInterface:
        """Resolve the adapter for a given SDK via the registry."""
        return self._adapter_registry.get_adapter(sdk)

    def _make_job_service(self, session: AsyncSession) -> JobService:
        from backend.persistence.job_repo import JobRepository
        from backend.services.git_service import GitService
        from backend.services.job_service import JobService

        return JobService(
            job_repo=JobRepository(session),
            git_service=GitService(self._config),
            config=self._config,
        )

    async def _finalize_diff_safe(self, job_id: str, worktree_path: str | None, base_ref: str | None) -> None:
        """Finalize the diff snapshot, swallowing exceptions."""
        if self._diff_service is None or not worktree_path or not base_ref:
            return
        try:
            await self._diff_service.finalize(job_id, worktree_path, base_ref)
        except (Exception, asyncio.CancelledError):
            log.warning("diff_finalize_failed", job_id=job_id, exc_info=True)

    @property
    def running_count(self) -> int:
        """Number of currently running job tasks."""
        return len(self._tasks)

    @property
    def max_concurrent(self) -> int:
        return self._config.runtime.max_concurrent_jobs

    async def start_or_enqueue(
        self,
        job: Job,
        override_prompt: str | None = None,
        resume_sdk_session_id: str | None = None,
        permission_mode: str | None = None,
    ) -> None:
        """Start the job if capacity allows, otherwise keep it queued."""
        if permission_mode:
            self._permission_overrides[job.id] = permission_mode
        if self._shutting_down:
            log.warning("job_rejected_shutting_down", job_id=job.id)
            return
        async with self._dequeue_lock:
            if self.running_count >= self.max_concurrent:
                if job.state == JobState.queued:
                    if override_prompt is not None:
                        self._queued_override_prompts[job.id] = override_prompt
                    if resume_sdk_session_id is not None:
                        self._queued_resume_session_ids[job.id] = resume_sdk_session_id
                else:
                    self._pending_starts[job.id] = (override_prompt, resume_sdk_session_id)
                    log.info("job_waiting_for_capacity", job_id=job.id, state=job.state, running=self.running_count)
                    return
                # Job is already queued from create_job; only transition if needed
                if job.state != JobState.queued:
                    async with self._session_factory() as session:
                        svc = self._make_job_service(session)
                        await svc.transition_state(job.id, JobState.queued)
                        await session.commit()
                    await self._publish_state_event(job.id, None, JobState.queued)
                log.info("job_enqueued", job_id=job.id, running=self.running_count)
                return

            await self._start_job(job, override_prompt=override_prompt, resume_sdk_session_id=resume_sdk_session_id)

    async def _ensure_resumable_worktree(self, job_repo: JobRepository, job: Job) -> Job:
        """Ensure a job has a usable worktree before resuming or recovering it."""
        from pathlib import Path

        from backend.services.git_service import GitError, GitService
        from backend.services.job_service import StateConflictError

        if not job.worktree_path or job.worktree_path == job.repo:
            return job

        wt = Path(job.worktree_path)
        if wt.exists():
            return job

        if not job.branch:
            raise StateConflictError(
                f"Job {job.id} cannot be resumed because its worktree is missing "
                "and no branch is available to restore it."
            )

        git = GitService(self._config)
        try:
            new_wt = await git.reattach_worktree(job.repo, job.id, job.branch)
            await job_repo.update_worktree_path(job.id, new_wt)
            job.worktree_path = new_wt
            log.info("worktree_reattached", job_id=job.id, path=new_wt)
            return job
        except GitError as exc:
            raise StateConflictError(
                f"Job {job.id} cannot be resumed because its worktree could not be restored: {exc}"
            ) from exc

    async def _recover_active_job(
        self,
        job_id: str,
        *,
        instruction: str = _SERVER_RESTART_RECOVERY_INSTRUCTION,
    ) -> Job:
        """Restart an active job after backend restart without marking it failed."""
        from backend.persistence.job_repo import JobRepository
        from backend.services.job_service import JobNotFoundError, StateConflictError

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            if job.state not in (JobState.running, JobState.waiting_for_approval):
                raise StateConflictError(f"Job {job_id} is not active and cannot be recovered (current: {job.state}).")

            previous_state = job.state
            previous_session_count = job.session_count
            previous_completed_at = job.completed_at
            previous_resolution = job.resolution
            previous_failure_reason = job.failure_reason
            previous_archived_at = job.archived_at
            previous_merge_status = job.merge_status
            previous_pr_url = job.pr_url

            job = await self._ensure_resumable_worktree(job_repo, job)

            new_session_count = job.session_count + 1
            if job.sdk_session_id:
                override_prompt = instruction
                resume_sdk_session_id: str | None = job.sdk_session_id
            else:
                override_prompt = await self._build_resume_handoff_prompt_for_job(
                    session,
                    job,
                    instruction,
                    new_session_count,
                )
                resume_sdk_session_id = None

            await job_repo.reset_for_recovery(job_id, new_session_count, new_state=JobState.running)
            await session.commit()

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            reloaded = await job_repo.get(job_id)
        if reloaded is None:
            raise ValueError(f"Job {job_id} not found after recovery reset")

        try:
            await self.start_or_enqueue(
                reloaded,
                override_prompt=override_prompt,
                resume_sdk_session_id=resume_sdk_session_id,
            )
        except Exception:
            async with self._session_factory() as session:
                job_repo = JobRepository(session)
                await job_repo.restore_after_failed_resume(
                    job_id,
                    previous_state=previous_state,
                    previous_session_count=previous_session_count,
                    completed_at=previous_completed_at,
                    resolution=previous_resolution,
                    failure_reason=previous_failure_reason,
                    archived_at=previous_archived_at,
                    merge_status=previous_merge_status,
                    pr_url=previous_pr_url,
                )
                await session.commit()
            raise

        if previous_state == JobState.waiting_for_approval:
            await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)

        now = datetime.now(UTC)
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=now,
                kind=DomainEventKind.session_resumed,
                payload={
                    "session_number": new_session_count,
                    "instruction": instruction,
                    "timestamp": now.isoformat(),
                    "reason": "process_restarted",
                },
            )
        )

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            final_job = await job_repo.get(job_id)
        if final_job is None:
            raise ValueError(f"Job {job_id} not found after recovery start")
        return final_job

    async def _start_job(
        self, job: Job, override_prompt: str | None = None, resume_sdk_session_id: str | None = None
    ) -> None:
        """Create an asyncio task to execute the job."""
        if job.id in self._tasks:
            return  # Already running (in-memory guard)

        # DB-level compare-and-swap: prevents double-start if recovery and
        # an HTTP request race on the same job.  Only the winner proceeds.
        from backend.persistence.job_repo import JobRepository

        async with self._session_factory() as session:
            repo = JobRepository(session)
            claimed = await repo.claim_for_start(job.id)
            await session.commit()
        if not claimed:
            log.warning("job_start_claim_lost", job_id=job.id)
            return

        agent_session = _AgentSession()
        self._agent_sessions[job.id] = agent_session

        # The DB CAS already set the state to running; publish the event
        # if the domain object's state hasn't caught up yet.
        if job.state != JobState.running:
            await self._publish_state_event(job.id, job.state, JobState.running)

        try:
            session_config = build_session_config(
                job,
                self._config,
                self._permission_overrides.pop(job.id, None),
            )
            if override_prompt is not None:
                import dataclasses

                session_config = dataclasses.replace(session_config, prompt=override_prompt)
            if resume_sdk_session_id is not None:
                import dataclasses

                session_config = dataclasses.replace(session_config, resume_sdk_session_id=resume_sdk_session_id)

            task = asyncio.create_task(
                self._run_job_guarded(job.id, agent_session, session_config, session_number=job.session_count),
                name=f"job-{job.id}",
            )
        except Exception:
            # Task creation failed after the DB CAS set state to running.
            # Revert to the pre-claim state so the job isn't orphaned.
            self._agent_sessions.pop(job.id, None)
            log.error("job_start_task_creation_failed", job_id=job.id, exc_info=True)
            async with self._session_factory() as session:
                repo = JobRepository(session)
                await repo.update_state(job.id, job.state, datetime.now(UTC))
                await session.commit()
            raise
        self._tasks[job.id] = task
        # Pre-register prompt for echo suppression so the SDK user.message
        # echo of the initial prompt is discarded (shown via the synthetic entry).
        self._echo_suppress.setdefault(job.id, set()).add(session_config.prompt)
        log.info("job_started", job_id=job.id)

    async def _run_job_guarded(
        self,
        job_id: str,
        agent_session: _AgentSession,
        config: SessionConfig,
        session_number: int = 1,
    ) -> None:
        """Wrapper that guarantees ``_cleanup_job_state`` runs even when
        ``CancelledError`` hits before the inner try/except in ``_run_job``."""
        try:
            await self._run_job(job_id, agent_session, config, session_number=session_number)
        except asyncio.CancelledError:
            if self._shutting_down:
                log.info("shutdown_task_cancelled", job_id=job_id)
            else:
                log.info("job_canceled_safety_net", job_id=job_id)
                # Clear pending task-level cancellation so the DB operations
                # below are not immediately re-interrupted.  This handles the
                # case where anyio's cancel-scope teardown (during SDK client
                # disconnect) flagged this task for cancellation.
                _cur = asyncio.current_task()
                if _cur is not None:
                    _cur.uncancel()
                # Safety net: _handle_job_canceled inside _run_job may have
                # been interrupted by a second CancelledError during abort().
                # Attempt the DB transition here so the job doesn't stay stuck
                # in 'running'.
                try:
                    async with self._session_factory() as session:
                        svc = self._make_job_service(session)
                        current = await svc.get_job(job_id)
                        if current and current.state not in TERMINAL_STATES:
                            await svc.transition_state(job_id, JobState.canceled)
                            await session.commit()
                            self._set_progress_terminal_state(job_id, JobState.canceled)
                except (Exception, asyncio.CancelledError):
                    log.error("safety_net_cancel_failed", job_id=job_id, exc_info=True)
        finally:
            log.debug("_run_job_guarded_finally", job_id=job_id, in_tasks=job_id in self._tasks)
            # The inner _run_job finally handles cleanup in the normal case.
            # This catches the case where CancelledError hit during setup,
            # before the inner try was entered.
            if job_id in self._tasks:
                heartbeat = self._heartbeat_tasks.pop(job_id, None)
                if heartbeat:
                    heartbeat.cancel()
                await self._cleanup_job_state(job_id)

    async def _run_job(
        self,
        job_id: str,
        agent_session: _AgentSession,
        config: SessionConfig,
        session_number: int = 1,
    ) -> None:
        """Execute the agent session, translate events, and handle completion."""
        import time

        self._last_activity[job_id] = time.monotonic()
        _job_wall_start = time.monotonic()  # captured here so adapter cleanup can't erase it
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id),
            name=f"heartbeat-{job_id}",
        )
        self._heartbeat_tasks[job_id] = heartbeat_task

        # Start progress tracking (headline milestones + plan extraction)
        if self._progress_tracking is not None:
            self._progress_tracking.start_tracking(job_id)
            # Proactively scale the utility pool to match running jobs
            if self._utility_session is not None:
                await self._utility_session.notify_job_started()

        # Start telemetry tracking — init OTEL spans and SQLite summary row.
        import time as _time

        from backend.services import telemetry as tel

        tel.start_job_span(job_id, sdk=config.sdk, model=config.model or "")

        # Initialize the summary row in SQLite so event-driven upserts work.
        # Fire-and-forget via a background task to avoid holding a write lock
        # that could conflict with concurrent recovery transactions.
        async def _init_telemetry_row() -> None:
            try:
                async with self._session_factory() as session:
                    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

                    repo_path = ""
                    branch_name = ""
                    sdk_name = ""
                    try:
                        svc = self._make_job_service(session)
                        job_for_tel = await svc.get_job(job_id)
                        if job_for_tel is not None:
                            repo_path = job_for_tel.repo or ""
                            branch_name = job_for_tel.branch or ""
                            sdk_name = job_for_tel.sdk or ""
                    except Exception:
                        pass
                    await TelemetrySummaryRepo(session).init_job(
                        job_id,
                        sdk=sdk_name or "unknown",
                        model=config.model or "",
                        repo=repo_path,
                        branch=branch_name,
                    )
                    await session.commit()
            except (Exception, asyncio.CancelledError):
                log.warning("telemetry_init_failed", job_id=job_id, exc_info=True)

        asyncio.create_task(_init_telemetry_row(), name=f"telemetry-init-{job_id[:8]}")

        # Emit environment_setup phase
        self._resolve_adapter(config.sdk).set_execution_phase(job_id, "environment_setup")
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.execution_phase_changed,
                payload={"phase": "environment_setup"},
            )
        )

        # Resolve worktree_path and base_ref for diff calculations
        worktree_path: str | None = None
        base_ref: str | None = None
        post_conflict_merge_requested = False
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
            if job is not None:
                worktree_path = job.worktree_path or job.repo
                base_ref = job.base_ref
                post_conflict_merge_requested = job.merge_status == Resolution.conflict
        except Exception:
            log.warning("diff_job_lookup_failed", job_id=job_id, exc_info=True)

        session_id: str | None = None
        error_reason: str | None = None
        try:
            # Emit agent_reasoning phase before main session execution
            self._resolve_adapter(config.sdk).set_execution_phase(job_id, "agent_reasoning")
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.execution_phase_changed,
                    payload={"phase": "agent_reasoning"},
                )
            )

            result = await self._execute_session_attempt(
                job_id,
                agent_session,
                config,
                worktree_path,
                base_ref,
                session_number=session_number,
            )
            session_id = result.session_id
            error_reason = result.error_reason

            # Resume fallback: first attempt errored without progress on a resumed session
            if error_reason and config.resume_sdk_session_id and not result.made_progress:
                result = await self._attempt_resume_fallback(
                    job_id,
                    config,
                    worktree_path,
                    base_ref,
                    session_number=session_number,
                )
                session_id = result.session_id
                error_reason = result.error_reason

            # Model downgrade (from either attempt): finish diff, move to review with note, skip verify
            if result.downgrade is not None:
                requested, actual = result.downgrade
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                reason = f"Model downgraded: requested {requested} but received {actual}"
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    await svc.transition_state(job_id, JobState.review, failure_reason=reason)
                    from backend.persistence.job_repo import JobRepository

                    job_repo = JobRepository(session)
                    await job_repo.update_resolution(job_id, Resolution.unresolved)
                    await session.commit()

                self._set_progress_terminal_state(job_id, JobState.review)
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=DomainEvent.make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.job_review,
                        payload={
                            "resolution": Resolution.unresolved,
                            "model_downgraded": True,
                            "requested_model": requested,
                            "actual_model": actual,
                        },
                    )
                )
                log.info("job_moved_to_review_model_downgrade", job_id=job_id)
                return

            if error_reason:
                # An error event was received during execution — finalize diff before failing
                log.warning("job_error_reason_detected", job_id=job_id, error_reason=error_reason)
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                await self._fail_job(job_id, error_reason)
                return

            # Final diff snapshot before resolution
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)

            # Run optional verify / self-review follow-up turns
            await self._run_verify_review(
                job_id, config, session_id, worktree_path, base_ref, session_number=session_number
            )

            final_resolution = Resolution.unresolved
            final_pr_url: str | None = None
            final_merge_status: str | None = None
            resolution_event = None

            # Strategy completed normally → review
            #
            # Commit the state transition BEFORE running merge resolution.
            # Merge operations open their own sessions to persist merge_status
            # and publish events — if the outer session is still uncommitted
            # SQLite will deadlock on the jobs table write lock.
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job_id, JobState.review)
                if not post_conflict_merge_requested or self._merge_service is None:
                    from backend.persistence.job_repo import JobRepository

                    job_repo = JobRepository(session)
                    await job_repo.update_resolution(job_id, final_resolution, pr_url=None)
                    if post_conflict_merge_requested and self._merge_service is None:
                        log.warning("post_conflict_merge_unavailable", job_id=job_id)
                await session.commit()

            # Merge resolution runs in its own session(s) — no lock contention.
            if post_conflict_merge_requested and self._merge_service is not None:
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    current_job = await svc.get_job(job_id)
                    if current_job is None:
                        raise ValueError(f"Job {job_id} not found before post-conflict merge")

                    log.info("job_attempting_post_conflict_merge", job_id=job_id)
                    resolved, final_pr_url, _, _ = await svc.execute_resolve(
                        job=current_job,
                        action="merge",
                        merge_service=self._merge_service,
                    )
                    final_resolution = cast("Resolution", resolved)
                    resolution_event = svc.build_job_resolved_event(
                        job_id,
                        resolved,
                        pr_url=final_pr_url,
                    )
                    await session.commit()


            if resolution_event is not None:
                await self._event_bus.publish(resolution_event)

            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                updated_job = await svc.get_job(job_id)
            if updated_job is not None:
                final_merge_status = updated_job.merge_status
                final_pr_url = updated_job.pr_url

            if final_resolution == Resolution.unresolved:
                log.info("job_awaiting_review", job_id=job_id)
            else:
                log.info(
                    "job_completed_with_resolution",
                    job_id=job_id,
                    resolution=final_resolution,
                    merge_status=final_merge_status,
                )

            # Determine final state — execute_resolve may have already
            # transitioned review → completed for successful merges.
            final_state = JobState.review
            if final_resolution in (Resolution.merged, Resolution.pr_created, Resolution.discarded):
                final_state = JobState.completed
            final_event_kind = (
                DomainEventKind.job_completed if final_state == JobState.completed else DomainEventKind.job_review
            )

            self._set_progress_terminal_state(job_id, final_state)
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=final_event_kind,
                    payload={
                        "resolution": final_resolution,
                        "merge_status": final_merge_status,
                        "pr_url": final_pr_url,
                    },
                )
            )
            log.info(
                final_event_kind.value,
                job_id=job_id,
                resolution=final_resolution,
                merge_status=final_merge_status,
            )
        except asyncio.CancelledError:
            if self._shutting_down:
                # Server is shutting down — leave job state as-is so
                # recover_on_startup picks it back up on next launch.
                log.info("job_interrupted_by_shutdown", job_id=job_id)
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await agent_session.abort()
            else:
                log.info("job_canceled_by_operator", job_id=job_id)
                await self._handle_job_canceled(job_id, agent_session, worktree_path, base_ref)
        except Exception as exc:
            log.error("job_execution_failed", job_id=job_id, exc_info=True)
            # Finalize diff so changes are preserved even for crashed jobs
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)
            await self._fail_job(job_id, f"Execution error: {exc}")
        finally:
            tel.end_job_span(job_id)

            # Emit finalization phase
            try:
                self._resolve_adapter(config.sdk).set_execution_phase(job_id, "finalization")
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=DomainEvent.make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.execution_phase_changed,
                        payload={"phase": "finalization"},
                    )
                )
            except Exception:
                pass

            # Finalize the summary row with terminal status and duration.
            try:
                async with self._session_factory() as session:
                    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

                    # Determine status from job state
                    status = "review"
                    try:
                        svc = self._make_job_service(session)
                        job_final = await svc.get_job(job_id)
                        if job_final is not None:
                            st = str(job_final.state)
                            if "fail" in st:
                                status = "failed"
                            elif "cancel" in st:
                                status = "cancelled"
                            elif st == "completed":
                                status = "completed"
                    except Exception:
                        pass

                    # Duration from wall-clock start captured at _run_job entry
                    duration = int((_time.monotonic() - _job_wall_start) * 1000)

                    await TelemetrySummaryRepo(session).finalize(
                        job_id,
                        status=status,
                        duration_ms=duration,
                    )
                    await session.commit()

                # Run post-job cost attribution pipeline
                try:
                    async with self._session_factory() as session:
                        from backend.services.cost_attribution import compute_attribution

                        await compute_attribution(session, job_id)
                        await session.commit()
                except Exception:
                    log.warning("cost_attribution_failed", job_id=job_id, exc_info=True)

                # Run statistical analysis (fire-and-forget, non-blocking)
                try:
                    async with self._session_factory() as session:
                        from backend.services.statistical_analysis import run_analysis

                        await run_analysis(session)
                        await session.commit()
                except Exception:
                    log.debug("statistical_analysis_failed", job_id=job_id, exc_info=True)

                # Signal clients that final telemetry is available
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=DomainEvent.make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.telemetry_updated,
                        payload={"job_id": job_id},
                    )
                )
            except Exception:
                log.warning("telemetry_finalize_failed", job_id=job_id, exc_info=True)

            # --- Store post-completion artifacts (telemetry, plan, approvals) ---
            await self._store_post_completion_artifacts(job_id)

            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(job_id, None)
            if self._progress_tracking is not None:
                self._progress_tracking.stop_tracking(job_id)
                await self._progress_tracking.finalize_plan_steps(job_id)
            await self._cleanup_job_state(job_id)

    async def _store_post_completion_artifacts(
        self,
        job_id: str,
    ) -> None:
        """Persist internal state (telemetry, plan, approvals) as downloadable artifacts."""
        try:
            # Look up job slug for human-friendly artifact names
            slug = ""
            try:
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    job = await svc.get_job(job_id)
                if job is not None:
                    slug = (job.worktree_name or job.title or "").strip()
            except Exception:
                pass

            async with self._session_factory() as session:
                from backend.persistence.artifact_repo import ArtifactRepository
                from backend.services.artifact_service import ArtifactService

                artifact_svc = ArtifactService(ArtifactRepository(session))

                # Telemetry report – load from the persisted summary row
                try:
                    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

                    summary = await TelemetrySummaryRepo(session).get(job_id)
                    if summary is not None:
                        await artifact_svc.store_telemetry_report(
                            job_id,
                            summary,
                            slug=slug,
                        )
                except Exception:
                    log.debug("telemetry_artifact_failed", job_id=job_id, exc_info=True)

                # Agent plan steps (from in-memory progress tracker)
                if self._progress_tracking is not None:
                    steps = self._progress_tracking.get_plan_steps(job_id)
                    if steps:
                        try:
                            await artifact_svc.store_agent_plan(job_id, steps, slug=slug)
                        except Exception:
                            log.debug("plan_artifact_failed", job_id=job_id, exc_info=True)

                # Approval history
                try:
                    from backend.persistence.approval_repo import ApprovalRepository

                    approval_repo = ApprovalRepository(session)
                    approvals = await approval_repo.list_for_job(job_id)
                    if approvals:
                        approval_dicts = [
                            {
                                "id": a.id,
                                "description": a.description,
                                "proposed_action": a.proposed_action,
                                "requested_at": a.requested_at.isoformat(),
                                "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                                "resolution": a.resolution,
                            }
                            for a in approvals
                        ]
                        await artifact_svc.store_approval_history(
                            job_id,
                            approval_dicts,
                            slug=slug,
                        )
                except Exception:
                    log.debug("approval_artifact_failed", job_id=job_id, exc_info=True)

                # Agent log artifact — snapshot of all log_line_emitted events as
                # a plain-text file, grouped by session for jobs with handoffs.
                try:
                    from backend.persistence.event_repo import EventRepository

                    event_repo = EventRepository(session)
                    log_events = await event_repo.list_by_job(job_id, [DomainEventKind.log_line_emitted], limit=10000)
                    if log_events:
                        await artifact_svc.store_log_artifact(
                            job_id,
                            [e.payload for e in log_events],
                            slug=slug,
                        )
                except Exception:
                    log.debug("log_artifact_failed", job_id=job_id, exc_info=True)

                await session.commit()
        except Exception:
            log.warning("post_completion_artifacts_failed", job_id=job_id, exc_info=True)

    def _start_snapshot_task(self, job_id: str) -> None:
        if self._shutting_down:
            return
        if self._summarization_service is None:
            return
        existing = self._snapshot_tasks.get(job_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(
            self._summarization_service.store_session_snapshot(job_id),
            name=f"snapshot-{job_id}",
        )
        self._snapshot_tasks[job_id] = task

        def _cleanup_snapshot_task(completed: asyncio.Task[None]) -> None:
            current = self._snapshot_tasks.get(job_id)
            if current is completed:
                self._snapshot_tasks.pop(job_id, None)

        task.add_done_callback(_cleanup_snapshot_task)

    def _set_progress_terminal_state(self, job_id: str, outcome: str) -> None:
        """Forward terminal outcome to the progress tracker."""
        if self._progress_tracking is not None:
            self._progress_tracking.set_terminal_state(job_id, outcome)

    async def _cleanup_job_state(self, job_id: str) -> None:
        """Remove all per-job in-memory state and trigger post-job hooks."""
        # Last-resort guard: if the job is still non-terminal after all error
        # handlers have run, force it to failed so it doesn't stay stuck.
        await self._ensure_terminal_state(job_id)

        if self._progress_tracking is not None:
            self._progress_tracking.cleanup(job_id)
        self._tasks.pop(job_id, None)
        self._agent_sessions.pop(job_id, None)
        self._last_activity.pop(job_id, None)
        self._waiting_for_approval.discard(job_id)
        self._session_ids.pop(job_id, None)
        self._echo_suppress.pop(job_id, None)
        self._pending_starts.pop(job_id, None)
        self._queued_override_prompts.pop(job_id, None)
        self._queued_resume_session_ids.pop(job_id, None)
        if self._utility_session is not None:
            await self._utility_session.notify_job_ended()
        if self._approval_service is not None:
            self._approval_service.cleanup_job(job_id)
        if self._diff_service is not None:
            self._diff_service.cleanup(job_id)
        self._start_snapshot_task(job_id)
        await self._dequeue_next()

    async def _ensure_terminal_state(self, job_id: str) -> None:
        """Ensure the job is in a terminal state.  Called as a last-resort
        safety net during cleanup so that no job is ever permanently stuck
        in 'running'.

        During server shutdown, jobs are intentionally left as-is so that
        ``recover_on_startup`` can resume them on the next launch.
        """
        if self._shutting_down:
            return
        # Clear any pending task-level cancellation so the DB transition
        # below is not immediately interrupted.
        _cur = asyncio.current_task()
        if _cur is not None:
            _cur.uncancel()
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
                if job is not None and job.state not in TERMINAL_STATES:
                    log.error(
                        "ensure_terminal_state_forcing_failure",
                        job_id=job_id,
                        current_state=str(job.state),
                    )
                    await svc.transition_state(
                        job_id, JobState.failed,
                        failure_reason="Job cleanup: forced to failed (previous state transitions failed)",
                    )
                    await session.commit()
                    self._set_progress_terminal_state(job_id, JobState.failed)
                    await self._event_bus.publish(
                        DomainEvent(
                            event_id=DomainEvent.make_event_id(),
                            job_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=DomainEventKind.job_failed,
                            payload={"reason": "Job cleanup: previous error handlers failed to transition state"},
                        )
                    )
        except (Exception, asyncio.CancelledError):
            log.error("ensure_terminal_state_failed", job_id=job_id, exc_info=True)

    async def _handle_approval_request(
        self,
        job_id: str,
        domain_event: DomainEvent,
        rejection_message: str,
    ) -> str:
        """Handle an approval_requested event: transition state, wait for operator, return resolution.

        Returns the resolution string (``"approved"`` or ``"rejected"``).
        """
        import time

        assert self._approval_service is not None

        async with self._session_factory() as sess:
            svc = self._make_job_service(sess)
            await svc.transition_state(job_id, JobState.waiting_for_approval)
            await sess.commit()

        self._waiting_for_approval.add(job_id)

        await self._event_bus.publish(domain_event)

        approval_id = domain_event.payload.get("approval_id", "")
        resolution = await self._approval_service.wait_for_resolution(approval_id)

        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.approval_resolved,
                payload={
                    "approval_id": approval_id,
                    "resolution": resolution,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

        async with self._session_factory() as sess:
            svc = self._make_job_service(sess)
            await svc.transition_state(job_id, JobState.running)
            await sess.commit()
        self._waiting_for_approval.discard(job_id)
        await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)
        self._last_activity[job_id] = time.monotonic()

        return resolution

    async def _attempt_resume_fallback(
        self,
        job_id: str,
        config: SessionConfig,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> _SessionAttemptResult:
        """Try a fresh session after a failed resume."""
        import dataclasses

        await self._clear_sdk_session_id(job_id)
        try:
            fallback_prompt = await self._build_resume_handoff_prompt(job_id, config.prompt)
        except Exception:
            log.warning("resume_handoff_prompt_build_failed", job_id=job_id, exc_info=True)
            return _SessionAttemptResult(error_reason="Resume handoff prompt build failed")

        log.warning(
            "resume_sdk_session_unusable_falling_back",
            job_id=job_id,
            sdk_session_id=config.resume_sdk_session_id,
        )
        fallback_session = _AgentSession()
        self._agent_sessions[job_id] = fallback_session
        fallback_config = dataclasses.replace(
            config,
            prompt=fallback_prompt,
            resume_sdk_session_id=None,
        )
        fallback_result = await self._execute_session_attempt(
            job_id,
            fallback_session,
            fallback_config,
            worktree_path,
            base_ref,
            session_number=session_number,
        )
        return fallback_result

    async def _handle_job_canceled(
        self,
        job_id: str,
        agent_session: _AgentSession,
        worktree_path: str | None,
        base_ref: str | None,
    ) -> None:
        """Process cancellation: finalize diff, abort agent, transition state."""
        try:
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)
        except (Exception, asyncio.CancelledError):
            log.warning("cancel_diff_finalize_failed", job_id=job_id, exc_info=True)
        try:
            await agent_session.abort()
        except (Exception, asyncio.CancelledError):
            log.warning("agent_abort_failed", job_id=job_id, exc_info=True)
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                current = await svc.get_job(job_id)
                if current and current.state not in TERMINAL_STATES:
                    await svc.transition_state(job_id, JobState.canceled)
                    await session.commit()
                    self._set_progress_terminal_state(job_id, JobState.canceled)
                    await self._event_bus.publish(
                        DomainEvent(
                            event_id=DomainEvent.make_event_id(),
                            job_id=job_id,
                            timestamp=datetime.now(UTC),
                            kind=DomainEventKind.job_canceled,
                            payload={"reason": "operator_cancel"},
                        )
                    )
                else:
                    await session.commit()
        except (Exception, asyncio.CancelledError):
            log.warning("job_cancel_transition_failed", job_id=job_id, exc_info=True)

    # ------------------------------------------------------------------
    # Shared event processing
    # ------------------------------------------------------------------

    async def _process_agent_event(
        self,
        job_id: str,
        session_event: SessionEvent,
        agent_session: _AgentSession,
        worktree_path: str | None,
        base_ref: str | None,
        rejection_message: str,
    ) -> tuple[_EventAction, DomainEvent | None, str | None]:
        """Process a single agent session event (shared by main + follow-up loops).

        Returns ``(action, domain_event, error_reason)``:

        * **skip** – event consumed internally, caller should ``continue``.
        * **publish** – caller should emit *domain_event* via the event bus.
          *error_reason* is set when the event signals a failure but the loop
          should keep draining.
        * **abort** – caller should ``break``; *error_reason* explains why.
        """
        import time

        self._last_activity[job_id] = time.monotonic()

        # Diff recalculation on file changes
        if (
            session_event.kind == SessionEventKind.file_changed
            and self._diff_service is not None
            and worktree_path
            and base_ref
        ):
            await self._diff_service.on_worktree_file_modified(job_id, worktree_path, base_ref)
            return _EventAction.skip, None, None

        # Diff recalculation on tool completions (skip internal markers like report_intent)
        if (
            session_event.kind == SessionEventKind.transcript
            and session_event.payload.get("role") == "tool_call"
            and session_event.payload.get("tool_name") != "report_intent"
            and self._diff_service is not None
            and worktree_path
            and base_ref
        ):
            await self._diff_service.on_worktree_file_modified(job_id, worktree_path, base_ref)

        domain_event = self._translate_event(job_id, session_event)
        if domain_event is None:
            return _EventAction.skip, None, None

        error_reason: str | None = None
        if domain_event.kind == DomainEventKind.job_failed:
            error_reason = domain_event.payload.get("message", "Agent error")

        # Suppress SDK echoes
        if domain_event.kind == DomainEventKind.transcript_updated and job_id in self._echo_suppress:
            content = domain_event.payload.get("content", "")
            if content in self._echo_suppress[job_id]:
                self._echo_suppress[job_id].discard(content)
                return _EventAction.skip, None, None

        # Handle approval requests
        if domain_event.kind == DomainEventKind.approval_requested and self._approval_service is not None:
            resolution = await self._handle_approval_request(
                job_id,
                domain_event,
                rejection_message,
            )
            if resolution == "rejected":
                return _EventAction.abort, None, rejection_message
            return _EventAction.skip, None, None

        return _EventAction.publish, domain_event, error_reason

    async def _execute_session_attempt(
        self,
        job_id: str,
        agent_session: _AgentSession,
        config: SessionConfig,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> _SessionAttemptResult:
        session_id: str | None = None
        error_reason: str | None = None
        made_progress = False
        downgrade: tuple[str, str] | None = None

        async for session_event in agent_session.execute(config, self._resolve_adapter(config.sdk)):
            made_progress = made_progress or _session_event_counts_as_resume_progress(session_event)

            action, domain_event, evt_error = await self._process_agent_event(
                job_id,
                session_event,
                agent_session,
                worktree_path,
                base_ref,
                "Approval rejected by operator",
            )

            if action == _EventAction.skip:
                continue
            if action == _EventAction.abort:
                error_reason = evt_error
                break

            assert domain_event is not None  # publish always provides an event

            if evt_error:
                error_reason = evt_error
                log.warning("agent_error_event", job_id=job_id, error_reason=error_reason)

            # Session ID for return value + persistence
            if session_id is None and agent_session.session_id:
                session_id = agent_session.session_id
                self._session_ids[job_id] = session_id
                await self._persist_sdk_session_id(job_id, session_id)

            # Model downgrade: publish event, abort session, signal caller
            if domain_event.kind == DomainEventKind.model_downgraded:
                requested = domain_event.payload.get("requested_model", "")
                actual = domain_event.payload.get("actual_model", "")
                log.warning(
                    "model_downgrade_detected",
                    job_id=job_id,
                    requested=requested,
                    actual=actual,
                )
                await self._event_bus.publish(domain_event)
                try:
                    await agent_session.abort()
                except Exception:
                    log.warning("agent_abort_on_downgrade_failed", job_id=job_id, exc_info=True)
                downgrade = (requested, actual)
                break

            # Progress tracking (main loop only) — skip ephemeral delta chunks
            if domain_event.kind == DomainEventKind.transcript_updated and self._progress_tracking is not None:
                role = domain_event.payload.get("role", "")
                if role != "agent_delta":
                    content = domain_event.payload.get("content", "")
                    tool_intent = str(domain_event.payload.get("tool_intent") or "")
                    self._progress_tracking.feed_transcript(job_id, role, content, tool_intent)

                # Native plan capture: extract structured plan data from the
                # agent's own todo/plan tool instead of relying on LLM extraction.
                if role == "tool_call":
                    tool_name = domain_event.payload.get("tool_name", "")
                    if tool_name in ("manage_todo_list", "TodoWrite"):
                        await self._ingest_native_plan(job_id, domain_event.payload)

            # Tag log lines with the current session number so callers can filter
            # by session when a job has been resumed one or more times.
            if domain_event.kind == DomainEventKind.log_line_emitted:
                domain_event.payload.setdefault("session_number", session_number)

            await self._event_bus.publish(domain_event)

        return _SessionAttemptResult(
            session_id=session_id,
            error_reason=error_reason,
            made_progress=made_progress,
            downgrade=downgrade,
        )

    async def _ingest_native_plan(self, job_id: str, payload: dict[str, object]) -> None:
        """Extract plan steps from a manage_todo_list / TodoWrite tool call."""
        import json as _json

        if self._progress_tracking is None:
            return
        raw_args = payload.get("tool_args")
        if not raw_args:
            return

        try:
            args = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (ValueError, TypeError):
            return
        if not isinstance(args, dict):
            return

        # Copilot: {"todoList": [...]}   Claude: {"todos": [...]}
        items = args.get("todoList") or args.get("todos") or []
        if not isinstance(items, list):
            return

        try:
            await self._progress_tracking.feed_native_plan(job_id, items)
        except Exception:
            log.debug("native_plan_ingest_failed", job_id=job_id, exc_info=True)

    async def _run_followup_turn(
        self,
        job_id: str,
        prompt: str,
        base_config: SessionConfig,
        resume_session_id: str | None,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> tuple[str | None, str | None]:
        """Run a single follow-up agent turn (verify or self-review).

        Returns ``(new_session_id, error_reason)``.  *error_reason* is set if
        the turn encountered an error; callers decide whether to abort.
        """
        import dataclasses

        followup_session = _AgentSession()
        followup_config = dataclasses.replace(
            base_config,
            prompt=prompt,
            resume_sdk_session_id=resume_session_id,
        )

        # Suppress echo of the follow-up prompt
        self._echo_suppress.setdefault(job_id, set()).add(prompt)

        error_reason: str | None = None
        new_session_id: str | None = None

        try:
            async for event in followup_session.execute(followup_config, self._resolve_adapter(base_config.sdk)):
                action, domain_event, evt_error = await self._process_agent_event(
                    job_id,
                    event,
                    followup_session,
                    worktree_path,
                    base_ref,
                    "Approval rejected during verification",
                )

                if action == _EventAction.skip:
                    continue
                if action == _EventAction.abort:
                    error_reason = evt_error
                    break

                assert domain_event is not None

                if evt_error:
                    error_reason = evt_error

                # Capture follow-up session ID
                if new_session_id is None and followup_session.session_id:
                    new_session_id = followup_session.session_id
                    self._session_ids[job_id] = new_session_id
                    await self._persist_sdk_session_id(job_id, new_session_id)

                if domain_event.kind == DomainEventKind.log_line_emitted:
                    domain_event.payload.setdefault("session_number", session_number)

                await self._event_bus.publish(domain_event)
        except Exception:
            log.warning("followup_turn_failed", job_id=job_id, exc_info=True)
            error_reason = "Follow-up turn execution error"

        return new_session_id, error_reason

    async def _run_verify_review(
        self,
        job_id: str,
        base_config: SessionConfig,
        session_id: str | None,
        worktree_path: str | None,
        base_ref: str | None,
        session_number: int = 1,
    ) -> None:
        """Run optional verify and self-review turns after the main agent session."""
        job: Job | None = None
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
        except Exception:
            log.warning("verify_job_lookup_failed", job_id=job_id, exc_info=True)
            return

        if job is None:
            return

        do_verify = job.verify if job.verify is not None else self._config.verification.verify
        do_self_review = job.self_review if job.self_review is not None else self._config.verification.self_review

        if not do_verify and not do_self_review:
            return

        max_turns = job.max_turns if job.max_turns is not None else self._config.verification.max_turns
        verify_prompt = job.verify_prompt or self._config.verification.verify_prompt or DEFAULT_VERIFY_PROMPT
        self_review_prompt = (
            job.self_review_prompt or self._config.verification.self_review_prompt or DEFAULT_SELF_REVIEW_PROMPT
        )

        # Emit verification phase change
        self._resolve_adapter(base_config.sdk).set_execution_phase(job_id, "verification")
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.execution_phase_changed,
                payload={"phase": "verification"},
            )
        )

        current_session_id = session_id

        if do_verify:
            for turn in range(1, max_turns + 1):
                log.info("verify_turn_start", job_id=job_id, turn=turn, max_turns=max_turns)
                new_sid, error = await self._run_followup_turn(
                    job_id,
                    verify_prompt,
                    base_config,
                    current_session_id,
                    worktree_path,
                    base_ref,
                    session_number=session_number,
                )
                if new_sid:
                    current_session_id = new_sid
                if error:
                    log.warning("verify_turn_error", job_id=job_id, turn=turn, error=error)
                    break
                log.info("verify_turn_complete", job_id=job_id, turn=turn)

        if do_self_review:
            log.info("self_review_start", job_id=job_id)
            new_sid, error = await self._run_followup_turn(
                job_id,
                self_review_prompt,
                base_config,
                current_session_id,
                worktree_path,
                base_ref,
                session_number=session_number,
            )
            if new_sid:
                current_session_id = new_sid
            if error:
                log.warning("self_review_error", job_id=job_id, error=error)
            else:
                log.info("self_review_complete", job_id=job_id)

        # Final diff snapshot after verify/review turns
        await self._finalize_diff_safe(job_id, worktree_path, base_ref)

    async def _heartbeat_loop(self, job_id: str) -> None:
        """Emit periodic heartbeats; timeout based on time since last activity."""
        import time

        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

                last = self._last_activity.get(job_id)
                if last is None:
                    return
                since_last = time.monotonic() - last

                if since_last >= _HEARTBEAT_TIMEOUT_S:
                    if job_id in self._waiting_for_approval:
                        continue
                    log.warning("job_heartbeat_timeout", job_id=job_id, idle_s=since_last)
                    await self._fail_job(job_id, "heartbeat_timeout")
                    task = self._tasks.get(job_id)
                    if task:
                        task.cancel()
                    return

                if since_last >= _HEARTBEAT_WARNING_S and job_id not in self._waiting_for_approval:
                    log.warning("job_heartbeat_warning", job_id=job_id, idle_s=since_last)

                session_id = self._session_ids.get(job_id, "")
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=DomainEvent.make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.session_heartbeat,
                        payload={
                            "job_id": job_id,
                            "session_id": session_id,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                )
        except asyncio.CancelledError:
            pass

    async def cancel(self, job_id: str) -> None:
        """Cancel a running job by cancelling its asyncio task.

        State transitions for non-running jobs (e.g. queued) are handled
        by the service layer (JobService.cancel_job). This method only
        interacts with in-memory runtime tasks.
        """
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
            log.info("job_cancel_requested", job_id=job_id)
        else:
            log.info("job_cancel_no_running_task", job_id=job_id)

    async def send_message(self, job_id: str, message: str) -> bool:
        """Send an operator message to a running job.

        Publishes the transcript event locally for immediate UI feedback and
        suppresses the SDK echo to avoid showing the message twice.

        If no live agent session exists (e.g. after a server restart or when the
        UI has a stale job state), the job is automatically resumed with the
        message as the instruction so the operator message is never silently lost.
        """
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            return await self._resume_orphaned(job_id, message)
        # Lift any tool block from a previous pause before sending.
        agent_session.resume_tools()
        now = datetime.now(UTC)
        await agent_session.send_message(message)
        # Publish immediately so the operator message appears in the transcript
        # without waiting for the SDK to echo it back.
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=now,
                kind=DomainEventKind.transcript_updated,
                payload={
                    "job_id": job_id,
                    "seq": 0,
                    "timestamp": now.isoformat(),
                    "role": "operator",
                    "content": message,
                },
            )
        )
        # Suppress the SDK echo so the same content is not published twice.
        self._echo_suppress.setdefault(job_id, set()).add(message)
        return True

    async def _resume_orphaned(self, job_id: str, message: str) -> bool:
        """Auto-resume a job that has no live agent session.

        Called by ``send_message`` when the in-memory session map has no entry
        for the job.  This covers two cases:

        * **Stale UI state** — the frontend still shows ``running`` or
          ``waiting_for_approval`` but the agent already finished and the SSE
          update hasn't reached the client yet.  The DB state will already be
          terminal, so we can resume directly.

                * **Orphaned non-terminal job** — the server restarted (or crashed)
                    before ``recover_on_startup`` ran, leaving the DB in ``running`` or
                    ``waiting_for_approval`` with no live task.  We recover it in place
                    instead of creating a synthetic failure transition.

        Returns ``True`` if the resume was successfully initiated, ``False`` if
        the job does not exist.
        """
        from backend.models.domain import TERMINAL_STATES
        from backend.persistence.job_repo import JobRepository

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)

        if job is None:
            log.warning("send_message_job_not_found", job_id=job_id)
            return False

        if job.state not in TERMINAL_STATES and job.state != JobState.review:
            # Orphaned non-terminal job — recover it in place.
            log.warning(
                "send_message_orphaned_non_terminal",
                job_id=job_id,
                state=job.state,
            )
            try:
                await self._recover_active_job(job_id, instruction=message)
            except Exception:
                log.warning("send_message_auto_resume_failed", job_id=job_id, exc_info=True)
                return False
            return True

        log.info("send_message_auto_resume", job_id=job_id)
        try:
            await self.resume_job(job_id, message)
        except Exception:
            log.warning("send_message_auto_resume_failed", job_id=job_id, exc_info=True)
            return False
        return True

    async def pause_job(self, job_id: str) -> bool:
        """Forcefully pause a running agent. Returns True if sent.

        Immediately blocks all tool execution for the session so the agent
        cannot take further actions, interrupts the current turn (on SDKs
        that support it), and sends a follow-up message instructing the
        agent to wait.  The pause message is never shown in the transcript.
        """
        _pause_msg = (
            "Please stop what you are doing right now and wait. "
            "Do not take any further actions until the operator sends a follow-up message."
        )
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            log.warning("pause_job_no_session", job_id=job_id)
            return False
        # Block all tool calls immediately so the agent cannot act.
        agent_session.pause_tools()
        # Interrupt the current turn so the agent stops immediately.
        try:
            await agent_session.interrupt()
        except Exception:
            log.warning("pause_interrupt_failed", job_id=job_id, exc_info=True)
        # Pre-register the echo suppression before sending so the SDK echo
        # (if any) is discarded and never appears in the transcript.
        self._echo_suppress.setdefault(job_id, set()).add(_pause_msg)
        await agent_session.send_message(_pause_msg)
        log.info("job_pause_requested", job_id=job_id)
        return True

    async def _dequeue_next(self) -> None:
        """Start the next queued job if capacity allows."""
        if self._shutting_down:
            return
        async with self._dequeue_lock:
            if self.running_count >= self.max_concurrent:
                return
            try:
                if self._pending_starts:
                    job_id, (override_prompt, resume_sdk_session_id) = next(iter(self._pending_starts.items()))
                    self._pending_starts.pop(job_id, None)
                    async with self._session_factory() as session:
                        from backend.persistence.job_repo import JobRepository

                        job = await JobRepository(session).get(job_id)
                    if job is not None:
                        await self._start_job(
                            job,
                            override_prompt=override_prompt,
                            resume_sdk_session_id=resume_sdk_session_id,
                        )
                    return

                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    queued_jobs = await svc.list_jobs(state=JobState.queued, limit=1)
                    jobs, _, _ = queued_jobs
                if jobs:
                    job = jobs[0]
                    override_prompt = self._queued_override_prompts.pop(job.id, None)
                    resume_sdk_session_id = self._queued_resume_session_ids.pop(job.id, None)
                    await self._start_job(
                        job,
                        override_prompt=override_prompt,
                        resume_sdk_session_id=resume_sdk_session_id,
                    )
            except Exception:
                log.error("dequeue_failed", exc_info=True)

    async def _fail_job(self, job_id: str, reason: str) -> None:
        """Transition a job to failed state and publish the event.

        The DB transition is run inside ``asyncio.shield`` so that a
        pending task-level cancellation (e.g. from anyio cancel-scope
        teardown) cannot interrupt the write.
        """
        async def _do_fail() -> None:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.get_job(job_id)
                await svc.transition_state(job_id, JobState.failed, failure_reason=reason)
                await session.commit()

        try:
            await asyncio.shield(_do_fail())
            self._set_progress_terminal_state(job_id, JobState.failed)
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_failed,
                    payload={"reason": reason},
                )
            )
        except (Exception, asyncio.CancelledError):
            log.error("fail_job_transition_failed", job_id=job_id, exc_info=True)

    async def _persist_sdk_session_id(self, job_id: str, sdk_session_id: str) -> None:
        """Persist the Copilot SDK session ID so resume_job() can reconnect to it later."""
        try:
            async with self._session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                job_repo = JobRepository(session)
                await job_repo.update_sdk_session_id(job_id, sdk_session_id)
                await session.commit()
        except Exception:
            log.warning("persist_sdk_session_id_failed", job_id=job_id, exc_info=True)

    async def _clear_sdk_session_id(self, job_id: str) -> None:
        """Clear a stale Copilot SDK session ID so resume falls back cleanly."""
        try:
            async with self._session_factory() as session:
                from backend.persistence.job_repo import JobRepository

                job_repo = JobRepository(session)
                await job_repo.update_sdk_session_id(job_id, None)
                await session.commit()
        except Exception:
            log.warning("clear_sdk_session_id_failed", job_id=job_id, exc_info=True)

    async def _load_handoff_context_for_job(
        self,
        session: AsyncSession,
        job: Job,
    ) -> tuple[str | None, list[str]]:
        from pathlib import Path

        from backend.persistence.artifact_repo import ArtifactRepository
        from backend.persistence.event_repo import EventRepository
        from backend.services.artifact_service import ArtifactService
        from backend.services.summarization_service import _extract_changed_files

        artifact_repo = ArtifactRepository(session)
        artifact_svc = ArtifactService(artifact_repo)
        summary_artifact = await artifact_svc.get_latest_session_summary(job.id)

        event_repo = EventRepository(session)
        diff_events = await event_repo.list_by_job(job.id, kinds=[DomainEventKind.diff_updated])
        changed_files = _extract_changed_files(diff_events)

        if summary_artifact is None and self._summarization_service is not None:
            log_artifact = await artifact_svc.get_session_log(job.id)
            if log_artifact is not None:
                try:
                    import json as _json

                    log_text = Path(log_artifact.disk_path).read_text(encoding="utf-8")
                    log_data = _json.loads(log_text)

                    _parts: list[str] = []
                    _counter = 0
                    all_sessions = log_data.get("sessions", [])
                    if not all_sessions and log_data.get("transcript_turns"):
                        all_sessions = [log_data]
                    for sess in all_sessions:
                        sess_num = sess.get("session_number", "?")
                        _turns = sess.get("transcript_turns", [])
                        if len(all_sessions) > 1:
                            _counter += 1
                            _parts.append(f"=== Session {sess_num} ===")
                        for t in _turns:
                            role = t.get("role", "")
                            if role == "tool_call":
                                # Skip internal intent markers — they are frontend-only labels
                                if t.get("tool_name") == "report_intent":
                                    continue
                                _counter += 1
                                display = t.get("tool_display") or t.get("tool_intent") or t.get("tool_name", "tool")
                                ok = "\u2713" if t.get("tool_success", True) else "\u2717"
                                _parts.append(f"[{_counter}] TOOL {ok}: {display}")
                            else:
                                _counter += 1
                                _parts.append(f"[{_counter}] {role.upper()}: {t.get('content', '')}")
                    transcript_text = "\n---\n".join(_parts) or "(no transcript)"
                    log_changed = log_data.get("all_changed_files") or log_data.get("changed_files", [])
                    if log_changed:
                        changed_files = log_changed
                    await self._summarization_service.summarize_and_store(
                        job.id,
                        job.session_count,
                        job.prompt,
                        pre_built_transcript=transcript_text,
                        pre_built_changed_files=changed_files,
                    )
                    # summarize_and_store commits in its own inner session; the
                    # outer session's WAL read snapshot predates that commit and
                    # cannot see the new artifact.  Open a fresh session so the
                    # lookup reflects the latest committed state.
                    async with self._session_factory() as fresh_session:
                        fresh_svc = ArtifactService(ArtifactRepository(fresh_session))
                        summary_artifact = await fresh_svc.get_latest_session_summary(job.id)
                except Exception:
                    log.warning("session_log_summarization_failed", job_id=job.id, exc_info=True)

            if summary_artifact is None:
                try:
                    await self._summarization_service.summarize_and_store(job.id, job.session_count, job.prompt)
                    # Same fresh-session pattern: inner commit is not visible to
                    # the outer session's transaction snapshot.
                    async with self._session_factory() as fresh_session:
                        fresh_svc = ArtifactService(ArtifactRepository(fresh_session))
                        summary_artifact = await fresh_svc.get_latest_session_summary(job.id)
                except Exception:
                    log.warning("inline_summarization_failed", job_id=job.id, exc_info=True)

        summary_text: str | None = None
        if summary_artifact is not None:
            try:
                summary_text = Path(summary_artifact.disk_path).read_text(encoding="utf-8")
            except Exception:
                log.warning("summary_read_failed", job_id=job.id, exc_info=True)

        return summary_text, changed_files

    async def _build_resume_handoff_prompt_for_job(
        self,
        session: AsyncSession,
        job: Job,
        instruction: str,
        session_number: int,
    ) -> str:
        from backend.services.summarization_service import _build_resume_prompt

        summary_text, changed_files = await self._load_handoff_context_for_job(session, job)
        return _build_resume_prompt(summary_text, changed_files, instruction, session_number, job.id, job.prompt)

    async def _build_followup_handoff_prompt_for_job(
        self,
        session: AsyncSession,
        job: Job,
        instruction: str,
    ) -> str:
        from backend.services.summarization_service import _build_followup_prompt

        summary_text, changed_files = await self._load_handoff_context_for_job(session, job)
        return _build_followup_prompt(summary_text, changed_files, instruction, job.id, job.prompt)

    async def _build_resume_handoff_prompt(self, job_id: str, instruction: str) -> str:
        """Build the opaque handoff prompt used when native resume is unavailable."""
        from backend.persistence.job_repo import JobRepository
        from backend.services.job_service import JobNotFoundError

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            return await self._build_resume_handoff_prompt_for_job(session, job, instruction, job.session_count)

    async def create_followup_job(self, job_id: str, instruction: str) -> Job:
        """Create and start a new follow-up job with parent-job handoff context.

        Raises ValueError if the parent job has already been merged — once merged,
        the work is in the base branch and a follow-up must be started as a fresh job.
        """
        from backend.models.domain import PermissionMode

        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("Follow-up instruction must not be empty")

        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            original = await svc.get_job(job_id)

            # Block follow-ups on already-merged jobs — the work is already in the
            # base branch, so a new job should be started from scratch instead.
            _merged_resolutions = (Resolution.merged, Resolution.pr_created)
            if original.resolution in _merged_resolutions:
                raise ValueError(
                    f"Job {job_id} has already been merged (resolution={original.resolution.value}). "
                    "Start a new job instead of creating a follow-up."
                )

            # Build a naming context hint so the LLM can produce a name that
            # reflects both the new instruction AND its follow-up relationship.
            parent_label = original.title or original.id
            parent_job_context = f"This is a follow-up task continuing work from '{parent_label}' (parent job: {original.id})."

            override_prompt = await self._build_followup_handoff_prompt_for_job(
                session,
                original,
                normalized_instruction,
            )
            followup = await svc.create_job(
                repo=original.repo,
                prompt=normalized_instruction,
                base_ref=original.base_ref,
                permission_mode=original.permission_mode or PermissionMode.full_auto,
                model=original.model,
                sdk=original.sdk,
                verify=original.verify,
                self_review=original.self_review,
                max_turns=original.max_turns,
                verify_prompt=original.verify_prompt,
                self_review_prompt=original.self_review_prompt,
                parent_job_id=original.id,
                parent_job_context=parent_job_context,
            )
            await session.commit()

        if followup.state != JobState.failed:
            await self.start_or_enqueue(followup, override_prompt=override_prompt)
            async with self._session_factory() as session:
                followup = await self._make_job_service(session).get_job(followup.id)

        return followup

    async def resume_job(self, job_id: str, instruction: str | None = None) -> Job:
        """Resume a terminal or review job in-place.

        Primary path: reconnect to the existing Copilot SDK session (full conversation history
        intact, no summarization cost). Fallback: use LLM-generated session summary when the
        SDK session is no longer available (daemon restart, session expired, etc.).
        """
        from backend.models.domain import TERMINAL_STATES
        from backend.persistence.job_repo import JobRepository
        from backend.services.job_service import JobNotFoundError, StateConflictError

        resumable_states = TERMINAL_STATES | {JobState.review}
        normalized_instruction = _normalize_resume_instruction(instruction)

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            if job.state not in resumable_states:
                raise StateConflictError(f"Job {job_id} is not in a resumable state (current: {job.state}).")

            previous_state = job.state
            previous_session_count = job.session_count
            previous_completed_at = job.completed_at
            previous_resolution = job.resolution
            previous_failure_reason = job.failure_reason
            previous_archived_at = job.archived_at
            previous_merge_status = job.merge_status
            previous_pr_url = job.pr_url
            resume_merge_status = (
                Resolution.conflict
                if previous_merge_status == Resolution.conflict or previous_resolution == Resolution.conflict
                else None
            )

            job = await self._ensure_resumable_worktree(job_repo, job)

            new_session_count = job.session_count + 1

            if job.sdk_session_id:
                # Primary path: SDK native session resume — full history intact, no summarization cost.
                log.info("resume_via_sdk_session", job_id=job_id, sdk_session_id=job.sdk_session_id)
                override_prompt = normalized_instruction
                resume_sdk_session_id: str | None = job.sdk_session_id
            else:
                log.info("resume_via_summarization", job_id=job_id)
                override_prompt = await self._build_resume_handoff_prompt_for_job(
                    session,
                    job,
                    normalized_instruction,
                    new_session_count,
                )
                resume_sdk_session_id = None

            await job_repo.reset_for_resume(job_id, new_session_count, merge_status=resume_merge_status)
            await session.commit()

        # Reload job and start execution
        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found after resume reset")

        try:
            await self.start_or_enqueue(
                job,
                override_prompt=override_prompt,
                resume_sdk_session_id=resume_sdk_session_id,
            )
        except Exception:
            async with self._session_factory() as session:
                job_repo = JobRepository(session)
                await job_repo.restore_after_failed_resume(
                    job_id,
                    previous_state=previous_state,
                    previous_session_count=previous_session_count,
                    completed_at=previous_completed_at,
                    resolution=previous_resolution,
                    failure_reason=previous_failure_reason,
                    archived_at=previous_archived_at,
                    merge_status=previous_merge_status,
                    pr_url=previous_pr_url,
                )
                await session.commit()
            raise

        # Publish session_resumed only after startup succeeds so callers do not
        # see a false-positive resume when task initialization fails.
        now = datetime.now(UTC)
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=now,
                kind=DomainEventKind.session_resumed,
                payload={
                    "session_number": new_session_count,
                    "instruction": normalized_instruction,
                    "timestamp": now.isoformat(),
                },
            )
        )
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=now,
                kind=DomainEventKind.transcript_updated,
                payload={
                    "job_id": job_id,
                    "seq": 0,
                    "timestamp": now.isoformat(),
                    "role": "operator",
                    "content": normalized_instruction,
                },
            )
        )

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            reloaded = await job_repo.get(job_id)
        if reloaded is None:
            raise ValueError(f"Job {job_id} not found after start")
        return reloaded

    async def _cleanup_job_worktree(self, job: Job) -> None:
        """Remove the secondary worktree for a finished job (failed/canceled).

        The main worktree (where worktree_path == repo) is never removed.
        """
        import contextlib

        worktree_path = job.worktree_path
        if not worktree_path or worktree_path == job.repo:
            return  # main worktree — leave it alone
        from backend.services.git_service import GitService

        git = GitService(self._config)
        with contextlib.suppress(Exception):
            await git.remove_worktree(job.repo, worktree_path)
            log.info("worktree_cleaned_up", job_id=job.id, worktree=worktree_path)

    async def _try_create_pr(self, job_id: str) -> str | None:
        """Best-effort PR creation via platform adapter. Returns the PR URL or None."""
        if self._platform_registry is None:
            log.info("pr_creation_skipped_no_registry", job_id=job_id)
            return None

        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            job = await svc.get_job(job_id)

        if job is None or not job.worktree_path or not job.branch:
            log.info("pr_creation_skipped_no_worktree", job_id=job_id)
            return None

        _ref_pattern = re.compile(r"^[a-zA-Z0-9/_.-]+$")
        if not _ref_pattern.match(job.branch):
            log.warning("pr_creation_invalid_branch", job_id=job_id)
            return None
        if not _ref_pattern.match(job.base_ref):
            log.warning("pr_creation_invalid_base_ref", job_id=job_id)
            return None

        adapter = await self._platform_registry.get_adapter(job.repo)
        pr_result = await adapter.create_pr(
            cwd=job.worktree_path,
            head=job.branch,
            base=job.base_ref,
            title=f"[CodePlane] {job.prompt[:80]}",
            body=f"Automated PR created by CodePlane for job `{job_id}`.",
        )
        if pr_result.ok:
            log.info("pr_created", job_id=job_id, pr_url=pr_result.url, platform=adapter.name)
            return pr_result.url
        log.warning("pr_creation_failed", job_id=job_id, platform=adapter.name, error=pr_result.error)
        return None

    async def _publish_state_event(self, job_id: str, previous_state: str | None, new_state: str) -> None:
        """Publish a job state change event."""
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.job_state_changed,
                payload={
                    "previous_state": previous_state,
                    "new_state": new_state,
                },
            )
        )

    def _translate_event(self, job_id: str, event: SessionEvent) -> DomainEvent | None:
        """Translate a SessionEvent into a DomainEvent."""
        mapping: dict[SessionEventKind, DomainEventKind] = {
            SessionEventKind.log: DomainEventKind.log_line_emitted,
            SessionEventKind.transcript: DomainEventKind.transcript_updated,
            SessionEventKind.approval_request: DomainEventKind.approval_requested,
            SessionEventKind.error: DomainEventKind.job_failed,
            SessionEventKind.model_downgraded: DomainEventKind.model_downgraded,
        }
        kind = mapping.get(event.kind)
        if kind is None:
            # 'done' events are handled at the _run_job level
            return None
        return DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=job_id,
            timestamp=datetime.now(UTC),
            kind=kind,
            payload=event.payload,
        )

    async def recover_on_startup(self) -> None:
        """Recover from a previous crash by restarting active jobs and re-enqueueing queued ones."""
        # Restore in-memory futures for approvals that survived the restart
        # so that recovered jobs in waiting_for_approval can be unblocked.
        if self._approval_service is not None:
            await self._approval_service.recover_pending_approvals()

        orphaned_jobs: list[tuple[Job, JobState]] = []
        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            # Recover jobs that were already in progress before the backend restart.
            for state in (JobState.running, JobState.waiting_for_approval):
                jobs, _, _ = await svc.list_jobs(state=state, limit=10000)
                orphaned_jobs.extend((job, state) for job in jobs)

            # Re-enqueue queued jobs
            queued_jobs, _, _ = await svc.list_jobs(state=JobState.queued, limit=10000)

        for job, state in orphaned_jobs:
            log.warning("recovering_orphaned_job", job_id=job.id, state=state)
            await self._recover_active_job(job.id)

        for job in queued_jobs:
            await self.start_or_enqueue(job)

    async def shutdown(self) -> None:
        """Gracefully shut down all running jobs.

        Jobs are left in their current state (running / waiting_for_approval)
        so that ``recover_on_startup`` can pick them up on the next launch
        instead of marking them as canceled (which confused users).
        """
        self._shutting_down = True
        for job_id in list(self._tasks):
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
                log.info("shutdown_task_cancelled", job_id=job_id)
        # Wait briefly for tasks to complete
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        snapshot_tasks = list(self._snapshot_tasks.values())
        if snapshot_tasks:
            await asyncio.gather(*snapshot_tasks, return_exceptions=True)

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down
