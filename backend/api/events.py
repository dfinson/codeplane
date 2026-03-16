"""SSE streaming endpoint."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query, Request

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
from starlette.responses import JSONResponse, StreamingResponse

from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.services.sse_manager import SSEConnection

router = APIRouter(tags=["events"])


@router.get("/events", response_model=None)
async def stream_events(
    request: Request,
    job_id: str | None = Query(default=None),
    last_event_id: str | None = Query(default=None, alias="Last-Event-ID"),
) -> StreamingResponse | JSONResponse:
    """SSE stream for live events.

    Optional ``job_id`` query param scopes the stream to a single job.
    ``Last-Event-ID`` (header or query) enables reconnection replay.
    """
    try:
        sse_manager = request.app.state.sse_manager
        session_factory = request.app.state.session_factory
    except AttributeError:
        return JSONResponse(
            status_code=503,
            content={"detail": "SSE infrastructure not ready"},
        )

    # Also check the standard SSE header
    header_last_id = request.headers.get("Last-Event-ID") or last_event_id

    conn = SSEConnection(job_id=job_id)
    sse_manager.register(conn)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Handle reconnection replay
            if header_last_id is not None:
                try:
                    numeric_id = int(header_last_id)
                    async with session_factory() as session:
                        event_repo = EventRepository(session)
                        job_repo = JobRepository(session)
                        approval_repo = ApprovalRepository(session)
                        await sse_manager.replay_events(
                            conn,
                            event_repo,
                            job_repo,
                            numeric_id,
                            approval_repo=approval_repo,
                        )
                except (ValueError, TypeError):
                    pass  # invalid Last-Event-ID, skip replay

            # Send immediate heartbeat so the connection is established
            # and proxies see data flowing immediately.
            yield "event: session_heartbeat\ndata: {}\n\n"

            while not conn.closed:
                try:
                    data = await asyncio.wait_for(conn.queue.get(), timeout=5.0)
                    yield data
                except TimeoutError:
                    # Send a real SSE event as heartbeat — SSE comments
                    # (: keepalive) are invisible to HTTP/2 proxies and
                    # don't prevent idle stream timeouts.
                    yield "event: session_heartbeat\ndata: {}\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
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
