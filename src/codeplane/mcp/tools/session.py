"""Session MCP tool - unified session handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


class SessionParams(BaseParams):
    """Parameters for session tool."""

    action: Literal["create", "close", "info"]

    # create params
    task_description: str | None = None

    # close params
    reason: str | None = None

    # Inherited from BaseParams: session_id


@registry.register(
    "session",
    "Session management: create, close, or get info about sessions",
    SessionParams,
)
async def session(ctx: AppContext, params: SessionParams) -> dict[str, Any]:
    """Unified session tool.

    Actions:
    - create: Create a new session
    - close: Close an active session
    - info: Get info about a session
    """
    if params.action == "create":
        sess = ctx.session_manager.get_or_create(params.session_id)
        return {
            "session_id": sess.session_id,
            "created_at": sess.created_at,
            "status": "active",
            "summary": f"session {sess.session_id[:8]} created",
        }

    if params.action == "close":
        if not params.session_id:
            return {
                "error": "session_id is required to close a session",
                "closed": False,
                "summary": "error: session_id required",
            }
        ctx.session_manager.close(params.session_id)
        return {
            "session_id": params.session_id,
            "status": "closed",
            "reason": params.reason,
            "summary": f"session {params.session_id[:8]} closed",
        }

    if params.action == "info":
        if not params.session_id:
            return {"error": "session_id required", "summary": "error: session_id required"}
        info_sess = ctx.session_manager.get(params.session_id)
        if info_sess is None:
            return {
                "error": "Session not found",
                "session_id": params.session_id,
                "summary": "session not found",
            }
        return {
            "session_id": info_sess.session_id,
            "created_at": info_sess.created_at,
            "last_active": info_sess.last_active,
            "fingerprints": info_sess.fingerprints,
            "counters": info_sess.counters,
            "summary": f"session {info_sess.session_id[:8]} active",
        }

    return {"error": f"unknown action: {params.action}", "summary": "error: unknown action"}
