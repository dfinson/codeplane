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

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """Non-agentic single-turn completion. Returns the full response text."""
        return ""


class CopilotAdapter(AgentAdapterInterface):
    """Wraps the Python Copilot SDK behind the adapter interface.

    Uses a callback-to-iterator bridge: SDK callbacks push SessionEvent
    items onto an asyncio.Queue; stream_events() yields from the queue.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[SessionEvent | None]] = {}
        self._sessions: dict[str, CopilotSession] = {}
        self._session_to_job: dict[str, str] = {}  # session_id → job_id for telemetry
        self._tool_start_times: dict[str, float] = {}  # tool_call_id → start monotonic

    def set_job_id(self, session_id: str, job_id: str) -> None:
        """Associate a session with a job for telemetry routing."""
        self._session_to_job[session_id] = job_id

    def _cleanup_session(self, session_id: str) -> None:
        """Remove session and queue references for a completed/aborted session."""
        self._sessions.pop(session_id, None)
        self._queues.pop(session_id, None)
        self._session_to_job.pop(session_id, None)

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
        # and extracts telemetry from Copilot-specific event types.
        def _on_event(sdk_event: SdkSessionEvent) -> None:
            kind_str = sdk_event.type.value if sdk_event.type else "log"
            payload = sdk_event.data.to_dict() if sdk_event.data else {}
            data = sdk_event.data

            # --- Copilot SDK → standard telemetry contract ---
            # Compare against event type string values to avoid importing
            # SessionEventType (which mypy flags as not re-exported).
            job_id = self._session_to_job.get(session_id)
            if job_id and data:
                from backend.services.telemetry import collector as tel

                if kind_str == "assistant_usage":
                    tel.record_llm_usage(
                        job_id,
                        model=data.model or "",
                        input_tokens=int(data.input_tokens or 0),
                        output_tokens=int(data.output_tokens or 0),
                        cache_read_tokens=int(data.cache_read_tokens or 0),
                        cache_write_tokens=int(data.cache_write_tokens or 0),
                        cost=float(data.cost or 0),
                        duration_ms=float(data.duration or 0),
                    )
                elif kind_str == "tool_execution_start":
                    tool_id = data.tool_call_id or ""
                    import time as _time

                    self._tool_start_times[tool_id] = _time.monotonic()
                elif kind_str == "tool_execution_complete":
                    tool_id = data.tool_call_id or ""
                    import time as _time

                    start = self._tool_start_times.pop(tool_id, _time.monotonic())
                    dur = (_time.monotonic() - start) * 1000
                    tel.record_tool_call(
                        job_id,
                        tool_name=data.tool_name or data.mcp_tool_name or "unknown",
                        duration_ms=dur,
                        success=bool(data.success) if data.success is not None else True,
                    )
                elif kind_str == "session_context_changed":
                    tel.record_context_change(
                        job_id,
                        current_tokens=int(data.current_tokens or 0),
                    )
                elif kind_str == "session_compaction_complete":
                    tel.record_compaction(
                        job_id,
                        pre_tokens=int(data.pre_compaction_tokens or 0),
                        post_tokens=int(data.post_compaction_tokens or 0),
                    )
                    if data.post_compaction_tokens:
                        tel.record_context_change(
                            job_id,
                            current_tokens=int(data.post_compaction_tokens),
                        )
                elif kind_str == "session_truncation":
                    if data.token_limit:
                        tel.record_context_change(
                            job_id,
                            window_size=int(data.token_limit),
                        )
                elif kind_str == "session_model_change":
                    if data.new_model:
                        t = tel.get(job_id)
                        if t:
                            t.model = data.new_model
                elif kind_str == "assistant_message":
                    tel.record_message(job_id, role="agent")
                elif kind_str == "user_message":
                    tel.record_message(job_id, role="operator")

            # --- Bridge to SessionEvent queue ---
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

    async def complete(self, prompt: str) -> str:
        """Create a minimal session for single-turn completion, collect the response."""
        from copilot import CopilotClient

        client = CopilotClient()
        tmp_session_id = str(uuid.uuid4())
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[tmp_session_id] = queue

        async def _noop_permission(request: object, invocation: object) -> object:
            from copilot import PermissionRequestResult

            return PermissionRequestResult(kind="approved")

        try:
            session = await client.create_session(
                {
                    "working_directory": "/tmp",
                    "on_permission_request": _noop_permission,
                }
            )
            self._sessions[tmp_session_id] = session

            collected: list[str] = []
            done_event = asyncio.Event()

            def _on_event(sdk_event: SdkSessionEvent) -> None:
                kind_str = sdk_event.type.value if sdk_event.type else ""
                payload = sdk_event.data.to_dict() if sdk_event.data else {}
                if kind_str == "assistant.message":
                    content = payload.get("content") or ""
                    if content:
                        collected.append(content)
                    done_event.set()
                elif kind_str in ("session.task_complete", "session.idle", "session.error", "session.shutdown"):
                    done_event.set()

            session.on(_on_event)
            await session.send({"prompt": prompt, "mode": "immediate", "attachments": []})
            try:
                await asyncio.wait_for(done_event.wait(), timeout=30)
            except TimeoutError:
                log.warning("complete_timeout")
            return "\n".join(collected)
        except Exception:
            log.error("complete_failed", exc_info=True)
            return ""
        finally:
            try:
                s = self._sessions.get(tmp_session_id)
                if s:
                    await s.abort()
            except Exception:
                pass
            self._cleanup_session(tmp_session_id)
