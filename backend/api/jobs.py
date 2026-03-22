"""Job CRUD and control endpoints."""

from __future__ import annotations

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
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    runtime_service: FromDishka[RuntimeService],
) -> CreateJobResponse:
    """Create a follow-up job with a new instruction on the same repo/config."""
    job = await svc.continue_job(job_id, body.instruction)

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


@router.post("/jobs/{job_id}/resume", response_model=JobResponse)
async def resume_job(
    job_id: str,
    body: ResumeJobRequest,
    runtime_service: FromDishka[RuntimeService],
) -> JobResponse:
    """Resume a completed/failed/canceled job in-place with a new instruction."""
    job = await runtime_service.resume_job(job_id, body.instruction)
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
) -> list[LogLinePayload]:
    """Return historical log lines for a job, filtered by minimum severity.

    ``level`` is a *minimum* severity filter (inclusive):
    - ``debug``  → all lines (debug, info, warn, error)
    - ``info``   → info, warn, error
    - ``warn``   → warn, error
    - ``error``  → error only
    """
    _level_order = {"debug": 0, "info": 1, "warn": 2, "error": 3}
    min_priority = _level_order.get(level, 0)
    events = await svc.list_events_by_job(job_id, [DomainEventKind.log_line_emitted], limit=limit)
    return [
        LogLinePayload(
            job_id=event.job_id,
            seq=event.payload.get("seq", 0),
            timestamp=event.payload.get("timestamp", event.timestamp),
            level=event.payload.get("level", "info"),
            message=event.payload.get("message", ""),
            context=event.payload.get("context"),
        )
        for event in events
        if _level_order.get(event.payload.get("level", "info"), 1) >= min_priority
    ]


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


@router.get("/jobs/{job_id}/telemetry")
async def get_job_telemetry(
    job_id: str,
    session: FromDishka[AsyncSession],
) -> dict[str, object]:
    """Get telemetry data for a job run.

    Returns in-memory live telemetry when available.  Falls back to the
    persisted snapshot (written at session end) so metrics are always
    visible even after backend restarts or for archived / terminal jobs.
    """
    from backend.services.telemetry import TelemetryCollector, collector

    tel = collector.get(job_id)

    from backend.persistence.job_repo import JobRepository
    from backend.persistence.metrics_repo import MetricsRepository

    job_repo = JobRepository(session)

    if tel is None:
        # Try loading from the persisted job_metrics snapshot.
        snapshot = await MetricsRepository(session).load_snapshot(job_id)
        if snapshot:
            # Reconstruct a transient JobTelemetry so we can call to_dict()
            # which produces the camelCase API shape.
            temp = TelemetryCollector()
            temp.start_job(job_id)
            temp.restore_from_snapshot(job_id, snapshot)
            temp.end_job(job_id)
            temp_tel = temp.get(job_id)
            if temp_tel is not None:
                job_row = await job_repo.get(job_id)
                sdk = job_row.sdk if job_row else ""
                return {**temp_tel.to_dict(), "sdk": sdk, "available": True}
        return {"jobId": job_id, "available": False}

    job_row = await job_repo.get(job_id)
    sdk = job_row.sdk if job_row else ""
    return {**tel.to_dict(), "sdk": sdk, "available": True}


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
    """Resolve a succeeded job: merge, create PR, discard, or resolve with agent."""
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

    resolution, pr_url, conflict_files_result = await svc.execute_resolve(
        job=job,
        action=body.action,
        merge_service=merge_service,
        event_bus=event_bus,
    )
    await session.commit()

    return ResolveJobResponse(
        resolution=resolution,
        pr_url=pr_url,
        conflict_files=conflict_files_result,
    )


@router.post("/jobs/{job_id}/archive", status_code=204)
async def archive_job(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
    event_bus: FromDishka[EventBus],
) -> None:
    """Archive a completed job (hide from Kanban board)."""
    await svc.archive_job(job_id, event_bus=event_bus)
    await session.commit()


@router.post("/jobs/{job_id}/unarchive", status_code=204)
async def unarchive_job(
    job_id: str,
    svc: FromDishka[JobService],
    session: FromDishka[AsyncSession],
) -> None:
    """Unarchive a job (show on Kanban board again)."""
    await svc.unarchive_job(job_id)
    await session.commit()
