"""Job CRUD and control endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from backend.config import TowerConfig, load_config
from backend.models.api_schemas import (
    CreateJobRequest,
    CreateJobResponse,
    JobListResponse,
    JobResponse,
)
from backend.persistence.job_repo import JobRepository
from backend.services.git_service import GitService
from backend.services.job_service import JobNotFoundError, JobService, RepoNotAllowedError, StateConflictError

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
) -> JobService:
    job_repo = JobRepository(session)
    git_service = GitService(config)
    return JobService(job_repo=job_repo, git_service=git_service, config=config)


def _job_to_response(job: object) -> JobResponse:
    """Map a domain Job to a JobResponse."""
    from backend.models.domain import Job  # noqa: TC001

    j: Job = job  # type: ignore[assignment]
    return JobResponse(
        id=j.id,
        repo=j.repo,
        prompt=j.prompt,
        state=j.state,
        strategy=j.strategy,
        base_ref=j.base_ref,
        worktree_path=j.worktree_path,
        branch=j.branch,
        created_at=j.created_at,
        updated_at=j.updated_at,
        completed_at=j.completed_at,
    )


@router.post("/jobs", response_model=CreateJobResponse, status_code=201)
async def create_job(
    body: CreateJobRequest,
    svc: Annotated[JobService, Depends(_get_job_service)],
) -> CreateJobResponse:
    """Create a new job."""
    try:
        job = await svc.create_job(
            repo=body.repo,
            prompt=body.prompt,
            base_ref=body.base_ref,
            branch=body.branch,
            strategy=body.strategy or "single_agent",
        )
    except RepoNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreateJobResponse(
        id=job.id,
        state=job.state,
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
) -> JobListResponse:
    """List jobs with optional state filter and cursor pagination."""
    jobs, next_cursor, has_more = await svc.list_jobs(
        state=state,
        limit=limit,
        cursor=cursor,
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
) -> JobResponse:
    """Cancel a running or queued job."""
    try:
        job = await svc.cancel_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_to_response(job)


@router.post("/jobs/{job_id}/rerun", response_model=CreateJobResponse, status_code=201)
async def rerun_job(
    job_id: str,
    svc: Annotated[JobService, Depends(_get_job_service)],
) -> CreateJobResponse:
    """Create a new job from an existing job's configuration."""
    try:
        job = await svc.rerun_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepoNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreateJobResponse(
        id=job.id,
        state=job.state,
        branch=job.branch,
        worktree_path=job.worktree_path,
        created_at=job.created_at,
    )
