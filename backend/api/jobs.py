"""Job CRUD and control endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["jobs"])


# POST /api/jobs — Create a new job
# GET /api/jobs — List jobs (filterable, paginated)
# GET /api/jobs/{job_id} — Get full job detail
# POST /api/jobs/{job_id}/cancel — Cancel a running or queued job
# POST /api/jobs/{job_id}/rerun — Create a new job from this job's config
# POST /api/jobs/{job_id}/messages — Send an operator message
