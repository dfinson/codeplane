"""Verification and self-review follow-up turns extracted from RuntimeService.

This module handles running optional verify / self-review turns after the main
agent session completes, before the job transitions to review state.
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import DBAPIError

from backend.config import DEFAULT_SELF_REVIEW_PROMPT, DEFAULT_VERIFY_PROMPT
from backend.models.api_schemas import ExecutionPhase
from backend.models.events import EventKind, new_event

if TYPE_CHECKING:
    from backend.models.domain import Job, SessionConfig
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


async def run_followup_turn(
    host: RuntimeService,
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
    from backend.services.runtime.service import AgentSession, EventAction

    followup_session = AgentSession()
    followup_config = dataclass_replace(
        base_config,
        prompt=prompt,
        resume_sdk_session_id=resume_session_id,
        peer_directory_handler=None,
        peer_message_handler=None,
    )

    # Suppress echo of the follow-up prompt
    host._echo_suppress.setdefault(job_id, set()).add(prompt)

    error_reason: str | None = None
    new_session_id: str | None = None

    try:
        async for event in followup_session.execute(followup_config, host._resolve_adapter(base_config.sdk)):
            action, evt_error = await host._process_agent_event(
                job_id,
                event,
                followup_session,
                "Approval rejected during verification",
            )

            if action == EventAction.skip:
                continue
            if action == EventAction.abort:
                error_reason = evt_error
                break

            if evt_error:
                error_reason = evt_error

            # Capture follow-up session ID
            if new_session_id is None and followup_session.session_id:
                new_session_id = followup_session.session_id
                host._session_ids[job_id] = new_session_id
                await host._persist_sdk_session_id(job_id, new_session_id)

            if event.kind == EventKind.log_line_emitted:
                event.payload.setdefault("session_number", session_number)

            # Forward to the one funnel — step/trail annotation + publish happen
            # inside the EventProcessor (shared with the main execution loop).
            if host._event_processor is not None:
                await host._event_processor.process_event(job_id, event, worktree_path, base_ref)
            else:
                await host._event_bus.publish(event)
    except Exception:
        log.warning("followup_turn_failed", job_id=job_id, exc_info=True)
        error_reason = "Follow-up turn execution error"

    return new_session_id, error_reason


async def run_verify_review(
    host: RuntimeService,
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
        async with host._session_factory() as session:
            svc = host._make_job_service(session)
            job = await svc.get_job(job_id)
    except DBAPIError:
        log.warning("verify_job_lookup_failed", job_id=job_id, exc_info=True)
        return

    if job is None:
        return

    do_verify = job.verify if job.verify is not None else host._config.verification.verify
    do_self_review = job.self_review if job.self_review is not None else host._config.verification.self_review

    if not do_verify and not do_self_review:
        return

    max_turns = job.max_turns if job.max_turns is not None else host._config.verification.max_turns
    verify_prompt = job.verify_prompt or host._config.verification.verify_prompt or DEFAULT_VERIFY_PROMPT
    self_review_prompt = (
        job.self_review_prompt or host._config.verification.self_review_prompt or DEFAULT_SELF_REVIEW_PROMPT
    )

    # Emit verification phase change
    host._resolve_adapter(base_config.sdk).set_execution_phase(job_id, ExecutionPhase.verification)
    await host._event_bus.publish(
        new_event(
            session_id=job_id,
            timestamp=datetime.now(UTC),
            kind=EventKind.execution_phase_changed,
            payload={"phase": ExecutionPhase.verification},
        )
    )

    current_session_id = session_id

    if do_verify:
        for turn in range(1, max_turns + 1):
            log.info("verify_turn_start", job_id=job_id, turn=turn, max_turns=max_turns)
            new_sid, error = await run_followup_turn(
                host,
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
        new_sid, error = await run_followup_turn(
            host,
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
    await host._finalize_diff_safe(job_id, worktree_path, base_ref)
