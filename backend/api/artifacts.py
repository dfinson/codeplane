"""Artifact retrieval endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["artifacts"])


# GET /api/jobs/{job_id}/artifacts — List artifacts for a job
# GET /api/artifacts/{artifact_id} — Download artifact file
