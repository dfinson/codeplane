"""Session MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


class SessionCreateParams(BaseParams):
    """Parameters for session.create."""

    task_description: str | None = None
    session_id: str | None = None


class SessionCloseParams(BaseParams):
    """Parameters for session.close."""

    reason: str | None = None
    session_id: str | None = None


class SessionInfoParams(BaseParams):
    """Parameters for session.info."""

    session_id: str | None = None


@registry.register("session_create", "Create a new session", SessionCreateParams)
async def session_create(ctx: AppContext, params: SessionCreateParams) -> dict[str, Any]:
    """Create a new session."""
    # If session_id is provided, try to use it
    session = ctx.session_manager.get_or_create(params.session_id)

    # In a real implementation we'd store the task description
    if params.task_description:
        # TODO: Store task info in session state
        pass

    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "status": "active",
        "summary": f"session {session.session_id[:8]} created",
    }


@registry.register("session_close", "Close a session", SessionCloseParams)
async def session_close(ctx: AppContext, params: SessionCloseParams) -> dict[str, Any]:
    """Close an active session."""
    # If no session_id provided, the wrapper logic in server.py might have
    # created/fetched a session. We need to know WHICH session to close.
    # The 'params' object passed here works, but we also rely on server.py
    # to have bound a session.

    sid = params.session_id
    # If explicit ID not provided, how do we know the "implicit" one?
    # server.py injects it into the response, but doesn't inject it into params
    # unless we mod server.py to do so.

    # For now, require explicit session_id OR rely on the one in params
    # (server.py checks params.session_id, it doesn't inject it if missing).

    # If the user didn't provide it, we can't close "the current session"
    # easily without context injection.
    # BUT, server.py calls get_or_create(session_id). If session_id is None, it makes a new one.
    # So `session_close` without an ID would close a *brand new* session, which is useless.
    # So effectively, session_id is required here or we need a way to get the "current" one
    # if we were tracking connection-based sessions (which we aren't yet).

    if not sid:
        return {
            "error": "session_id is required to close a session",
            "closed": False,
            "summary": "error: session_id required",
        }

    ctx.session_manager.close(sid)
    return {
        "session_id": sid,
        "status": "closed",
        "reason": params.reason,
        "summary": f"session {sid[:8]} closed",
    }


@registry.register("session_info", "Get session status", SessionInfoParams)
async def session_info(ctx: AppContext, params: SessionInfoParams) -> dict[str, Any]:
    """Get info about a session."""
    if not params.session_id:
        return {"error": "session_id required", "summary": "error: session_id required"}

    session = ctx.session_manager.get(params.session_id)
    if not session:
        return {
            "error": "Session not found",
            "session_id": params.session_id,
            "summary": "session not found",
        }

    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "last_active": session.last_active,
        "fingerprints": session.fingerprints,
        "counters": session.counters,
        "summary": f"session {session.session_id[:8]} active",
    }
