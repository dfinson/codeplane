"""Job CRUD and control endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from backend.config import TowerConfig, load_config
from backend.models.api_schemas import (
    ContinueJobRequest,
    CreateJobRequest,
    CreateJobResponse,
    DiffFileModel,
    JobListResponse,
    JobResponse,
    LogLinePayload,
    ResolveJobRequest,
    ResolveJobResponse,
    ResumeJobRequest,
    TranscriptPayload,
)
from backend.models.events import DomainEventKind
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.services.git_service import GitService
from backend.services.job_service import JobNotFoundError, JobService, RepoNotAllowedError, StateConflictError
from backend.services.naming_service import NamingService

if TYPE_CHECKING:
    from backend.services.merge_service import MergeService
    from backend.services.runtime_service import RuntimeService

router = APIRouter(tags=["jobs"])


def _get_config() -> TowerConfig:
    return load_config()


# The session_factory will be injected at app startup via app.state
async def _get_session(
    config: Annotated[TowerConfig, Depends(_get_config)],
) -> AsyncSession:
    """Placeholder — replaced by the real dependency at startup."""
    raise NotImplementedError("Session factory not wired")  # pragma: no cover


def _get_job_service(
    session: Annotated[AsyncSession, Depends(_get_session)],
    config: Annotated[TowerConfig, Depends(_get_config)],
    request: Request,
) -> JobService:
    job_repo = JobRepository(session)
    git_service = GitService(config)
    # Pass naming service so title + branch are generated before worktree creation
    utility = getattr(request.app.state, "utility_session", None)
    naming = NamingService(utility) if utility is not None else None
    return JobService(job_repo=job_repo, git_service=git_service, config=config, naming_service=naming)


def _make_job_service(session: AsyncSession) -> JobService:
    """Create a JobService from a session (no request context needed)."""
    config = load_config()
    job_repo = JobRepository(session)
    git_service = GitService(config)
    return JobService(job_repo=job_repo, git_service=git_service, config=config)


def _get_merge_service(request: Request) -> MergeService | None:
    """Get MergeService from app state (may be None if not configured)."""
    return getattr(request.app.state, "merge_service", None)


def _job_to_response(job: object) -> JobResponse:
    """Map a domain Job to a JobResponse."""
    from backend.models.domain import Job  # noqa: TC001

    j: Job = job  # type: ignore[assignment]
    return JobResponse(
        id=j.id,
        repo=j.repo,
        prompt=j.prompt,
        title=j.title,
        state=j.state,
        strategy=j.strategy,
        base_ref=j.base_ref,
        worktree_path=j.worktree_path,
        branch=j.branch,
        created_at=j.created_at,
        updated_at=j.updated_at,
        completed_at=j.completed_at,
        pr_url=j.pr_url,
        merge_status=j.merge_status,
        resolution=j.resolution,
        completion_strategy=j.completion_strategy,
        failure_reason=j.failure_reason,
        model=j.model,
    )


@router.post("/jobs", response_model=CreateJobResponse, status_code=201)
async def create_job(
    body: CreateJobRequest,
    svc: Annotated[JobService, Depends(_get_job_service)],
    session: Annotated[AsyncSession, Depends(_get_session)],
    request: Request,
) -> CreateJobResponse:
    """Create a new job."""
    try:
        job = await svc.create_job(
            repo=body.repo,
            prompt=body.prompt,
            base_ref=body.base_ref,
            branch=body.branch,
            strategy=body.strategy or "single_agent",
            permission_mode=body.permission_mode or "auto",
            model=body.model,
        )
    except RepoNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Commit so the job row is visible to RuntimeService (separate session)
    await session.commit()

    # Hand off to RuntimeService for execution / queueing (skip if already failed)
    if job.state != "failed":
        runtime: RuntimeService = request.app.state.runtime_service
        await runtime.start_or_enqueue(
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
        created_at=job.created_at,
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    svc: Annotated[JobService, Depends(_get_job_service)],
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
    return JobListResponse(
        items=[_job_to_response(j) for j in jobs],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    svc: Annotated[JobService, Depends(_get_job_service)],
) -> JobResponse:
    """Get full job detail."""
    try:
        job = await svc.get_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _job_to_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    svc: Annotated[JobService, Depends(_get_job_service)],
    request: Request,
) -> JobResponse:
    """Cancel a running or queued job."""
    try:
        job = await svc.cancel_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Also cancel the runtime task if running
    runtime: RuntimeService = request.app.state.runtime_service
    await runtime.cancel(job_id)

    return _job_to_response(job)


@router.post("/jobs/{job_id}/rerun", response_model=CreateJobResponse, status_code=201)
async def rerun_job(
    job_id: str,
    svc: Annotated[JobService, Depends(_get_job_service)],
    session: Annotated[AsyncSession, Depends(_get_session)],
    request: Request,
) -> CreateJobResponse:
    """Create a new job from an existing job's configuration."""
    try:
        job = await svc.rerun_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepoNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()

    if job.state != "failed":
        runtime: RuntimeService = request.app.state.runtime_service
        await runtime.start_or_enqueue(job)
        job = await svc.get_job(job.id)

    return CreateJobResponse(
        id=job.id,
        state=job.state,
        title=job.title,
        branch=job.branch,
        worktree_path=job.worktree_path,
        created_at=job.created_at,
    )


@router.post("/jobs/{job_id}/pause", status_code=204)
async def pause_job(
    job_id: str,
    svc: Annotated[JobService, Depends(_get_job_service)],
    request: Request,
) -> None:
    """Send a silent pause instruction to the agent of a running job."""
    try:
        await svc.get_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runtime: RuntimeService = request.app.state.runtime_service
    sent = await runtime.pause_job(job_id)
    if not sent:
        raise HTTPException(status_code=409, detail="Job is not currently running")


@router.post("/jobs/{job_id}/continue", response_model=CreateJobResponse, status_code=201)
async def continue_job(
    job_id: str,
    body: ContinueJobRequest,
    svc: Annotated[JobService, Depends(_get_job_service)],
    session: Annotated[AsyncSession, Depends(_get_session)],
    request: Request,
) -> CreateJobResponse:
    """Create a follow-up job with a new instruction on the same repo/config."""
    try:
        job = await svc.continue_job(job_id, body.instruction)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepoNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()

    if job.state != "failed":
        runtime: RuntimeService = request.app.state.runtime_service
        await runtime.start_or_enqueue(job)
        job = await svc.get_job(job.id)

    return CreateJobResponse(
        id=job.id,
        state=job.state,
        branch=job.branch,
        worktree_path=job.worktree_path,
        created_at=job.created_at,
    )


@router.post("/jobs/{job_id}/resume", response_model=JobResponse)
async def resume_job(
    job_id: str,
    body: ResumeJobRequest,
    request: Request,
) -> JobResponse:
    """Resume a completed/failed/canceled job in-place with a new instruction."""
    runtime: RuntimeService = request.app.state.runtime_service
    try:
        job = await runtime.resume_job(job_id, body.instruction)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_to_response(job)


@router.get("/models")
async def list_models() -> list[dict[str, object]]:
    """List available models from the Copilot SDK."""
    try:
        from copilot import CopilotClient

        client = CopilotClient()
        await client.start()
        models = await client.list_models()
        await client.stop()
        return [m.to_dict() for m in models]
    except Exception as exc:
        import structlog

        structlog.get_logger().warning("list_models_failed", error=str(exc))
        return []


@router.get("/jobs/{job_id}/logs", response_model=list[LogLinePayload])
async def get_job_logs(
    job_id: str,
    session: Annotated[AsyncSession, Depends(_get_session)],
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
    event_repo = EventRepository(session)
    events = await event_repo.list_by_job(job_id, [DomainEventKind.log_line_emitted], limit=limit)
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
    session: Annotated[AsyncSession, Depends(_get_session)],
) -> list[DiffFileModel]:
    """Return the most recent diff snapshot for a job from the event store."""
    event_repo = EventRepository(session)
    events = await event_repo.list_by_job(job_id, [DomainEventKind.diff_updated])
    if not events:
        return []
    raw_files = events[-1].payload.get("changed_files", [])
    return [DiffFileModel.model_validate(f) for f in raw_files]


@router.get("/jobs/{job_id}/transcript", response_model=list[TranscriptPayload])
async def get_job_transcript(
    job_id: str,
    session: Annotated[AsyncSession, Depends(_get_session)],
    limit: Annotated[int, Query(ge=1, le=5000)] = 2000,
) -> list[TranscriptPayload]:
    """Return historical transcript entries for a job from the event store."""
    event_repo = EventRepository(session)
    events = await event_repo.list_by_job(job_id, [DomainEventKind.transcript_updated], limit=limit)
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
        )
        for event in events
    ]


@router.get("/jobs/{job_id}/telemetry")
async def get_job_telemetry(job_id: str) -> dict[str, object]:
    """Get telemetry data for a job run."""
    from backend.services.telemetry import collector

    tel = collector.get(job_id)
    if tel is None:
        return {"jobId": job_id, "available": False}
    return {**tel.to_dict(), "available": True}


@router.post("/jobs/{job_id}/resolve")
async def resolve_job(
    job_id: str,
    body: ResolveJobRequest,
    session: Annotated[AsyncSession, Depends(_get_session)],
    request: Request,
) -> ResolveJobResponse:
    """Resolve a succeeded job: merge, create PR, or discard."""
    svc = _make_job_service(session)
    try:
        job = await svc.resolve_job(job_id, body.action)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    merge_service: MergeService | None = getattr(request.app.state, "merge_service", None)
    if merge_service is None:
        raise HTTPException(status_code=503, detail="Merge service not configured")

    result = await merge_service.resolve_job(
        job_id=job.id,
        action=body.action,
        repo_path=job.repo,
        worktree_path=job.worktree_path,
        branch=job.branch,
        base_ref=job.base_ref,
        prompt=job.prompt,
    )

    # Determine the resolution status
    if result.status == "merged":
        resolution = "merged"
    elif result.status == "pr_created":
        resolution = "pr_created"
    elif result.status == "discarded":
        resolution = "discarded"
    elif result.status == "conflict":
        resolution = "conflict"
    else:
        resolution = "unresolved"

    # Persist resolution
    repo = JobRepository(session)
    await repo.update_resolution(job_id, resolution, pr_url=result.pr_url)
    await session.commit()

    # Publish event
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        import uuid
        from datetime import UTC, datetime

        from backend.models.events import DomainEvent, DomainEventKind

        payload: dict[str, Any] = {"resolution": resolution}
        if result.pr_url:
            payload["pr_url"] = result.pr_url
        if result.conflict_files:
            payload["conflict_files"] = result.conflict_files

        await event_bus.publish(
            DomainEvent(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.job_resolved,
                payload=payload,
            )
        )

    return ResolveJobResponse(
        resolution=resolution,
        pr_url=result.pr_url,
        conflict_files=result.conflict_files,
    )


@router.post("/jobs/{job_id}/archive", status_code=204)
async def archive_job(
    job_id: str,
    session: Annotated[AsyncSession, Depends(_get_session)],
    request: Request,
) -> None:
    """Archive a completed job (hide from Kanban board)."""
    svc = _make_job_service(session)
    try:
        await svc.archive_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()

    # Publish event
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        import uuid
        from datetime import UTC, datetime

        from backend.models.events import DomainEvent, DomainEventKind

        await event_bus.publish(
            DomainEvent(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.job_archived,
                payload={},
            )
        )


@router.post("/jobs/{job_id}/unarchive", status_code=204)
async def unarchive_job(
    job_id: str,
    session: Annotated[AsyncSession, Depends(_get_session)],
) -> None:
    """Unarchive a job (show on Kanban board again)."""
    svc = _make_job_service(session)
    try:
        await svc.unarchive_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
