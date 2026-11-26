"""Copilot SDK adapter — bridges the Copilot Python SDK into CodePlane."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Never, TypedDict, cast

import structlog

from backend.models.domain import StateConflictError
from backend.models.events import EventKind, SessionEvent, new_event
from backend.models.session_event import ToolMotivation
from backend.models.usage import UsageAmount
from backend.services.adapters.agent_adapter import CODEPLANE_SYSTEM_PROMPT, CompletionResult, UtilityCompletionError
from backend.services.adapters.base_adapter import (
    CLIENT_STOP_TIMEOUT_S,
    COMPLETION_TIMEOUT_S,
    BaseAgentAdapter,
    PermissionDecision,
)
from backend.services.adapters.completion_context import current_completion_context, validate_completion_identity
from backend.services.auth.permission_policy import PermissionRequest as PolicyRequest
from backend.services.copilot_adapter._client import create_copilot_client, stop_copilot_client
from backend.services.copilot_adapter._schema import prepare_response_schema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from copilot import CopilotClient
    from copilot.generated.session_events import (
        AssistantMessageData,
        AssistantMessageDeltaData,
        AssistantReasoningData,
        AssistantReasoningDeltaData,
        AssistantUsageData,
        PermissionRequest,
        ProviderQuotaState,
        SessionCompactionCompleteData,
        SessionModelChangeData,
        SessionQuotaObservationData,
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
    from copilot.session import (
        CopilotSession,
        PermissionInvocation,
        PermissionRequestResult,
        PreToolUseHookInput,
        PreToolUseHookOutput,
        SessionHooks,
        SystemMessageAppendConfig,
        SystemMessageReplaceConfig,
    )
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import SessionConfig
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()


class _InferenceAuthKwargs(TypedDict, total=False):
    github_token: str


def _sdk_excluded_tools(tools: list[str] | None) -> list[str] | None:
    if tools is not None and "*" in tools:
        return ["builtin:*", "mcp:*", "custom:*"]
    return tools


def _structured_reply_text(text: str) -> tuple[str, bool]:
    envelope = re.fullmatch(r"```json\r?\n(.*?)\r?\n```(?:\r?\n)?", text, flags=re.DOTALL)
    if envelope is not None and text.count("```") != 2:
        raise ValueError("Structured response contains multiple code fences")
    body = envelope.group(1) if envelope is not None else text

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Structured response contains duplicate object keys")
            value[key] = item
        return value

    def invalid_constant(value: str) -> Never:
        raise ValueError(f"Structured response contains a non-JSON constant: {value}")

    parsed = json.loads(body, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if not isinstance(parsed, dict):
        raise ValueError("Structured response must contain one JSON object")
    return body, envelope is not None


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
        self._session_credentials: dict[str, str | None] = {}
        self._callback_tasks: dict[str, set[asyncio.Task[None]]] = {}
        self._stop_tasks: dict[str, asyncio.Task[None]] = {}
        self._opening_tasks: set[asyncio.Task[Any]] = set()
        self._unsubscribe: dict[str, Callable[[], None]] = {}
        self._accepting_events: set[str] = set()
        self._closing = False

        # Tool-call IDs are only unique within one SDK session.
        # Populated on tool.execution_start, drained on tool.execution_complete to
        # compute duration_ms and carry start-time context (re-homed from EventPipeline).
        self._tool_buf: dict[tuple[str, str], dict[str, Any]] = {}

    def _begin_session_cleanup(self, session_id: str) -> asyncio.Task[None]:
        existing = self._stop_tasks.get(session_id)
        if existing is not None:
            return existing
        self.pause_tools(session_id)
        self._accepting_events.discard(session_id)
        unsubscribe = self._unsubscribe.pop(session_id, None)
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                log.warning("copilot_unsubscribe_failed", session_id=session_id, exc_info=True)
        for task in self._callback_tasks.get(session_id, ()):
            task.cancel()
        queue = self._queues.get(session_id)
        if queue is not None:
            queue.put_nowait(None)
        task = asyncio.create_task(self._close_session(session_id), name=f"copilot-stop-{session_id[:8]}")
        self._stop_tasks[session_id] = task
        task.add_done_callback(lambda completed: self._forget_stop(session_id, completed))
        return task

    async def _close_session(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is not None:
            await self._stop_client(client)
        callbacks = tuple(self._callback_tasks.get(session_id, ()))
        if callbacks:
            await asyncio.gather(*callbacks, return_exceptions=True)
        self._sessions.pop(session_id, None)
        self._session_credentials.pop(session_id, None)
        job_id = self._session_to_job.get(session_id)
        last_for_job = not any(sid != session_id and jid == job_id for sid, jid in self._session_to_job.items())
        if job_id and last_for_job:
            self._log_seqs.pop(job_id, None)
        for key in [key for key in self._tool_buf if key[0] == session_id]:
            self._tool_buf.pop(key, None)
        if not last_for_job:
            self._session_to_job.pop(session_id, None)
        super()._cleanup_session_state(session_id)

    def _forget_stop(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._stop_tasks.get(session_id) is task:
            self._stop_tasks.pop(session_id, None)
        if not task.cancelled() and task.exception() is not None:
            log.error("copilot_stop_task_failed", session_id=session_id, error=str(task.exception()))

    async def _cleanup_session(self, session_id: str) -> None:
        """Retain ownership until SDK closure and callback draining both finish."""
        task = self._begin_session_cleanup(session_id)
        cancelled: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError as exc:
                if task.cancelled():
                    raise
                cancelled = exc
        if cancelled is not None:
            raise cancelled

    async def _finish_session(self, session_id: str) -> None:
        await self._cleanup_session(session_id)

    async def shutdown(self) -> None:
        """Stop admission, cancel pending starts and drain all owned SDK work."""
        self._closing = True
        opening = [task for task in self._opening_tasks if task is not asyncio.current_task()]
        for task in opening:
            task.cancel()
        callbacks = [task for tasks in self._callback_tasks.values() for task in tasks]
        for session_id in list(self._clients):
            self._begin_session_cleanup(session_id)
        pending = set(opening + callbacks + list(self._stop_tasks.values()) + self._write_tasks)
        if pending:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=2 * CLIENT_STOP_TIMEOUT_S + 1,
            )
        self._write_tasks.clear()

    @staticmethod
    async def _stop_client(client: CopilotClient) -> None:
        """Stop a CopilotClient, terminating its CLI server process."""
        await stop_copilot_client(client, timeout=CLIENT_STOP_TIMEOUT_S)

    async def _maybe_broadcast_telemetry(self, job_id: str, *, totals: dict[str, float | int] | None = None) -> None:
        if not any(jid == job_id and sid in self._accepting_events for sid, jid in self._session_to_job.items()):
            return
        await super()._maybe_broadcast_telemetry(job_id, totals=totals)

    async def _handle_permission_request(
        self,
        request: PermissionRequest,
        invocation: PermissionInvocation,
        config: SessionConfig,
    ) -> PermissionRequestResult:
        """Bridge SDK permission requests into CodePlane's approval system.

        Wraps the base adapter's ``_evaluate_permission`` to return SDK-
        specific PermissionRequestResult objects.
        """
        from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

        kind_val = request.kind
        sid = invocation.get("session_id", "")
        fence = self._capture_permission_fence(sid)
        if not self._permission_fence_is_current(sid, fence):
            return PermissionDecisionReject()
        file_name = getattr(request, "file_name", None)
        path = getattr(request, "path", None)
        possible_paths = getattr(request, "possible_paths", None)
        full_command_text = getattr(request, "full_command_text", None)
        tool_name = getattr(request, "tool_name", "") or ""

        # Collect path candidates for policy evaluation
        candidate_paths: list[str] = []
        if file_name:
            candidate_paths.append(file_name)
        if path:
            candidate_paths.append(path)
        if possible_paths:
            candidate_paths.extend(possible_paths)

        if config.session_kind == "preflight":
            from backend.services.tools.preflight_curator import scout_read_allowed

            if full_command_text or not scout_read_allowed(config.workspace_path, kind_val, tool_name, candidate_paths):
                return PermissionDecisionReject()

        # Build tool_input dict for the base method
        tool_input: dict[str, Any] = {}
        if full_command_text:
            tool_input["command"] = full_command_text

        perm_request = PolicyRequest(
            kind=kind_val,
            workspace_path=config.workspace_path,
            full_command_text=full_command_text,
            file_name=file_name,
            path=path,
            read_only=getattr(request, "read_only", False),
            possible_paths=candidate_paths or None,
        )
        from backend.services.adapters.capability_guard import enforce_tool_capabilities
        from backend.services.adapters.contracts import CapabilityDeniedError

        try:
            await enforce_tool_capabilities(
                config.execution_capabilities,
                workspace=config.workspace_path,
                kind=kind_val,
                tool_name=tool_name,
                paths=candidate_paths,
                command=full_command_text,
                dialect="powershell" if tool_name == "powershell" else None,
            )
        except CapabilityDeniedError as exc:
            log.warning("execution_capability_denied", job_id=config.job_id, reason=str(exc))
            return PermissionDecisionReject()
        decision = await self._evaluate_permission(
            sid,
            fence[0],
            perm_request,
            tool_name=tool_name,
            tool_input=tool_input or None,
            fence=fence,
            tool_call_id=getattr(request, "tool_call_id", None),
            dialect="powershell" if tool_name == "powershell" else "bash" if tool_name in {"bash", "Bash"} else None,
        )
        if decision == PermissionDecision.allow:
            return PermissionDecisionApproveOnce()
        return PermissionDecisionReject()

    # --- Pipeline-based event processing ---

    async def _process_sdk_event_via_pipeline(
        self,
        sdk_event: SdkSessionEvent,
        session_id: str,
        job_id: str | None,
        requested_model: str,
        model_verified: list[bool],
        queue: asyncio.Queue[SessionEvent | None],
        *,
        explicit_account: bool = False,
    ) -> None:
        """Process a single SDK event: telemetry, enrichment, log emission, delivery.

        Called via create_task from the synchronous SDK callback. Handles
        telemetry, enrichment, log emission, and delivery of native
        SessionEvents onto the queue.
        """
        if self._queues.get(session_id) is not queue or session_id not in self._accepting_events:
            return
        try:
            async with asyncio.timeout(CLIENT_STOP_TIMEOUT_S):
                await self._process_sdk_event_inner(
                    sdk_event,
                    session_id,
                    job_id,
                    requested_model,
                    model_verified,
                    queue,
                    explicit_account=explicit_account,
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
        *,
        explicit_account: bool = False,
    ) -> None:
        """Inner implementation for SDK event processing (no exception guard)."""
        if self._queues.get(session_id) is not queue or session_id not in self._accepting_events:
            return
        kind_str = sdk_event.type.value if sdk_event.type else ""
        data = sdk_event.data

        if not job_id:
            # No job association yet — only bridge done/error for sentinel
            if kind_str in ("session.task_complete", "session.idle", "session.shutdown") or kind_str == "session.error":
                self._accepting_events.discard(session_id)
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            return

        # --- Transcript events ---
        if kind_str == "assistant.message":
            am = cast("AssistantMessageData", data) if data else None
            content = (am.content or "") if am else ""
            is_scout = self.get_session_kind(session_id) == "preflight"
            has_tool_requests = bool(getattr(am, "tool_requests", None))
            if content.strip() or is_scout or has_tool_requests:
                title = getattr(am, "title", None) if am else None
                payload: dict[str, Any] = {"content": content}
                payload["has_tool_requests"] = has_tool_requests
                if title:
                    payload["title"] = title
                self._emit_tf(session_id, job_id, EventKind.message_assistant, payload)

        elif kind_str == "assistant.turn_start" and self.get_session_kind(session_id) == "preflight":
            self._emit_tf(session_id, job_id, EventKind.turn_started, {})

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

                self._tool_buf[session_id, tool_id] = {
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

                buffered = self._tool_buf.pop((session_id, tool_id), {})
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
                metadata = self._session_metadata.get(session_id)
                is_attempt = metadata is not None and metadata.attempt_id is not None

                # Model verification (first call only)
                if not model_verified[0] and actual_model and (requested_model or is_attempt):
                    model_verified[0] = True
                    if self.get_session_kind(session_id) == "preflight" and not is_attempt:
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
                main_model = self._session_models.get(session_id, self._job_main_models.get(job_id, ""))
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
                        "copilot_cost_unit": float(au.cost) if au.cost is not None else None,
                        "duration_ms": au.duration.total_seconds() * 1000 if au.duration else 0.0,
                        "is_subagent": is_subagent,
                        "advance_turn": True,
                        "num_turns": 1,
                        "native_usage": self._native_usage(
                            session_id,
                            {
                                "input_tokens": au.input_tokens,
                                "output_tokens": au.output_tokens,
                                "cache_read_tokens": au.cache_read_tokens,
                                "cache_write_tokens": au.cache_write_tokens,
                                "copilot_cost_unit": au.cost,
                            },
                            "assistant.usage",
                            model=actual_model or None,
                        ),
                    },
                )

        elif kind_str == "session.quota_observation":
            observation = cast("SessionQuotaObservationData", data) if data else None
            if observation:
                self._record_quota_observation(observation.observation, job_id)

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
            metadata = self._session_metadata.get(session_id)
            if (
                mc
                and mc.new_model
                and (
                    self.get_session_kind(session_id) != "preflight"
                    or (metadata is not None and metadata.attempt_id is not None)
                )
            ):
                if metadata is not None and metadata.attempt_id is not None:
                    self._verify_and_set_model(session_id, job_id, mc.new_model, requested_model)
                else:
                    self._session_models[session_id] = mc.new_model
                    self._job_main_models[job_id] = mc.new_model
                    self._schedule_db_write(self._db_write_set_model(job_id=job_id, model=mc.new_model))
                self._emit_log_line(session_id, job_id, f"Model changed to {mc.new_model}", "info")

        elif kind_str == "session.shutdown":
            try:
                sd = cast("SessionShutdownData", data) if data else None
                if sd and sd._total_premium_requests is not None:  # noqa: SLF001
                    premium = sd._total_premium_requests  # noqa: SLF001
                    from backend.services.analytics import telemetry as tel

                    tel.premium_requests_counter.add(premium, {"job_id": job_id, "sdk": "copilot"})
                    self._schedule_db_write(self._db_write_increment(job_id=job_id, premium_requests=premium))
            finally:
                self._accepting_events.discard(session_id)
                queue.put_nowait(None)
            return

        # --- Session lifecycle (sentinel terminates stream_events) ---
        if kind_str in ("session.task_complete", "session.idle", "session.shutdown"):
            self._accepting_events.discard(session_id)
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        elif kind_str == "session.error":
            self._accepting_events.discard(session_id)
            err_payload = (
                {"message": "Session failed for the selected Copilot credential"}
                if explicit_account
                else data.to_dict()
                if data and hasattr(data, "to_dict")
                else {}
            )
            self._emit_tf(session_id, job_id, EventKind.job_failed, cast("dict[str, Any]", err_payload))
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    def _record_quota_observation(self, observation: ProviderQuotaState, job_id: str) -> None:
        """Normalize an SDK budget observation into the existing quota transport."""
        import json as _json

        from backend.services.analytics import telemetry as tel

        budget = observation.budget_metadata
        if budget is None:
            return
        key = observation.compatibility_key or observation.quota_id
        reset_date = (
            datetime.fromtimestamp(budget.reset_at_epoch_ms / 1000, tz=UTC).isoformat()
            if budget.reset_at_epoch_ms is not None
            else ""
        )
        parsed = {
            key: {
                "used_requests": budget.consumed,
                "entitlement_requests": budget.entitlement,
                "remaining_percentage": budget.remaining_percentage,
                "overage": budget.overage,
                "overage_allowed": budget.overage_allowed_when_exhausted,
                "is_unlimited": budget.unlimited,
                "reset_date": reset_date,
            }
        }
        attrs = {"job_id": job_id, "sdk": "copilot", "resource": key}
        tel.quota_used_gauge.set(budget.consumed, attrs)
        tel.quota_entitlement_gauge.set(budget.entitlement, attrs)
        tel.quota_remaining_gauge.set(budget.remaining_percentage, attrs)
        self._schedule_db_write(self._db_write_set_quota(job_id=job_id, quota_remaining=_json.dumps(parsed)))

    async def create_session(self, config: SessionConfig) -> str:
        from backend.services.inference_account import InferenceAccountError, resolve_inference_token

        if self._closing:
            raise RuntimeError("Copilot adapter is shutting down")
        token = await resolve_inference_token(
            self._session_factory, job_id=config.job_id, credential_id=config.copilot_credential_id
        )
        try:
            client = create_copilot_client(github_token=token) if token else create_copilot_client()
        except Exception:
            if token:
                raise InferenceAccountError("Session failed for the selected Copilot credential") from None
            raise
        lease_id = str(uuid.uuid4())
        self._clients[lease_id] = client
        owner = asyncio.current_task()
        if owner is not None:
            self._opening_tasks.add(owner)
        try:
            return await self._create_owned_session(client, lease_id, config, token=token)
        except BaseException as exc:
            for session_id, owned_client in list(self._clients.items()):
                if owned_client is client:
                    await self._finish_session(session_id)
            if token and isinstance(exc, Exception):
                raise InferenceAccountError("Session failed for the selected Copilot credential") from None
            raise
        finally:
            try:
                await self._finish_session(lease_id)
            finally:
                if owner is not None:
                    self._opening_tasks.discard(owner)

    async def _create_owned_session(
        self, client: CopilotClient, lease_id: str, config: SessionConfig, *, token: str | None = None
    ) -> str:
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        from backend.services.action_policy.session_policy import SessionPolicy
        from backend.services.copilot_adapter._peer_tools import build_peer_tools

        peer_tools = build_peer_tools(config)
        auth_kwargs: _InferenceAuthKwargs = {"github_token": token} if token else {}

        # Thin closure that delegates to the instance method, capturing only `config`.
        async def _on_permission(
            request: PermissionRequest, invocation: PermissionInvocation
        ) -> PermissionRequestResult:
            return await self._handle_permission_request(request, invocation, config)

        async def _guard_invocation(
            request: PreToolUseHookInput, invocation: dict[str, str]
        ) -> PreToolUseHookOutput | None:
            from backend.services.adapters.capability_guard import (
                enforce_tool_capabilities,
                invocation_permission_request,
            )
            from backend.services.adapters.contracts import CapabilityDeniedError

            sid = invocation.get("session_id", "")
            fence = self._capture_permission_fence(sid)
            try:
                if not self._permission_fence_is_current(sid, fence):
                    raise CapabilityDeniedError("Tool callback no longer owns the current execution")
                arguments = request.get("toolArgs")
                if not isinstance(arguments, dict):
                    raise CapabilityDeniedError("Tool arguments are unavailable for capability enforcement")
                permission_request = invocation_permission_request(
                    config.workspace_path,
                    request["toolName"],
                    arguments,
                )
                if config.session_kind == "preflight":
                    from backend.services.tools.preflight_curator import scout_read_allowed

                    if permission_request.full_command_text or not scout_read_allowed(
                        config.workspace_path,
                        permission_request.kind,
                        request["toolName"],
                        permission_request.possible_paths or [],
                    ):
                        raise CapabilityDeniedError("Scout tool invocation exceeds its read-only workspace")
                await enforce_tool_capabilities(
                    config.execution_capabilities,
                    workspace=config.workspace_path,
                    kind=permission_request.kind,
                    tool_name=request["toolName"],
                    paths=permission_request.possible_paths or (),
                    command=permission_request.full_command_text,
                    dialect="powershell" if request["toolName"] == "powershell" else None,
                )
                if not self._permission_fence_is_current(sid, fence):
                    raise CapabilityDeniedError("Tool callback no longer owns the current execution")
                if isinstance(self._policy_router.get(config.job_id), SessionPolicy):
                    decision = await self._evaluate_permission(
                        sid,
                        config.job_id,
                        permission_request,
                        tool_name=request["toolName"],
                        tool_input=arguments,
                        fence=fence,
                        tool_call_id=request.get("toolCallId"),
                        dialect="powershell"
                        if request["toolName"] == "powershell"
                        else "bash"
                        if request["toolName"].lower() == "bash"
                        else None,
                    )
                    return {"permissionDecision": "allow" if decision == PermissionDecision.allow else "deny"}
            except CapabilityDeniedError as exc:
                log.warning("execution_capability_denied", job_id=config.job_id, reason=str(exc))
                return {"permissionDecision": "deny", "permissionDecisionReason": str(exc)}
            return None

        standalone = isinstance(self._policy_router.get(config.job_id), SessionPolicy)
        hooks: SessionHooks | None = (
            {"on_pre_tool_use": _guard_invocation} if config.execution_capabilities is not None or standalone else None
        )
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
        from backend.services.adapters.capability_guard import excluded_tools_for_capabilities

        excluded_tools = excluded_tools_for_capabilities(config.execution_capabilities, config.disallowed_tools)
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
                    **auth_kwargs,
                    on_permission_request=_on_permission,
                    working_directory=config.workspace_path,
                    model=config.model or None,
                    system_message=system_message,
                    tools=peer_tools,
                    excluded_tools=_sdk_excluded_tools(excluded_tools or None),
                    available_tools=available_tools,
                    hooks=hooks,
                )
                log.info("sdk_session_resumed", sdk_session_id=_resume_id)
            except (JsonRpcError, ProcessExitedError, ConnectionError, TimeoutError):
                log.warning("sdk_session_resume_failed_creating_new", sdk_session_id=_resume_id, exc_info=not token)
                session = await client.create_session(
                    **auth_kwargs,
                    on_permission_request=_on_permission,
                    working_directory=config.workspace_path,
                    system_message=system_message,
                    model=config.model or None,
                    available_tools=available_tools,
                    tools=peer_tools,
                    excluded_tools=_sdk_excluded_tools(excluded_tools or None),
                    hooks=hooks,
                )
        else:
            session = await client.create_session(
                **auth_kwargs,
                on_permission_request=_on_permission,
                working_directory=config.workspace_path,
                system_message=system_message,
                model=config.model or None,
                available_tools=available_tools,
                tools=peer_tools,
                excluded_tools=_sdk_excluded_tools(excluded_tools or None),
                hooks=hooks,
            )

        session_id = session.session_id  # Use SDK-assigned ID as CodePlane's session identifier
        if self._closing:
            raise RuntimeError("Copilot adapter is shutting down")
        if (
            session_id in self._sessions
            or session_id in self._stop_tasks
            or any(not task.done() for task in self._callback_tasks.get(session_id, ()))
        ):
            raise RuntimeError(f"Copilot session is already active: {session_id}")
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[session_id] = queue
        self.set_session_metadata(session_id, config)
        self._sessions[session_id] = session
        self._session_credentials[session_id] = config.copilot_credential_id
        self._clients[session_id] = client
        self._clients.pop(lease_id, None)
        self._accepting_events.add(session_id)

        # Wire telemetry mapping before registering the callback so
        # no early SDK events are lost.
        if config.job_id:
            self.set_job_id(session_id, config.job_id)
        if config.session_kind != "job":
            self.set_session_kind(session_id, config.session_kind)

        # Track whether we've verified the model on the first usage event.
        _model_verified = [False]
        pending_events: asyncio.Queue[SdkSessionEvent] = asyncio.Queue()

        async def _drain_events() -> None:
            while not pending_events.empty():
                if self._queues.get(session_id) is not queue or session_id not in self._accepting_events:
                    return
                await self._process_sdk_event_via_pipeline(
                    pending_events.get_nowait(),
                    session_id,
                    self._session_to_job.get(session_id),
                    requested_model,
                    _model_verified,
                    queue,
                    explicit_account=bool(token),
                )

        def _on_event(sdk_event: SdkSessionEvent) -> None:
            """Keep one ordered, tracked callback worker per SDK session."""
            if self._queues.get(session_id) is not queue or session_id not in self._accepting_events:
                return
            pending_events.put_nowait(sdk_event)
            tasks = self._callback_tasks.setdefault(session_id, set())
            if any(not task.done() for task in tasks):
                return
            task = asyncio.create_task(_drain_events(), name=f"copilot-pipeline-{session_id[:8]}")
            tasks.add(task)

            def _finished(completed: asyncio.Task[None]) -> None:
                tasks.discard(completed)
                if not tasks and self._callback_tasks.get(session_id) is tasks:
                    self._callback_tasks.pop(session_id, None)

            task.add_done_callback(_finished)

        try:
            unsubscribe = session.on(_on_event)
            if callable(unsubscribe):
                self._unsubscribe[session_id] = unsubscribe
            if not config.defer_start:
                await session.send(config.prompt, mode="immediate")
        except BaseException:
            await self._finish_session(session_id)
            raise
        log.info("copilot_session_created", session_id=session_id)
        return str(session_id)

    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        queue = self._queues.get(session_id)
        if queue is None:
            log.error("copilot_stream_no_queue", session_id=session_id)
            from backend.models.session_event import EventMetadata

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
            await self._finish_session(session_id)

    async def _validate_session_account(self, session_id: str) -> bool:
        from backend.services.inference_account import inference_scope, resolve_inference_token

        # A peer caller's task-local account must not replace the recipient's binding.
        with inference_scope():
            token = await resolve_inference_token(
                self._session_factory,
                job_id=self._session_to_job.get(session_id),
                credential_id=self._session_credentials.get(session_id),
            )
        return bool(token)

    async def send_message(self, session_id: str, message: str) -> None:
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError

        from backend.services.completers.copilot_steer import NativeDeliveryError

        session = self._sessions.get(session_id)
        if session is None:
            log.warning("copilot_send_no_session", session_id=session_id)
            raise NativeDeliveryError("Managed Copilot session is no longer live")
        explicit_account = await self._validate_session_account(session_id)
        try:
            await session.send(message, mode="immediate")
        except (JsonRpcError, ProcessExitedError, ConnectionError) as exc:
            if explicit_account:
                from backend.services.inference_account import InferenceAccountError

                raise InferenceAccountError("Message failed for the selected Copilot credential") from None
            log.warning("copilot_send_message_failed", session_id=session_id, exc_info=True)
            if isinstance(exc, JsonRpcError):
                raise NativeDeliveryError("Managed Copilot rejected the message") from exc
            raise NativeDeliveryError("Managed Copilot delivery is unknown", uncertain=True) from exc

    async def send_peer_message(
        self, session_id: str, message: str, *, can_send: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise StateConflictError("The target SDK session is no longer active")
        await self._validate_session_account(session_id)
        if can_send is not None and not await can_send():
            raise StateConflictError("Peer execution changed or is no longer eligible for dispatch")
        if (
            self._sessions.get(session_id) is not session
            or session_id in self._paused_sessions
            or session_id in self._stop_tasks
        ):
            raise StateConflictError("The target SDK session is no longer active or is paused")
        await session.send(message, mode="enqueue")

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
        try:
            if session is not None:
                await asyncio.wait_for(session.abort(), timeout=CLIENT_STOP_TIMEOUT_S)
        except (TimeoutError, JsonRpcError, ProcessExitedError):
            log.warning("copilot_abort_failed", session_id=session_id, exc_info=True)
        finally:
            await self._finish_session(session_id)

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        """Complete a text-only turn, failing explicitly unless the turn finishes."""
        from copilot._jsonrpc import JsonRpcError, ProcessExitedError
        from copilot.generated.rpc import PermissionDecisionReject

        from backend.services.inference_account import resolve_inference_token

        token = await resolve_inference_token(self._session_factory, job_id=current_completion_context().job_id)
        auth_kwargs: _InferenceAuthKwargs = {"github_token": token} if token else {}
        if self._closing:
            raise UtilityCompletionError("Copilot adapter is shutting down", reason="shutdown")
        try:
            client = create_copilot_client(github_token=token) if token else create_copilot_client()
        except Exception as exc:
            if token:
                raise UtilityCompletionError(
                    "Completion failed for the selected Copilot credential", reason="sdk_error"
                ) from None
            raise UtilityCompletionError("Text-only client construction failed", reason="sdk_error") from exc
        tmp_session_id = str(uuid.uuid4())
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[tmp_session_id] = queue
        self._clients[tmp_session_id] = client
        owner = asyncio.current_task()
        if owner is not None:
            self._opening_tasks.add(owner)

        tool_attempted = False

        async def _deny_permission(request: object, invocation: PermissionInvocation) -> PermissionRequestResult:
            nonlocal tool_attempted
            tool_attempted = True
            return PermissionDecisionReject()

        accepting = True
        completion = current_completion_context()
        usage: list[UsageAmount] = []
        served_model = ""
        collected_bytes = 0
        result: CompletionResult | None = None
        try:
            import tempfile

            prepared_schema = (
                prepare_response_schema(completion.response_schema) if completion.response_schema is not None else None
            )
            if prepared_schema is not None:
                log.info(
                    "copilot_structured_schema_prepared",
                    job_id=completion.job_id,
                    execution_epoch=completion.execution_epoch,
                    **prepared_schema.provenance,
                )
            sys_msg_config: SystemMessageAppendConfig | SystemMessageReplaceConfig | None = None
            if system_message is not None:
                if completion.response_schema is None:
                    sys_msg_config = {"mode": "append", "content": system_message}
                else:
                    sys_msg_config = {"mode": "replace", "content": system_message}

            collected: list[str] = []
            done_event = asyncio.Event()
            failure: UtilityCompletionError | None = None

            def _on_event(sdk_event: SdkSessionEvent) -> None:
                nonlocal failure, served_model, collected_bytes
                if not accepting or self._queues.get(tmp_session_id) is not queue:
                    return
                kind_str = sdk_event.type.value if sdk_event.type else ""
                payload = sdk_event.data.to_dict() if sdk_event.data else {}
                if kind_str == "assistant.message":
                    content = payload.get("content") or ""
                    if isinstance(content, str) and content:
                        collected_bytes += len(content.encode("utf-8")) + (1 if collected else 0)
                        if collected_bytes > completion.max_output_bytes:
                            failure = UtilityCompletionError(
                                "Text-only output exceeds its bound", reason="output_limit"
                            )
                            done_event.set()
                            return
                        collected.append(content)
                    if payload.get("toolRequests") or payload.get("tool_requests"):
                        failure = UtilityCompletionError("Text-only completion requested tools", reason="tool_request")
                        done_event.set()
                elif kind_str == "assistant.usage":
                    model_name = payload.get("model")
                    if isinstance(model_name, str) and model_name:
                        if served_model and served_model != model_name:
                            failure = UtilityCompletionError("Utility served multiple models", reason="model_mismatch")
                            done_event.set()
                            return
                        served_model = model_name
                    for unit, keys in (
                        ("input_tokens", ("inputTokens", "input_tokens")),
                        ("output_tokens", ("outputTokens", "output_tokens")),
                        ("cache_read_tokens", ("cacheReadTokens", "cache_read_tokens")),
                        ("cache_write_tokens", ("cacheWriteTokens", "cache_write_tokens")),
                        ("copilot_cost_unit", ("cost",)),
                    ):
                        value = next((payload[key] for key in keys if key in payload), None)
                        if isinstance(value, (float, int)) and not isinstance(value, bool):
                            try:
                                amount = UsageAmount.model_validate(
                                    {
                                        "amount": value,
                                        "unit": unit,
                                        "producer": "copilot",
                                        "model": served_model or None,
                                        "job_id": completion.job_id,
                                        "execution_epoch": completion.execution_epoch,
                                        "role": completion.role,
                                        "source": "assistant.usage",
                                    }
                                )
                            except ValueError:
                                failure = UtilityCompletionError("Invalid provider usage", reason="invalid_usage")
                                done_event.set()
                                return
                            usage.append(amount)
                elif kind_str in ("tool.execution_start", "tool.execution_complete"):
                    failure = UtilityCompletionError("Text-only completion attempted tools", reason="tool_request")
                    done_event.set()
                elif kind_str in ("abort", "session.abort"):
                    failure = UtilityCompletionError("Text-only completion was aborted", reason="aborted")
                    done_event.set()
                elif kind_str == "session.error":
                    failure = UtilityCompletionError(
                        "Completion failed for the selected Copilot credential"
                        if token
                        else str(payload.get("message") or "Copilot completion failed"),
                        reason="sdk_error",
                    )
                    done_event.set()
                elif kind_str in ("session.task_complete", "session.idle", "session.shutdown"):
                    if completion.response_schema is None:
                        done_event.set()

            async with asyncio.timeout(COMPLETION_TIMEOUT_S):
                if completion.response_schema is not None:
                    if not model:
                        raise UtilityCompletionError(
                            "Structured completion requires an explicit catalog model",
                            reason="model_catalog_unavailable",
                        )
                    try:
                        await client.start()
                        catalog = await client.list_models()
                    except (
                        JsonRpcError,
                        ProcessExitedError,
                        OSError,
                        RuntimeError,
                        ValueError,
                        TypeError,
                        AssertionError,
                    ) as exc:
                        log.warning(
                            "copilot_structured_catalog_unavailable", model=model, error_type=type(exc).__name__
                        )
                        raise UtilityCompletionError(
                            "Structured completion could not load the SDK model catalog",
                            reason="model_catalog_unavailable",
                        ) from exc
                    matches = [entry for entry in catalog if entry.id == model]
                    if len(matches) != 1 or (matches[0].policy is not None and matches[0].policy.state != "enabled"):
                        log.warning("copilot_structured_model_unavailable", model=model, catalog_matches=len(matches))
                        raise UtilityCompletionError(
                            "Structured completion requires one available SDK catalog entry for the requested model",
                            reason="model_catalog_unavailable",
                        )
                    if (
                        self._closing
                        or tmp_session_id in self._stop_tasks
                        or self._clients.get(tmp_session_id) is not client
                        or self._queues.get(tmp_session_id) is not queue
                    ):
                        raise UtilityCompletionError("Structured completion ownership ended", reason="shutdown")
                session = await client.create_session(
                    **auth_kwargs,
                    working_directory=tempfile.gettempdir(),
                    on_permission_request=_deny_permission,
                    model=model or None,
                    system_message=sys_msg_config,
                    excluded_tools=_sdk_excluded_tools(excluded_tools if excluded_tools is not None else ["*"]),
                    available_tools=[],
                )
                if self._closing:
                    raise UtilityCompletionError("Copilot adapter is shutting down", reason="shutdown")
                self._sessions[tmp_session_id] = session
                unsubscribe = session.on(_on_event)
                if callable(unsubscribe):
                    self._unsubscribe[tmp_session_id] = unsubscribe
                if prepared_schema is None:
                    await session.send(prompt, mode="immediate")
                    await done_event.wait()
                else:
                    pending_response = asyncio.create_task(
                        session.send_and_wait(
                            prompt,
                            mode="enqueue",
                            response_schema=prepared_schema.wire_schema,
                            timeout=COMPLETION_TIMEOUT_S,
                        )
                    )
                    guard = asyncio.create_task(done_event.wait())
                    try:
                        await asyncio.wait((pending_response, guard), return_when=asyncio.FIRST_COMPLETED)
                        if failure is not None:
                            raise failure
                        response = await pending_response
                        content = response.data.to_dict().get("content") if response and response.data else None
                        collected[:] = [content] if isinstance(content, str) else []
                    finally:
                        pending_response.cancel()
                        guard.cancel()
                        await asyncio.gather(pending_response, guard, return_exceptions=True)
            if failure is not None:
                raise failure
            text = "\n".join(collected)
            oversized = len(text.encode("utf-8")) > completion.max_output_bytes
            result = CompletionResult(
                text="" if oversized else text,
                model=served_model,
                usage=tuple(usage),
                input_tokens=sum(int(item.amount) for item in usage if item.unit == "input_tokens"),
                output_tokens=sum(int(item.amount) for item in usage if item.unit == "output_tokens"),
            )
            if tool_attempted:
                raise UtilityCompletionError(
                    "Text-only completion requested tool permission", reason="tool_request", completion=result
                )
            if oversized:
                raise UtilityCompletionError(
                    "Text-only output exceeds its bound", reason="output_limit", completion=result
                )
            if not text.strip():
                raise UtilityCompletionError(
                    "Text-only completion returned no text", reason="empty_response", completion=result
                )
            validate_completion_identity(model, served_model, completion, result=result)
            if completion.response_schema is not None:
                result.raw_text = text
                try:
                    result.text, normalized = _structured_reply_text(text)
                except ValueError as exc:
                    raise UtilityCompletionError(
                        "Structured completion is not one unambiguous JSON object",
                        reason="invalid_structured_output",
                        completion=result,
                    ) from exc
                if normalized:
                    result.response_normalization = "single_json_code_fence"
            return result
        except UtilityCompletionError as exc:
            if token:
                raise UtilityCompletionError(
                    "Completion failed for the selected Copilot credential",
                    reason=exc.reason,
                    completion=exc.completion,
                ) from None
            raise
        except TimeoutError as exc:
            if token:
                raise UtilityCompletionError(
                    "Completion failed for the selected Copilot credential", reason="timeout"
                ) from None
            raise UtilityCompletionError("Text-only completion timed out", reason="timeout") from exc
        except Exception as exc:
            if token:
                raise UtilityCompletionError(
                    "Completion failed for the selected Copilot credential", reason="sdk_error"
                ) from None
            raise UtilityCompletionError("Text-only completion failed", reason="sdk_error") from exc
        finally:
            accepting = False
            try:
                cleanup_session = self._sessions.get(tmp_session_id)
                if cleanup_session is not None:
                    try:
                        await asyncio.wait_for(cleanup_session.abort(), timeout=CLIENT_STOP_TIMEOUT_S)
                    except Exception:
                        log.warning("copilot_complete_abort_failed", session_id=tmp_session_id, exc_info=not token)
            finally:
                try:
                    await self._finish_session(tmp_session_id)
                except Exception as exc:
                    if token:
                        raise UtilityCompletionError(
                            "Text-only completion cleanup failed", reason="cleanup_failed", completion=result
                        ) from None
                    raise UtilityCompletionError(
                        "Text-only completion cleanup failed", reason="cleanup_failed", completion=result
                    ) from exc
                finally:
                    if owner is not None:
                        self._opening_tasks.discard(owner)
