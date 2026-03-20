"""Copilot SDK adapter — bridges the Copilot Python SDK into CodePlane."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import (
    PermissionMode,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.services.agent_adapter import CODEPLANE_SYSTEM_PROMPT, AgentAdapterInterface
from backend.services.permission_policy import (
    PolicyDecision,
    evaluate,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from copilot import PermissionRequest, PermissionRequestResult
    from copilot.generated.session_events import SessionEvent as SdkSessionEvent
    from copilot.session import CopilotSession

    from backend.services.approval_service import ApprovalService
    from backend.services.event_bus import EventBus

log = structlog.get_logger()


class CopilotAdapter(AgentAdapterInterface):
    """Wraps the Python Copilot SDK behind the adapter interface.

    Uses a callback-to-iterator bridge: SDK callbacks push SessionEvent
    items onto an asyncio.Queue; stream_events() yields from the queue.
    """

    def __init__(
        self,
        approval_service: ApprovalService | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._queues: dict[str, asyncio.Queue[SessionEvent | None]] = {}
        self._sessions: dict[str, CopilotSession] = {}
        self._session_to_job: dict[str, str] = {}  # session_id → job_id for telemetry
        self._tool_start_times: dict[str, float] = {}  # tool_call_id → start monotonic
        # Buffers tool.execution_start data so we can emit a combined entry on complete
        self._tool_call_buffer: dict[str, dict[str, str]] = {}  # tool_call_id → {tool_name, tool_args, turn_id}
        self._approval_service = approval_service
        self._event_bus = event_bus

    def set_job_id(self, session_id: str, job_id: str) -> None:
        """Associate a session with a job for telemetry routing."""
        self._session_to_job[session_id] = job_id

    def _cleanup_session(self, session_id: str) -> None:
        """Remove session and queue references for a completed/aborted session."""
        self._sessions.pop(session_id, None)
        self._queues.pop(session_id, None)
        self._session_to_job.pop(session_id, None)

    async def _handle_permission_request(
        self,
        request: PermissionRequest,
        invocation: dict[str, str],
        config: SessionConfig,
    ) -> PermissionRequestResult:
        """Bridge SDK permission requests into CodePlane's approval system.

        Extracted from the ``create_session`` closure so it can be tested and
        reviewed independently.  The ``config`` provides the permission mode
        and workspace path that were captured by the original closure.
        """
        from copilot import PermissionRequestResult as _Result

        kind_val = request.kind.value if request.kind else "unknown"
        mode = config.permission_mode
        sid = invocation.get("session_id", "")

        # --- Check if operator has trusted this job session ---
        approval_service = self._approval_service
        job_id = self._session_to_job.get(sid)
        if approval_service is not None and job_id and approval_service.is_trusted(job_id):
            log.debug("permission_auto_approved", mode="trusted", kind=kind_val)
            return _Result(kind="approved")

        # --- AUTO: approve everything (full execution permission) ---
        if mode == PermissionMode.auto:
            candidate_paths: list[str] = []
            if request.file_name:
                candidate_paths.append(request.file_name)
            if request.path:
                candidate_paths.append(request.path)
            if request.possible_paths:
                candidate_paths.extend(request.possible_paths)

            decision = evaluate(
                mode=PermissionMode.auto,
                kind=kind_val,
                workspace_path=config.workspace_path,
                possible_paths=candidate_paths or None,
                file_name=request.file_name,
                path=request.path,
            )
            if decision == PolicyDecision.approve:
                log.debug("permission_auto_approved", mode=PermissionMode.auto, kind=kind_val)
                return _Result(kind="approved")

        # --- READ_ONLY: deny mutations ---
        if mode == PermissionMode.read_only:
            decision = evaluate(
                mode=PermissionMode.read_only,
                kind=kind_val,
                workspace_path=config.workspace_path,
                full_command_text=request.full_command_text,
                file_name=request.file_name,
                path=request.path,
                read_only=request.read_only,
            )
            if decision == PolicyDecision.approve:
                log.debug("permission_auto_approved", mode=PermissionMode.read_only, kind=kind_val)
                return _Result(kind="approved")
            if decision == PolicyDecision.deny:
                log.info("permission_denied_readonly", kind=kind_val)
                return _Result(kind="denied-interactively-by-user")
            # ask → fall through (shouldn't happen in read_only, but safe)

        # --- APPROVAL_REQUIRED: check policy ---
        if mode == PermissionMode.approval_required:
            decision = evaluate(
                mode=PermissionMode.approval_required,
                kind=kind_val,
                workspace_path=config.workspace_path,
                full_command_text=request.full_command_text,
                file_name=request.file_name,
                path=request.path,
                read_only=request.read_only,
            )
            if decision == PolicyDecision.approve:
                log.debug("permission_auto_approved", mode=PermissionMode.approval_required, kind=kind_val)
                return _Result(kind="approved")

        # --- Route to operator for approval ---
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

        approval_service = self._approval_service
        job_id = self._session_to_job.get(sid)

        if approval_service is None or job_id is None:
            log.warning("permission_ask_no_infra", kind=kind_val)
            return _Result(kind="approved")

        # Persist the approval request and create a Future
        approval = await approval_service.create_request(
            job_id=job_id,
            description=description,
            proposed_action=request.full_command_text,
        )

        # Emit approval_request event so RuntimeService transitions state
        q = self._queues.get(sid)
        if q is not None:
            q.put_nowait(
                SessionEvent(
                    kind=SessionEventKind.approval_request,
                    payload={
                        "description": description,
                        "proposed_action": request.full_command_text,
                        "approval_id": approval.id,
                    },
                )
            )

        log.info(
            "permission_awaiting_operator",
            approval_id=approval.id,
            kind=kind_val,
            description=description,
        )

        # Block the SDK until the operator responds
        resolution = await approval_service.wait_for_resolution(approval.id)

        if resolution == "approved":
            return _Result(kind="approved")
        return _Result(kind="denied-interactively-by-user")

    # --- Dispatch tables for telemetry and SDK→SessionEvent bridging ---

    _TELEMETRY_DISPATCH: dict[str, str] = {
        "assistant.usage": "_handle_usage_event",
        "tool.execution_start": "_handle_tool_start",
        "tool.execution_complete": "_handle_tool_end",
        "session.context_changed": "_handle_context_changed",
        "session.compaction_complete": "_handle_compaction",
    }

    _SDK_KIND_MAP: dict[str, SessionEventKind] = {
        "session.task_complete": SessionEventKind.done,
        "session.idle": SessionEventKind.done,
        "session.shutdown": SessionEventKind.done,
        "session.error": SessionEventKind.error,
        "assistant.message": SessionEventKind.transcript,
        "user.message": SessionEventKind.transcript,
        # assistant.reasoning is intentionally NOT mapped — it duplicates
        # the reasoning_text already embedded in assistant.message.
        "tool.execution_complete": SessionEventKind.transcript,
        "session.workspace_file_changed": SessionEventKind.file_changed,
    }

    # --- Extracted telemetry handlers ---

    def _handle_usage_event(
        self, data: object, job_id: str, tel: object,
        requested_model: str, model_verified: list[bool], queue: asyncio.Queue,
    ) -> None:
        actual_model = data.model or ""
        if not model_verified[0] and requested_model and actual_model:
            model_verified[0] = True
            if actual_model != requested_model:
                log.error(
                    "model_mismatch",
                    requested=requested_model,
                    actual=actual_model,
                    job_id=job_id,
                )
                queue.put_nowait(
                    SessionEvent(
                        kind=SessionEventKind.model_downgraded,
                        payload={
                            "requested_model": requested_model,
                            "actual_model": actual_model,
                        },
                    )
                )
            else:
                log.info("model_confirmed", model=actual_model, job_id=job_id)
            # Lock in the main model once confirmed (on first usage event)
            tel.set_main_model(job_id, actual_model)
        tel.record_llm_usage(
            job_id,
            model=actual_model,
            input_tokens=int(data.input_tokens or 0),
            output_tokens=int(data.output_tokens or 0),
            cache_read_tokens=int(data.cache_read_tokens or 0),
            cache_write_tokens=int(data.cache_write_tokens or 0),
            cost=float(data.cost or 0),
            duration_ms=float(data.duration or 0),
        )
        # Capture Copilot quota snapshots if present
        raw_snapshots = getattr(data, "quota_snapshots", None)
        if raw_snapshots:
            from backend.services.telemetry import QuotaSnapshot as _QS

            parsed = {
                key: _QS(
                    used_requests=float(getattr(snap, "used_requests", 0) or 0),
                    entitlement_requests=float(getattr(snap, "entitlement_requests", 0) or 0),
                    remaining_percentage=float(getattr(snap, "remaining_percentage", 0) or 0),
                    overage=float(getattr(snap, "overage", 0) or 0),
                    overage_allowed=bool(getattr(snap, "overage_allowed_with_exhausted_quota", False)),
                    is_unlimited=bool(getattr(snap, "is_unlimited_entitlement", False)),
                    usage_allowed_with_exhausted_quota=bool(getattr(snap, "usage_allowed_with_exhausted_quota", False)),
                    reset_date=str(getattr(snap, "reset_date", "") or ""),
                )
                for key, snap in raw_snapshots.items()
            }
            tel.record_quota_snapshots(job_id, snapshots=parsed)

    def _handle_tool_start(self, data: object, job_id: str, tel: object) -> None:
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
        # Capture human-readable intent/title fields from the SDK if present
        tool_intent: str = getattr(data, "intention", None) or ""
        tool_title: str = getattr(data, "tool_title", None) or ""
        # Also extract description from arguments (SDK provides it for bash etc.)
        tool_description: str = ""
        if isinstance(data.arguments, dict):
            tool_description = str(data.arguments.get("description", ""))
        self._tool_call_buffer[tool_id] = {
            "tool_name": t_name_display,
            "tool_args": args_str or "",
            "turn_id": str(data.turn_id) if hasattr(data, "turn_id") and data.turn_id else "",
            "tool_intent": tool_intent or tool_description,
            "tool_title": tool_title,
        }

    def _handle_tool_end(self, data: object, job_id: str, tel: object) -> None:
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

    def _handle_context_changed(self, data: object, job_id: str, tel: object) -> None:
        tel.record_context_change(
            job_id,
            current_tokens=int(data.current_tokens or 0),
        )

    def _handle_compaction(self, data: object, job_id: str, tel: object) -> None:
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

    # --- Log emission ---

    def _emit_log_event(
        self, kind_str: str, data: object, requested_model: str,
        queue: asyncio.Queue, log_seq: list[int],
    ) -> None:
        """Emit a log SessionEvent for operational SDK events."""
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
            if model and requested_model and model != requested_model:
                _log_msg = (
                    f"\u26a0 MODEL MISMATCH: requested {requested_model} but serving {model} ({in_tok}+{out_tok} tokens)"
                )
                _log_level = "error"
            else:
                _log_msg = f"LLM call: {model} ({in_tok}+{out_tok} tokens)"
                _log_level = "debug"
        elif kind_str == "session.compaction_complete" and data:
            pre = int(data.pre_compaction_tokens or 0)
            post = int(data.post_compaction_tokens or 0)
            _log_msg = f"Context compacted: {pre} \u2192 {post} tokens"
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

    # --- SDK → SessionEvent queue bridge ---

    def _bridge_to_session_queue(
        self, kind_str: str, data: object, payload: dict,
        queue: asyncio.Queue, session_id: str,
    ) -> None:
        """Map SDK events to SessionEvent entries and push onto the queue."""
        kind = self._SDK_KIND_MAP.get(kind_str)
        if kind is None:
            return  # unrecognised SDK event – skip silently
        try:
            event_payload: dict[str, object] = {}
            if kind == SessionEventKind.transcript:
                if kind_str == "assistant.message":
                    content = (data.content or "") if data else ""
                    # SDK emits empty assistant.message events for tool-dispatch
                    # turns (content is just whitespace). Skip these — the tool
                    # calls themselves are separate transcript events.
                    if not content.strip():
                        return
                    event_payload = {
                        "role": "agent",
                        "content": content,
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
                elif kind_str == "tool.execution_complete":
                    tool_id = (data.tool_call_id or "") if data else ""
                    buffered = self._tool_call_buffer.pop(tool_id, {})
                    tool_name = buffered.get(
                        "tool_name",
                        (data.tool_name or data.mcp_tool_name or "tool") if data else "tool",
                    )
                    # Drop SDK-internal tools (e.g. report_intent) from transcript
                    if tool_name in ("report_intent",):
                        return
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
                    from backend.services.tool_formatters import extract_tool_issue, format_tool_display

                    tool_args_str = buffered.get("tool_args")
                    success = bool(data.success) if data and data.success is not None else True
                    tool_issue = extract_tool_issue(result_text) if not success else None
                    event_payload = {
                        "role": "tool_call",
                        "content": tool_name,
                        "tool_name": tool_name,
                        "tool_args": tool_args_str,
                        "tool_result": result_text,
                        "tool_success": success,
                        "tool_issue": tool_issue or ("Tool reported an issue" if not success else None),
                        "turn_id": buffered.get("turn_id") or (data.turn_id if data else None),
                        "tool_intent": buffered.get("tool_intent"),
                        "tool_title": buffered.get("tool_title"),
                        "tool_display": format_tool_display(
                            tool_name,
                            tool_args_str,
                            tool_result=result_text or None,
                            tool_success=success,
                        ),
                    }
            else:
                event_payload = payload if isinstance(payload, dict) else {}
            queue.put_nowait(SessionEvent(kind=kind, payload=event_payload))
        except Exception:
            log.warning("copilot_queue_put_failed", session_id=session_id)
        if kind == SessionEventKind.done or kind == SessionEventKind.error:
            with contextlib.suppress(Exception):
                queue.put_nowait(None)  # sentinel

    async def create_session(self, config: SessionConfig) -> str:
        from copilot import CopilotClient, PermissionRequest
        from copilot.types import ResumeSessionConfig
        from copilot.types import SessionConfig as SdkSessionConfig

        client = CopilotClient()

        # Thin closure that delegates to the instance method, capturing only `config`.
        async def _on_permission(request: PermissionRequest, invocation: dict[str, str]) -> PermissionRequestResult:
            return await self._handle_permission_request(request, invocation, config)

        # Build session options dict — used for both create and resume
        session_opts = SdkSessionConfig(
            working_directory=config.workspace_path,
            on_permission_request=_on_permission,
            # CodePlane is a headless orchestrator — there is no interactive terminal.
            # Appending this instruction prevents the agent from entering plan mode
            # (which requires Shift+Tab to exit and has no equivalent in a web UI).
            system_message={
                "mode": "append",
                "content": (
                    CODEPLANE_SYSTEM_PROMPT
                    + " Before making tool calls, call report_intent first to declare your current intent."
                ),
            },
        )
        requested_model = config.model or ""
        if config.model:
            session_opts["model"] = config.model
            log.info("sdk_session_model_requested", model=config.model)

        # Create or resume SDK session; use the SDK-assigned session_id as CodePlane's identifier.
        _resume_id = config.resume_sdk_session_id
        if _resume_id:
            resume_opts = ResumeSessionConfig(
                working_directory=config.workspace_path,
                on_permission_request=_on_permission,
            )
            if config.model:
                resume_opts["model"] = config.model
            try:
                session = await client.resume_session(_resume_id, resume_opts)
                log.info("sdk_session_resumed", sdk_session_id=_resume_id)
            except Exception:
                log.warning("sdk_session_resume_failed_creating_new", sdk_session_id=_resume_id, exc_info=True)
                session = await client.create_session(session_opts)
        else:
            session = await client.create_session(session_opts)

        session_id = session.session_id  # Use SDK-assigned ID as CodePlane's session identifier
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[session_id] = queue
        self._sessions[session_id] = session

        # Wire telemetry mapping before registering the callback so
        # no early SDK events are lost.
        if config.job_id:
            self.set_job_id(session_id, config.job_id)

        # Sequence counter for log events emitted from this session.
        log_seq = [0]

        # Track whether we've verified the model on the first usage event.
        _model_verified = [False]

        # Register SDK callback that bridges into the async queue
        # and extracts telemetry from Copilot-specific event types.
        def _on_event(sdk_event: SdkSessionEvent) -> None:
            kind_str = sdk_event.type.value if sdk_event.type else "log"
            payload = sdk_event.data.to_dict() if sdk_event.data else {}
            data = sdk_event.data

            # --- Copilot SDK → standard telemetry contract ---
            job_id = self._session_to_job.get(session_id)
            if job_id and data:
                from backend.services.telemetry import collector as tel

                handler_name = self._TELEMETRY_DISPATCH.get(kind_str)
                if handler_name:
                    handler = getattr(self, handler_name)
                    if kind_str == "assistant.usage":
                        handler(data, job_id, tel, requested_model, _model_verified, queue)
                    else:
                        handler(data, job_id, tel)
                elif kind_str == "session.truncation":
                    if data.token_limit:
                        tel.record_context_change(job_id, window_size=int(data.token_limit))
                elif kind_str == "session.model_change":
                    if data.new_model:
                        tel.set_main_model(job_id, data.new_model)
                elif kind_str == "assistant.message":
                    tel.record_message(job_id, role="agent")
                elif kind_str == "user.message":
                    tel.record_message(job_id, role="operator")
                elif kind_str == "session.shutdown":
                    total_pr = getattr(data, "total_premium_requests", None)
                    if data and total_pr is not None:
                        tel.record_premium_requests(job_id, count=float(total_pr))

            # --- Emit log events for operational SDK events ---
            self._emit_log_event(kind_str, data, requested_model, queue, log_seq)

            # --- Bridge to SessionEvent queue ---
            self._bridge_to_session_queue(kind_str, data, payload, queue, session_id)

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
        try:
            await session.send({"prompt": message, "mode": "immediate", "attachments": []})
        except Exception:
            log.warning("copilot_send_message_failed", session_id=session_id, exc_info=True)

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

    async def complete(self, prompt: str) -> str | None:
        """Create a minimal session for single-turn completion, collect the response."""
        from copilot import CopilotClient
        from copilot import PermissionRequestResult as _Result
        from copilot.types import SessionConfig as SdkSessionConfig

        client = CopilotClient()
        tmp_session_id = str(uuid.uuid4())
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[tmp_session_id] = queue

        async def _noop_permission(request: object, invocation: dict[str, str]) -> PermissionRequestResult:
            return _Result(kind="approved")

        try:
            import tempfile

            session = await client.create_session(
                SdkSessionConfig(
                    working_directory=tempfile.gettempdir(),
                    on_permission_request=_noop_permission,
                )
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
            log.error("complete_failed", prompt_len=len(prompt), exc_info=True)
            return None
        finally:
            try:
                s = self._sessions.get(tmp_session_id)
                if s:
                    await s.abort()
            except Exception:
                log.warning("copilot_complete_cleanup_failed", session_id=tmp_session_id, exc_info=True)
            self._cleanup_session(tmp_session_id)
