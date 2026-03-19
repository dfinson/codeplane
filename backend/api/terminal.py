"""Terminal REST + WebSocket API routes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.models.api_schemas import (
    CreateTerminalSessionRequest,
    CreateTerminalSessionResponse,
    TerminalAskRequest,
    TerminalAskResponse,
    TerminalSessionInfo,
)

if TYPE_CHECKING:
    from backend.services.terminal_service import TerminalService

log = structlog.get_logger()

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


@dataclass
class _TerminalState:
    """Encapsulates mutable wiring set by main.py during lifespan."""

    service: TerminalService | None = field(default=None, repr=False)
    utility_session: Any = field(default=None, repr=False)


_state = _TerminalState()


def set_terminal_service(service: TerminalService) -> None:
    _state.service = service


def set_utility_session(session: object) -> None:
    _state.utility_session = session


def _svc() -> TerminalService:
    if _state.service is None:
        raise RuntimeError("TerminalService not initialized")
    return _state.service


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


@router.post("/sessions", response_model=CreateTerminalSessionResponse, status_code=201)
async def create_session(req: CreateTerminalSessionRequest) -> CreateTerminalSessionResponse:
    """Create a new terminal session."""
    svc = _svc()
    try:
        session = svc.create_session(
            cwd=req.cwd,
            shell=req.shell,
            job_id=req.job_id,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreateTerminalSessionResponse(
        id=session.id,
        shell=session.shell,
        cwd=session.cwd,
        job_id=session.job_id,
        pid=session.process.pid,
    )


@router.get("/sessions", response_model=list[TerminalSessionInfo])
async def list_sessions() -> list[TerminalSessionInfo]:
    """List all active terminal sessions."""
    svc = _svc()
    sessions = svc.list_sessions()
    return [TerminalSessionInfo(**s) for s in sessions]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """Kill a terminal session."""
    svc = _svc()
    killed = await svc.kill_session(session_id)
    if not killed:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/ask", response_model=TerminalAskResponse)
async def ask_ai(req: TerminalAskRequest) -> TerminalAskResponse:
    """Translate natural language to a shell command using the utility model."""
    # Access utility session from app state (set in main.py)
    try:
        if _state.utility_session is None:
            return TerminalAskResponse(command="", explanation="AI assistant not available")

        prompt = f"""Translate this natural language request into a single shell command.
Respond with ONLY valid JSON: {{"command": "...", "explanation": "..."}}

The explanation should be one short sentence describing what the command does.

Terminal context (recent output):
{req.context or "(none)"}

User request: {req.prompt}"""

        result = await _state.utility_session.complete(prompt, timeout=10.0)
        try:
            parsed = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
            return TerminalAskResponse(command=parsed["command"], explanation=parsed.get("explanation", ""))
        except (json.JSONDecodeError, KeyError):
            log.warning(
                "terminal_ask_parse_failed",
                raw_result=result[:200],
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


@router.websocket("/ws")
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
    await ws.accept()
    svc = _svc()
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
                        svc.write(attached_session_id, data.encode("utf-8"))

            elif msg_type == "resize":
                if attached_session_id:
                    cols = msg.get("cols", 120)
                    rows = msg.get("rows", 30)
                    if isinstance(cols, int) and isinstance(rows, int) and 0 < cols <= 500 and 0 < rows <= 200:
                        svc.resize(attached_session_id, cols, rows)

            elif msg_type == "detach":
                if attached_session_id:
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
