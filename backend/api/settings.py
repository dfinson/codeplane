"""Settings management endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["settings"])


# GET /api/settings/global — Get current global config
# PUT /api/settings/global — Update global config
# GET /api/settings/repos — List repo configs
# POST /api/settings/cleanup-worktrees — Clean up completed job worktrees
