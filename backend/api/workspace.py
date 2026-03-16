"""File browsing endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.config import CPLConfig, load_config
from backend.models.api_schemas import WorkspaceEntry, WorkspaceEntryType, WorkspaceListResponse
from backend.persistence.job_repo import JobRepository
from backend.services.job_service import JobNotFoundError, JobService

router = APIRouter(tags=["workspace"])


def _get_config() -> CPLConfig:
    return load_config()


@router.get("/jobs/{job_id}/workspace", response_model=WorkspaceListResponse)
async def list_workspace(
    request: Request,
    job_id: str,
    config: Annotated[CPLConfig, Depends(_get_config)],
    path: str = "",
    cursor: str | None = Query(None),
    limit: int = Query(200, ge=1, le=200),
) -> WorkspaceListResponse:
    """List files in the job's worktree (max 200 entries per page)."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        job_svc = JobService(
            job_repo=JobRepository(session),
            git_service=None,  # type: ignore[arg-type]
            config=config,
        )
        try:
            job = await job_svc.get_job(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    worktree = Path(job.worktree_path or job.repo).resolve()
    if not worktree.is_dir():
        raise HTTPException(status_code=404, detail="Worktree not found")

    # Resolve requested subdirectory
    target = (worktree / path).resolve()
    # Path traversal prevention
    if not target.is_relative_to(worktree):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    # Collect entries
    entries: list[WorkspaceEntry] = []
    try:
        sorted_items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        sorted_items = []

    # Apply cursor-based pagination (cursor = last path seen)
    skip = cursor is not None
    for item in sorted_items:
        rel = str(item.relative_to(worktree))
        if skip:
            if rel == cursor:
                skip = False
            continue
        # Skip hidden files/directories starting with .
        if item.name.startswith("."):
            continue
        entry_type = WorkspaceEntryType.directory if item.is_dir() else WorkspaceEntryType.file
        size = item.stat().st_size if item.is_file() else None
        entries.append(WorkspaceEntry(path=rel, type=entry_type, size_bytes=size))
        if len(entries) >= limit:
            break

    has_more = len(entries) == limit
    next_cursor = entries[-1].path if has_more else None
    return WorkspaceListResponse(items=entries, cursor=next_cursor, has_more=has_more)


@router.get("/jobs/{job_id}/workspace/file")
async def get_workspace_file(
    request: Request,
    job_id: str,
    config: Annotated[CPLConfig, Depends(_get_config)],
    path: str = Query(..., description="Relative path within the worktree"),
) -> dict[str, str]:
    """Get the contents of a single file in the job's worktree."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        job_svc = JobService(
            job_repo=JobRepository(session),
            git_service=None,  # type: ignore[arg-type]
            config=config,
        )
        try:
            job = await job_svc.get_job(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    worktree = Path(job.worktree_path or job.repo).resolve()
    file_path = (worktree / path).resolve()

    # Path traversal prevention
    if not file_path.is_relative_to(worktree):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Reject files larger than 5 MB to avoid memory exhaustion
    max_file_size = 5 * 1024 * 1024
    if file_path.stat().st_size > max_file_size:
        raise HTTPException(status_code=413, detail="File too large to preview")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError) as exc:
        raise HTTPException(status_code=403, detail="Cannot read file") from exc

    return {"path": path, "content": content}
