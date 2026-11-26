"""File browsing endpoints."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from backend.models.api_schemas import WorkspaceEntry, WorkspaceEntryType, WorkspaceFileResponse, WorkspaceListResponse
from backend.services.job.job_service import JobService

router = APIRouter(tags=["workspace"], route_class=DishkaRoute)


@router.get("/jobs/{job_id}/workspace", response_model=WorkspaceListResponse)
async def list_workspace(
    job_id: str,
    svc: FromDishka[JobService],
    path: str = "",
    cursor: str | None = Query(None),
    limit: int = Query(200, ge=1, le=200),
) -> WorkspaceListResponse:
    """List files in the job's worktree (max 200 entries per page)."""
    job = await svc.get_job(job_id)

    worktree = Path(job.execution_workspace).resolve()
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


@router.get("/jobs/{job_id}/workspace/file", response_model=WorkspaceFileResponse)
async def get_workspace_file(
    job_id: str,
    svc: FromDishka[JobService],
    path: str = Query(..., description="Relative path within the worktree"),
) -> WorkspaceFileResponse:
    """Get the contents of a single file in the job's worktree."""
    job = await svc.get_job(job_id)

    worktree = Path(job.execution_workspace).resolve()
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

    return WorkspaceFileResponse(path=path, content=content)


@router.get("/jobs/{job_id}/workspace/file/raw", response_class=FileResponse, response_model=None)
async def get_workspace_file_raw(
    job_id: str,
    svc: FromDishka[JobService],
    path: str = Query(..., description="Relative path within the worktree"),
) -> FileResponse:
    """Serve a workspace file with its native content type (for images, PDFs, videos, etc.)."""
    job = await svc.get_job(job_id)

    worktree = Path(job.execution_workspace).resolve()
    file_path = (worktree / path).resolve()

    # Path traversal prevention
    if not file_path.is_relative_to(worktree):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Reject files larger than 50 MB
    max_file_size = 50 * 1024 * 1024
    if file_path.stat().st_size > max_file_size:
        raise HTTPException(status_code=413, detail="File too large to serve")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        media_type=mime_type,
        filename=file_path.name,
    )
