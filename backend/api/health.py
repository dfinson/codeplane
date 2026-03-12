"""Health check endpoint."""

from __future__ import annotations

import time

from fastapi import APIRouter

from backend.models.api_schemas import HealthResponse, HealthStatus

router = APIRouter(tags=["health"])

_start_time = time.monotonic()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service health and status."""
    return HealthResponse(
        status=HealthStatus.healthy,
        version="0.1.0",
        uptime_seconds=round(time.monotonic() - _start_time, 1),
        active_jobs=0,
        queued_jobs=0,
    )
