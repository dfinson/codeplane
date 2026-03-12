"""SSE streaming endpoint."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["events"])


# GET /api/events — SSE stream for all jobs
# GET /api/events?job_id={id} — SSE stream scoped to one job
