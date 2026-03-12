"""File browsing endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["workspace"])


# GET /api/jobs/{job_id}/workspace — List files in job's worktree
# GET /api/jobs/{job_id}/workspace/file — Get file contents (?path=relative/path)
