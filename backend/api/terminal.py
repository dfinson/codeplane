"""Terminal REST + WebSocket API routes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from backend.models.api_schemas import CamelModel

if TYPE_CHECKING:
    from backend.services.terminal_service import TerminalService

log = structlog.get_logger()

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

# Will be set by main.py during lifespan
_terminal_service: TerminalService | None = None


def set_terminal_service(service: TerminalService) -> None:
    global _terminal_service  # noqa: PLW0603
    _terminal_service = service


def _svc() -> TerminalService:
    assert _terminal_service is not None, "TerminalService not initialized"
    return _terminal_service


# ------------------------------------------------------------------
# Request / Response schemas
# ------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    shell: str | None = None
    cwd: str | None = None
    job_id: str | None = Field(None, alias="jobId")


class CreateSessionResponse(CamelModel):
    id: str
    shell: str
    cwd: str
    job_id: str | None = None
    pid: int


class SessionInfo(CamelModel):
    id: str
    shell: str
    cwd: str
    job_id: str | None = None
    pid: int
    clients: int


class AskRequest(BaseModel):
    prompt: str
    context: str | None = None  # recent terminal output for context


class AskResponse(CamelModel):
    command: str
    explanation: str


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    """Create a new terminal session."""
    svc = _svc()
    try:
        session = svc.create_session(
            cwd=req.cwd,
            shell=req.shell,
            job_id=req.job_id,
        )
    except (RuntimeError, ValueError) as exc:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"error": str(exc)})  # type: ignore[return-value]
    return CreateSessionResponse(
        id=session.id,
        shell=session.shell,
        cwd=session.cwd,
        job_id=session.job_id,
        pid=session.process.pid,
    )


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions() -> list[SessionInfo]:
    """List all active terminal sessions."""
    svc = _svc()
    sessions = svc.list_sessions()
    return [SessionInfo(**s) for s in sessions]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """Kill a terminal session."""
    svc = _svc()
    killed = await svc.kill_session(session_id)
    if not killed:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"error": "Session not found"})  # type: ignore[return-value]


@router.post("/ask", response_model=AskResponse)
async def ask_ai(req: AskRequest) -> AskResponse:
    """Translate natural language to a shell command using the utility model."""
    # Access utility session from app state (set in main.py)
    try:
        if _ask_utility_session is None:
            return AskResponse(command="", explanation="AI assistant not available")

        prompt = f"""Translate this natural language request into a single shell command.
Respond with ONLY valid JSON: {{"command": "...", "explanation": "..."}}

The explanation should be one short sentence describing what the command does.

Terminal context (recent output):
{req.context or "(none)"}

User request: {req.prompt}"""

        result = await _ask_utility_session.complete(prompt, timeout=10.0)
        try:
            parsed = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
            return AskResponse(command=parsed["command"], explanation=parsed.get("explanation", ""))
        except (json.JSONDecodeError, KeyError):
            return AskResponse(command=result.strip(), explanation="")
    except Exception as exc:
        log.warning("terminal_ask_failed", error=str(exc))
        return AskResponse(command="", explanation=f"Error: {exc}")


_ask_utility_session: Any = None


def set_utility_session(session: object) -> None:
    global _ask_utility_session  # noqa: PLW0603
    _ask_utility_session = session


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
        pass
    except Exception:
        log.warning("terminal_ws_error", exc_info=True)
    finally:
        # Clean up on disconnect
        if attached_session_id:
            session = svc.get_session(attached_session_id)
            if session:
                session.clients.discard(ws)
