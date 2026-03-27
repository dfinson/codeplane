"""Job CRUD and control endpoints."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import CPLConfig
from backend.di import CachedModelsBySdk
from backend.models.api_schemas import (
    ContinueJobRequest,
    CreateJobRequest,
    CreateJobResponse,
    DiffFileModel,
    JobListResponse,
    JobResponse,
    LogLinePayload,
    ModelInfoResponse,
    ProgressHeadlinePayload,
    ResolutionAction,
    ResolveJobRequest,
    ResolveJobResponse,
    ResumeJobRequest,
    SuggestNamesRequest,
    SuggestNamesResponse,
    TranscriptPayload,
)
from backend.models.events import DomainEventKind
from backend.services.event_bus import EventBus
from backend.services.job_service import JobService, ProgressPreview
from backend.services.merge_service import MergeService
from backend.services.naming_service import NamingService
from backend.services.runtime_service import RuntimeService
from backend.services.utility_session import UtilitySessionService

if TYPE_CHECKING:
    from backend.models.domain import Job

from backend.models.domain import JobState, PermissionMode, Resolution

router = APIRouter(tags=["jobs"], route_class=DishkaRoute)


def _job_to_response(job: Job, progress_preview: ProgressPreview | None = None) -> JobResponse:
    """Map a domain Job to a JobResponse."""
    return JobResponse(
        id=job.id,
        repo=job.repo,
        prompt=job.prompt,
        title=job.title,
        state=job.state,
        base_ref=job.base_ref,
        worktree_path=job.worktree_path,
        branch=job.branch,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        pr_url=job.pr_url,
        merge_status=job.merge_status,
        resolution=job.resolution,
        archived_at=job.archived_at,
        failure_reason=job.failure_reason,
        progress_headline=progress_preview.headline if progress_preview is not None else None,
        progress_summary=progress_preview.summary if progress_preview is not None else None,
        model=job.model,
        worktree_name=job.worktree_name,
        verify=job.verify,
        self_review=job.self_review,
        max_turns=job.max_turns,
        verify_prompt=job.verify_prompt,
        self_review_prompt=job.self_review_prompt,
        parent_job_id=job.parent_job_id,
    )


@router.post("/jobs/suggest-names", response_model=SuggestNamesResponse)
async def suggest_names(
    body: SuggestNamesRequest,
    utility_session: FromDishka[UtilitySessionService],
) -> SuggestNamesResponse:
    """Generate a suggested title, branch name, and worktree name for a task description.

    Calls the utility LLM (NamingService) in the background so the frontend can
    pre-populate the branch field before the user submits the job.
    Returns 503 if the utility LLM is not configured.
    """
    from backend.services.naming_service import NamingError

    naming = NamingService(utility_session)
    try:
        title, branch_name, worktree_name = await naming.generate(body.prompt)
    except NamingError as exc:
        raise HTTPException(status_code=503, detail=f"Naming failed: {exc}") from exc

    return SuggestNamesResponse(title=title, branch_name=branch_name, worktree_name=worktree_name)


@router.post("/jobs", response_model=CreateJobResponse, status_code=201)
async def create_job(
    body: CreateJobRequest,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    runtime_service: FromDishka[RuntimeService],
) -> CreateJobResponse:
    """Create a new job."""
    job = await svc.create_job(
        repo=body.repo,
        prompt=body.prompt,
        base_ref=body.base_ref,
        branch=body.branch,
        permission_mode=body.permission_mode or PermissionMode.auto,
        model=body.model,
        sdk=body.sdk,
        verify=body.verify,
        self_review=body.self_review,
        max_turns=body.max_turns,
        verify_prompt=body.verify_prompt,
        self_review_prompt=body.self_review_prompt,
    )

    # Commit so the job row is visible to RuntimeService (separate session)
    await session.commit()

    # Hand off to RuntimeService for execution / queueing (skip if already failed)
    if job.state != JobState.failed:
        await runtime_service.start_or_enqueue(
            job,
            permission_mode=body.permission_mode.value if body.permission_mode else None,
        )

        # Re-fetch to get updated state (may have been enqueued)
        job = await svc.get_job(job.id)

    return CreateJobResponse(
        id=job.id,
        state=job.state,
        title=job.title,
        branch=job.branch,
        worktree_path=job.worktree_path,
        sdk=job.sdk,
        created_at=job.created_at,
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    svc: FromDishka[JobService],
    state: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    archived: Annotated[bool | None, Query()] = None,
) -> JobListResponse:
    """List jobs with optional state filter and cursor pagination.

    Pass archived=true to list only archived jobs, archived=false to
    exclude them. Default (None) returns all jobs.
    """
    jobs, next_cursor, has_more = await svc.list_jobs(
        state=state,
        limit=limit,
        cursor=cursor,
        archived=archived,
    )
    progress_by_job = await svc.list_latest_progress_previews([job.id for job in jobs])
    return JobListResponse(
        items=[_job_to_response(j, progress_by_job.get(j.id)) for j in jobs],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    svc: FromDishka[JobService],
) -> JobResponse:
    """Get full job detail."""
    job = await svc.get_job(job_id)
    progress_preview = await svc.get_latest_progress_preview(job_id)
    return _job_to_response(job, progress_preview)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    svc: FromDishka[JobService],
    runtime_service: FromDishka[RuntimeService],
) -> JobResponse:
    """Cancel a running or queued job."""
    job = await svc.cancel_job(job_id)

    # Also cancel the runtime task if running
    await runtime_service.cancel(job_id)

    return _job_to_response(job)


@router.post("/jobs/{job_id}/rerun", response_model=CreateJobResponse, status_code=201)
async def rerun_job(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    runtime_service: FromDishka[RuntimeService],
) -> CreateJobResponse:
    """Create a new job from an existing job's configuration."""
    job = await svc.rerun_job(job_id)

    await session.commit()

    if job.state != JobState.failed:
        await runtime_service.start_or_enqueue(job)
        job = await svc.get_job(job.id)

    return CreateJobResponse(
        id=job.id,
        state=job.state,
        title=job.title,
        branch=job.branch,
        worktree_path=job.worktree_path,
        sdk=job.sdk,
        created_at=job.created_at,
    )


@router.post("/jobs/{job_id}/pause", status_code=204)
async def pause_job(
    job_id: str,
    svc: FromDishka[JobService],
    runtime_service: FromDishka[RuntimeService],
) -> None:
    """Send a silent pause instruction to the agent of a running job."""
    await svc.get_job(job_id)
    sent = await runtime_service.pause_job(job_id)
    if not sent:
        raise HTTPException(status_code=409, detail="Job is not currently running")


@router.post("/jobs/{job_id}/continue", response_model=CreateJobResponse, status_code=201)
async def continue_job(
    job_id: str,
    body: ContinueJobRequest,
    runtime_service: FromDishka[RuntimeService],
) -> CreateJobResponse:
    """Create a follow-up job with a new instruction and parent-job handoff context."""
    try:
        job = await runtime_service.create_followup_job(job_id, body.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return CreateJobResponse(
        id=job.id,
        state=job.state,
        title=job.title,
        branch=job.branch,
        worktree_path=job.worktree_path,
        sdk=job.sdk,
        created_at=job.created_at,
    )


@router.post("/jobs/{job_id}/resume", response_model=JobResponse)
async def resume_job(
    job_id: str,
    runtime_service: FromDishka[RuntimeService],
    body: ResumeJobRequest | None = None,
) -> JobResponse:
    """Resume a completed/failed/canceled job in-place, optionally with extra instruction."""
    job = await runtime_service.resume_job(job_id, body.instruction if body is not None else None)
    return _job_to_response(job)


@router.get("/models", response_model=list[ModelInfoResponse])
async def list_models(
    cached_models_by_sdk: FromDishka[CachedModelsBySdk],
    sdk: str | None = Query(default=None, description="SDK id (copilot | claude). Omit for default."),
) -> list[ModelInfoResponse]:
    """Return the model list for the requested SDK, cached at server startup."""
    models = cached_models_by_sdk.get(sdk, []) if sdk is not None else cached_models_by_sdk.get("copilot", [])
    return [ModelInfoResponse.model_validate(m) for m in models]


@router.get("/jobs/{job_id}/logs", response_model=list[LogLinePayload])
async def get_job_logs(
    job_id: str,
    svc: FromDishka[JobService],
    level: Annotated[str, Query(pattern="^(debug|info|warn|error)$")] = "debug",
    limit: Annotated[int, Query(ge=1, le=5000)] = 2000,
    session: Annotated[int | None, Query(ge=1, description="Filter to a specific session number (1-based)")] = None,
) -> list[LogLinePayload]:
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
    events = await svc.list_events_by_job(job_id, [DomainEventKind.log_line_emitted], limit=limit)
    lines = []
    for event in events:
        p = event.payload
        event_level = p.get("level", "info")
        if _level_order.get(event_level, 1) < min_priority:
            continue
        event_session = p.get("session_number")
        if session is not None and (event_session or 1) != session:
            continue
        lines.append(
            LogLinePayload(
                job_id=event.job_id,
                seq=p.get("seq", 0),
                timestamp=p.get("timestamp", event.timestamp),
                level=event_level,
                message=p.get("message", ""),
                context=p.get("context"),
                session_number=event_session,
            )
        )
    return lines


@router.get("/jobs/{job_id}/diff", response_model=list[DiffFileModel])
async def get_job_diff(
    job_id: str,
    svc: FromDishka[JobService],
    event_bus: FromDishka[EventBus],
    config: FromDishka[CPLConfig],
) -> list[DiffFileModel]:
    """Return the current diff for a job.

    For running jobs, calculates a fresh diff from the worktree.
    For completed/archived jobs, returns the last stored diff snapshot.
    """
    job = await svc.get_job(job_id)

    # For active jobs with a worktree, calculate a fresh diff
    if (
        job.state in (JobState.running, JobState.waiting_for_approval)
        and job.worktree_path
        and job.worktree_path != job.repo
    ):
        from backend.services.diff_service import DiffService
        from backend.services.git_service import GitService

        git = GitService(config)
        ds = DiffService(git_service=git, event_bus=event_bus)
        try:
            return await ds.calculate_diff(job.worktree_path, job.base_ref)
        except Exception:
            structlog.get_logger(__name__).warning(
                "get_job_diff_live_failed",
                job_id=job_id,
                worktree_path=str(job.worktree_path),
                base_ref=job.base_ref,
                exc_info=True,
            )

    # Fallback: read from event store (completed/archived/failed jobs)
    events = await svc.list_events_by_job(job_id, [DomainEventKind.diff_updated])
    if not events:
        return []
    raw_files = events[-1].payload.get("changed_files", [])
    return [DiffFileModel.model_validate(f) for f in raw_files]


@router.get("/jobs/{job_id}/transcript", response_model=list[TranscriptPayload])
async def get_job_transcript(
    job_id: str,
    svc: FromDishka[JobService],
    limit: int = Query(default=2000, ge=1, le=5000),
) -> list[TranscriptPayload]:
    """Return historical transcript entries for a job from the event store."""
    events = await svc.list_events_by_job(job_id, [DomainEventKind.transcript_updated], limit=limit)

    # Build a turn_id → summary map from stored tool_group_summary events so
    # that restored transcripts include AI-generated group labels.
    summary_events = await svc.list_events_by_job(job_id, [DomainEventKind.tool_group_summary], limit=5000)
    group_summary_by_turn: dict[str, str] = {
        str(ev.payload.get("turn_id")): str(ev.payload.get("summary"))
        for ev in summary_events
        if ev.payload.get("turn_id") and ev.payload.get("summary")
    }

    return [
        TranscriptPayload(
            job_id=event.job_id,
            seq=event.payload.get("seq", 0),
            timestamp=event.payload.get("timestamp", event.timestamp),
            role=event.payload.get("role", "agent"),
            content=event.payload.get("content", ""),
            title=event.payload.get("title"),
            turn_id=event.payload.get("turn_id"),
            tool_name=event.payload.get("tool_name"),
            tool_args=event.payload.get("tool_args"),
            tool_result=event.payload.get("tool_result"),
            tool_success=event.payload.get("tool_success"),
            tool_issue=event.payload.get("tool_issue"),
            tool_intent=event.payload.get("tool_intent"),
            tool_title=event.payload.get("tool_title"),
            tool_display=event.payload.get("tool_display"),
            tool_duration_ms=event.payload.get("tool_duration_ms"),
            tool_group_summary=group_summary_by_turn.get(event.payload.get("turn_id") or ""),
        )
        for event in events
    ]


@router.get("/jobs/{job_id}/timeline", response_model=list[ProgressHeadlinePayload])
async def get_job_timeline(
    job_id: str,
    svc: FromDishka[JobService],
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[ProgressHeadlinePayload]:
    """Return historical progress_headline milestones for a job.

    Events with ``replaces_count > 0`` retroactively collapse earlier entries,
    so the returned list is the final milestone timeline, not raw events.
    """
    events = await svc.list_events_by_job(job_id, [DomainEventKind.progress_headline], limit=limit)

    # Replay events to reconstruct the collapsed milestone list
    milestones: list[ProgressHeadlinePayload] = []
    for event in events:
        replaces = event.payload.get("replaces_count", 0)
        if replaces > 0:
            milestones = milestones[:-replaces] if replaces < len(milestones) else []
        milestones.append(
            ProgressHeadlinePayload(
                job_id=event.job_id,
                headline=event.payload.get("headline", ""),
                headline_past=event.payload.get("headline_past", ""),
                summary=event.payload.get("summary", ""),
                timestamp=event.timestamp,
            )
        )
    return milestones


@router.get("/jobs/{job_id}/snapshot")
async def get_job_snapshot(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    event_bus: FromDishka[EventBus],
    config: FromDishka[CPLConfig],
) -> dict[str, object]:
    """Full state hydration for a single job.

    Returns the job, logs, transcript, diff, approvals, and timeline in a
    single response. Used by the frontend after SSE reconnection or page
    refresh to ensure the UI is fully consistent with backend state.
    """
    from backend.models.api_schemas import JobSnapshotResponse

    job = await svc.get_job(job_id)
    progress_preview = await svc.get_latest_progress_preview(job_id)

    # Collect all sub-resources in parallel via gather
    import asyncio as _aio

    logs_coro = svc.list_events_by_job(job_id, [DomainEventKind.log_line_emitted], limit=2000)
    transcript_coro = svc.list_events_by_job(job_id, [DomainEventKind.transcript_updated], limit=2000)
    timeline_coro = svc.list_events_by_job(job_id, [DomainEventKind.progress_headline], limit=200)
    summary_coro = svc.list_events_by_job(job_id, [DomainEventKind.tool_group_summary], limit=5000)

    log_events, transcript_events, timeline_events, summary_events = await _aio.gather(
        logs_coro, transcript_coro, timeline_coro, summary_coro
    )

    # Build logs
    logs = [
        LogLinePayload(
            job_id=e.job_id,
            seq=e.payload.get("seq", 0),
            timestamp=e.payload.get("timestamp", e.timestamp),
            level=e.payload.get("level", "info"),
            message=e.payload.get("message", ""),
            context=e.payload.get("context"),
        )
        for e in log_events
    ]

    # Build transcript with group summaries
    group_summary_by_turn: dict[str, str] = {
        str(ev.payload.get("turn_id")): str(ev.payload.get("summary"))
        for ev in summary_events
        if ev.payload.get("turn_id") and ev.payload.get("summary")
    }
    transcript = [
        TranscriptPayload(
            job_id=e.job_id,
            seq=e.payload.get("seq", 0),
            timestamp=e.payload.get("timestamp", e.timestamp),
            role=e.payload.get("role", "agent"),
            content=e.payload.get("content", ""),
            title=e.payload.get("title"),
            turn_id=e.payload.get("turn_id"),
            tool_name=e.payload.get("tool_name"),
            tool_args=e.payload.get("tool_args"),
            tool_result=e.payload.get("tool_result"),
            tool_success=e.payload.get("tool_success"),
            tool_issue=e.payload.get("tool_issue"),
            tool_intent=e.payload.get("tool_intent"),
            tool_title=e.payload.get("tool_title"),
            tool_display=e.payload.get("tool_display"),
            tool_duration_ms=e.payload.get("tool_duration_ms"),
            tool_group_summary=group_summary_by_turn.get(e.payload.get("turn_id") or ""),
        )
        for e in transcript_events
    ]

    # Build timeline
    milestones: list[ProgressHeadlinePayload] = []
    for event in timeline_events:
        replaces = event.payload.get("replaces_count", 0)
        if replaces > 0:
            milestones = milestones[:-replaces] if replaces < len(milestones) else []
        milestones.append(
            ProgressHeadlinePayload(
                job_id=event.job_id,
                headline=event.payload.get("headline", ""),
                headline_past=event.payload.get("headline_past", ""),
                summary=event.payload.get("summary", ""),
                timestamp=event.timestamp,
            )
        )

    # Build diff (live or from snapshot)
    diff: list[DiffFileModel] = []
    if (
        job.state in (JobState.running, JobState.waiting_for_approval)
        and job.worktree_path
        and job.worktree_path != job.repo
    ):
        from backend.services.diff_service import DiffService
        from backend.services.git_service import GitService

        git = GitService(config)
        ds = DiffService(git_service=git, event_bus=event_bus)
        with contextlib.suppress(Exception):
            diff = await ds.calculate_diff(job.worktree_path, job.base_ref)

    if not diff:
        diff_events = await svc.list_events_by_job(job_id, [DomainEventKind.diff_updated])
        if diff_events:
            raw_files = diff_events[-1].payload.get("changed_files", [])
            diff = [DiffFileModel.model_validate(f) for f in raw_files]

    # Build approvals from DB state (includes resolution status)
    from backend.models.api_schemas import ApprovalResponse
    from backend.persistence.approval_repo import ApprovalRepository

    approval_repo = ApprovalRepository(session)
    db_approvals = await approval_repo.list_for_job(job_id)
    approval_list: list[ApprovalResponse] = [
        ApprovalResponse(
            id=a.id,
            job_id=a.job_id,
            description=a.description,
            proposed_action=a.proposed_action,
            requested_at=a.requested_at,
            resolved_at=a.resolved_at,
            resolution=a.resolution,
        )
        for a in db_approvals
    ]

    resp = JobSnapshotResponse(
        job=_job_to_response(job, progress_preview),
        logs=logs,
        transcript=transcript,
        diff=diff,
        approvals=approval_list,
        timeline=milestones,
    )
    return resp.model_dump(by_alias=True)


@router.get("/jobs/{job_id}/telemetry")
async def get_job_telemetry(
    job_id: str,
    session: FromDishka[AsyncSession],
) -> dict[str, object]:
    """Get telemetry data for a job run.

    Returns the persisted telemetry summary from the OTEL-backed SQLite store.
    Includes per-call span detail (tool calls, LLM calls) when available.
    """
    import json
    from datetime import UTC, datetime

    from backend.persistence.cost_attribution_repo import CostAttributionRepo
    from backend.persistence.file_access_repo import FileAccessRepo
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

    summary = await TelemetrySummaryRepo(session).get(job_id)
    if summary is None:
        return {"jobId": job_id, "available": False}

    job_row = await JobRepository(session).get(job_id)
    sdk = job_row.sdk if job_row else ""

    # Parse quota JSON if present
    quota_snapshots = None
    if summary.get("quota_json"):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            quota_snapshots = json.loads(summary["quota_json"])

    # Compute derived fields
    input_tok = summary.get("input_tokens", 0)
    output_tok = summary.get("output_tokens", 0)
    cache_read = summary.get("cache_read_tokens", 0)
    window_size = summary.get("context_window_size", 0)
    current_ctx = summary.get("current_context_tokens", 0)

    # Load span detail for tool/LLM call breakdowns
    spans = await TelemetrySpansRepo(session).list_for_job(job_id)
    attribution_rows = await CostAttributionRepo(session).for_job(job_id)
    file_stats = await FileAccessRepo(session).reread_stats(job_id)
    top_files = await FileAccessRepo(session).most_accessed_files(job_id=job_id)
    tool_calls = []
    llm_calls = []
    for span in spans:
        attrs = span.get("attrs", {})
        if span.get("span_type") == "tool":
            tool_calls.append(
                {
                    "name": span["name"],
                    "durationMs": float(span.get("duration_ms", 0)),
                    "success": attrs.get("success", True),
                    "offsetSec": float(span.get("started_at", 0)),
                }
            )
        elif span.get("span_type") == "llm":
            llm_calls.append(
                {
                    "model": span["name"],
                    "inputTokens": attrs.get("input_tokens", 0),
                    "outputTokens": attrs.get("output_tokens", 0),
                    "cacheReadTokens": attrs.get("cache_read_tokens", 0),
                    "cacheWriteTokens": attrs.get("cache_write_tokens", 0),
                    "cost": attrs.get("cost", 0),
                    "durationMs": float(span.get("duration_ms", 0)),
                    "isSubagent": attrs.get("is_subagent", False),
                    "offsetSec": float(span.get("started_at", 0)),
                }
            )

    grouped_dimensions: dict[str, list[dict[str, object]]] = {}
    turn_curve: list[dict[str, object]] = []
    for row in attribution_rows:
        bucket = {
            "dimension": row.get("dimension", "unknown"),
            "bucket": row.get("bucket", "unknown"),
            "costUsd": float(row.get("cost_usd", 0) or 0),
            "inputTokens": int(row.get("input_tokens", 0) or 0),
            "outputTokens": int(row.get("output_tokens", 0) or 0),
            "callCount": int(row.get("call_count", 0) or 0),
        }
        dimension = str(row.get("dimension", "unknown"))
        grouped_dimensions.setdefault(dimension, []).append(bucket)
        if dimension == "turn":
            turn_curve.append(bucket)

    turn_curve.sort(key=lambda item: int(str(item.get("bucket", "0"))) if str(item.get("bucket", "0")).isdigit() else 0)

    # For running jobs, compute live duration from created_at instead of
    # the stored 0 which is only finalized when the job completes.
    duration_ms = summary.get("duration_ms", 0)
    if duration_ms == 0 and summary.get("status") == "running" and summary.get("created_at"):
        try:
            created = datetime.fromisoformat(summary["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            duration_ms = int((datetime.now(UTC) - created).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    result: dict[str, object] = {
        "available": True,
        "jobId": job_id,
        "sdk": sdk,
        "model": summary.get("model", ""),
        "mainModel": summary.get("model", ""),
        "durationMs": duration_ms,
        "inputTokens": input_tok,
        "outputTokens": output_tok,
        "totalTokens": input_tok + output_tok,
        "cacheReadTokens": cache_read,
        "cacheWriteTokens": summary.get("cache_write_tokens", 0),
        "totalCost": float(summary.get("total_cost_usd", 0)),
        "contextWindowSize": window_size,
        "currentContextTokens": current_ctx,
        "contextUtilization": (current_ctx / window_size) if window_size else 0,
        "compactions": summary.get("compactions", 0),
        "tokensCompacted": summary.get("tokens_compacted", 0),
        "toolCallCount": summary.get("tool_call_count", 0),
        "totalToolDurationMs": summary.get("total_tool_duration_ms", 0),
        "toolCalls": tool_calls,
        "llmCallCount": summary.get("llm_call_count", 0),
        "totalLlmDurationMs": summary.get("total_llm_duration_ms", 0),
        "llmCalls": llm_calls,
        "approvalCount": summary.get("approval_count", 0),
        "totalApprovalWaitMs": summary.get("approval_wait_ms", 0),
        "agentMessages": summary.get("agent_messages", 0),
        "operatorMessages": summary.get("operator_messages", 0),
        "premiumRequests": float(summary.get("premium_requests", 0)),
        "costDrivers": {
            "activity": grouped_dimensions.get("activity", []),
        },
        "turnEconomics": {
            "totalTurns": int(summary.get("total_turns", 0) or 0),
            "peakTurnCostUsd": float(summary.get("peak_turn_cost_usd", 0) or 0),
            "avgTurnCostUsd": float(summary.get("avg_turn_cost_usd", 0) or 0),
            "costFirstHalfUsd": float(summary.get("cost_first_half_usd", 0) or 0),
            "costSecondHalfUsd": float(summary.get("cost_second_half_usd", 0) or 0),
            "turnCurve": turn_curve,
        },
        "fileAccess": {
            "stats": {
                "totalAccesses": int(file_stats.get("total_accesses", 0) or 0),
                "uniqueFiles": int(file_stats.get("unique_files", 0) or 0),
                "totalReads": int(file_stats.get("total_reads", 0) or 0),
                "totalWrites": int(file_stats.get("total_writes", 0) or 0),
                "rereadCount": int(file_stats.get("reread_count", 0) or 0),
            },
            "topFiles": [
                {
                    "filePath": str(row.get("file_path", "")),
                    "accessCount": int(row.get("access_count", 0) or 0),
                    "readCount": int(row.get("read_count", 0) or 0),
                    "writeCount": int(row.get("write_count", 0) or 0),
                }
                for row in top_files
            ],
        },
    }
    if quota_snapshots is not None:
        # Convert snake_case keys from DB JSON to camelCase for the frontend
        result["quotaSnapshots"] = {
            resource: {
                "usedRequests": snap.get("used_requests", 0),
                "entitlementRequests": snap.get("entitlement_requests", 0),
                "remainingPercentage": snap.get("remaining_percentage", 0),
                "overage": snap.get("overage", 0),
                "overageAllowed": snap.get("overage_allowed", False),
                "isUnlimited": snap.get("is_unlimited", False),
                "resetDate": snap.get("reset_date", ""),
            }
            for resource, snap in quota_snapshots.items()
            if isinstance(snap, dict)
        }

    return result


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
    job = await svc.resolve_job(job_id, body.action)

    # agent_merge: hand the conflict back to the agent to resolve
    if body.action == ResolutionAction.agent_merge:
        if job.resolution != Resolution.conflict:
            raise HTTPException(status_code=409, detail="agent_merge is only valid when resolution is 'conflict'")

        # Retrieve conflict files from the latest merge_conflict event
        conflict_events = await svc.list_events_by_job(job_id, kinds=[DomainEventKind.merge_conflict])
        conflict_files: list[str] = []
        if conflict_events:
            conflict_files = conflict_events[-1].payload.get("conflict_files", [])

        files_detail = (
            "\nThe following files have conflicts:\n" + "\n".join(f"  - {f}" for f in conflict_files)
            if conflict_files
            else ""
        )
        conflict_prompt = (
            f"A merge conflict was detected when attempting to merge branch '{job.branch}' "
            f"into '{job.base_ref}'.{files_detail}\n\n"
            "Please resolve the merge conflicts:\n"
            "1. Run `git merge <base_ref>` in the worktree to reproduce the conflict markers\n"
            "2. Edit the conflicting files to resolve all conflicts, preserving the functional "
            "intent of both sides without compromising either set of changes\n"
            "3. Stage and commit the resolved files\n"
            "Do not make any other modifications beyond resolving the merge conflicts."
        )

        await runtime_service.resume_job(job_id, conflict_prompt)
        return ResolveJobResponse(resolution="agent_merge")

    resolution, pr_url, conflict_files_result, error = await svc.execute_resolve(
        job=job,
        action=body.action,
        merge_service=merge_service,
    )
    await session.commit()

    # Publish job_resolved event (resolution details)
    await event_bus.publish(
        svc.build_job_resolved_event(
            job.id,
            resolution,
            pr_url=pr_url,
            conflict_files=conflict_files_result,
            error=error,
        )
    )

    # If the job transitioned to completed, publish the terminal event
    if resolution in (Resolution.merged, Resolution.pr_created, Resolution.discarded):
        from backend.models.events import DomainEvent

        await event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job.id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.job_completed,
                payload={
                    "resolution": resolution,
                    "merge_status": resolution,
                    "pr_url": pr_url,
                },
            )
        )

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
