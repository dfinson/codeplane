"""Claude CLI Stop hook endpoint.

The only hook CodePlane registers with Claude CLI is the Stop hook.
Claude POSTs synchronously on every Stop event. The response can include
operator messages queued by CodePlane's supervision UI.

All session data ingestion is handled by ClaudeSessionStateWatcher via
file-tailing — hooks are NOT used for data ingestion.
"""

from __future__ import annotations

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.services.ingest.claude_source import ClaudeSessionStateWatcher

router = APIRouter(tags=["hooks"], route_class=DishkaRoute)


@router.post("/hooks/claude", response_model=None)
async def claude_stop_hook(
    request: Request,
    watcher: FromDishka[ClaudeSessionStateWatcher],
) -> JSONResponse:
    """Receive Claude CLI Stop hook. Returns pending operator messages.

    Claude CLI calls this synchronously on every Stop event. If there are
    queued operator messages, they are returned in the response body which
    Claude displays to the agent.
    """
    body = await request.json()
    from backend.config import load_config
    from backend.services.adapters.integration_policy import IntegrationPolicy

    if not IntegrationPolicy(load_config()).enabled("claude"):
        return JSONResponse(status_code=403, content={"detail": "Claude integration is disabled by policy"})
    session_id = body.get("session_id", "")

    if not session_id:
        return JSONResponse(content={})

    messages = watcher.get_pending_messages(session_id)
    if not messages:
        return JSONResponse(content={})

    # Return messages as the stop hook response — Claude injects these
    # into the agent's context as operator feedback
    return JSONResponse(
        content={
            "decision": "block",
            "reason": "\n\n".join(messages),
        }
    )
