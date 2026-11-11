"""Job artifact and query endpoints (logs, diff, transcript, steps, timeline, snapshot, story)."""

from __future__ import annotations

from typing import Annotated, Any, cast

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.jobs import (
    _stringify_tool_args,
    job_to_response,
    resolve_tool_display,
    resolve_tool_display_full,
)
from backend.models.api_schemas import (
    DiffFileModel,
    DiffListResponse,
    FileMotivation,
    HunkMotivation,
    JobMotivationsResponse,
    JobSnapshotResponse,
    LogLinePayload,
    LogListResponse,
    PlanStepPayload,
    ProgressHeadlinePayload,
    ResolutionAction,
    ResolveJobRequest,
    ResolveJobResponse,
    RestoreRequest,
    RestoreResponse,
    StepDiffPayload,
    StepListResponse,
    StoryBlock,
    StoryResponse,
    TimelineListResponse,
    TranscriptListResponse,
    TranscriptPayload,
    TranscriptSearchListResponse,
    TranscriptSearchResult,
)
from backend.models.domain import JobState, Resolution
from backend.models.events import TRANSCRIPT_KINDS, EventKind
from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.event_repo import EventRepository
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
from backend.services.artifacts.diff_service import DiffService
from backend.services.events.event_bus import EventBus
from backend.services.git.git_service import GitError, GitService
from backend.services.job.job_service import JobService
from backend.services.merge_service import MergeService
from backend.services.runtime import RuntimeService
from backend.services.steps.diff_service import StepDiffService

log = structlog.get_logger()

router = APIRouter(tags=["jobs"], route_class=DishkaRoute)

# Event query limits
_EVENT_QUERY_DEFAULT = 2000
_EVENT_QUERY_CEILING = 5000
_HEADLINE_QUERY_LIMIT = 200

# ── Structural analysis response cache ──
# Keyed on (job_id, endpoint_name, latest_end_sha). Entries are invalidated
# when the worktree advances (new SHA = cache miss, old entry evicted).












@router.get("/jobs/{job_id}/logs", response_model=LogListResponse)
async def get_job_logs(
    job_id: str,
    svc: FromDishka[JobService],
    level: Annotated[str, Query(pattern="^(debug|info|warn|error)$")] = "debug",
    limit: Annotated[int, Query(ge=1, le=_EVENT_QUERY_CEILING)] = _EVENT_QUERY_DEFAULT,
    session: Annotated[int | None, Query(ge=1, description="Filter to a specific session number (1-based)")] = None,
) -> LogListResponse:
    """Return historical log lines for a job, filtered by minimum severity.

    ``level`` is a *minimum* severity filter (inclusive):
    - ``debug``  → all lines (debug, info, warn, error)
    - ``info``   → info, warn, error
    - ``warn``   → warn, error
    - ``error``  → error only

    ``session`` optionally restricts results to a single session number.
    Session 1 is the initial run; subsequent numbers correspond to resume/
    handoff sessions.  Omit to return logs from all sessions.
    """
    _level_order = {"debug": 0, "info": 1, "warn": 2, "error": 3}
    min_priority = _level_order.get(level, 0)
    events = await svc.list_events_by_job(job_id, [EventKind.log_line_emitted], limit=limit)
    lines = []
    for event in events:
        payload = event.payload
        event_level = payload.get("level", "info")
        if _level_order.get(event_level, 1) < min_priority:
            continue
        event_session = payload.get("session_number")
        if session is not None and (event_session or 1) != session:
            continue
        lines.append(
            LogLinePayload(
                job_id=event.session_id,
                seq=payload.get("seq", 0),
                timestamp=payload.get("timestamp", event.timestamp),
                level=event_level,
                message=payload.get("message", ""),
                context=payload.get("context"),
                session_number=event_session,
            )
        )
    return LogListResponse(items=lines)


@router.get("/jobs/{job_id}/diff", response_model=DiffListResponse)
async def get_job_diff(
    job_id: str,
    svc: FromDishka[JobService],
    diff_service: FromDishka[DiffService],
    spans_repo: FromDishka[TelemetrySpansRepository],
) -> DiffListResponse:
    """Return the current diff for a job.

    For running jobs, calculates a fresh diff from the worktree.
    For completed/archived jobs, returns the last stored diff snapshot.
    """
    job = await svc.get_job(job_id)

    files: list[DiffFileModel] = []

    # For active jobs with a worktree, calculate a fresh diff
    if job.state in (JobState.running, JobState.waiting_for_approval) and job.worktree_path:
        try:
            files = await diff_service.calculate_diff(job.worktree_path, job.base_ref)
        except (GitError, OSError):
            log.warning(
                "get_job_diff_live_failed",
                job_id=job_id,
                worktree_path=str(job.worktree_path),
                base_ref=job.base_ref,
                exc_info=True,
            )

    if not files:
        # Fallback: read from event store (completed/archived/failed jobs)
        events = await svc.list_events_by_job(job_id, [EventKind.diff_updated])
        if not events:
            return DiffListResponse(items=[])
        raw_files = cast("list[dict[str, Any]]", events[-1].payload.get("changed_files", []))
        files = [DiffFileModel.model_validate(f) for f in raw_files]

    # Enrich with per-file write/retry churn data
    churn_rows = await spans_repo.file_write_churn(job_id)
    if churn_rows:
        churn_by_file = {r["tool_target"]: r for r in churn_rows}
        for f in files:
            row = churn_by_file.get(f.path)
            if row:
                f.write_count = row["write_count"]
                f.retry_count = row["retry_count"]

    # (backfill for historical events stored without symbols)

    return DiffListResponse(items=DiffService.truncate_large_files(files))


@router.get("/jobs/{job_id}/diff-file", response_model=DiffFileModel)
async def get_job_diff_file(
    job_id: str,
    path: Annotated[str, Query(description="Relative file path within the diff")],
    svc: FromDishka[JobService],
    diff_service: FromDishka[DiffService],
) -> DiffFileModel:
    """Return the full (non-truncated) diff for a single file.

    Used by the frontend to lazily load large file diffs on demand.
    """
    job = await svc.get_job(job_id)

    file: DiffFileModel | None = None

    if job.state in (JobState.running, JobState.waiting_for_approval) and job.worktree_path:
        try:
            file = await diff_service.calculate_diff_single_file(job.worktree_path, job.base_ref, path)
        except (GitError, OSError):
            log.warning("get_job_diff_file_failed", job_id=job_id, path=path, exc_info=True)

    if file is None:
        # Fallback: read from event store
        events = await svc.list_events_by_job(job_id, [EventKind.diff_updated])
        if events:
            raw_files = cast("list[dict[str, Any]]", events[-1].payload.get("changed_files", []))
            for raw in raw_files:
                candidate = DiffFileModel.model_validate(raw)
                if candidate.path == path:
                    file = candidate
                    break

    if file is None:
        raise HTTPException(status_code=404, detail=f"File not found in diff: {path}")

    return file


@router.get("/jobs/{job_id}/transcript", response_model=TranscriptListResponse)
async def get_job_transcript(
    job_id: str,
    svc: FromDishka[JobService],
    limit: int = Query(default=_EVENT_QUERY_DEFAULT, ge=1, le=_EVENT_QUERY_CEILING),
) -> TranscriptListResponse:
    """Return historical transcript entries for a job from the event store."""
    events = await svc.list_events_by_job(job_id, list(TRANSCRIPT_KINDS), limit=limit)

    # Include persisted sidecar events so they survive page reload.
    sidecar_events = await svc.list_events_by_job(
        job_id,
        [EventKind.sidecar_transcript, EventKind.sidecar_agent_message],
        limit=limit,
    )

    # Build a turn_id → summary map from stored tool_group_summary events so
    # that restored transcripts include AI-generated group labels.
    summary_events = await svc.list_events_by_job(job_id, [EventKind.tool_group_summary], limit=_EVENT_QUERY_CEILING)
    group_summary_by_turn: dict[str, str] = {
        str(ev.payload.get("turn_id")): str(ev.payload.get("summary"))
        for ev in summary_events
        if ev.payload.get("turn_id") and ev.payload.get("summary")
    }

    items: list[TranscriptPayload] = [
        TranscriptPayload(
            job_id=event.session_id,
            event_id=event.id,
            sequence=event.metadata.sequence,
            timestamp=(p := event.payload).get("timestamp", event.timestamp),
            kind=str(event.kind),
            content=p.get("content", ""),
            title=p.get("title"),
            turn_id=event.metadata.turn_id or p.get("turn_id"),
            tool_call_id=p.get("tool_call_id"),
            tool_name=p.get("tool_name"),
            arguments=_stringify_tool_args(p.get("arguments")),
            result=p.get("result"),
            success=p.get("success"),
            tool_issue=p.get("tool_issue"),
            tool_intent=(event.metadata.motivation.intent if event.metadata.motivation else None),
            tool_title=p.get("tool_title"),
            tool_display=p.get("tool_display") or event.metadata.tool_display,
            tool_display_full=p.get("tool_display_full") or p.get("tool_display") or event.metadata.tool_display,
            tool_duration_ms=(int(event.metadata.duration_ms) if event.metadata.duration_ms is not None else None),
            tool_group_summary=group_summary_by_turn.get(p.get("turn_id") or ""),
        )
        for event in events
    ]

    # Append sidecar entries (kind == "sidecar.*")
    for event in sidecar_events:
        p = event.payload
        items.append(
            TranscriptPayload(
                job_id=event.session_id,
                event_id=event.id,
                sequence=event.metadata.sequence,
                timestamp=p.get("timestamp", event.timestamp),
                kind=str(event.kind),
                content=p.get("content", ""),
                sidecar_name=p.get("sidecar_name"),
                sidecar_icon=p.get("sidecar_icon"),
                sidecar_description=p.get("sidecar_description"),
                sidecar_template_id=p.get("sidecar_template_id"),
            )
        )

    # Producer-stream sequence can order the transcript only when every event
    # provides one. Otherwise retain deterministic storage query order.
    if all(item.sequence is not None for item in items):
        items.sort(key=lambda item: item.sequence or 0)

    return TranscriptListResponse(items=items)


@router.get("/jobs/{job_id}/steps", response_model=StepListResponse)
async def get_job_steps(
    job_id: str,
    svc: FromDishka[JobService],
) -> StepListResponse:
    """Return plan steps for a job, hydrated from persisted PlanStepUpdated events.

    During execution, plan steps are also delivered live via SSE.  This
    endpoint lets late-joining clients catch up on steps that were emitted
    before they connected.
    """
    events = await svc.list_events_by_job(job_id, [EventKind.plan_step_updated], limit=_EVENT_QUERY_CEILING)
    # De-duplicate: keep the latest event per plan_step_id (events are ordered chronologically)
    latest_by_id: dict[str, dict[str, Any]] = {}
    for ev in events:
        p = ev.payload
        step_id = p.get("plan_step_id", "")
        if step_id:
            latest_by_id[step_id] = p

    # Build response preserving insertion order (first-seen order = plan order)
    seen_order: list[str] = []
    for ev in events:
        p = ev.payload
        sid = p.get("plan_step_id", "")
        if sid and sid not in seen_order:
            seen_order.append(sid)

    result: list[PlanStepPayload] = []
    for sid in seen_order:
        step_payload = latest_by_id[sid]
        # Skip pending steps that were never started (dropped on finalization)
        if step_payload.get("status") == "pending":
            continue
        result.append(
            PlanStepPayload(
                job_id=job_id,
                plan_step_id=step_payload.get("plan_step_id", ""),
                label=step_payload.get("label", ""),
                summary=step_payload.get("summary"),
                status=step_payload.get("status", "pending"),
                order=step_payload.get("order", 0),
                tool_count=step_payload.get("tool_count", 0),
                files_written=step_payload.get("files_written"),
                started_at=step_payload.get("started_at"),
                completed_at=step_payload.get("completed_at"),
                duration_ms=step_payload.get("duration_ms"),
                start_sha=step_payload.get("start_sha"),
                end_sha=step_payload.get("end_sha"),
            )
        )
    return StepListResponse(items=result)


@router.get("/jobs/{job_id}/steps/{step_id}/diff", response_model=StepDiffPayload)
async def get_step_diff(
    job_id: str,
    step_id: str,
    step_diff_svc: FromDishka[StepDiffService],
) -> StepDiffPayload:
    """Return the Git diff for a specific step.

    The step_id can be either a plan_step_id (ps-*) from plan_step_updated
    events, an internal step_id (step-*) from the StepRow table, or a
    turn_id from the SDK — all are looked up to find start_sha/end_sha.
    """
    return await step_diff_svc.get_step_diff(job_id, step_id)


@router.get("/jobs/{job_id}/transcript/search", response_model=TranscriptSearchListResponse)
async def search_transcript(
    job_id: str,
    event_repo: FromDishka[EventRepository],
    q: str = Query(..., min_length=2, max_length=200),  # noqa: B008
    kinds: list[str] | None = Query(None),  # noqa: B008
    step_id: str | None = None,
    limit: int = Query(50, le=200),  # noqa: B008
) -> TranscriptSearchListResponse:
    """Full-text search within a job's transcript events."""
    _valid_kinds = {k.value for k in TRANSCRIPT_KINDS}
    if kinds:
        kinds = [k for k in kinds if k in _valid_kinds]

    events = await event_repo.search_transcript(job_id, q, kinds=kinds, step_id=step_id, limit=limit)
    results = []
    for evt in events:
        payload = evt.payload
        results.append(
            TranscriptSearchResult(
                seq=int(payload.get("seq", 0)),
                kind=str(evt.kind),
                content=str(payload.get("content", "")),
                tool_name=str(payload.get("tool_name")) if payload.get("tool_name") else None,
                step_id=str(payload.get("step_id")) if payload.get("step_id") else None,
                step_number=int(payload["step_number"]) if payload.get("step_number") is not None else None,
                timestamp=evt.timestamp,
            )
        )
    return TranscriptSearchListResponse(items=results)


@router.post("/jobs/{job_id}/restore", response_model=RestoreResponse)
async def restore_to_sha(
    job_id: str,
    body: RestoreRequest,
    svc: FromDishka[JobService],
    git_service: FromDishka[GitService],
) -> RestoreResponse:
    """Reset the job's worktree to a specific commit SHA.

    Destructive — requires frontend confirmation dialog.
    Blocked while the agent is actively running.
    """
    from fastapi import HTTPException

    from backend.models.domain import JobState

    job = await svc.get_job(job_id)
    if job.state in (JobState.running, JobState.waiting_for_approval):
        raise HTTPException(
            status_code=409,
            detail="Cannot restore while the agent is running. Cancel the job first.",
        )
    if not job.worktree_path:
        raise HTTPException(status_code=404, detail="Job has no worktree.")

    await git_service.reset_hard(body.sha, cwd=job.worktree_path)
    return RestoreResponse(restored=True, sha=body.sha)


@router.post("/jobs/{job_id}/reenrich")
async def reenrich_job(
    job_id: str,
    session_factory: FromDishka[async_sessionmaker[AsyncSession]],
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Re-enrich historical events for a job through TraceForge.

    Replays all persisted events through a fresh TraceForge Enricher so
    that classification, visibility, tool_display and other metadata are
    backfilled.  Idempotent — a marker event prevents double processing
    unless ``force=true``.
    """
    from backend.services.events.reenrich import reenrich_job_events

    updated = await reenrich_job_events(job_id, session_factory, force=force)
    return {"job_id": job_id, "updated_events": updated}


@router.get("/jobs/{job_id}/timeline", response_model=TimelineListResponse)
async def get_job_timeline(
    job_id: str,
    svc: FromDishka[JobService],
    limit: int = Query(default=200, ge=1, le=1000),
) -> TimelineListResponse:
    """Return historical progress_headline milestones for a job.

    Events with ``replaces_count > 0`` retroactively collapse earlier entries,
    so the returned list is the final milestone timeline, not raw events.
    """
    events = await svc.list_events_by_job(job_id, [EventKind.progress_headline], limit=limit)

    # Replay events to reconstruct the collapsed milestone list
    milestones: list[ProgressHeadlinePayload] = []
    for event in events:
        ep = event.payload
        replaces = int(ep.get("replaces_count", 0) or 0)
        if replaces > 0:
            milestones = milestones[:-replaces] if replaces < len(milestones) else []
        milestones.append(
            ProgressHeadlinePayload(
                job_id=event.session_id,
                headline=ep.get("headline", ""),
                headline_past=ep.get("headline_past", ""),
                summary=ep.get("summary", ""),
                timestamp=event.timestamp,
            )
        )
    return TimelineListResponse(items=milestones)


@router.get("/jobs/{job_id}/snapshot", response_model=JobSnapshotResponse)
async def get_job_snapshot(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    diff_service: FromDishka[DiffService],
    approval_repo: FromDishka[ApprovalRepository],
    session_factory: FromDishka[async_sessionmaker[AsyncSession]],
) -> JobSnapshotResponse:
    """Full state hydration for a single job.

    Returns the job, logs, transcript, diff, approvals, and timeline in a
    single response. Used by the frontend after SSE reconnection or page
    refresh to ensure the UI is fully consistent with backend state.
    """
    from backend.services.artifacts.snapshot_helpers import assemble_snapshot

    job = await svc.get_job(job_id)
    progress_preview = await svc.get_latest_progress_preview(job_id)

    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

    ct = (await TelemetrySummaryRepository(session).batch_cost_tokens([job_id])).get(job_id, {})

    return await assemble_snapshot(
        job=job,
        progress_preview=progress_preview,
        svc=svc,
        diff_service=diff_service,
        approval_repo=approval_repo,
        resolve_display=resolve_tool_display,
        resolve_display_full=resolve_tool_display_full,
        job_to_response=lambda j, pp: job_to_response(
            j,
            pp,
            total_cost_usd=ct.get("total_cost_usd"),
            total_tokens=ct.get("total_tokens"),
            input_tokens=ct.get("input_tokens"),
            output_tokens=ct.get("output_tokens"),
        ),
        filter_transcript_deltas=True,
        detect_plan_generations=True,
        exclude_pending_steps=False,
        deduplicate_turn_summaries=True,
        session_factory=session_factory,
    )


@router.post("/jobs/{job_id}/resolve", response_model=ResolveJobResponse)
async def resolve_job(
    job_id: str,
    body: ResolveJobRequest,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    runtime_service: FromDishka[RuntimeService],
    merge_service: FromDishka[MergeService],
    event_bus: FromDishka[EventBus],
) -> ResolveJobResponse:
    """Resolve a review job: merge, create PR, discard, or resolve with agent."""
    job = await svc.validate_for_resolution(job_id)

    # agent_merge: hand the conflict back to the agent to resolve
    if body.action == ResolutionAction.agent_merge:
        if job.resolution != Resolution.conflict:
            raise HTTPException(status_code=409, detail="agent_merge is only valid when resolution is 'conflict'")

        conflict_prompt = await svc.build_conflict_resume_prompt(job_id)
        await runtime_service.resume_job(job_id, conflict_prompt)
        return ResolveJobResponse(resolution="agent_merge")

    resolution, pr_url, conflict_files_result, error, events = await svc.resolve_and_complete(
        job=job,
        action=body.action,
        merge_service=merge_service,
    )
    await session.commit()

    for event in events:
        await event_bus.publish(event)

    return ResolveJobResponse(
        resolution=resolution,
        pr_url=pr_url,
        conflict_files=conflict_files_result,
        error=error,
    )


@router.post("/jobs/{job_id}/archive", status_code=204)
async def archive_job(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    event_bus: FromDishka[EventBus],
) -> None:
    """Archive a completed job (hide from Kanban board)."""
    await svc.archive_job(job_id)
    await session.commit()
    await event_bus.publish(svc.build_job_archived_event(job_id))


@router.post("/jobs/{job_id}/unarchive", status_code=204)
async def unarchive_job(
    job_id: str,
    svc: FromDishka[JobService],
) -> None:
    """Archived jobs are final and cannot be returned to the active board."""
    await svc.get_job(job_id)
    raise HTTPException(status_code=409, detail="Archived jobs are complete; create a follow-up job instead.")


@router.get("/jobs/{job_id}/story", response_model=StoryResponse)
async def get_job_story(
    job_id: str,
    session: FromDishka[AsyncSession],
    regenerate: bool = False,
) -> StoryResponse:
    """Return a cached code-review story for a job.

    Stories are pre-generated in the background as soon as a job enters
    review state.  This endpoint reads from the DB cache only — it never
    blocks on LLM generation.

    Pass ?regenerate=true to force a fresh generation (fire-and-forget;
    returns empty immediately and the background loop will populate it).
    """
    from sqlalchemy import text as sa_text

    # Verify job exists
    exists = await session.execute(
        sa_text("SELECT 1 FROM jobs WHERE id = :jid"),
        {"jid": job_id},
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if regenerate:
        # Clear the cached column so the drain loop regenerates it
        await session.execute(
            sa_text("UPDATE jobs SET story_text = NULL WHERE id = :jid"),
            {"jid": job_id},
        )
        await session.commit()
        return StoryResponse(job_id=job_id, blocks=[], cached=False, pending=True)

    # Read from cache only
    row = await session.execute(
        sa_text("SELECT story_text FROM jobs WHERE id = :jid"),
        {"jid": job_id},
    )
    cached = row.scalar_one_or_none()
    if cached:
        import json

        try:
            payload = json.loads(cached)
            blocks = [StoryBlock(**b) for b in payload.get("blocks", [])]
            return StoryResponse(
                job_id=job_id,
                blocks=blocks,
                cached=True,
                beat_count=payload.get("beat_count", 0),
                has_decisions=payload.get("has_decisions", False),
                has_backtracks=payload.get("has_backtracks", False),
            )
        except (json.JSONDecodeError, TypeError):
            pass

    # Not yet generated — the background drain loop will produce it
    return StoryResponse(job_id=job_id, blocks=[], cached=False, pending=True)




# -- Structural diff helpers --------------------------------------------------

# Category severity for risk scoring (§9.4)
_CATEGORY_SEVERITY = {
    "breaking": 1.0,
    "body": 0.5,
    "additive": 0.1,
    "non-structural": 0.0,
}














# -- Multi-session intelligence (§10) -----------------------------------------




# -- Impact graph drill-down (§9.5) -------------------------------------------

_IMPACT_TIER_MAP = {"high": "verified", "medium": "inferred", "unknown": "unverified"}








# -- Community clustering view (§9.7) -----------------------------------------




# -- Review story artifact (§11) ----------------------------------------------






# ---------------------------------------------------------------------------
# Motivations endpoint (job-level)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/motivations", response_model=JobMotivationsResponse)
async def get_job_motivations(
    job_id: str,
    svc: FromDishka[JobService],
    spans_repo: FromDishka[TelemetrySpansRepository],
) -> JobMotivationsResponse:
    """Return all motivation annotations for a job's changed files."""
    job = await svc.get_job(job_id)

    # Telemetry stores absolute paths; the diff API uses repo-relative paths.
    # Compute the prefix to strip so keys align with the diff contract.
    wt_prefix = ((job.worktree_path or job.repo or "") + "/").replace("//", "/")
    repo_prefix = ((job.repo or "") + "/").replace("//", "/")

    spans = await spans_repo.motivated_spans_for_job(job_id=job_id)
    file_motivations: dict[str, FileMotivation] = {}
    hunk_motivations: dict[str, HunkMotivation] = {}

    for span in spans:
        target = span.get("tool_target", "")
        summary = span.get("motivation_summary", "")
        if not target or not summary:
            continue

        # Normalize to relative path — spans created before the worktree existed
        # store repo-absolute paths, so try both prefixes.
        if target.startswith(wt_prefix):
            rel_target = target[len(wt_prefix) :]
        elif target.startswith(repo_prefix):
            rel_target = target[len(repo_prefix) :]
        else:
            rel_target = target

        # Parse motivation_summary as "Title: rest" or just use as why
        if ": " in summary and len(summary.split(": ", 1)[0]) < 80:
            title, why = summary.split(": ", 1)
        else:
            title = ""
            why = summary

        file_motivations[rel_target] = FileMotivation(title=title, why=why)

        # Parse edit_motivations JSON if present
        edit_motivations_raw = span.get("edit_motivations")
        if edit_motivations_raw:
            import json as _json

            try:
                edits = (
                    _json.loads(edit_motivations_raw) if isinstance(edit_motivations_raw, str) else edit_motivations_raw
                )
                for i, edit in enumerate(edits if isinstance(edits, list) else []):
                    key = f"{rel_target}:{i}"
                    # The stored JSON uses "summary" (not "title"/"why").
                    # Split first line as title, remainder as why.
                    em_summary = edit.get("summary", "")
                    em_lines = em_summary.strip().split("\n", 1)
                    em_title = em_lines[0].strip()
                    em_why = em_lines[1].strip() if len(em_lines) > 1 else em_summary
                    hunk_motivations[key] = HunkMotivation(
                        edit_key=edit.get("edit_key", key),
                        title=em_title,
                        why=em_why,
                    )
            except (ValueError, TypeError):
                pass

    return JobMotivationsResponse(
        job_id=job_id,
        file_motivations=file_motivations,
        hunk_motivations=hunk_motivations,
    )


# ---------------------------------------------------------------------------
# Coverage / Blast Radius endpoints
# ---------------------------------------------------------------------------
