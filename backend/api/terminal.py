"""Terminal REST + WebSocket API routes."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.models.api_schemas import (
    CreateTerminalSessionRequest,
    CreateTerminalSessionResponse,
    TerminalAskRequest,
    TerminalAskResponse,
    TerminalSessionInfo,
    TerminalSessionListResponse,
)
from backend.services.auth.middleware import LOCALHOST_ADDRS, check_websocket_auth
from backend.services.sidecar.session import SidecarSessionManager
from backend.services.terminal.terminal_service import TerminalService

log = structlog.get_logger()

router = APIRouter(tags=["terminal"], route_class=DishkaRoute)


def _require_svc(svc: TerminalService | None) -> TerminalService:
    """Raise 503 when the terminal subsystem is disabled."""
    if svc is None:
        raise HTTPException(status_code=503, detail="Terminal service not enabled")
    return svc


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


@router.post("/terminal/sessions", response_model=CreateTerminalSessionResponse, status_code=201)
async def create_session(
    req: CreateTerminalSessionRequest,
    svc: FromDishka[TerminalService],
) -> CreateTerminalSessionResponse:
    """Create a new terminal session."""
    svc = _require_svc(svc)
    try:
        session = svc.create_session(
            cwd=req.cwd,
            shell=req.shell,
            job_id=req.job_id,
            prompt_label=req.prompt_label,
        )
    except (RuntimeError, ValueError) as exc:
        log.warning("terminal_create_failed", exc_info=exc)
        raise HTTPException(status_code=400, detail="Failed to create terminal session") from exc
    return CreateTerminalSessionResponse(
        id=session.id,
        shell=session.shell,
        cwd=session.cwd,
        job_id=session.job_id,
        pid=session.process.pid,
    )


@router.get("/terminal/sessions", response_model=TerminalSessionListResponse)
def list_sessions(svc: FromDishka[TerminalService]) -> TerminalSessionListResponse:
    """List all active terminal sessions."""
    svc = _require_svc(svc)
    sessions = svc.list_sessions()
    return TerminalSessionListResponse(items=[TerminalSessionInfo(**s) for s in sessions])


@router.delete("/terminal/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, svc: FromDishka[TerminalService]) -> None:
    """Kill a terminal session."""
    svc = _require_svc(svc)
    killed = await svc.kill_session(session_id)
    if not killed:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/terminal/ask", response_model=TerminalAskResponse)
async def ask_ai(
    req: TerminalAskRequest,
    sidecar_sessions: FromDishka[SidecarSessionManager],
) -> TerminalAskResponse:
    """Translate natural language to a shell command using the utility model."""
    try:
        if sidecar_sessions is None:
            return TerminalAskResponse(command="", explanation="AI assistant not available")

        prompt = f"""Translate this natural language request into a single shell command.
Respond with ONLY valid JSON: {{"command": "...", "explanation": "..."}}

The explanation should be one short sentence describing what the command does.

Terminal context (recent output):
{req.context or "(none)"}

User request: {req.prompt}"""

        result = await sidecar_sessions.complete(prompt, timeout=10.0)
        try:
            parsed = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
            return TerminalAskResponse(command=parsed["command"], explanation=parsed.get("explanation", ""))
        except (json.JSONDecodeError, KeyError):
            log.warning(
                "terminal_ask_parse_failed",
                raw_result=result,
                prompt=req.prompt,
                exc_info=True,
            )
            return TerminalAskResponse(command=result.strip(), explanation="")
    except Exception as exc:
        log.warning("terminal_ask_failed", error=str(exc))
        return TerminalAskResponse(command="", explanation=f"Error: {exc}")


# ------------------------------------------------------------------
# WebSocket endpoint
# ------------------------------------------------------------------


@router.websocket("/terminal/ws")
async def terminal_ws(ws: WebSocket) -> None:
    """Bidirectional terminal I/O over WebSocket.

    Protocol:
        Client → Server:
            { "type": "attach", "sessionId": "..." }
            { "type": "input", "data": "..." }
            { "type": "resize", "cols": N, "rows": N }
            { "type": "detach" }

        Server → Client:
            { "type": "attached", "sessionId": "..." }
            { "type": "output", "data": "..." }
            { "type": "exit", "code": N }
            { "type": "error", "message": "..." }
    """
    client_host = ws.client.host if ws.client else None

    # --- Origin validation ---
    # Reject cross-origin WebSocket connections to prevent malicious pages from
    # connecting to a local CodePlane instance.
    origin = ws.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        origin_host = parsed.hostname or ""
        if origin_host not in LOCALHOST_ADDRS:
            from backend.app_factory import get_allowed_ws_origins

            if origin not in get_allowed_ws_origins():
                log.warning("terminal_ws_origin_rejected", origin=origin, client=client_host)
                await ws.close(code=1008, reason="Origin not allowed")
                return

    if not check_websocket_auth(
        client_host=client_host,
        cookies=ws.cookies,
        cf_access_jwt=ws.headers.get("cf-access-jwt-assertion"),
        policy=getattr(ws.app.state, "auth_policy", None),
    ):
        await ws.close(code=1008, reason="Authentication required")
        return

    await ws.accept()
    container = ws.app.state.dishka_container
    svc = _require_svc(await container.get(TerminalService))
    attached_session_id: str | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            msg_type = msg.get("type")

            if msg_type == "attach":
                session_id = msg.get("sessionId", "")
                session = svc.get_session(session_id)
                if session is None:
                    await ws.send_text(json.dumps({"type": "error", "message": "Session not found"}))
                    continue

                # Detach from previous session if any
                if attached_session_id:
                    prev = svc.get_session(attached_session_id)
                    if prev:
                        prev.clients.discard(ws)

                # Attach to new session
                session.clients.add(ws)
                attached_session_id = session_id

                # Send scrollback replay
                scrollback = svc.get_scrollback(session_id)
                if scrollback:
                    await ws.send_text(json.dumps({"type": "output", "data": scrollback}))

                await ws.send_text(json.dumps({"type": "attached", "sessionId": session_id}))
                log.debug("terminal_ws_attached", session_id=session_id)

            elif msg_type == "input":
                if attached_session_id:
                    data = msg.get("data", "")
                    if isinstance(data, str):
                        input_bytes = data.encode("utf-8")
                        svc.write(attached_session_id, input_bytes)

            elif msg_type == "resize":
                if attached_session_id:
                    cols = msg.get("cols", 120)
                    rows = msg.get("rows", 30)
                    if isinstance(cols, int) and isinstance(rows, int) and 0 < cols <= 500 and 0 < rows <= 200:
                        svc.resize(attached_session_id, cols, rows)

            elif msg_type == "detach" and attached_session_id:
                session = svc.get_session(attached_session_id)
                if session:
                    session.clients.discard(ws)
                attached_session_id = None

    except WebSocketDisconnect:
        log.debug("terminal_ws_disconnected", session_id=attached_session_id)
    except Exception:
        log.warning("terminal_ws_error", session_id=attached_session_id, exc_info=True)
    finally:
        # Clean up on disconnect
        if attached_session_id:
            session = svc.get_session(attached_session_id)
            if session:
                session.clients.discard(ws)
