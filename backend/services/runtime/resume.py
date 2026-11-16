"""Resume, recovery, and follow-up job creation extracted from RuntimeService.

This module handles:
- Resuming terminal/review jobs (native SDK session or summarization fallback)
- Creating follow-up jobs from completed parents
- Recovering orphaned jobs after server restart
- Handling resume fallback when SDK sessions are stale
"""

from __future__ import annotations

import uuid
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import (
    TERMINAL_STATES,
    GitMergeOutcome,
    Job,
    JobMode,
    JobNotFoundError,
    JobSpec,
    JobState,
    Resolution,
    StateConflictError,
)
from backend.models.events import EventKind, new_event
from backend.models.execution_contracts import ExecutionAdmission
from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
from backend.persistence.job_repo import JobRepository
from backend.services.runtime.handoff import PendingHandoff

if TYPE_CHECKING:
    from backend.models.domain import SessionConfig
    from backend.services.runtime.service import (
        RecoverySnapshot,
        RuntimeService,
        SessionAttemptResult,
    )

log = structlog.get_logger()

_SERVER_RESTART_RECOVERY_INSTRUCTION = (
    "The CodePlane server restarted while this job was in progress. "
    "Resume this existing job in place from the current worktree and prior context. "
    "Do not start over or create a duplicate job."
)

_PLAN_IMPLEMENTING_RECOVERY_INSTRUCTION = (
    "The CodePlane server restarted while this job was in the implementation phase. "
    "You were executing an approved plan. Resume implementation from where you left off — "
    "check the worktree for completed work and continue with the remaining plan items. "
    "Do not re-plan or start over."
)

_DEFAULT_RESUME_INSTRUCTION = "Continue the current task from where you left off and finish it."


def _normalize_resume_instruction(instruction: str | None) -> str:
    """Return a default continue instruction when the operator doesn't provide one."""
    normalized = (instruction or "").strip()
    return normalized or _DEFAULT_RESUME_INSTRUCTION


def _require_legacy_execution_released(host: RuntimeService, job_id: str) -> None:
    if job_id in host._tasks or job_id in host._starting_tasks:
        raise StateConflictError(
            "The previous execution is still releasing this Job; a new admission cannot start yet"
        )


async def ensure_resumable_worktree(host: RuntimeService, job_repo: JobRepository, job: Job) -> Job:
    """Ensure a job has a usable worktree before resuming or recovering it."""
    from pathlib import Path

    from backend.services.git.git_service import GitError, GitService

    if not job.worktree_path or job.worktree_path == job.repo:
        return job

    wt = Path(job.worktree_path)
    if wt.exists():
        return job

    if not job.branch:
        raise StateConflictError(
            f"Job {job.id} cannot be resumed because its worktree is missing and no branch is available to restore it."
        )

    git = GitService(host._config)
    try:
        new_wt = await git.reattach_worktree(job.execution_repo, job.id, job.branch)
        await job_repo.update_worktree_path(job.id, new_wt)
        job.worktree_path = new_wt
        log.info("worktree_reattached", job_id=job.id, path=new_wt)
        return job
    except GitError as exc:
        raise StateConflictError(
            f"Job {job.id} cannot be resumed because its worktree could not be restored: {exc}"
        ) from exc


async def rollback_recovery(host: RuntimeService, job_id: str, snapshot: RecoverySnapshot) -> None:
    """Restore job state after a failed recovery attempt."""
    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        await job_repo.restore_after_failed_resume(
            job_id,
            previous_state=snapshot.state,
            previous_session_count=snapshot.session_count,
            completed_at=snapshot.completed_at,
            resolution=snapshot.resolution,
            failure_reason=snapshot.failure_reason,
            archived_at=snapshot.archived_at,
            merge_status=GitMergeOutcome(snapshot.merge_status) if snapshot.merge_status else None,
            pr_url=snapshot.pr_url,
        )
        await session.commit()


async def recover_active_job(
    host: RuntimeService,
    job_id: str,
    *,
    instruction: str = _SERVER_RESTART_RECOVERY_INSTRUCTION,
) -> Job:
    """Restart an active job after backend restart without marking it failed."""
    from backend.services.runtime.service import RecoverySnapshot

    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        job = await job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist.")
        if job.state not in (JobState.running, JobState.waiting_for_approval):
            raise StateConflictError(f"Job {job_id} is not active and cannot be recovered (current: {job.state}).")

        # Plan-mode jobs in waiting_for_approval were mid-approval when the server
        # crashed. The in-memory plan context is lost and we cannot reconstruct the
        # approval gate listener. Fail gracefully instead of silently corrupting state.
        if job.mode == JobMode.plan and job.state == JobState.waiting_for_approval:
            log.warning("plan_mode.approval_lost_on_restart", job_id=job_id)
            now = datetime.now(UTC)
            await job_repo.update_state(
                job_id,
                new_state=JobState.failed,
                updated_at=now,
                completed_at=now,
                failure_reason="Server restarted while plan was awaiting approval. Please re-run the job.",
            )
            await session.commit()
            reloaded = await job_repo.get(job_id)
            if reloaded is None:
                raise JobNotFoundError(f"Job {job_id} not found after marking failed")
            return reloaded

        snapshot = RecoverySnapshot(
            state=job.state,
            session_count=job.session_count,
            completed_at=job.completed_at,
            resolution=job.resolution,
            failure_reason=job.failure_reason,
            archived_at=job.archived_at,
            merge_status=job.merge_status,
            pr_url=job.pr_url,
        )

        job = await ensure_resumable_worktree(host, job_repo, job)

        # Use a plan-implementing-specific instruction when recovering mid-implementation
        if job.mode == JobMode.plan_implementing and instruction == _SERVER_RESTART_RECOVERY_INSTRUCTION:
            instruction = _PLAN_IMPLEMENTING_RECOVERY_INSTRUCTION

        new_session_count = job.session_count + 1
        deferred_handoff = None
        if job.sdk_session_id:
            override_prompt = instruction
            resume_sdk_session_id: str | None = job.sdk_session_id
        elif host._execution_contracts is not None:
            admission = await ExecutionAdmissionRepository(session).current(job_id)
            deferred_handoff = PendingHandoff(
                admission.id if admission is not None else None, instruction, job.session_count, new_session_count,
            )
            override_prompt = instruction
            resume_sdk_session_id = None
        else:
            override_prompt = await host._build_resume_handoff_prompt_for_job(
                session,
                job,
                instruction,
                new_session_count,
            )
            resume_sdk_session_id = None

        await job_repo.reset_for_recovery(job_id, new_session_count, new_state=JobState.running)
        await session.commit()

    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        reloaded = await job_repo.get(job_id)
    if reloaded is None:
        raise JobNotFoundError(f"Job {job_id} not found after recovery reset")

    recovery_marker = object()
    host._legacy_recoveries[job_id] = recovery_marker
    if deferred_handoff is not None:
        host._legacy_handoffs[job_id] = deferred_handoff
    try:
        await host.start_or_enqueue(
            reloaded,
            override_prompt=override_prompt,
            resume_sdk_session_id=resume_sdk_session_id,
        )
    except Exception:
        log.error("recovery_start_failed", job_id=job_id, exc_info=True)
        if host._legacy_recoveries.get(job_id) is recovery_marker:
            host._legacy_recoveries.pop(job_id, None)
        if deferred_handoff is not None and host._legacy_handoffs.get(job_id) is deferred_handoff:
            host._legacy_handoffs.pop(job_id, None)
        await rollback_recovery(host, job_id, snapshot)
        raise

    if snapshot.state == JobState.waiting_for_approval:
        await host._publish_state_event(job_id, JobState.waiting_for_approval, JobState.running)

    now = datetime.now(UTC)
    await host._event_bus.publish(
        new_event(
            session_id=job_id,
            timestamp=now,
            kind=EventKind.session_resumed,
            payload={
                "session_number": new_session_count,
                "instruction": instruction,
                "timestamp": now.isoformat(),
                "reason": "server_restart",
            },
        )
    )

    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        final_job = await job_repo.get(job_id)
    if final_job is None:
        raise JobNotFoundError(f"Job {job_id} not found after recovery start")
    return final_job


async def attempt_resume_fallback(
    host: RuntimeService,
    job_id: str,
    config: SessionConfig,
    worktree_path: str | None,
    base_ref: str | None,
    session_number: int = 1,
) -> SessionAttemptResult:
    """Try a fresh session after a failed resume."""
    from backend.services.runtime.service import AgentSession, SessionAttemptResult

    await host._clear_sdk_session_id(job_id)
    try:
        fallback_prompt = await _build_resume_handoff_prompt(host, job_id, config.prompt)
    except (OSError, KeyError, ValueError, LookupError):
        log.warning("resume_handoff_prompt_build_failed", job_id=job_id, exc_info=True)
        return SessionAttemptResult(error_reason="Resume handoff prompt build failed")

    log.warning(
        "resume_sdk_session_unusable_falling_back",
        job_id=job_id,
        sdk_session_id=config.resume_sdk_session_id,
    )
    fallback_session = AgentSession()
    host._agent_sessions[job_id] = fallback_session
    fallback_config = dataclass_replace(
        config,
        prompt=fallback_prompt,
        resume_sdk_session_id=None,
    )
    fallback_result = await host._execute_session_attempt(
        job_id,
        fallback_session,
        fallback_config,
        worktree_path,
        base_ref,
        session_number=session_number,
    )
    return fallback_result


async def resume_orphaned(host: RuntimeService, job_id: str, message: str) -> bool:
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

    Raises domain exceptions (``StateConflictError``, ``JobNotFoundError``)
    so that callers can surface the real error instead of a generic failure.
    """

    async with host._session_factory() as session:
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
            await recover_active_job(host, job_id, instruction=message)
        except (StateConflictError, JobNotFoundError):
            raise
        except Exception:
            log.warning("send_message_auto_resume_failed", job_id=job_id, exc_info=True)
            return False
        return True

    log.info("send_message_auto_resume", job_id=job_id)
    try:
        await resume_job(host, job_id, message)
    except (StateConflictError, JobNotFoundError):
        raise
    except Exception:
        log.warning("send_message_auto_resume_failed", job_id=job_id, exc_info=True)
        return False
    return True


async def _build_resume_handoff_prompt(host: RuntimeService, job_id: str, instruction: str) -> str:
    """Build the opaque handoff prompt used when native resume is unavailable."""

    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        job = await job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist.")
        return await host._build_resume_handoff_prompt_for_job(session, job, instruction, job.session_count)


async def resume_job(host: RuntimeService, job_id: str, instruction: str | None = None) -> Job:
    """Resume a terminal or review job in-place.

    Primary path: reconnect to the existing Copilot SDK session (full conversation history
    intact, no summarization cost). Fallback: use LLM-generated session summary when the
    SDK session is no longer available (daemon restart, session expired, etc.).
    """
    resumable_states = TERMINAL_STATES | {JobState.review}
    from backend.models.domain import JobSource
    from backend.persistence.recipe_run_repo import RecipeRunRepository
    from backend.services.adapters.integration_policy import IntegrationPolicy

    original = await host._get_job(job_id)
    if original is None:
        raise JobNotFoundError(f"Job {job_id} does not exist")
    IntegrationPolicy(host._config).require_enabled(original.sdk)
    if original.source != JobSource.managed:
        raise StateConflictError("Native sessions must be resumed through their owning CLI")
    _require_legacy_execution_released(host, job_id)
    normalized_instruction = _normalize_resume_instruction(instruction)

    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        job = await job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist.")
        if job.state not in resumable_states:
            raise StateConflictError(f"Job {job_id} is not in a resumable state (current: {job.state}).")
        if await RecipeRunRepository(session).get_for_job(job_id) is not None:
            raise StateConflictError("Recipe continuation must use its owning run admission, not legacy resume")
        admissions = ExecutionAdmissionRepository(session)
        previous_admission = await admissions.current(job_id)

        previous_state = job.state
        previous_session_count = job.session_count
        previous_completed_at = job.completed_at
        previous_resolution = job.resolution
        previous_failure_reason = job.failure_reason
        previous_archived_at = job.archived_at
        previous_merge_status = job.merge_status
        previous_pr_url = job.pr_url
        resume_merge_status = (
            GitMergeOutcome.conflict
            if previous_merge_status == GitMergeOutcome.conflict or previous_resolution == Resolution.conflict
            else None
        )

        job = await ensure_resumable_worktree(host, job_repo, job)

        new_session_count = job.session_count + 1

        handoff_from = None
        if job.sdk_session_id:
            # Primary path: SDK native session resume — full history intact, no summarization cost.
            log.info("resume_via_sdk_session", job_id=job_id, sdk_session_id=job.sdk_session_id)
            override_prompt = normalized_instruction
            resume_sdk_session_id: str | None = job.sdk_session_id
        elif host._execution_contracts is not None:
            handoff_from = job.session_count
            override_prompt = normalized_instruction
            resume_sdk_session_id = None
        else:
            log.info("resume_via_summarization", job_id=job_id)
            override_prompt = await host._build_resume_handoff_prompt_for_job(
                session,
                job,
                normalized_instruction,
                new_session_count,
            )
            resume_sdk_session_id = None

        _require_legacy_execution_released(host, job_id)
        await job_repo.reset_for_resume(job_id, new_session_count, merge_status=resume_merge_status)
        admission = ExecutionAdmission(
            id=str(uuid.uuid4()), job_id=job_id, origin="continuation",
            previous_admission_id=previous_admission.id if previous_admission is not None else None,
        )
        await admissions.declare_legacy(
            admission, expected_current=previous_admission.id if previous_admission is not None else None,
        )
        await session.commit()

    # Reload job and start execution
    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        job = await job_repo.get(job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found after resume reset")

    host._legacy_recoveries.pop(job_id, None)
    deferred_handoff = None
    if handoff_from is not None:
        deferred_handoff = PendingHandoff(
            admission.id, normalized_instruction, handoff_from, new_session_count,
        )
        host._legacy_handoffs[job_id] = deferred_handoff
    try:
        await host.start_or_enqueue(
            job,
            override_prompt=override_prompt,
            resume_sdk_session_id=resume_sdk_session_id,
        )
    except Exception:
        log.error("resume_start_failed", job_id=job_id, exc_info=True)
        if deferred_handoff is not None and host._legacy_handoffs.get(job_id) is deferred_handoff:
            host._legacy_handoffs.pop(job_id, None)
        async with host._session_factory() as session:
            job_repo = JobRepository(session)
            await ExecutionAdmissionRepository(session).abandon(
                admission, reason="Legacy continuation startup failed before start_or_enqueue returned",
            )
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
    await host._event_bus.publish(
        new_event(
            session_id=job_id,
            timestamp=now,
            kind=EventKind.session_resumed,
            payload={
                "session_number": new_session_count,
                "instruction": normalized_instruction,
                "timestamp": now.isoformat(),
            },
        )
    )

    # Emit context_handoff so the UI shows what was carried forward
    if resume_sdk_session_id is None:
        # Summarization-based resume — context was compiled from prior session
        await host._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=now,
                kind=EventKind.context_handoff,
                payload={
                    "source": "resume",
                    "summary": f"Carried forward context from session {previous_session_count}",
                    "content": override_prompt,
                },
            )
        )
    else:
        # Native SDK resume — full conversation history intact
        await host._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=now,
                kind=EventKind.context_handoff,
                payload={
                    "source": "resume_native",
                    "summary": f"Resumed SDK session (full history from session {previous_session_count})",
                    "content": None,
                },
            )
        )

    await host._event_bus.publish(
        new_event(
            session_id=job_id,
            timestamp=now,
            kind=EventKind.message_user,
            payload={
                "job_id": job_id,
                "seq": 0,
                "timestamp": now.isoformat(),
                "content": normalized_instruction,
            },
        )
    )

    async with host._session_factory() as session:
        job_repo = JobRepository(session)
        reloaded = await job_repo.get(job_id)
    if reloaded is None:
        raise JobNotFoundError(f"Job {job_id} not found after start")
    return reloaded


async def create_followup_job(host: RuntimeService, job_id: str, instruction: str) -> Job:
    """Create and start a new follow-up job with parent-job handoff context.

    Raises ValueError if the parent job has already been merged — once merged,
    the work is in the base branch and a follow-up must be started as a fresh job.
    """
    normalized_instruction = instruction.strip()
    if not normalized_instruction:
        raise ValueError("Follow-up instruction must not be empty")

    async with host._session_factory() as session:
        svc = host._make_job_service(session)
        original = await svc.get_job(job_id)

        # Block follow-ups on already-merged jobs — the work is already in the
        # base branch, so a new job should be started from scratch instead.
        _merged_resolutions = (Resolution.merged, Resolution.pr_created)
        if original.resolution in _merged_resolutions:
            raise StateConflictError(
                f"Job {job_id} has already been merged (resolution={original.resolution.value}). "
                "Start a new job instead of creating a follow-up."
            )

        # Build a naming context hint so the LLM can produce a name that
        # reflects both the new instruction AND its follow-up relationship.
        parent_label = original.title or original.id
        parent_job_context = (
            f"This is a follow-up task continuing work from '{parent_label}' (parent job: {original.id})."
        )

        override_prompt = await host._build_followup_handoff_prompt_for_job(
            session,
            original,
            normalized_instruction,
        )
        followup = await svc.create_job(
            JobSpec(
                repo=original.execution_repo,
                prompt=normalized_instruction,
                base_ref=original.execution_base_ref,
                preset=original.preset,
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
        )
        await session.commit()

    if followup.state != JobState.failed:
        await host.start_or_enqueue(followup, override_prompt=override_prompt)

        # Emit context_handoff for the new follow-up job
        await host._event_bus.publish(
            new_event(
                session_id=followup.id,
                timestamp=datetime.now(UTC),
                kind=EventKind.context_handoff,
                payload={
                    "source": "followup",
                    "summary": f"Continuing from parent job '{parent_label}'",
                    "content": override_prompt,
                },
            )
        )

        async with host._session_factory() as session:
            followup = await host._make_job_service(session).get_job(followup.id)

    return followup
