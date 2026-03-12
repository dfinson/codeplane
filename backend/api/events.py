"""SSE streaming endpoint."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query, Request

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
from starlette.responses import StreamingResponse

from backend.services.sse_manager import SSEConnection

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream_events(
    request: Request,
    job_id: str | None = Query(default=None),
    last_event_id: str | None = Query(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE stream for live events.

    Optional ``job_id`` query param scopes the stream to a single job.
    ``Last-Event-ID`` (header or query) enables reconnection replay.
    """
    sse_manager = request.app.state.sse_manager
    event_repo = request.app.state.event_repo_factory
    job_repo = request.app.state.job_repo_factory

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
                    session = await event_repo()
                    job_session = await job_repo()
                    await sse_manager.replay_events(conn, session, job_session, numeric_id)
                except (ValueError, TypeError):
                    pass  # invalid Last-Event-ID, skip replay

            while not conn.closed:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(conn.queue.get(), timeout=15.0)
                    yield data
                except TimeoutError:
                    # Send SSE keep-alive comment
                    yield ": keepalive\n\n"
        finally:
            sse_manager.unregister(conn)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
