"""Agent adapter interface and implementations."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from abc import ABC, abstractmethod
from datetime import UTC
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import SessionConfig, SessionEvent, SessionEventKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from copilot import PermissionRequestResult
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
        # Buffers tool.execution_start data so we can emit a combined entry on complete
        self._tool_call_buffer: dict[str, dict[str, str]] = {}  # tool_call_id → {tool_name, tool_args, turn_id}

    def set_job_id(self, session_id: str, job_id: str) -> None:
        """Associate a session with a job for telemetry routing."""
        self._session_to_job[session_id] = job_id

    def _cleanup_session(self, session_id: str) -> None:
        """Remove session and queue references for a completed/aborted session."""
        self._sessions.pop(session_id, None)
        self._queues.pop(session_id, None)
        self._session_to_job.pop(session_id, None)

    async def create_session(self, config: SessionConfig) -> str:
        from copilot import CopilotClient, PermissionRequest

        client = CopilotClient()

        # Permission handler — bridges SDK permission requests into Tower's
        # approval system based on the job's permission mode.
        async def _on_permission(request: PermissionRequest, invocation: dict[str, str]) -> PermissionRequestResult:
            from copilot import PermissionRequestResult as _Result

            kind_val = request.kind.value if request.kind else "unknown"
            mode = config.permission_mode

            # --- readonly: deny any mutation ---
            if mode == "readonly":
                if kind_val in ("write", "shell", "url"):
                    log.info("permission_denied_readonly", kind=kind_val)
                    return _Result(kind="denied-interactively-by-user")
                return _Result(kind="approved")

            # --- supervised: block write/url on operator decision, allow everything else ---
            if mode == "supervised":
                if kind_val not in ("write", "url"):
                    return _Result(kind="approved")
                # Build human-readable description
                if kind_val == "write":
                    description = f"Write file: {request.file_name or request.intention or ''}"
                elif kind_val == "shell":
                    description = f"Run shell: {request.full_command_text or request.intention or ''}"
                elif kind_val == "url":
                    description = f"Fetch URL: {request.url or request.intention or ''}"
                elif kind_val in ("mcp", "custom-tool"):
                    label = request.tool_title or request.tool_name or kind_val
                    description = f"{label}: {request.intention or ''}"
                else:
                    description = request.intention or request.full_command_text or kind_val
                handler = config.blocking_permission_handler
                if handler is not None:
                    import inspect as _inspect

                    resolution = handler(description, request.full_command_text)  # type: ignore[operator]
                    if _inspect.isawaitable(resolution):
                        resolution = await resolution
                    if resolution == "approved":
                        return _Result(kind="approved")
                    return _Result(kind="denied-interactively-by-user")
                return _Result(kind="approved")  # no handler wired — fall through

            # --- auto (default): approve everything silently ---
            return _Result(kind="approved")

        # Build session options dict — used for both create and resume
        session_opts: dict[str, object] = {
            "working_directory": config.workspace_path,
            "on_permission_request": _on_permission,
        }
        if config.model:
            session_opts["model"] = config.model

        # Create or resume SDK session; use the SDK-assigned session_id as Tower's identifier.
        _resume_id = config.resume_sdk_session_id
        if _resume_id:
            try:
                session = await client.resume_session(_resume_id, session_opts)
                log.info("sdk_session_resumed", sdk_session_id=_resume_id)
            except Exception:
                log.warning("sdk_session_resume_failed_creating_new", sdk_session_id=_resume_id, exc_info=True)
                session = await client.create_session(session_opts)
        else:
            session = await client.create_session(session_opts)

        session_id = session.session_id  # Use SDK-assigned ID as Tower's session identifier
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[session_id] = queue
        self._sessions[session_id] = session

        # Wire telemetry mapping before registering the callback so
        # no early SDK events are lost.
        if config.job_id:
            self.set_job_id(session_id, config.job_id)

        # Sequence counter for log events emitted from this session.
        log_seq = [0]

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

                if kind_str == "assistant.usage":
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
                elif kind_str == "tool.execution_start":
                    tool_id = data.tool_call_id or ""
                    import json as _json
                    import time as _time

                    self._tool_start_times[tool_id] = _time.monotonic()
                    # Buffer args for the combined transcript entry emitted on complete
                    args_str: str | None = None
                    if data.arguments is not None:
                        try:
                            args_str = (
                                _json.dumps(data.arguments) if not isinstance(data.arguments, str) else data.arguments
                            )
                        except Exception:
                            args_str = str(data.arguments)
                    t_name = data.tool_name or data.mcp_tool_name or "tool"
                    t_name_display = (
                        f"{data.mcp_server_name}/{data.mcp_tool_name}"
                        if data.mcp_server_name and data.mcp_tool_name
                        else t_name
                    )
                    self._tool_call_buffer[tool_id] = {
                        "tool_name": t_name_display,
                        "tool_args": args_str or "",
                        "turn_id": str(data.turn_id) if hasattr(data, "turn_id") and data.turn_id else "",
                    }
                elif kind_str == "tool.execution_complete":
                    tool_id = data.tool_call_id or ""
                    import time as _time

                    start = self._tool_start_times.pop(tool_id, _time.monotonic())
                    dur = (_time.monotonic() - start) * 1000
                    # Prefer the display name buffered at tool.execution_start
                    buffered_name = self._tool_call_buffer.get(tool_id, {}).get("tool_name")
                    resolved_name = buffered_name or data.tool_name or data.mcp_tool_name or "tool"
                    tel.record_tool_call(
                        job_id,
                        tool_name=resolved_name,
                        duration_ms=dur,
                        success=bool(data.success) if data.success is not None else True,
                    )
                elif kind_str == "session.context_changed":
                    tel.record_context_change(
                        job_id,
                        current_tokens=int(data.current_tokens or 0),
                    )
                elif kind_str == "session.compaction_complete":
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
                elif kind_str == "session.truncation":
                    if data.token_limit:
                        tel.record_context_change(
                            job_id,
                            window_size=int(data.token_limit),
                        )
                elif kind_str == "session.model_change":
                    if data.new_model:
                        t = tel.get(job_id)
                        if t:
                            t.model = data.new_model
                elif kind_str == "assistant.message":
                    tel.record_message(job_id, role="agent")
                elif kind_str == "user.message":
                    tel.record_message(job_id, role="operator")

            # --- Emit log events for operational SDK events ---
            # These show up in the LogsPanel and are persisted as log_line_emitted.
            _log_msg: str | None = None
            _log_level: str = "info"
            if kind_str == "tool.execution_start" and data:
                t_name = data.tool_name or data.mcp_tool_name or "tool"
                if data.mcp_server_name and data.mcp_tool_name:
                    t_name = f"{data.mcp_server_name}/{data.mcp_tool_name}"
                _log_msg = f"Tool started: {t_name}"
                _log_level = "debug"
            elif kind_str == "tool.execution_complete" and data:
                buffered_log_name = self._tool_call_buffer.get((data.tool_call_id or ""), {}).get("tool_name")
                t_name = buffered_log_name or data.tool_name or data.mcp_tool_name or "tool"
                ok = bool(data.success) if data.success is not None else True
                _log_msg = f"Tool {'completed' if ok else 'failed'}: {t_name}"
                _log_level = "info" if ok else "warn"
            elif kind_str == "assistant.usage" and data:
                in_tok = int(data.input_tokens or 0)
                out_tok = int(data.output_tokens or 0)
                model = data.model or ""
                _log_msg = f"LLM call: {model} ({in_tok}+{out_tok} tokens)"
                _log_level = "debug"
            elif kind_str == "session.compaction_complete" and data:
                pre = int(data.pre_compaction_tokens or 0)
                post = int(data.post_compaction_tokens or 0)
                _log_msg = f"Context compacted: {pre} → {post} tokens"
                _log_level = "warn"
            elif kind_str == "session.model_change" and data:
                _log_msg = f"Model changed to {data.new_model}"
                _log_level = "info"

            if _log_msg is not None:
                from datetime import datetime as _dt

                log_seq[0] += 1
                queue.put_nowait(
                    SessionEvent(
                        kind=SessionEventKind.log,
                        payload={
                            "seq": log_seq[0],
                            "timestamp": _dt.now(UTC).isoformat(),
                            "level": _log_level,
                            "message": _log_msg,
                        },
                    )
                )

            # --- Bridge to SessionEvent queue ---
            # Map SDK dot-notation event types to internal SessionEventKind.
            # Events not in this map are silently ignored.
            _sdk_kind_map: dict[str, SessionEventKind] = {
                "session.task_complete": SessionEventKind.done,
                "session.idle": SessionEventKind.done,
                "session.shutdown": SessionEventKind.done,
                "session.error": SessionEventKind.error,
                "assistant.message": SessionEventKind.transcript,
                "user.message": SessionEventKind.transcript,
                "assistant.reasoning": SessionEventKind.transcript,
                "tool.execution_complete": SessionEventKind.transcript,
                "session.workspace_file_changed": SessionEventKind.file_changed,
            }
            kind = _sdk_kind_map.get(kind_str)
            if kind is None:
                return  # unrecognised SDK event – skip silently
            try:
                event_payload: dict[str, object] = {}
                if kind == SessionEventKind.transcript:
                    if kind_str == "assistant.message":
                        event_payload = {
                            "role": "agent",
                            "content": (data.content or "") if data else "",
                            "title": data.title if data else None,
                            "turn_id": data.turn_id if data else None,
                        }
                    elif kind_str == "user.message":
                        content = (data.content or data.message or "") if data else ""
                        # SDK injects internal system_notification messages (e.g.
                        # agent completion status) — suppress these from the
                        # transcript since they are not real operator messages.
                        if "<system_notification>" in content:
                            return
                        event_payload = {
                            "role": "operator",
                            "content": content,
                        }
                    elif kind_str == "assistant.reasoning":
                        event_payload = {
                            "role": "reasoning",
                            "content": (data.reasoning_text or "") if data else "",
                            "turn_id": (data.reasoning_id or data.turn_id or None) if data else None,
                        }
                    elif kind_str == "tool.execution_complete":
                        tool_id = (data.tool_call_id or "") if data else ""
                        buffered = self._tool_call_buffer.pop(tool_id, {})
                        result_text = ""
                        if data:
                            result_obj = data.result
                            if result_obj is not None and hasattr(result_obj, "content") and result_obj.content:
                                parts = result_obj.content
                                if isinstance(parts, list):
                                    result_text = "\n".join(
                                        str(c.text) if hasattr(c, "text") and c.text else str(c) for c in parts
                                    )
                                else:
                                    result_text = str(parts)
                            if not result_text and data.partial_output:
                                result_text = data.partial_output
                        tool_name = buffered.get(
                            "tool_name",
                            (data.tool_name or data.mcp_tool_name or "tool") if data else "tool",
                        )
                        event_payload = {
                            "role": "tool_call",
                            "content": tool_name,
                            "tool_name": tool_name,
                            "tool_args": buffered.get("tool_args"),
                            "tool_result": result_text,
                            "tool_success": bool(data.success) if data and data.success is not None else True,
                            "turn_id": buffered.get("turn_id") or (data.turn_id if data else None),
                        }
                else:
                    event_payload = payload if isinstance(payload, dict) else {}
                queue.put_nowait(SessionEvent(kind=kind, payload=event_payload))
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
        return str(session_id)

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
                await asyncio.wait_for(done_event.wait(), timeout=180)
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
