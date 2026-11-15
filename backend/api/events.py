"""SSE streaming endpoint."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.services.events.sse_manager import SSEConnection, SSEManager

log = structlog.get_logger()

router = APIRouter(tags=["events"], route_class=DishkaRoute)


@router.get("/events", response_model=None, response_class=StreamingResponse)
async def stream_events(
    request: Request,
    sse_manager: FromDishka[SSEManager],
    session_factory: FromDishka[async_sessionmaker],  # type: ignore[type-arg]  # dishka DI resolves the full parameterized type at runtime
    job_id: str | None = Query(default=None),
    last_event_id: str | None = Query(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE stream for live events.

    Optional ``job_id`` query param scopes the stream to a single job.
    ``Last-Event-ID`` (header or query) enables reconnection replay.
    """
    from backend.services.auth.middleware import is_request_authenticated

    if not is_request_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
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
                    await sse_manager.replay_from_factory(
                        conn,
                        session_factory,
                        numeric_id,
                    )
                except (ValueError, TypeError):
                    log.warning(
                        "sse_replay_invalid_last_event_id",
                        last_event_id=header_last_id,
                        exc_info=True,
                    )

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
                    log.debug(
                        "sse_client_disconnected",
                        job_id=job_id,
                    )
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
