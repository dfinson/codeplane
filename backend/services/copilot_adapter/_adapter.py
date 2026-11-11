"""Copilot SDK adapter — bridges the Copilot Python SDK into CodePlane."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

import structlog
from traceforge.types import ToolMotivation

from backend.models.events import EventKind, SessionEvent, new_event
from backend.services.adapters.agent_adapter import CODEPLANE_SYSTEM_PROMPT, CompletionResult
from backend.services.adapters.base_adapter import (
    CLIENT_STOP_TIMEOUT_S,
    COMPLETION_TIMEOUT_S,
    BaseAgentAdapter,
    PermissionDecision,
)
from backend.services.auth.permission_policy import PermissionRequest as PolicyRequest
from backend.services.copilot_adapter._client import create_copilot_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from copilot import CopilotClient
    from copilot.generated.session_events import (
        AssistantMessageData,
        AssistantMessageDeltaData,
        AssistantReasoningData,
        AssistantReasoningDeltaData,
        AssistantUsageData,
        PermissionRequest,
        SessionCompactionCompleteData,
        SessionModelChangeData,
        SessionShutdownData,
        SessionTruncationData,
        SessionUsageInfoData,
        ToolExecutionCompleteData,
        ToolExecutionPartialResultData,
        ToolExecutionStartData,
        UserMessageData,
    )
    from copilot.generated.session_events import (
        SessionEvent as SdkSessionEvent,
    )
    from copilot.session import CopilotSession, PermissionRequestResult, SystemMessageAppendConfig
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import SessionConfig
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()


class CopilotAdapter(BaseAgentAdapter):
    """Wraps the Python Copilot SDK behind the adapter interface.

    Uses a callback-to-iterator bridge: SDK callbacks push SessionEvent
    items onto an asyncio.Queue; stream_events() yields from the queue.
    """

    _source_framework = "copilot"

    def __init__(
        self,
        approval_service: ApprovalService | None = None,
        event_bus: EventBus | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        super().__init__(
            approval_service=approval_service,
            event_bus=event_bus,
            session_factory=session_factory,
        )
        self._sessions: dict[str, CopilotSession] = {}

        # Tool-call buffer: tool_call_id -> {job_id, tool_name, arguments, intent, title, start}
        # Populated on tool.execution_start, drained on tool.execution_complete to
        # compute duration_ms and carry start-time context (re-homed from EventPipeline).
        self._tool_buf: dict[str, dict[str, Any]] = {}

    def _cleanup_session(self, session_id: str) -> None:
        """Remove session and queue references for a completed/aborted session.

        Also stops the CopilotClient that owns the backing CLI server process
        to prevent leaked child processes from accumulating over time.
        """
        self._sessions.pop(session_id, None)
        client = self._clients.get(session_id)
        if client is not None:
            asyncio.ensure_future(self._stop_client(client))
        # Drop any un-drained tool-buffer entries for this job.
        job_id = self._session_to_job.get(session_id)
        if job_id:
            self._log_seqs.pop(job_id, None)
            for cid in [c for c, v in self._tool_buf.items() if v.get("job_id") == job_id]:
                self._tool_buf.pop(cid, None)
        super()._cleanup_session_state(session_id)

    @staticmethod
    async def _stop_client(client: CopilotClient) -> None:
        """Stop a CopilotClient, terminating its CLI server process."""
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        try:
            await asyncio.wait_for(client.stop(), timeout=CLIENT_STOP_TIMEOUT_S)
        except TimeoutError:
            log.warning("copilot_client_stop_timeout_forcing")
            with contextlib.suppress(ProcessExitedError, JsonRpcError, OSError):
                await client.force_stop()
        except (ProcessExitedError, JsonRpcError, OSError):
            log.warning("copilot_client_stop_failed", exc_info=True)
            with contextlib.suppress(ProcessExitedError, JsonRpcError, OSError):
                await client.force_stop()

    async def _handle_permission_request(
        self,
        request: PermissionRequest,
        invocation: dict[str, str],
        config: SessionConfig,
    ) -> PermissionRequestResult:
        """Bridge SDK permission requests into CodePlane's approval system.

        Wraps the base adapter's ``_evaluate_permission`` to return SDK-
        specific PermissionRequestResult objects.
        """
        from copilot.session import PermissionRequestResult as _Result

        kind_val = request.kind.value if request.kind else "unknown"
        sid = invocation.get("session_id", "")

        # Collect path candidates for policy evaluation
        candidate_paths: list[str] = []
        if request.file_name:
            candidate_paths.append(request.file_name)
        if request.path:
            candidate_paths.append(request.path)
        if request.possible_paths:
            candidate_paths.extend(request.possible_paths)

        if config.session_kind == "preflight":
            from backend.services.tools.preflight_curator import scout_read_allowed

            if request.full_command_text or not scout_read_allowed(
                config.workspace_path, kind_val, request.tool_name or "", candidate_paths
            ):
                return _Result(kind="reject")

        # Build tool_input dict for the base method
        tool_input: dict[str, Any] = {}
        if request.full_command_text:
            tool_input["command"] = request.full_command_text

        perm_request = PolicyRequest(
            kind=kind_val,
            workspace_path=config.workspace_path,
            full_command_text=request.full_command_text,
            file_name=request.file_name,
            path=request.path,
            read_only=request.read_only,
            possible_paths=candidate_paths or None,
        )
        decision = await self._evaluate_permission(
            sid,
            self._session_to_job.get(sid),
            perm_request,
            tool_name=request.tool_name or "",
            tool_input=tool_input or None,
        )
        if decision == PermissionDecision.allow:
            return _Result(kind="approve-once")
        return _Result(kind="reject")

    # --- Pipeline-based event processing ---

    async def _process_sdk_event_via_pipeline(
        self,
        sdk_event: SdkSessionEvent,
        session_id: str,
        job_id: str | None,
        requested_model: str,
        model_verified: list[bool],
        queue: asyncio.Queue[SessionEvent | None],
    ) -> None:
        """Process a single SDK event: telemetry, enrichment, log emission, delivery.

        Called via create_task from the synchronous SDK callback. Handles
        telemetry, enrichment, log emission, and delivery of native
        SessionEvents onto the queue.
        """
        try:
            await self._process_sdk_event_inner(
                sdk_event,
                session_id,
                job_id,
                requested_model,
                model_verified,
                queue,
            )
        except Exception:
            log.warning(
                "copilot_pipeline_event_error",
                session_id=session_id,
                job_id=job_id,
                event_type=getattr(sdk_event.type, "value", None),
                exc_info=True,
            )

    async def _process_sdk_event_inner(
        self,
        sdk_event: SdkSessionEvent,
        session_id: str,
        job_id: str | None,
        requested_model: str,
        model_verified: list[bool],
        queue: asyncio.Queue[SessionEvent | None],
    ) -> None:
        """Inner implementation for SDK event processing (no exception guard)."""
        kind_str = sdk_event.type.value if sdk_event.type else ""
        data = sdk_event.data

        if not job_id:
            # No job association yet — only bridge done/error for sentinel
            if kind_str in ("session.task_complete", "session.idle", "session.shutdown") or kind_str == "session.error":
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            return

        # --- Transcript events ---
        if kind_str == "assistant.message":
            am = cast("AssistantMessageData", data) if data else None
            content = (am.content or "") if am else ""
            if content.strip():
                title = getattr(am, "title", None) if am else None
                payload: dict[str, Any] = {"content": content}
                if title:
                    payload["title"] = title
                self._emit_tf(session_id, job_id, EventKind.message_assistant, payload)

        elif kind_str == "assistant.message_delta":
            md = cast("AssistantMessageDeltaData", data) if data else None
            delta = (md.delta_content or "") if md else ""
            if delta:
                self._emit_tf(session_id, job_id, EventKind.message_delta, {"content": delta}, partial=True)

        elif kind_str == "assistant.reasoning":
            ar = cast("AssistantReasoningData", data) if data else None
            content = (ar.content or "") if ar else ""
            if content:
                self._emit_tf(session_id, job_id, EventKind.llm_reasoning_chunk, {"content": content})

        elif kind_str == "assistant.reasoning_delta":
            rd = cast("AssistantReasoningDeltaData", data) if data else None
            delta = (rd.delta_content or "") if rd else ""
            if delta:
                self._emit_tf(session_id, job_id, EventKind.llm_reasoning_chunk, {"content": delta}, partial=True)

        elif kind_str == "user.message":
            um = cast("UserMessageData", data) if data else None
            content = (um.content or "") if um else ""
            if "<system_notification>" in content:
                return
            if content.strip():
                self._emit_tf(session_id, job_id, EventKind.message_user, {"content": content})

        # --- Tool lifecycle ---
        elif kind_str == "tool.execution_start":
            ts = cast("ToolExecutionStartData", data) if data else None
            if ts:
                tool_name = ts.tool_name or ts.mcp_tool_name or "tool"
                if ts.mcp_server_name and ts.mcp_tool_name:
                    tool_name = f"{ts.mcp_server_name}/{ts.mcp_tool_name}"

                tool_id = ts.tool_call_id or ""
                args_val: Any = ts.arguments

                tool_intent = getattr(ts, "intention", None) or ""
                tool_title = getattr(ts, "tool_title", None) or ""
                if not tool_intent and isinstance(ts.arguments, dict):
                    tool_intent = str(ts.arguments.get("description", ""))

                self._tool_buf[tool_id] = {
                    "job_id": job_id,
                    "tool_name": tool_name,
                    "arguments": args_val,
                    "intent": tool_intent,
                    "title": tool_title,
                    "start": time.monotonic(),
                }

                start_payload: dict[str, Any] = {
                    "tool_name": tool_name,
                    "arguments": args_val,
                    "tool_call_id": tool_id,
                    "content": tool_name,
                }
                if tool_title:
                    start_payload["title"] = tool_title
                self._emit_tf(
                    session_id,
                    job_id,
                    EventKind.tool_call_started,
                    start_payload,
                    motivation=ToolMotivation(intent=tool_intent) if tool_intent else None,
                )

        elif kind_str == "tool.execution_partial_result":
            pr = cast("ToolExecutionPartialResultData", data) if data else None
            if pr:
                tool_id = pr.tool_call_id or ""
                chunk = pr.partial_output or ""
                if chunk:
                    self._emit_tf(
                        session_id,
                        job_id,
                        EventKind.tool_result_chunk,
                        {"tool_call_id": tool_id, "content": chunk},
                        partial=True,
                    )

        elif kind_str == "tool.execution_complete":
            tc = cast("ToolExecutionCompleteData", data) if data else None
            if tc:
                tool_id = tc.tool_call_id or ""
                success = bool(tc.success) if tc.success is not None else True
                result_text = ""
                if tc.result is not None:
                    result_content: object | None = getattr(tc.result, "content", None)
                    if result_content:
                        result_text = self._extract_result_text(result_content)
                    elif result_content is None:
                        # No content attribute or it's None — leave empty
                        pass
                    else:
                        result_text = str(tc.result)

                buffered = self._tool_buf.pop(tool_id, {})
                resolved_name = str(buffered.get("tool_name", "tool"))

                # Correct false failures for file-edit tools
                if not success:
                    from backend.services.tool_formatters import correct_edit_success

                    success = correct_edit_success(resolved_name, success, result_text)

                start = buffered.get("start")
                duration_ms = (time.monotonic() - start) * 1000.0 if start else None
                intent = str(buffered.get("intent") or "")
                self._emit_tf(
                    session_id,
                    job_id,
                    EventKind.tool_call_completed,
                    {
                        "tool_name": resolved_name,
                        "arguments": buffered.get("arguments"),
                        "result": result_text,
                        "success": success,
                        "tool_call_id": tool_id,
                    },
                    duration_ms=duration_ms,
                    motivation=ToolMotivation(intent=intent) if intent else None,
                )

        # --- File change notification ---
        elif kind_str == "session.workspace_file_changed":
            file_path = getattr(data, "file_path", None) or ""
            if file_path:
                self._emit_tf(session_id, job_id, EventKind.file_edited, {"path": file_path})

        # --- Usage / telemetry (native TF usage record; DB rollups re-homed to
        #     the telemetry bus-subscriber that consumes telemetry.usage) ---
        elif kind_str == "assistant.usage":
            au = cast("AssistantUsageData", data) if data else None
            if au:
                actual_model = au.model or ""

                # Model verification (first call only)
                if not model_verified[0] and requested_model and actual_model:
                    model_verified[0] = True
                    if self.get_session_kind(session_id) == "preflight":
                        log.info(
                            "preflight_model_observed",
                            job_id=job_id,
                            session_id=session_id,
                            requested_model=requested_model,
                            actual_model=actual_model,
                        )
                    else:
                        self._verify_and_set_model(session_id, job_id, actual_model, requested_model)

                # Sub-agent detection
                main_model = self._job_main_models.get(job_id, "")
                is_subagent = bool(main_model and actual_model != main_model)

                self._emit_tf(
                    session_id,
                    job_id,
                    EventKind.telemetry_usage,
                    {
                        "model": actual_model,
                        "input_tokens": int(au.input_tokens or 0),
                        "output_tokens": int(au.output_tokens or 0),
                        "cache_read_tokens": int(au.cache_read_tokens or 0),
                        "cache_write_tokens": int(au.cache_write_tokens or 0),
                        "cost_usd": float(au.cost or 0),
                        "duration_ms": float(au.duration or 0),
                        "is_subagent": is_subagent,
                        "advance_turn": True,
                        "num_turns": 1,
                    },
                )

                # Copilot-specific: quota snapshots
                if au.quota_snapshots:
                    self._record_quota_snapshots(au.quota_snapshots, job_id)

        # --- Managed-only session-state (context/compaction/truncation/premium/
        #     model). Not bus events (imported path has no equivalent); reproduced
        #     as direct DB writes + OTel metrics re-homed from EventPipeline. ---
        elif kind_str == "session.usage_info":
            sui = cast("SessionUsageInfoData", data) if data else None
            if sui:
                current = int(sui.current_tokens or 0)
                if current:
                    from backend.services.analytics import telemetry as tel

                    tel.context_tokens_gauge.set(current, {"job_id": job_id, "sdk": "copilot"})
                    self._schedule_db_write(self._db_write_set_context(job_id=job_id, current_tokens=current))

        elif kind_str == "session.compaction_complete":
            cc = cast("SessionCompactionCompleteData", data) if data else None
            if cc:
                pre = int(cc.pre_compaction_tokens or 0)
                post = int(cc.post_compaction_tokens or 0)
                compacted = max(0, pre - post)
                from backend.services.analytics import telemetry as tel

                attrs: dict[str, Any] = {"job_id": job_id, "sdk": "copilot"}
                tel.compactions_counter.add(1, attrs)
                tel.tokens_compacted.add(compacted, attrs)
                self._schedule_db_write(
                    self._db_write_increment(job_id=job_id, compactions=1, tokens_compacted=compacted)
                )
                if post:
                    tel.context_tokens_gauge.set(post, attrs)
                    self._schedule_db_write(self._db_write_set_context(job_id=job_id, current_tokens=post))
                self._emit_log_line(session_id, job_id, f"Context compacted: {pre} \u2192 {post} tokens", "warn")

        elif kind_str == "session.truncation":
            trunc = cast("SessionTruncationData", data) if data else None
            if trunc and trunc.token_limit:
                window = int(trunc.token_limit)
                from backend.services.analytics import telemetry as tel

                tel.context_window_gauge.set(window, {"job_id": job_id, "sdk": "copilot"})
                self._schedule_db_write(self._db_write_set_context(job_id=job_id, window_size=window))

        elif kind_str == "session.model_change":
            mc = cast("SessionModelChangeData", data) if data else None
            if mc and mc.new_model and self.get_session_kind(session_id) != "preflight":
                self._job_main_models[job_id] = mc.new_model
                self._schedule_db_write(self._db_write_set_model(job_id=job_id, model=mc.new_model))
                self._emit_log_line(session_id, job_id, f"Model changed to {mc.new_model}", "info")

        elif kind_str == "session.shutdown":
            sd = cast("SessionShutdownData", data) if data else None
            if sd and sd.total_premium_requests is not None:
                premium = int(sd.total_premium_requests)
                from backend.services.analytics import telemetry as tel

                tel.premium_requests_counter.add(premium, {"job_id": job_id, "sdk": "copilot"})
                self._schedule_db_write(self._db_write_increment(job_id=job_id, premium_requests=premium))

        # --- Session lifecycle (sentinel terminates stream_events) ---
        if kind_str in ("session.task_complete", "session.idle", "session.shutdown"):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        elif kind_str == "session.error":
            err_payload = data.to_dict() if data and hasattr(data, "to_dict") else {}
            self._emit_tf(session_id, job_id, EventKind.job_failed, cast("dict[str, Any]", err_payload))
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    def _record_quota_snapshots(self, quota_snapshots: Any, job_id: str) -> None:
        """Record Copilot quota snapshot telemetry (adapter-specific)."""
        import json as _json

        from backend.services.analytics import telemetry as tel

        parsed: dict[str, dict[str, Any]] = {}
        for key, snap in quota_snapshots.items():
            used = float(snap.used_requests or 0)
            entitlement = float(snap.entitlement_requests or 0)
            remaining = float(snap.remaining_percentage or 0)
            parsed[key] = {
                "used_requests": used,
                "entitlement_requests": entitlement,
                "remaining_percentage": remaining,
                "overage": float(snap.overage or 0),
                "overage_allowed": bool(snap.overage_allowed_with_exhausted_quota),
                "is_unlimited": bool(snap.is_unlimited_entitlement),
                "reset_date": str(snap.reset_date or ""),
            }
            tel.quota_used_gauge.set(
                used,
                {"job_id": job_id, "sdk": "copilot", "resource": key},
            )
            tel.quota_entitlement_gauge.set(
                entitlement,
                {"job_id": job_id, "sdk": "copilot", "resource": key},
            )
            tel.quota_remaining_gauge.set(
                remaining,
                {"job_id": job_id, "sdk": "copilot", "resource": key},
            )

        self._schedule_db_write(self._db_write_set_quota(job_id=job_id, quota_remaining=_json.dumps(parsed)))

    async def create_session(self, config: SessionConfig) -> str:
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        client = create_copilot_client()

        # Thin closure that delegates to the instance method, capturing only `config`.
        async def _on_permission(request: PermissionRequest, invocation: dict[str, str]) -> PermissionRequestResult:
            return await self._handle_permission_request(request, invocation, config)

        if config.system_prompt_override:
            # Sidecar/preflight sessions define their own identity — skip the
            # main-agent CODEPLANE_SYSTEM_PROMPT and report_intent boilerplate.
            base_prompt = config.system_prompt_override
        else:
            base_prompt = (
                CODEPLANE_SYSTEM_PROMPT + "\n\n"
                "**REPORT INTENT — REQUIRED BEFORE EVERY TOOL BURST:**\n"
                "Call `report_intent` in parallel with your FIRST tool call whenever you start "
                "a new group of related tool calls. The intent you declare is shown to the user "
                "in real-time so they understand what you are working on and why. Make it "
                "descriptive of the HIGH-LEVEL GOAL of the upcoming calls — not the mechanics "
                "(e.g., 'Exploring authentication module to understand token refresh flow' rather "
                "than 'reading files'). Call `report_intent` again whenever your focus shifts "
                "to a new sub-task. Never call it in isolation — always pair it with at least "
                "one other tool call in the same turn."
            )

        # Workspace memory — curated context hidden from transcript
        if config.memory_context:
            base_prompt = base_prompt + "\n\n## Workspace Memory\n\n" + config.memory_context

        # Common session kwargs shared by create and resume
        system_message: SystemMessageAppendConfig = {
            "mode": "append",
            "content": base_prompt,
        }

        available_tools = None
        if config.session_kind == "preflight":
            from backend.services.tools.preflight_curator import SCOUT_AVAILABLE_TOOLS

            available_tools = sorted(SCOUT_AVAILABLE_TOOLS)

        requested_model = config.model or ""
        if config.model:
            log.info("sdk_session_model_requested", model=config.model)

        # Create or resume SDK session; use the SDK-assigned session_id as CodePlane's identifier.
        _resume_id = config.resume_sdk_session_id
        if _resume_id:
            try:
                session = await client.resume_session(
                    _resume_id,
                    on_permission_request=_on_permission,
                    working_directory=config.workspace_path,
                    model=config.model or None,
                    available_tools=available_tools,
                )
                log.info("sdk_session_resumed", sdk_session_id=_resume_id)
            except (JsonRpcError, ProcessExitedError, ConnectionError, TimeoutError):
                log.warning("sdk_session_resume_failed_creating_new", sdk_session_id=_resume_id, exc_info=True)
                session = await client.create_session(
                    on_permission_request=_on_permission,
                    working_directory=config.workspace_path,
                    system_message=system_message,
                    model=config.model or None,
                    available_tools=available_tools,
                    excluded_tools=config.disallowed_tools or None,
                )
        else:
            session = await client.create_session(
                on_permission_request=_on_permission,
                working_directory=config.workspace_path,
                system_message=system_message,
                model=config.model or None,
                available_tools=available_tools,
                excluded_tools=config.disallowed_tools or None,
            )

        session_id = session.session_id  # Use SDK-assigned ID as CodePlane's session identifier
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[session_id] = queue
        self._sessions[session_id] = session
        self._clients[session_id] = client

        # Wire telemetry mapping before registering the callback so
        # no early SDK events are lost.
        if config.job_id:
            self.set_job_id(session_id, config.job_id)
        if config.session_kind != "job":
            self.set_session_kind(session_id, config.session_kind)

        # Track whether we've verified the model on the first usage event.
        _model_verified = [False]

        # Register SDK callback that bridges into the async queue
        # and extracts telemetry from Copilot-specific event types.
        def _on_event(sdk_event: SdkSessionEvent) -> None:
            """Synchronous SDK callback — bridges to the async pipeline via create_task."""
            job_id = self._session_to_job.get(session_id)
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._process_sdk_event_via_pipeline(
                    sdk_event,
                    session_id,
                    job_id,
                    requested_model,
                    _model_verified,
                    queue,
                ),
                name=f"copilot-pipeline-{session_id[:8]}",
            )

        session.on(_on_event)
        # Send initial prompt — cleanup on any failure.
        try:
            await session.send(config.prompt, mode="immediate")
        except BaseException:
            self._cleanup_session(session_id)
            raise
        log.info("copilot_session_created", session_id=session_id)
        return str(session_id)

    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        queue = self._queues.get(session_id)
        if queue is None:
            log.error("copilot_stream_no_queue", session_id=session_id)
            from traceforge.types import EventMetadata

            job_id = self._session_to_job.get(session_id, session_id)
            yield new_event(
                session_id=job_id,
                kind=EventKind.job_failed,
                payload={"message": "No queue for session"},
                metadata=EventMetadata(source_framework=self._source_framework),
            )
            return
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self._cleanup_session(session_id)

    async def send_message(self, session_id: str, message: str) -> None:
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        session = self._sessions.get(session_id)
        if session is None:
            log.warning("copilot_send_no_session", session_id=session_id)
            return
        try:
            await session.send(message, mode="immediate")
        except (JsonRpcError, ProcessExitedError, ConnectionError):
            log.warning("copilot_send_message_failed", session_id=session_id, exc_info=True)

    async def interrupt_session(self, session_id: str) -> None:
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            await session.abort()
            log.info("copilot_session_interrupted", session_id=session_id)
        except (JsonRpcError, ProcessExitedError):
            log.warning("copilot_interrupt_failed", session_id=session_id, exc_info=True)

    async def abort_session(self, session_id: str) -> None:
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            await session.abort()
        except (JsonRpcError, ProcessExitedError):
            log.warning("copilot_abort_failed", session_id=session_id, exc_info=True)
        finally:
            self._cleanup_session(session_id)

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        """Create a minimal session for single-turn completion, collect the response."""
        from copilot import CopilotClient
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError
        from copilot.session import PermissionRequestResult as _Result

        from backend.services.adapters.agent_adapter import CompletionResult

        client = CopilotClient()
        tmp_session_id = str(uuid.uuid4())
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[tmp_session_id] = queue
        self._clients[tmp_session_id] = client

        async def _noop_permission(request: object, invocation: dict[str, str]) -> PermissionRequestResult:
            return _Result(kind="approve-once")

        try:
            import tempfile

            # Build system message config if caller provided one
            sys_msg_config: SystemMessageAppendConfig | None = None
            if system_message:
                sys_msg_config = {
                    "mode": "append",
                    "content": system_message,
                }

            session = await client.create_session(
                working_directory=tempfile.gettempdir(),
                on_permission_request=_noop_permission,
                model=model or None,
                system_message=sys_msg_config,
                excluded_tools=excluded_tools,
            )
            self._sessions[tmp_session_id] = session

            collected: list[str] = []
            done_event = asyncio.Event()

            def _on_event(sdk_event: SdkSessionEvent) -> None:
                kind_str = sdk_event.type.value if sdk_event.type else ""
                payload = sdk_event.data.to_dict() if sdk_event.data else {}
                log.debug(
                    "complete_sdk_event",
                    session_id=tmp_session_id,
                    event_kind=kind_str,
                    payload_keys=list(payload.keys()) if payload else [],
                    content_len=len(payload.get("content", "")) if isinstance(payload.get("content"), str) else 0,
                    tool_name=payload.get("toolName", ""),
                    abort_reason=str(payload.get("reason", "")),
                    tool_requests=bool(payload.get("toolRequests")),
                    usage_tokens=payload.get("currentTokens"),
                    usage_limit=payload.get("tokenLimit"),
                )
                if kind_str == "assistant.message":
                    content = payload.get("content") or ""
                    if content:
                        collected.append(content)
                        done_event.set()
                    elif not payload.get("toolRequests"):
                        # Empty message with no tool requests — agent is done
                        done_event.set()
                    # else: empty content with tool requests — agent is mid-turn,
                    # let it continue executing tools before we return.
                elif kind_str == "abort":
                    log.warning(
                        "complete_sdk_abort",
                        session_id=tmp_session_id,
                        reason=str(payload.get("reason", "")),
                    )
                    done_event.set()
                elif kind_str in ("session.task_complete", "session.idle", "session.error", "session.shutdown"):
                    if kind_str == "session.error":
                        log.warning("complete_sdk_session_error", session_id=tmp_session_id, payload=payload)
                    done_event.set()

            session.on(_on_event)
            await session.send(prompt, mode="immediate")
            try:
                await asyncio.wait_for(done_event.wait(), timeout=COMPLETION_TIMEOUT_S)
            except TimeoutError:
                log.warning("complete_timeout", session_id=tmp_session_id, collected_chunks=len(collected))
            log.debug(
                "complete_result",
                session_id=tmp_session_id,
                collected_chunks=len(collected),
                total_chars=sum(len(c) for c in collected),
                prompt_len=len(prompt),
            )
            return CompletionResult(text="\n".join(collected))
        except (JsonRpcError, ProcessExitedError, ConnectionError, OSError):
            log.error("complete_failed", prompt_len=len(prompt), exc_info=True)
            return CompletionResult()
        finally:
            try:
                cleanup_session = self._sessions.get(tmp_session_id)
                if cleanup_session:
                    await cleanup_session.abort()
            except (JsonRpcError, ProcessExitedError):
                log.warning("copilot_complete_cleanup_failed", session_id=tmp_session_id, exc_info=True)
            self._cleanup_session(tmp_session_id)
