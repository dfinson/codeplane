"""Copilot SDK adapter — bridges the Copilot Python SDK into CodePlane."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC
from typing import TYPE_CHECKING, Any

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
    is_git_reset_hard,
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
        session_factory: Any | None = None,
    ) -> None:
        self._queues: dict[str, asyncio.Queue[SessionEvent | None]] = {}
        self._sessions: dict[str, CopilotSession] = {}
        self._clients: dict[str, Any] = {}  # session_id → CopilotClient (owns CLI server process)
        self._session_to_job: dict[str, str] = {}  # session_id → job_id for telemetry
        self._paused_sessions: set[str] = set()
        self._tool_start_times: dict[str, float] = {}  # tool_call_id → start monotonic
        # Buffers tool.execution_start data so we can emit a combined entry on complete
        self._tool_call_buffer: dict[str, dict[str, str]] = {}  # tool_call_id → {tool_name, tool_args, turn_id}
        self._approval_service = approval_service
        self._event_bus = event_bus
        self._session_factory = session_factory
        # Per-job monotonic start time for computing span offsets
        self._job_start_times: dict[str, float] = {}
        # Per-job confirmed main model
        self._job_main_models: dict[str, str] = {}

    def set_job_id(self, session_id: str, job_id: str) -> None:
        """Associate a session with a job for telemetry routing."""
        import time as _time

        self._session_to_job[session_id] = job_id
        self._job_start_times.setdefault(job_id, _time.monotonic())

    def _schedule_db_write(self, coro: Any) -> None:  # noqa: ANN401
        """Schedule an async DB write from a synchronous SDK callback."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass  # No event loop — skip DB write (shouldn't happen in normal operation)

    async def _db_write(self, fn_name: str, **kwargs: Any) -> None:
        """Execute a telemetry DB write in its own session."""
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo
                from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

                if fn_name == "increment":
                    await TelemetrySummaryRepo(session).increment(**kwargs)
                elif fn_name == "insert_span":
                    await TelemetrySpansRepo(session).insert(**kwargs)
                elif fn_name == "set_model":
                    await TelemetrySummaryRepo(session).set_model(**kwargs)
                elif fn_name == "set_context":
                    await TelemetrySummaryRepo(session).set_context(**kwargs)
                elif fn_name == "set_quota":
                    await TelemetrySummaryRepo(session).set_quota(**kwargs)
                await session.commit()
        except Exception:
            log.debug("telemetry_db_write_failed", fn=fn_name, exc_info=True)

    def _cleanup_session(self, session_id: str) -> None:
        """Remove session and queue references for a completed/aborted session.

        Also stops the CopilotClient that owns the backing CLI server process
        to prevent leaked child processes from accumulating over time.
        """
        self._paused_sessions.discard(session_id)
        job_id = self._session_to_job.pop(session_id, None)
        self._sessions.pop(session_id, None)
        self._queues.pop(session_id, None)
        client = self._clients.pop(session_id, None)
        if client is not None:
            asyncio.ensure_future(self._stop_client(client))
        if job_id:
            self._job_start_times.pop(job_id, None)
            self._job_main_models.pop(job_id, None)

    @staticmethod
    async def _stop_client(client: Any) -> None:  # noqa: ANN401
        """Stop a CopilotClient, terminating its CLI server process."""
        try:
            await asyncio.wait_for(client.stop(), timeout=10)
        except TimeoutError:
            log.warning("copilot_client_stop_timeout_forcing")
            with contextlib.suppress(Exception):
                await client.force_stop()
        except Exception:
            log.warning("copilot_client_stop_failed", exc_info=True)
            with contextlib.suppress(Exception):
                await client.force_stop()

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

        # Paused — immediately deny all tools so the agent cannot act.
        if sid in self._paused_sessions:
            return _Result(kind="denied-interactively-by-user")

        # ----------------------------------------------------------------
        # Hard block: git reset --hard always requires explicit operator
        # approval — no trust bypass, no auto mode bypass, ever.
        # ----------------------------------------------------------------
        if kind_val == "shell" and request.full_command_text and is_git_reset_hard(request.full_command_text):
            approval_service = self._approval_service
            job_id = self._session_to_job.get(sid)

            if approval_service is None or job_id is None:
                log.error(
                    "git_reset_hard_blocked_no_infra",
                    command=request.full_command_text[:200],
                )
                return _Result(kind="denied-interactively-by-user")

            description = (
                "⚠️ git reset --hard — this will discard ALL uncommitted changes and "
                f"move HEAD: {request.full_command_text}"
            )
            approval = await approval_service.create_request(
                job_id=job_id,
                description=description,
                proposed_action=request.full_command_text,
                requires_explicit_approval=True,
            )
            event_queue = self._queues.get(sid)
            if event_queue is not None:
                event_queue.put_nowait(
                    SessionEvent(
                        kind=SessionEventKind.approval_request,
                        payload={
                            "description": description,
                            "proposed_action": request.full_command_text,
                            "approval_id": approval.id,
                            "requires_explicit_approval": True,
                        },
                    )
                )
            log.warning(
                "git_reset_hard_awaiting_operator",
                approval_id=approval.id,
                job_id=job_id,
                command=request.full_command_text[:200],
            )
            resolution = await approval_service.wait_for_resolution(approval.id)
            if resolution == "approved":
                return _Result(kind="approved")
            return _Result(kind="denied-interactively-by-user")

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
        event_queue = self._queues.get(sid)
        if event_queue is not None:
            event_queue.put_nowait(
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
        "assistant.streaming_delta": SessionEventKind.transcript,
        "user.message": SessionEventKind.transcript,
        # assistant.reasoning is intentionally NOT mapped — it duplicates
        # the reasoning_text already embedded in assistant.message.
        "tool.execution_complete": SessionEventKind.transcript,
        "tool.execution_start": SessionEventKind.transcript,
        "session.workspace_file_changed": SessionEventKind.file_changed,
    }

    # --- Extracted telemetry handlers ---

    def _handle_usage_event(
        self,
        data: Any,
        job_id: str,
        requested_model: str,
        model_verified: list[bool],
        queue: asyncio.Queue[SessionEvent | None],
    ) -> None:
        import time as _time

        from backend.services import telemetry as tel

        actual_model = data.model or ""
        is_subagent = False

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
            self._job_main_models[job_id] = actual_model
            self._schedule_db_write(self._db_write("set_model", job_id=job_id, model=actual_model))

        # Sub-agent detection
        main_model = self._job_main_models.get(job_id, "")
        if main_model and actual_model and actual_model != main_model:
            is_subagent = True

        input_toks = int(data.input_tokens or 0)
        output_toks = int(data.output_tokens or 0)
        cache_read = int(data.cache_read_tokens or 0)
        cache_write = int(data.cache_write_tokens or 0)
        cost = float(data.cost or 0)
        duration_ms = float(data.duration or 0)

        attrs = {"job_id": job_id, "sdk": "copilot", "model": actual_model}

        # OTEL instruments
        tel.tokens_input.add(input_toks, attrs)
        tel.tokens_output.add(output_toks, attrs)
        tel.tokens_cache_read.add(cache_read, attrs)
        tel.tokens_cache_write.add(cache_write, attrs)
        tel.cost_usd.add(cost, attrs)
        tel.llm_duration.record(duration_ms, {**attrs, "is_subagent": is_subagent})

        # SQLite summary increment
        self._schedule_db_write(
            self._db_write(
                "increment",
                job_id=job_id,
                input_tokens=input_toks,
                output_tokens=output_toks,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                total_cost_usd=cost,
                llm_call_count=1,
                total_llm_duration_ms=int(duration_ms),
            )
        )

        # SQLite span detail
        start_time = self._job_start_times.get(job_id, _time.monotonic())
        offset = _time.monotonic() - start_time
        self._schedule_db_write(
            self._db_write(
                "insert_span",
                job_id=job_id,
                span_type="llm",
                name=actual_model or "unknown",
                started_at=round(offset, 2),
                duration_ms=duration_ms,
                attrs={
                    "input_tokens": input_toks,
                    "output_tokens": output_toks,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "cost": cost,
                    "is_subagent": is_subagent,
                },
            )
        )

        # Capture Copilot quota snapshots if present
        raw_snapshots = getattr(data, "quota_snapshots", None)
        if raw_snapshots:
            import json as _json

            parsed: dict[str, dict[str, Any]] = {}
            for key, snap in raw_snapshots.items():
                used = float(getattr(snap, "used_requests", 0) or 0)
                entitlement = float(getattr(snap, "entitlement_requests", 0) or 0)
                remaining = float(getattr(snap, "remaining_percentage", 0) or 0)
                parsed[key] = {
                    "used_requests": used,
                    "entitlement_requests": entitlement,
                    "remaining_percentage": remaining,
                    "overage": float(getattr(snap, "overage", 0) or 0),
                    "overage_allowed": bool(getattr(snap, "overage_allowed_with_exhausted_quota", False)),
                    "is_unlimited": bool(getattr(snap, "is_unlimited_entitlement", False)),
                    "reset_date": str(getattr(snap, "reset_date", "") or ""),
                }
                # OTEL gauges
                tel.quota_used_gauge.set(used, {"job_id": job_id, "sdk": "copilot", "resource": key})
                tel.quota_entitlement_gauge.set(entitlement, {"job_id": job_id, "sdk": "copilot", "resource": key})
                tel.quota_remaining_gauge.set(remaining, {"job_id": job_id, "sdk": "copilot", "resource": key})

            self._schedule_db_write(
                self._db_write(
                    "set_quota",
                    job_id=job_id,
                    quota_json=_json.dumps(parsed),
                )
            )

    def _handle_tool_start(self, data: Any, job_id: str) -> None:
        tool_id = data.tool_call_id or ""
        import json as _json
        import time as _time

        self._tool_start_times[tool_id] = _time.monotonic()
        # Buffer args for the combined transcript entry emitted on complete
        args_str: str | None = None
        if data.arguments is not None:
            try:
                args_str = _json.dumps(data.arguments) if not isinstance(data.arguments, str) else data.arguments
            except Exception:
                log.debug("tool_args_serialize_failed", tool_id=tool_id, exc_info=True)
                args_str = str(data.arguments)
        t_name = data.tool_name or data.mcp_tool_name or "tool"
        t_name_display = (
            f"{data.mcp_server_name}/{data.mcp_tool_name}" if data.mcp_server_name and data.mcp_tool_name else t_name
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

    def _handle_tool_end(self, data: Any, job_id: str) -> None:
        tool_id = data.tool_call_id or ""
        import time as _time

        from backend.services import telemetry as tel

        start = self._tool_start_times.pop(tool_id, _time.monotonic())
        dur = (_time.monotonic() - start) * 1000
        # Prefer the display name buffered at tool.execution_start
        buffered_name = self._tool_call_buffer.get(tool_id, {}).get("tool_name")
        resolved_name = buffered_name or data.tool_name or data.mcp_tool_name or "tool"
        success = bool(data.success) if data.success is not None else True

        attrs = {"job_id": job_id, "sdk": "copilot", "tool_name": resolved_name, "success": success}
        tel.tool_duration.record(dur, attrs)

        # SQLite writes
        self._schedule_db_write(
            self._db_write(
                "increment",
                job_id=job_id,
                tool_call_count=1,
                tool_failure_count=0 if success else 1,
                total_tool_duration_ms=int(dur),
            )
        )

        job_start = self._job_start_times.get(job_id, _time.monotonic())
        offset = _time.monotonic() - job_start
        self._schedule_db_write(
            self._db_write(
                "insert_span",
                job_id=job_id,
                span_type="tool",
                name=resolved_name,
                started_at=round(offset, 2),
                duration_ms=dur,
                attrs={"success": success},
            )
        )

    def _handle_context_changed(self, data: Any, job_id: str) -> None:
        from backend.services import telemetry as tel

        current = int(data.current_tokens or 0)
        attrs = {"job_id": job_id, "sdk": "copilot"}
        tel.context_tokens_gauge.set(current, attrs)

        self._schedule_db_write(
            self._db_write(
                "set_context",
                job_id=job_id,
                current_tokens=current,
            )
        )

    def _handle_compaction(self, data: Any, job_id: str) -> None:
        from backend.services import telemetry as tel

        pre = int(data.pre_compaction_tokens or 0)
        post = int(data.post_compaction_tokens or 0)
        attrs = {"job_id": job_id, "sdk": "copilot"}
        tel.compactions_counter.add(1, attrs)
        tel.tokens_compacted.add(max(0, pre - post), attrs)

        self._schedule_db_write(
            self._db_write(
                "increment",
                job_id=job_id,
                compactions=1,
                tokens_compacted=max(0, pre - post),
            )
        )

        if post:
            tel.context_tokens_gauge.set(post, attrs)
            self._schedule_db_write(
                self._db_write(
                    "set_context",
                    job_id=job_id,
                    current_tokens=post,
                )
            )

    # --- Log emission ---

    def _emit_log_event(
        self,
        kind_str: str,
        data: Any,
        requested_model: str,
        queue: asyncio.Queue[SessionEvent | None],
        log_seq: list[int],
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
                    f"\u26a0 MODEL MISMATCH: requested {requested_model}"
                    f" but serving {model} ({in_tok}+{out_tok} tokens)"
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
        self,
        kind_str: str,
        data: Any,
        payload: dict[str, object],
        queue: asyncio.Queue[SessionEvent | None],
        session_id: str,
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
                elif kind_str == "assistant.streaming_delta":
                    delta = (data.delta_content or "") if data else ""
                    if not delta:
                        return
                    event_payload = {
                        "role": "agent_delta",
                        "content": delta,
                        "turn_id": (str(data.turn_id) if data and data.turn_id else None),
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
                elif kind_str == "tool.execution_start":
                    tool_id = (data.tool_call_id or "") if data else ""
                    buffered = self._tool_call_buffer.get(tool_id, {})
                    tool_name = buffered.get("tool_name", "tool")
                    # Drop SDK-internal tools from transcript
                    if tool_name in ("report_intent",):
                        return
                    from backend.services.tool_formatters import format_tool_display

                    turn_id = buffered.get("turn_id") or (
                        str(data.turn_id) if data and hasattr(data, "turn_id") and data.turn_id else None
                    )
                    event_payload = {
                        "role": "tool_running",
                        "content": tool_name,
                        "tool_name": tool_name,
                        "tool_args": buffered.get("tool_args"),
                        "turn_id": turn_id,
                        "tool_intent": buffered.get("tool_intent"),
                        "tool_title": buffered.get("tool_title"),
                        "tool_display": format_tool_display(tool_name, buffered.get("tool_args")),
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
                    # Compute tool execution duration
                    import time as _time

                    _start = self._tool_start_times.get(tool_id)
                    dur_ms = int((_time.monotonic() - _start) * 1000) if _start is not None else None
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
                        "tool_duration_ms": dur_ms,
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
        from copilot import CopilotClient
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
        self._clients[session_id] = client

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

            # --- Copilot SDK → OTEL telemetry + SQLite ---
            job_id = self._session_to_job.get(session_id)
            if job_id and data:
                from backend.services import telemetry as tel

                handler_name = self._TELEMETRY_DISPATCH.get(kind_str)
                if handler_name:
                    handler = getattr(self, handler_name)
                    if kind_str == "assistant.usage":
                        handler(data, job_id, requested_model, _model_verified, queue)
                    else:
                        handler(data, job_id)
                elif kind_str == "session.truncation":
                    if data.token_limit:
                        window = int(data.token_limit)
                        tel.context_window_gauge.set(window, {"job_id": job_id, "sdk": "copilot"})
                        self._schedule_db_write(self._db_write("set_context", job_id=job_id, window_size=window))
                elif kind_str == "session.model_change":
                    if data.new_model:
                        self._job_main_models[job_id] = data.new_model
                        self._schedule_db_write(self._db_write("set_model", job_id=job_id, model=data.new_model))
                elif kind_str == "assistant.message":
                    tel.messages_counter.add(1, {"job_id": job_id, "sdk": "copilot", "role": "agent"})
                    self._schedule_db_write(self._db_write("increment", job_id=job_id, agent_messages=1))
                elif kind_str == "user.message":
                    tel.messages_counter.add(1, {"job_id": job_id, "sdk": "copilot", "role": "operator"})
                    self._schedule_db_write(self._db_write("increment", job_id=job_id, operator_messages=1))
                elif kind_str == "session.shutdown":
                    total_pr = getattr(data, "total_premium_requests", None)
                    if data and total_pr is not None:
                        tel.premium_requests_counter.add(float(total_pr), {"job_id": job_id, "sdk": "copilot"})
                        self._schedule_db_write(
                            self._db_write("increment", job_id=job_id, premium_requests=float(total_pr))
                        )

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

    def pause_tools(self, session_id: str) -> None:
        self._paused_sessions.add(session_id)

    def resume_tools(self, session_id: str) -> None:
        self._paused_sessions.discard(session_id)

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
        self._clients[tmp_session_id] = client

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
                cleanup_session = self._sessions.get(tmp_session_id)
                if cleanup_session:
                    await cleanup_session.abort()
            except Exception:
                log.warning("copilot_complete_cleanup_failed", session_id=tmp_session_id, exc_info=True)
            self._cleanup_session(tmp_session_id)
