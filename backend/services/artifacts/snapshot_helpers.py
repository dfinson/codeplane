"""Shared snapshot assembly — single source of truth for snapshot hydration.

Both ``/jobs/{id}/snapshot`` and ``/share/{token}/snapshot`` build the same
response shape.  This module houses the common building blocks so the route
handlers stay thin auth + delegation wrappers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any, cast

from backend.models.api_schemas import (
    ApprovalResponse,
    ContextHandoffPayload,
    DiffFileModel,
    JobSnapshotResponse,
    LogLinePayload,
    PlanStepPayload,
    ProgressHeadlinePayload,
    SecondarySessionEntryResponse,
    SecondarySessionResponse,
    TranscriptPayload,
    TurnSummaryPayload,
    VerificationResultResponse,
)
from backend.models.domain import JobState
from backend.models.events import TRANSCRIPT_KINDS, TRANSCRIPT_STREAMING_KINDS, EventKind
from backend.services.git.git_service import GitError
from backend.services.steps.tracker import hydrate_plan_steps
from backend.services.tool_formatters import classify_tool_visibility, format_tool_display, format_tool_display_full

if TYPE_CHECKING:
    from backend.persistence.approval_repo import ApprovalRepository
    from backend.services.artifacts.diff_service import DiffService
    from backend.services.job.job_service import JobService, ProgressPreview

# Event query limits — bound the maximum rows returned from the event store.
# Default (2000) covers a typical 1–2 hour session; ceiling (5000)
# accommodates long-running jobs.  Plan/step events use the ceiling because
# each event is small and completeness matters for the UI step tracker.
EVENT_QUERY_DEFAULT = 2000
EVENT_QUERY_CEILING = 5000
# Progress headlines are short one-line status updates — 200 covers even
# long sessions while keeping the snapshot response compact.
HEADLINE_QUERY_LIMIT = 200


# ── internal helpers ────────────────────────────────────────────────────────


def _tool_args_to_str(arguments: Any) -> str | None:
    if arguments is None:
        return None
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        return json.dumps(arguments, ensure_ascii=False)
    return str(arguments)


def _tool_visibility(e: Any) -> str | None:
    tool_name = e.payload.get("tool_name")
    if not tool_name:
        return None
    return classify_tool_visibility(str(tool_name), _tool_args_to_str(e.payload.get("arguments")))


def _is_hidden_tool_event(e: Any) -> bool:
    if e.kind not in (EventKind.tool_call_started, EventKind.tool_call_completed, EventKind.tool_result_chunk):
        return False
    return _tool_visibility(e) == "hidden"


def _metadata_tool_intent(e: Any) -> str | None:
    motivation = getattr(e.metadata, "motivation", None)
    intent = getattr(motivation, "intent", None)
    return str(intent) if intent else None


def _metadata_tool_duration_ms(e: Any) -> int | None:
    duration = getattr(e.metadata, "duration_ms", None)
    return int(duration) if isinstance(duration, (int, float)) else None


def _metadata_tool_display(e: Any, *, full: bool = False) -> str | None:
    tool_name = e.payload.get("tool_name")
    if not tool_name:
        return None
    arguments = _tool_args_to_str(e.payload.get("arguments"))
    if not full:
        display = getattr(e.metadata, "tool_display", None)
        if display is not None:
            return str(display)
        return format_tool_display(
            str(tool_name),
            arguments,
            tool_result=e.payload.get("result") or None,
            tool_success=e.payload.get("success") is not False,
        )
    return format_tool_display_full(
        str(tool_name),
        arguments,
        tool_result=e.payload.get("result") or None,
        tool_success=e.payload.get("success") is not False,
    )


def _build_logs(log_events: list[Any]) -> list[LogLinePayload]:
    return [
        LogLinePayload(
            job_id=e.session_id,
            seq=e.payload.get("seq", 0),
            timestamp=e.payload.get("timestamp", e.timestamp),
            level=e.payload.get("level", "info"),
            message=e.payload.get("message", ""),
            context=e.payload.get("context"),
        )
        for e in log_events
    ]


def _build_transcript(
    transcript_events: list[Any],
    summary_events: list[Any],
    _resolve_display: Any,
    _resolve_display_full: Any,
    *,
    filter_deltas: bool,
) -> list[TranscriptPayload]:
    group_summary_by_turn: dict[str, str] = {
        str(ev.payload.get("turn_id")): str(ev.payload.get("summary"))
        for ev in summary_events
        if ev.payload.get("turn_id") and ev.payload.get("summary")
    }
    result = [
        TranscriptPayload(
            job_id=e.session_id,
            event_id=e.id,
            sequence=e.metadata.sequence,
            timestamp=e.payload.get("timestamp", e.timestamp),
            kind=str(e.kind),
            content=e.payload.get("content", ""),
            admission_status=e.payload.get("admission_status"),
            execution_status=e.payload.get("execution_status"),
            operator_content=e.payload.get("operator_content"),
            metadata=e.metadata,
            title=e.payload.get("title"),
            turn_id=e.payload.get("turn_id"),
            tool_name=e.payload.get("tool_name"),
            arguments=_tool_args_to_str(e.payload.get("arguments")),
            result=e.payload.get("result"),
            success=e.payload.get("success"),
            tool_issue=e.payload.get("tool_issue"),
            tool_intent=_metadata_tool_intent(e),
            tool_title=e.payload.get("tool_title"),
            tool_display=_metadata_tool_display(e),
            tool_display_full=_metadata_tool_display(e, full=True),
            tool_duration_ms=_metadata_tool_duration_ms(e),
            tool_group_summary=group_summary_by_turn.get(e.payload.get("turn_id") or ""),
            tool_visibility=_tool_visibility(e),
            step_id=e.payload.get("step_id"),
            step_number=e.payload.get("step_number"),
        )
        for e in transcript_events
        if (not filter_deltas or e.kind not in TRANSCRIPT_STREAMING_KINDS) and not _is_hidden_tool_event(e)
    ]
    if all(item.sequence is not None for item in result):
        result.sort(key=lambda item: item.sequence or 0)
    return result


def _apply_reassignments(
    transcript: list[TranscriptPayload],
    reassign_events: list[Any],
) -> None:
    if not reassign_events:
        return
    reassign_map: dict[str, tuple[str, str]] = {}
    for ev in reassign_events:
        tid = ev.payload.get("turn_id", "")
        old_sid = ev.payload.get("old_step_id", "")
        new_sid = ev.payload.get("new_step_id", "")
        if tid and old_sid and new_sid:
            reassign_map[tid] = (old_sid, new_sid)
    if not reassign_map:
        return
    for entry in transcript:
        key = entry.turn_id or ""
        if key in reassign_map:
            old_sid, new_sid = reassign_map[key]
            if entry.step_id == old_sid:
                entry.step_id = new_sid


def _build_timeline(timeline_events: list[Any]) -> list[ProgressHeadlinePayload]:
    milestones: list[ProgressHeadlinePayload] = []
    for event in timeline_events:
        replaces = event.payload.get("replaces_count", 0)
        if replaces > 0:
            milestones = milestones[:-replaces] if replaces < len(milestones) else []
        milestones.append(
            ProgressHeadlinePayload(
                job_id=event.session_id,
                headline=event.payload.get("headline", ""),
                headline_past=event.payload.get("headline_past", ""),
                summary=event.payload.get("summary", ""),
                timestamp=event.timestamp,
            )
        )
    return milestones


async def _build_diff(
    job: Any,
    svc: JobService,
    diff_service: DiffService,
) -> list[DiffFileModel]:
    diff: list[DiffFileModel] = []
    if job.state in (JobState.running, JobState.waiting_for_approval) and job.worktree_path:
        with contextlib.suppress(GitError, OSError):
            diff = await diff_service.calculate_diff(job.worktree_path, job.base_ref)
    if not diff:
        diff_events = await svc.list_events_by_job(job.id, [EventKind.diff_updated])
        if diff_events:
            raw_files = cast("list[Any]", diff_events[-1].payload.get("changed_files", []))
            diff = [DiffFileModel.model_validate(f) for f in raw_files]
    return diff


async def _build_approvals(
    approval_repo: ApprovalRepository,
    job_id: str,
) -> list[ApprovalResponse]:
    db_approvals = await approval_repo.list_for_job(job_id)
    return [
        ApprovalResponse(
            id=a.id,
            job_id=a.job_id,
            description=a.description,
            proposed_action=a.proposed_action,
            requested_at=a.requested_at,
            resolved_at=a.resolved_at,
            resolution=a.resolution,
            requires_explicit_approval=a.requires_explicit_approval,
            notes=a.notes,
        )
        for a in db_approvals
    ]


def _build_turn_summaries(
    turn_summary_events: list[Any],
    job_id: str,
    *,
    deduplicate: bool,
) -> list[TurnSummaryPayload]:
    """Build turn summary payloads.

    When *deduplicate* is True, keep only the last event per turn_id but
    preserve the **first** event's ``is_new_activity`` flag (refinement
    re-emits always set it to False).  Events with ``replaces_turn_id`` cause
    the replaced entry to be removed (the merge updated the scroll target).
    """
    if deduplicate:
        first_new: dict[str, bool] = {}
        latest: dict[str, int] = {}
        # Track which turn_ids have been replaced by merges, and inherit
        # is_new_activity from the replaced entry to the replacing one.
        replaced_turn_ids: set[str] = set()
        inherited_new: dict[str, bool] = {}  # replacing_turn_id → inherited flag
        for idx, ev in enumerate(turn_summary_events):
            tid = ev.payload.get("turn_id", "")
            key = f"{ev.payload.get('kind', '')}:{tid}"
            if tid:
                if key not in first_new:
                    first_new[key] = bool(ev.payload.get("is_new_activity", False))
                previous = latest.get(key)
                previous_version = (
                    turn_summary_events[previous].payload.get("version", 0) if previous is not None else -1
                )
                if ev.payload.get("version", 0) >= previous_version:
                    latest[key] = idx
            replaces = ev.payload.get("replaces_turn_id")
            if replaces:
                replaced_turn_ids.add(replaces)
                # The replacing entry inherits the replaced entry's is_new_activity
                replaced_key = f"{ev.payload.get('kind', '')}:{replaces}"
                if replaced_key in first_new:
                    inherited_new[tid] = first_new[replaced_key]
        keep_idxs = set(latest.values())
        return [
            TurnSummaryPayload(
                job_id=job_id,
                turn_id=ev.payload.get("turn_id", ""),
                title=ev.payload.get("title", ""),
                kind=ev.payload.get("kind"),
                version=ev.payload.get("version"),
                activity_id=ev.payload.get("activity_id", ""),
                activity_label=ev.payload.get("activity_label", ""),
                activity_status=ev.payload.get("activity_status", "active"),
                is_new_activity=inherited_new.get(
                    ev.payload.get("turn_id", ""),
                    first_new.get(f"{ev.payload.get('kind', '')}:{ev.payload.get('turn_id', '')}", False),
                ),
                replaces_turn_id=ev.payload.get("replaces_turn_id"),
            )
            for idx, ev in enumerate(turn_summary_events)
            if idx in keep_idxs
            and ev.payload.get("turn_id")
            and ev.payload.get("title")
            and ev.payload.get("turn_id") not in replaced_turn_ids
        ]
    return [
        TurnSummaryPayload(
            job_id=job_id,
            turn_id=ev.payload.get("turn_id", ""),
            title=ev.payload.get("title", ""),
            kind=ev.payload.get("kind"),
            version=ev.payload.get("version"),
            activity_id=ev.payload.get("activity_id", ""),
            activity_label=ev.payload.get("activity_label", ""),
            activity_status=ev.payload.get("activity_status", "active"),
            is_new_activity=bool(ev.payload.get("is_new_activity", False)),
            replaces_turn_id=ev.payload.get("replaces_turn_id"),
        )
        for ev in turn_summary_events
        if ev.payload.get("turn_id") and ev.payload.get("title")
    ]


# ── public API ──────────────────────────────────────────────────────────────


async def assemble_snapshot(
    *,
    job: Any,
    progress_preview: ProgressPreview | None,
    svc: JobService,
    diff_service: DiffService,
    approval_repo: ApprovalRepository,
    resolve_display: Any,
    resolve_display_full: Any,
    job_to_response: Any,
    filter_transcript_deltas: bool = True,
    detect_plan_generations: bool = True,
    exclude_pending_steps: bool = False,
    deduplicate_turn_summaries: bool = True,
    session_factory: Any = None,
) -> JobSnapshotResponse:
    """Assemble a full job snapshot response.

    Parameters control the behavioural differences between the authenticated
    ``/jobs/{id}/snapshot`` endpoint and the public ``/share/{token}/snapshot``.
    """
    job_id = job.id

    (
        log_events,
        transcript_events,
        timeline_events,
        summary_events,
        step_events,
        reassign_events,
        turn_summary_events,
        handoff_events,
    ) = await asyncio.gather(
        svc.list_events_by_job(job_id, [EventKind.log_line_emitted], limit=EVENT_QUERY_DEFAULT),
        svc.list_events_by_job(job_id, list(TRANSCRIPT_KINDS), limit=EVENT_QUERY_DEFAULT),
        svc.list_events_by_job(job_id, [EventKind.progress_headline], limit=HEADLINE_QUERY_LIMIT),
        svc.list_events_by_job(job_id, [EventKind.tool_group_summary], limit=EVENT_QUERY_CEILING),
        svc.list_events_by_job(job_id, [EventKind.plan_step_updated], limit=EVENT_QUERY_CEILING),
        svc.list_events_by_job(job_id, [EventKind.step_entries_reassigned], limit=EVENT_QUERY_CEILING),
        svc.list_events_by_job(job_id, [EventKind.turn_summary], limit=EVENT_QUERY_CEILING),
        svc.list_events_by_job(job_id, [EventKind.context_handoff], limit=EVENT_QUERY_CEILING),
    )

    logs = _build_logs(log_events)

    transcript = _build_transcript(
        transcript_events,
        summary_events,
        resolve_display,
        resolve_display_full,
        filter_deltas=filter_transcript_deltas,
    )
    _apply_reassignments(transcript, reassign_events)

    timeline = _build_timeline(timeline_events)

    diff = await _build_diff(job, svc, diff_service)

    approvals = await _build_approvals(approval_repo, job_id)

    plan_steps = [
        PlanStepPayload(**p)
        for p in hydrate_plan_steps(
            step_events,
            job_id,
            detect_generations=detect_plan_generations,
            exclude_pending=exclude_pending_steps,
        )
    ]

    turn_summaries = _build_turn_summaries(
        turn_summary_events,
        job_id,
        deduplicate=deduplicate_turn_summaries,
    )

    # Secondary sessions (preflight, sidecars, monitors)
    secondary_sessions: list[SecondarySessionResponse] = []
    if session_factory is not None:
        try:
            from backend.persistence.secondary_session_repo import SecondarySessionRepository

            _ssrepo = SecondarySessionRepository(session_factory)
            rows = await _ssrepo.get_by_job(job_id)
            for row in rows:
                entries_rows = await _ssrepo.get_entries(row.id)
                entries = [
                    SecondarySessionEntryResponse(
                        seq=er.seq,
                        kind=er.kind,
                        content=er.content or "",
                        tool_name=er.tool_name,
                        tool_args=er.tool_args,
                        duration_ms=er.duration_ms,
                        tool_result=er.tool_result,
                        tool_display=er.tool_display,
                        tool_display_full=er.tool_display_full,
                        tool_success=er.tool_success,
                        tool_issue=er.tool_issue,
                        tool_visibility=er.tool_visibility,
                    )
                    for er in entries_rows
                ]
                secondary_sessions.append(
                    SecondarySessionResponse(
                        id=row.id,
                        kind=row.kind,
                        name=row.name,
                        icon=row.icon,
                        status=row.status,
                        started_at=row.started_at,
                        completed_at=row.completed_at,
                        output=row.output,
                        input_tokens=row.input_tokens or 0,
                        output_tokens=row.output_tokens or 0,
                        cost_usd=row.cost_usd or 0.0,
                        entries=entries,
                    )
                )
        except Exception:
            pass  # graceful degradation — don't break snapshot if table doesn't exist yet

    verification_events = await svc.list_events_by_job(
        job_id, [EventKind.verification_result], limit=EVENT_QUERY_CEILING
    )
    return JobSnapshotResponse(
        job=job_to_response(job, progress_preview),
        logs=logs,
        transcript=transcript,
        diff=diff,
        approvals=approvals,
        timeline=timeline,
        steps=plan_steps,
        turn_summaries=turn_summaries,
        secondary_sessions=secondary_sessions,
        verification_results=[VerificationResultResponse.model_validate(e.payload) for e in verification_events],
        context_handoffs=[
            ContextHandoffPayload(
                job_id=job_id,
                source=e.payload.get("source", ""),
                source_session_id=e.payload.get("source_session_id"),
                summary=e.payload.get("summary", ""),
                content=e.payload.get("content"),
                timestamp=e.timestamp,
            )
            for e in handoff_events
        ],
    )
