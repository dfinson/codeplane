"""Job sharing endpoints — generate and consume share tokens."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse

from backend.models.api_schemas import (
    JobResponse,
    JobSnapshotResponse,
    JobTelemetryResponse,
    ShareTokenResponse,
)
from backend.persistence.approval_repo import ApprovalRepository
from backend.services.analytics.telemetry_query_service import TelemetryQueryService
from backend.services.artifacts.diff_service import DiffService
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.services.job.job_service import JobService
from backend.services.sharing.share_service import InvalidShareTokenError, ShareService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

log = structlog.get_logger()

router = APIRouter(tags=["sharing"], route_class=DishkaRoute)


# ---------------------------------------------------------------------------
# Create a share link
# ---------------------------------------------------------------------------


@router.post("/jobs/{job_id}/share", response_model=ShareTokenResponse)
async def create_share_link(
    job_id: str,
    request: Request,
    share_service: FromDishka[ShareService],
    svc: FromDishka[JobService],
) -> ShareTokenResponse:
    """Generate a read-only share URL for a job."""
    # Ensure the job exists
    await svc.get_job(job_id)
    entry = await share_service.create_token(job_id)
    base = str(request.base_url).rstrip("/")
    return ShareTokenResponse(
        token=entry.token,
        job_id=job_id,
        url=f"{base}/shared/{entry.token}",
    )


@router.delete("/jobs/{job_id}/share/{token}", status_code=204, response_model=None)
async def revoke_share_link(
    job_id: str,
    token: str,
    share_service: FromDishka[ShareService],
) -> None:
    """Revoke this job's share link. Requires operator authentication; idempotent."""
    await share_service.revoke(token, job_id)


# ---------------------------------------------------------------------------
# Consume a share link — read-only job detail
# ---------------------------------------------------------------------------


@router.get("/share/{token}/job", response_model=JobResponse)
async def get_shared_job(
    token: str,
    share_service: FromDishka[ShareService],
    svc: FromDishka[JobService],
) -> JobResponse:
    """Read-only job detail via share token."""
    job_id = await share_service.validate(token)
    job = await svc.get_job(job_id)
    progress = await svc.get_latest_progress_preview(job_id)

    # Import here to avoid circular dependency
    from backend.api.jobs import job_to_response

    return job_to_response(job, progress)


# ---------------------------------------------------------------------------
# Shared SSE stream — read-only events for the shared job
# ---------------------------------------------------------------------------


@router.get("/share/{token}/events", response_model=None, response_class=StreamingResponse)
async def stream_shared_events(
    token: str,
    request: Request,
    share_service: FromDishka[ShareService],
    sse_manager: FromDishka[SSEManager],
    session_factory: FromDishka[async_sessionmaker],  # type: ignore[type-arg]  # dishka DI resolves the full parameterized type at runtime
) -> StreamingResponse:
    """SSE stream scoped to a shared job (read-only)."""
    job_id = await share_service.validate(token)

    header_last_id = request.headers.get("Last-Event-ID")
    conn = SSEConnection(job_id=job_id)
    sse_manager.register(conn)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            if header_last_id is not None:
                try:
                    numeric_id = int(header_last_id)
                    await sse_manager.replay_from_factory(conn, session_factory, numeric_id)
                except (ValueError, TypeError):
                    log.debug("sse_last_id_parse_failed", header_last_id=header_last_id)

            await share_service.validate(token)
            yield "event: session_heartbeat\ndata: {}\n\n"

            while not conn.closed:
                try:
                    data = await asyncio.wait_for(conn.queue.get(), timeout=5.0)
                except TimeoutError:
                    data = "event: session_heartbeat\ndata: {}\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
                # Recheck after waiting, including replayed events, before disclosing data.
                await share_service.validate(token)
                yield data
        except InvalidShareTokenError:
            return
        finally:
            sse_manager.unregister(conn)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Full snapshot — everything the shared UI needs in one request
# ---------------------------------------------------------------------------


@router.get("/share/{token}/snapshot", response_model=JobSnapshotResponse)
async def get_shared_snapshot(
    token: str,
    share_service: FromDishka[ShareService],
    svc: FromDishka[JobService],
    approval_repo: FromDishka[ApprovalRepository],
    diff_service: FromDishka[DiffService],
    session_factory: FromDishka[async_sessionmaker[AsyncSession]],
) -> JobSnapshotResponse:
    """Full state hydration via share token — same shape as /jobs/{id}/snapshot."""
    from backend.api.jobs import job_to_response, resolve_tool_display, resolve_tool_display_full
    from backend.services.artifacts.snapshot_helpers import assemble_snapshot

    job_id = await share_service.validate(token)
    job = await svc.get_job(job_id)
    progress_preview = await svc.get_latest_progress_preview(job_id)

    return await assemble_snapshot(
        job=job,
        progress_preview=progress_preview,
        svc=svc,
        diff_service=diff_service,
        approval_repo=approval_repo,
        resolve_display=resolve_tool_display,
        resolve_display_full=resolve_tool_display_full,
        job_to_response=job_to_response,
        filter_transcript_deltas=False,
        detect_plan_generations=False,
        exclude_pending_steps=True,
        deduplicate_turn_summaries=False,
        session_factory=session_factory,
    )


# ---------------------------------------------------------------------------
# Shared telemetry — read-only metrics
# ---------------------------------------------------------------------------


@router.get("/share/{token}/telemetry", response_model=JobTelemetryResponse)
async def get_shared_telemetry(
    token: str,
    share_service: FromDishka[ShareService],
    telemetry_svc: FromDishka[TelemetryQueryService],
) -> JobTelemetryResponse:
    """Read-only telemetry data via share token."""
    job_id = await share_service.validate(token)
    return await telemetry_svc.get_telemetry(job_id)
