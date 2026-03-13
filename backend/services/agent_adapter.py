"""Agent adapter interface and implementations."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import SessionConfig, SessionEvent, SessionEventKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from copilot.generated.session_events import SessionEvent as SdkSessionEvent
    from copilot.session import CopilotSession

log = structlog.get_logger()


class AgentAdapterInterface(ABC):
    """Wraps the agent runtime behind a generic interface."""

    @abstractmethod
    async def create_session(self, config: SessionConfig) -> str:
        """Create a session, return session_id."""

    @abstractmethod
    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        """Stream events from a running session."""
        yield  # type: ignore[misc]

    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message into a running session."""

    @abstractmethod
    async def abort_session(self, session_id: str) -> None:
        """Abort the current message processing. Session remains valid."""


class CopilotAdapter(AgentAdapterInterface):
    """Wraps the Python Copilot SDK behind the adapter interface.

    Uses a callback-to-iterator bridge: SDK callbacks push SessionEvent
    items onto an asyncio.Queue; stream_events() yields from the queue.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[SessionEvent | None]] = {}
        self._sessions: dict[str, CopilotSession] = {}

    def _cleanup_session(self, session_id: str) -> None:
        """Remove session and queue references for a completed/aborted session."""
        self._sessions.pop(session_id, None)
        self._queues.pop(session_id, None)

    async def create_session(self, config: SessionConfig) -> str:
        from copilot import CopilotClient, PermissionRequest, PermissionRequestResult

        client = CopilotClient()
        session_id = str(uuid.uuid4())
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[session_id] = queue

        # Permission handler — bridge SDK permission requests into Tower's
        # approval system by emitting approval_request SessionEvents.
        def _on_permission(request: PermissionRequest, invocation: dict[str, str]) -> PermissionRequestResult:
            queue.put_nowait(
                SessionEvent(
                    kind=SessionEventKind.approval_request,
                    payload={
                        "description": f"{request.tool_name}: {request.intention or request.subject or ''}",
                        "proposed_action": request.full_command_text,
                    },
                )
            )
            # For now approve all — the RuntimeService handles the approval flow
            # at a higher level via the approval_requested domain event.
            return PermissionRequestResult(kind="approved")

        session = await client.create_session(
            {
                "working_directory": config.workspace_path,
                "on_permission_request": _on_permission,
            }
        )
        self._sessions[session_id] = session

        # Register SDK callback that bridges into the async queue
        def _on_event(sdk_event: SdkSessionEvent) -> None:
            kind_str = sdk_event.type.value if sdk_event.type else "log"
            payload = sdk_event.data.to_dict() if sdk_event.data else {}
            try:
                kind = SessionEventKind(kind_str)
            except ValueError:
                kind = SessionEventKind.log
                payload = {"level": "debug", "message": f"Unknown SDK event: {kind_str}"}
            try:
                queue.put_nowait(SessionEvent(kind=kind, payload=payload if isinstance(payload, dict) else {}))
            except Exception:
                log.warning("copilot_queue_put_failed", session_id=session_id)
            if kind == SessionEventKind.done or kind == SessionEventKind.error:
                with contextlib.suppress(Exception):
                    queue.put_nowait(None)  # sentinel

        session.on(_on_event)
        # Send initial prompt
        try:
            await session.send({"prompt": config.prompt, "mode": "immediate", "attachments": []})
        except Exception:
            self._cleanup_session(session_id)
            raise
        log.info("copilot_session_created", session_id=session_id)
        return session_id

    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        queue = self._queues.get(session_id)
        if queue is None:
            log.error("copilot_stream_no_queue", session_id=session_id)
            yield SessionEvent(kind=SessionEventKind.error, payload={"message": "No queue for session"})
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=300)
                except TimeoutError:
                    yield SessionEvent(
                        kind=SessionEventKind.error,
                        payload={"message": "Session timed out waiting for events"},
                    )
                    return
                if event is None:
                    return
                yield event
        finally:
            self._cleanup_session(session_id)

    async def send_message(self, session_id: str, message: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            log.warning("copilot_send_no_session", session_id=session_id)
            return
        await session.send({"prompt": message, "mode": "immediate", "attachments": []})

    async def abort_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            await session.abort()
        except Exception:
            log.warning("copilot_abort_failed", session_id=session_id, exc_info=True)
        finally:
            self._cleanup_session(session_id)
