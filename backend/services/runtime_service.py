"""Long-running job execution manager.

RuntimeService orchestrates the full lifecycle of agent jobs: session creation,
event streaming, heartbeat monitoring, diff tracking, approval flow,
cancellation, and post-job cleanup.

Progress tracking (headline milestones and plan extraction) is delegated to
``ProgressTrackingService`` — see ``backend/services/progress_tracking_service.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

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
from backend.config import build_session_config
from backend.services.progress_tracking_service import ProgressTrackingService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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

# Heartbeat configuration
_HEARTBEAT_INTERVAL_S = 30
_HEARTBEAT_WARNING_S = 90
_HEARTBEAT_TIMEOUT_S = 300  # 5 minutes

# Default prompts for post-completion verification and self-review turns
DEFAULT_VERIFY_PROMPT = (
    "Before this task is complete: identify and run this project's test suite, "
    "linter, and type checker. If anything fails, fix it and re-run until "
    "everything passes. Assume that any failure is caused by your changes — "
    "do not dismiss failures as pre-existing or flaky. Also check that you "
    "haven't made unrelated changes outside the scope of the original task; "
    "revert any that you find. Report what you ran and the results."
)

DEFAULT_SELF_REVIEW_PROMPT = (
    "Review the changes you just made. Look at the full diff. Check for: "
    "missed edge cases, incomplete implementations, leftover debug code, "
    "broken imports, dead code, backwards-compatibility shims or fallback "
    "paths that may no longer be needed, and inconsistencies with the "
    "surrounding codebase. If you find issues, fix them."
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
        self._session_ids: dict[str, str] = {}
        self._permission_overrides: dict[str, str] = {}  # job_id → permission_mode
        self._dequeue_lock = asyncio.Lock()
        self._shutting_down = False
        self._snapshot_tasks: dict[str, asyncio.Task[None]] = {}
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

    async def _finalize_diff_safe(
        self, job_id: str, worktree_path: str | None, base_ref: str | None
    ) -> None:
        """Finalize the diff snapshot, swallowing exceptions."""
        if self._diff_service is None or not worktree_path or not base_ref:
            return
        try:
            await self._diff_service.finalize(job_id, worktree_path, base_ref)
        except Exception:
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

    async def _start_job(
        self, job: Job, override_prompt: str | None = None, resume_sdk_session_id: str | None = None
    ) -> None:
        """Create an asyncio task to execute the job."""
        if job.id in self._tasks:
            return  # Already running (race-condition guard)

        agent_session = _AgentSession()
        self._agent_sessions[job.id] = agent_session

        # Ensure job is in running state
        if job.state != JobState.running:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job.id, JobState.running)
                await session.commit()
            await self._publish_state_event(job.id, job.state, JobState.running)

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
            self._run_job(job.id, agent_session, session_config),
            name=f"job-{job.id}",
        )
        self._tasks[job.id] = task
        # Pre-register prompt for echo suppression so the SDK user.message
        # echo of the initial prompt is discarded (shown via the synthetic entry).
        self._echo_suppress.setdefault(job.id, set()).add(session_config.prompt)
        log.info("job_started", job_id=job.id)

    async def _run_job(
        self,
        job_id: str,
        agent_session: _AgentSession,
        config: SessionConfig,
    ) -> None:
        """Execute the agent session, translate events, and handle completion."""
        import time

        self._last_activity[job_id] = time.monotonic()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id),
            name=f"heartbeat-{job_id}",
        )
        self._heartbeat_tasks[job_id] = heartbeat_task

        # Start progress tracking (headline milestones + plan extraction)
        if self._progress_tracking is not None:
            self._progress_tracking.start_tracking(job_id)
            # Proactively scale the utility pool to match running jobs
            await self._utility_session.notify_job_started()

        # Start telemetry tracking
        from backend.services.telemetry import collector as tel

        tel.start_job(job_id, model=config.model or "")

        # Resolve worktree_path and base_ref for diff calculations
        worktree_path: str | None = None
        base_ref: str | None = None
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                job = await svc.get_job(job_id)
            if job is not None:
                worktree_path = job.worktree_path or job.repo
                base_ref = job.base_ref
        except Exception:
            log.warning("diff_job_lookup_failed", job_id=job_id, exc_info=True)

        session_id: str | None = None
        error_reason: str | None = None
        try:
            result = await self._execute_session_attempt(
                job_id,
                agent_session,
                config,
                worktree_path,
                base_ref,
            )
            session_id = result.session_id
            error_reason = result.error_reason

            # Resume fallback: first attempt errored without progress on a resumed session
            if error_reason and config.resume_sdk_session_id and not result.made_progress:
                result = await self._attempt_resume_fallback(
                    job_id, config, worktree_path, base_ref,
                )
                session_id = result.session_id
                error_reason = result.error_reason

            # Model downgrade (from either attempt): finish diff, succeed with note, skip verify
            if result.downgrade is not None:
                requested, actual = result.downgrade
                await self._finalize_diff_safe(job_id, worktree_path, base_ref)
                reason = f"Model downgraded: requested {requested} but received {actual}"
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    await svc.transition_state(job_id, JobState.succeeded, failure_reason=reason)
                    from backend.persistence.job_repo import JobRepository

                    job_repo = JobRepository(session)
                    await job_repo.update_resolution(job_id, Resolution.unresolved)
                    await session.commit()

                self._set_progress_terminal_state(job_id, JobState.succeeded)
                await self._event_bus.publish(
                    DomainEvent(
                        event_id=DomainEvent.make_event_id(),
                        job_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=DomainEventKind.job_succeeded,
                        payload={
                            "resolution": Resolution.unresolved,
                            "model_downgraded": True,
                            "requested_model": requested,
                            "actual_model": actual,
                        },
                    )
                )
                log.info("job_moved_to_signoff_model_downgrade", job_id=job_id)
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
            await self._run_verify_review(job_id, config, session_id, worktree_path, base_ref)

            # Always go to sign-off: leave resolution to operator
            final_resolution = Resolution.unresolved
            log.info("job_awaiting_sign_off", job_id=job_id)

            # Strategy completed normally → succeeded
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(job_id, JobState.succeeded)
                from backend.persistence.job_repo import JobRepository

                job_repo = JobRepository(session)
                await job_repo.update_resolution(job_id, final_resolution, pr_url=None)
                await session.commit()

            self._set_progress_terminal_state(job_id, JobState.succeeded)
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_succeeded,
                    payload={"resolution": final_resolution},
                )
            )
            log.info(
                "job_succeeded",
                job_id=job_id,
                resolution=final_resolution,
            )
        except asyncio.CancelledError:
            log.info("job_canceled_by_task", job_id=job_id)
            await self._handle_job_canceled(job_id, agent_session, worktree_path, base_ref)
        except Exception as exc:
            log.error("job_execution_failed", job_id=job_id, exc_info=True)
            # Finalize diff so changes are preserved even for crashed jobs
            await self._finalize_diff_safe(job_id, worktree_path, base_ref)
            await self._fail_job(job_id, f"Execution error: {exc}")
        finally:
            tel.end_job(job_id)
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(job_id, None)
            if self._progress_tracking is not None:
                self._progress_tracking.stop_tracking(job_id)
                await self._progress_tracking.finalize_plan_steps(job_id)
            await self._cleanup_job_state(job_id)

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
        if self._progress_tracking is not None:
            self._progress_tracking.cleanup(job_id)
        self._tasks.pop(job_id, None)
        self._agent_sessions.pop(job_id, None)
        self._last_activity.pop(job_id, None)
        self._session_ids.pop(job_id, None)
        self._echo_suppress.pop(job_id, None)
        if self._utility_session is not None:
            await self._utility_session.notify_job_ended()
        if self._approval_service is not None:
            self._approval_service.cleanup_job(job_id)
        if self._diff_service is not None:
            self._diff_service.cleanup(job_id)
        self._start_snapshot_task(job_id)
        await self._dequeue_next()

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
        await self._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)
        self._last_activity[job_id] = time.monotonic()

        return resolution

    async def _attempt_resume_fallback(
        self,
        job_id: str,
        config: SessionConfig,
        worktree_path: str | None,
        base_ref: str | None,
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
        await self._finalize_diff_safe(job_id, worktree_path, base_ref)
        try:
            await agent_session.abort()
        except Exception:
            log.warning("agent_abort_failed", job_id=job_id, exc_info=True)
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                current = await svc.get_job(job_id)
                if current and current.state != JobState.canceled:
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
        except Exception:
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

        # Diff recalculation on tool completions
        if (
            session_event.kind == SessionEventKind.transcript
            and session_event.payload.get("role") == "tool_call"
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
                job_id, domain_event, rejection_message,
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
    ) -> _SessionAttemptResult:
        session_id: str | None = None
        error_reason: str | None = None
        made_progress = False
        downgrade: tuple[str, str] | None = None

        async for session_event in agent_session.execute(config, self._resolve_adapter(config.sdk)):
            made_progress = made_progress or _session_event_counts_as_resume_progress(session_event)

            action, domain_event, evt_error = await self._process_agent_event(
                job_id, session_event, agent_session, worktree_path, base_ref,
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

            # Progress tracking (main loop only)
            if domain_event.kind == DomainEventKind.transcript_updated and self._progress_tracking is not None:
                role = domain_event.payload.get("role", "")
                content = domain_event.payload.get("content", "")
                tool_intent = str(domain_event.payload.get("tool_intent") or "")
                self._progress_tracking.feed_transcript(job_id, role, content, tool_intent)

            await self._event_bus.publish(domain_event)

        return _SessionAttemptResult(
            session_id=session_id,
            error_reason=error_reason,
            made_progress=made_progress,
            downgrade=downgrade,
        )

    async def _run_followup_turn(
        self,
        job_id: str,
        prompt: str,
        base_config: SessionConfig,
        resume_session_id: str | None,
        worktree_path: str | None,
        base_ref: str | None,
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
                    job_id, event, followup_session, worktree_path, base_ref,
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
                    job_id, verify_prompt, base_config, current_session_id, worktree_path, base_ref
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
                job_id, self_review_prompt, base_config, current_session_id, worktree_path, base_ref
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
                    log.warning("job_heartbeat_timeout", job_id=job_id, idle_s=since_last)
                    await self._fail_job(job_id, "heartbeat_timeout")
                    task = self._tasks.get(job_id)
                    if task:
                        task.cancel()
                    return

                if since_last >= _HEARTBEAT_WARNING_S:
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
          ``waiting_for_approval`` with no live task.  We fail the job first so
          that ``resume_job`` can restart it cleanly.

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

        if job.state not in TERMINAL_STATES:
            # Orphaned non-terminal job — mark it failed so resume_job() can proceed.
            log.warning(
                "send_message_orphaned_non_terminal",
                job_id=job_id,
                state=job.state,
            )
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(
                    job_id,
                    JobState.failed,
                    failure_reason="Orphaned: no active session (server restart?)",
                )
                await session.commit()
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_failed,
                    payload={"reason": "orphaned"},
                )
            )

        log.info("send_message_auto_resume", job_id=job_id)
        try:
            await self.resume_job(job_id, message)
        except Exception:
            log.warning("send_message_auto_resume_failed", job_id=job_id, exc_info=True)
            return False
        return True

    async def pause_job(self, job_id: str) -> bool:
        """Send a silent pause instruction to the agent. Returns True if sent.

        The pause message is never shown in the transcript — the agent receives
        the instruction to stop and wait for further operator input.
        """
        _pause_msg = (
            "Please stop what you are doing right now and wait. "
            "Do not take any further actions until the operator sends a follow-up message."
        )
        agent_session = self._agent_sessions.get(job_id)
        if agent_session is None:
            log.warning("pause_job_no_session", job_id=job_id)
            return False
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
                async with self._session_factory() as session:
                    svc = self._make_job_service(session)
                    queued_jobs = await svc.list_jobs(state=JobState.queued, limit=1)
                    jobs, _, _ = queued_jobs
                if jobs:
                    await self._start_job(jobs[0])
            except Exception:
                log.error("dequeue_failed", exc_info=True)

    async def _fail_job(self, job_id: str, reason: str) -> None:
        """Transition a job to failed state and publish the event."""
        try:
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.get_job(job_id)
                await svc.transition_state(job_id, JobState.failed, failure_reason=reason)
                await session.commit()
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
        except Exception:
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

    async def _build_resume_handoff_prompt_for_job(
        self,
        session: AsyncSession,
        job: Job,
        instruction: str,
        session_number: int,
    ) -> str:
        from pathlib import Path

        from backend.persistence.artifact_repo import ArtifactRepository
        from backend.persistence.event_repo import EventRepository
        from backend.services.artifact_service import ArtifactService
        from backend.services.summarization_service import _build_resume_prompt, _extract_changed_files

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
                            _counter += 1
                            role = t.get("role", "")
                            if role == "tool_call":
                                display = t.get("tool_display") or t.get("tool_intent") or t.get("tool_name", "tool")
                                ok = "\u2713" if t.get("tool_success", True) else "\u2717"
                                _parts.append(f"[{_counter}] TOOL {ok}: {display}")
                            else:
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

        return _build_resume_prompt(summary_text, changed_files, instruction, session_number, job.id, job.prompt)

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

    async def resume_job(self, job_id: str, instruction: str) -> Job:
        """Resume a terminal job in-place.

        Primary path: reconnect to the existing Copilot SDK session (full conversation history
        intact, no summarization cost). Fallback: use LLM-generated session summary when the
        SDK session is no longer available (daemon restart, session expired, etc.).
        """
        from pathlib import Path

        from backend.models.domain import TERMINAL_STATES
        from backend.persistence.job_repo import JobRepository
        from backend.services.job_service import JobNotFoundError, StateConflictError

        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            if job.state not in TERMINAL_STATES:
                raise StateConflictError(f"Job {job_id} is not in a terminal state (current: {job.state}).")

            # Ensure worktree still exists; re-create from branch if missing
            if job.worktree_path and job.worktree_path != job.repo:
                wt = Path(job.worktree_path)
                if not wt.exists() and job.branch:
                    from backend.services.git_service import GitService

                    git = GitService(self._config)
                    try:
                        new_wt = await git.reattach_worktree(job.repo, job.id, job.branch)
                        await job_repo.update_worktree_path(job_id, new_wt)
                        job.worktree_path = new_wt
                        log.info("worktree_reattached", job_id=job_id, path=new_wt)
                    except Exception:
                        log.warning("worktree_reattach_failed", job_id=job_id, exc_info=True)

            new_session_count = job.session_count + 1

            if job.sdk_session_id:
                # Primary path: SDK native session resume — full history intact, no summarization cost.
                log.info("resume_via_sdk_session", job_id=job_id, sdk_session_id=job.sdk_session_id)
                override_prompt = instruction
                resume_sdk_session_id: str | None = job.sdk_session_id
            else:
                log.info("resume_via_summarization", job_id=job_id)
                override_prompt = await self._build_resume_handoff_prompt_for_job(
                    session,
                    job,
                    instruction,
                    new_session_count,
                )
                resume_sdk_session_id = None

            await job_repo.reset_for_resume(job_id, new_session_count)
            await session.commit()

        # Publish session_resumed event
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
                },
            )
        )
        # Publish the operator's instruction as a transcript entry so it
        # appears in the chat trace.  The echo-suppression registered in
        # _start_job will prevent the SDK echo from duplicating it.
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
                    "content": instruction,
                },
            )
        )

        # Reload job and start execution
        async with self._session_factory() as session:
            job_repo = JobRepository(session)
            job = await job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found after resume reset")
        await self.start_or_enqueue(job, override_prompt=override_prompt, resume_sdk_session_id=resume_sdk_session_id)

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

        import re

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
        """Recover from a previous crash: fail orphaned running jobs, re-enqueue queued ones."""
        orphaned_jobs: list[tuple[Job, JobState]] = []
        async with self._session_factory() as session:
            svc = self._make_job_service(session)
            # Fail jobs that were 'running' or 'waiting_for_approval' — we can't reconnect
            for state in (JobState.running, JobState.waiting_for_approval):
                jobs, _, _ = await svc.list_jobs(state=state, limit=10000)
                orphaned_jobs.extend((job, state) for job in jobs)

            # Re-enqueue queued jobs
            queued_jobs, _, _ = await svc.list_jobs(state=JobState.queued, limit=10000)

        for job, state in orphaned_jobs:
            log.warning("recovering_orphaned_job", job_id=job.id, state=state)
            async with self._session_factory() as session:
                svc = self._make_job_service(session)
                await svc.transition_state(
                    job.id,
                    JobState.failed,
                    failure_reason="Server restarted while job was running",
                )
                await session.commit()
            await self._event_bus.publish(
                DomainEvent(
                    event_id=DomainEvent.make_event_id(),
                    job_id=job.id,
                    timestamp=datetime.now(UTC),
                    kind=DomainEventKind.job_failed,
                    payload={"reason": "process_restarted"},
                )
            )

        for job in queued_jobs:
            await self.start_or_enqueue(job)

    async def shutdown(self) -> None:
        """Gracefully shut down all running jobs."""
        self._shutting_down = True
        for job_id in list(self._tasks):
            # Cancel with server_shutdown reason
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
                try:
                    async with self._session_factory() as session:
                        svc = self._make_job_service(session)
                        current = await svc.get_job(job_id)
                        if current and current.state not in (
                            JobState.canceled,
                            JobState.succeeded,
                            JobState.failed,
                        ):
                            await svc.transition_state(job_id, JobState.canceled)
                            await session.commit()
                            await self._event_bus.publish(
                                DomainEvent(
                                    event_id=DomainEvent.make_event_id(),
                                    job_id=job_id,
                                    timestamp=datetime.now(UTC),
                                    kind=DomainEventKind.job_canceled,
                                    payload={"reason": "server_shutdown"},
                                )
                            )
                except Exception:
                    log.warning("shutdown_cancel_failed", job_id=job_id, exc_info=True)
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
