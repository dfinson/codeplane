"""Claude Agent SDK adapter — bridges the Claude Agent SDK into CodePlane.

Uses ClaudeSDKClient for multi-turn session management. The SDK's async
message iterator is consumed in a background task that pushes SessionEvent
items onto an asyncio.Queue; stream_events() yields from the queue.

Permission handling uses the ``can_use_tool`` callback to route tool
approval requests through CodePlane's approval system.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.domain import (
    PermissionMode,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)
from backend.services.agent_adapter import CODEPLANE_SYSTEM_PROMPT, AgentAdapterInterface, normalize_model_name
from backend.services.permission_policy import is_git_reset_hard

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from claude_code_sdk import ClaudeSDKClient

    from backend.services.approval_service import ApprovalService
    from backend.services.event_bus import EventBus
    from backend.services.retry_tracker import RetryTracker

log = structlog.get_logger()

# Truncation limits for approval action payloads and tool summaries
_TOOL_ACTION_MAX = 2000
_TOOL_SUMMARY_MAX = 200
_TOOL_SUMMARY_FALLBACK = 120

# Claude SDK tool names that are internal / should not appear in transcript
_HIDDEN_TOOLS = frozenset({"TodoWrite"})

# Map CodePlane permission modes to Claude SDK permission modes
_PERMISSION_MODE_MAP: dict[PermissionMode, str] = {
    PermissionMode.auto: "bypassPermissions",
    PermissionMode.read_only: "plan",
    PermissionMode.approval_required: "default",
}


class ClaudeAdapter(AgentAdapterInterface):
    """Wraps the Claude Agent SDK (Python) behind the adapter interface.

    Each session is backed by a ``ClaudeSDKClient`` instance that maintains
    conversation context.  A background asyncio task consumes the SDK's
    async message iterator and pushes translated ``SessionEvent`` objects
    onto a queue that ``stream_events()`` yields from.
    """

    def __init__(
        self,
        approval_service: ApprovalService | None = None,
        event_bus: EventBus | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._queues: dict[str, asyncio.Queue[SessionEvent | None]] = {}
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_to_job: dict[str, str] = {}
        self._tool_start_times: dict[str, float] = {}
        self._tool_call_buffer: dict[str, dict[str, str]] = {}
        self._current_turn_id: str = ""
        self._approval_service = approval_service
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._job_start_times: dict[str, float] = {}
        self._job_main_models: dict[str, str] = {}
        self._requested_models: dict[str, str] = {}
        self._model_verified: dict[str, bool] = {}
        self._paused_sessions: set[str] = set()
        # Debounce: last monotonic time a telemetry_updated SSE was fired per job
        self._last_telemetry_broadcast: dict[str, float] = {}
        # Stderr capture files for debugging failed sessions
        self._stderr_files: dict[str, str] = {}
        # Cost analytics: per-job turn counter, phase, retry tracker
        self._turn_counters: dict[str, int] = {}
        self._current_phases: dict[str, str] = {}
        self._retry_trackers: dict[str, RetryTracker] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_session(self, session_id: str) -> None:
        self._paused_sessions.discard(session_id)
        job_id = self._session_to_job.pop(session_id, None)
        client = self._clients.pop(session_id, None)
        self._queues.pop(session_id, None)
        task = self._consumer_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
        if client is not None:
            asyncio.ensure_future(self._disconnect_client(client))
        stderr_path = self._stderr_files.pop(session_id, None)
        if stderr_path:
            try:
                os.unlink(stderr_path)
            except OSError:
                pass
        if job_id:
            self._job_start_times.pop(job_id, None)
            self._job_main_models.pop(job_id, None)
            self._requested_models.pop(job_id, None)
            self._model_verified.pop(job_id, None)
            self._last_telemetry_broadcast.pop(job_id, None)
            self._turn_counters.pop(job_id, None)
            self._current_phases.pop(job_id, None)
            self._retry_trackers.pop(job_id, None)

    def set_execution_phase(self, job_id: str, phase: str) -> None:
        """Update the current execution phase for cost analytics span tagging."""
        self._current_phases[job_id] = phase

    def _read_session_stderr(self, session_id: str) -> str:
        """Read captured stderr from the Claude subprocess (last 4 KB)."""
        path = self._stderr_files.get(session_id)
        if not path:
            return ""
        try:
            with open(path) as f:
                return f.read()[-4096:]
        except OSError:
            return ""

    @staticmethod
    async def _disconnect_client(client: ClaudeSDKClient) -> None:
        """Disconnect a ClaudeSDKClient, terminating its backing subprocess."""
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except Exception:
            log.warning("claude_client_disconnect_failed", exc_info=True)

    def _schedule_db_write(self, coro: Any) -> None:  # noqa: ANN401
        """Schedule an async DB write from a synchronous or async context."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass  # No event loop — skip DB write

    _TELEMETRY_BROADCAST_INTERVAL = 2.0  # seconds — debounce SSE broadcasts

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
                elif fn_name == "set_quota":
                    await TelemetrySummaryRepo(session).set_quota(**kwargs)
                elif fn_name == "record_file_access":
                    from backend.persistence.file_access_repo import FileAccessRepo

                    await FileAccessRepo(session).record(**kwargs)
                await session.commit()
        except Exception:
            log.debug("telemetry_db_write_failed", fn=fn_name, exc_info=True)
            return

        # Broadcast a debounced telemetry_updated SSE for summary changes
        if fn_name != "insert_span":
            job_id = kwargs.get("job_id")
            if job_id:
                await self._maybe_broadcast_telemetry(job_id)

    async def _maybe_broadcast_telemetry(self, job_id: str) -> None:
        """Publish telemetry_updated if debounce interval has elapsed."""
        import time as _time

        from backend.models.events import DomainEvent, DomainEventKind

        if self._event_bus is None:
            return
        now = _time.monotonic()
        last = self._last_telemetry_broadcast.get(job_id, 0.0)
        if now - last < self._TELEMETRY_BROADCAST_INTERVAL:
            return
        self._last_telemetry_broadcast[job_id] = now
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.telemetry_updated,
                payload={"job_id": job_id},
            )
        )

    def _enqueue(self, session_id: str, event: SessionEvent) -> None:
        q = self._queues.get(session_id)
        if q is not None:
            q.put_nowait(event)

    def _enqueue_log(
        self,
        session_id: str,
        message: str,
        level: str = "info",
        seq: list[int] | None = None,
    ) -> None:
        """Enqueue a log event for the session.

        .. note::
            When *seq* is provided it is **mutated in-place** (``seq[0]``
            is incremented) so the caller's counter stays in sync across
            successive calls.
        """
        if seq is not None:
            seq[0] += 1
        self._enqueue(
            session_id,
            SessionEvent(
                kind=SessionEventKind.log,
                payload={
                    "seq": seq[0] if seq else 0,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "level": level,
                    "message": message,
                },
            ),
        )

    # ------------------------------------------------------------------
    # Permission callback builder
    # ------------------------------------------------------------------

    def _build_can_use_tool(self, config: SessionConfig, session_id: str) -> Any:  # noqa: ANN401
        """Build the ``can_use_tool`` callback for the Claude SDK.

        Returns a coroutine that the SDK calls before each tool execution.
        We inspect the CodePlane permission mode and either auto-approve,
        deny, or route the request to the operator via the approval service.
        """
        from claude_code_sdk import PermissionResultAllow, PermissionResultDeny

        async def _can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            context: object,
        ) -> PermissionResultAllow | PermissionResultDeny:
            # Paused — immediately deny all tools so the agent cannot act.
            if session_id in self._paused_sessions:
                return PermissionResultDeny(message="Session is paused — waiting for operator")

            mode = config.permission_mode
            job_id = self._session_to_job.get(session_id)

            # ----------------------------------------------------------------
            # Hard block: git reset --hard always requires explicit operator
            # approval — no trust bypass, no auto mode bypass, ever.
            # ----------------------------------------------------------------
            _shell_cmd = input_data.get("command", "") or "" if tool_name == "Bash" else ""
            if _shell_cmd and is_git_reset_hard(_shell_cmd):
                if self._approval_service is None or job_id is None:
                    log.error(
                        "git_reset_hard_blocked_no_infra",
                        tool=tool_name,
                        command=_shell_cmd[:200],
                    )
                    return PermissionResultDeny(
                        message=(
                            "git reset --hard requires operator approval but no approval infrastructure is available"
                        )
                    )

                description = (
                    "⚠️ git reset --hard — this will discard ALL uncommitted changes and "
                    f"move HEAD: {_summarize_tool_input(tool_name, input_data)}"
                )
                approval = await self._approval_service.create_request(
                    job_id=job_id,
                    description=description,
                    proposed_action=json.dumps(input_data, default=str)[:_TOOL_ACTION_MAX],
                    requires_explicit_approval=True,
                )
                self._enqueue(
                    session_id,
                    SessionEvent(
                        kind=SessionEventKind.approval_request,
                        payload={
                            "description": description,
                            "proposed_action": json.dumps(input_data, default=str)[:_TOOL_ACTION_MAX],
                            "approval_id": approval.id,
                            "requires_explicit_approval": True,
                        },
                    ),
                )
                log.warning(
                    "git_reset_hard_awaiting_operator",
                    approval_id=approval.id,
                    job_id=job_id,
                    command=_shell_cmd[:200],
                )
                resolution = await self._approval_service.wait_for_resolution(approval.id)
                if resolution == "approved":
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Operator denied git reset --hard")

            # Check trust
            if self._approval_service is not None and job_id and self._approval_service.is_trusted(job_id):
                return PermissionResultAllow()

            # AUTO — approve everything
            if mode == PermissionMode.auto:
                return PermissionResultAllow()

            # READ_ONLY — only allow read-type tools
            if mode == PermissionMode.read_only:
                read_tools = {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "ToolSearch"}
                if tool_name in read_tools:
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Read-only mode: tool blocked")

            # APPROVAL_REQUIRED — read tools auto-approved, everything else → operator
            read_tools = {"Read", "Glob", "Grep"}
            if tool_name in read_tools:
                return PermissionResultAllow()

            # Route to operator
            if self._approval_service is None or job_id is None:
                log.warning("claude_permission_no_infra", tool=tool_name)
                return PermissionResultAllow()

            # Build human-readable description
            description = f"{tool_name}: {_summarize_tool_input(tool_name, input_data)}"

            approval = await self._approval_service.create_request(
                job_id=job_id,
                description=description,
                proposed_action=json.dumps(input_data, default=str)[:_TOOL_ACTION_MAX],
            )

            # Emit approval_request event
            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.approval_request,
                    payload={
                        "description": description,
                        "proposed_action": json.dumps(input_data, default=str)[:_TOOL_ACTION_MAX],
                        "approval_id": approval.id,
                    },
                ),
            )

            log.info(
                "claude_permission_awaiting_operator",
                approval_id=approval.id,
                tool=tool_name,
            )

            resolution = await self._approval_service.wait_for_resolution(approval.id)
            if resolution == "approved":
                return PermissionResultAllow()
            return PermissionResultDeny(message="Operator denied the action")

        return _can_use_tool

    # ------------------------------------------------------------------
    # Message consumer — runs in a background task per session
    # ------------------------------------------------------------------

    async def _consume_messages(self, session_id: str, client: object) -> None:
        """Consume messages from the ClaudeSDKClient and translate to SessionEvents."""
        from claude_code_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            UserMessage,
        )

        # Guard against SDK message-parse failures for unknown event types
        # (e.g. rate_limit_event in SDK ≤0.0.25).
        try:
            from claude_code_sdk._errors import MessageParseError
        except ImportError:
            MessageParseError = None  # type: ignore[assignment,misc]

        seq = [0]
        queue = self._queues.get(session_id)
        if queue is None:
            return

        try:
            async for message in client.receive_messages():  # type: ignore[attr-defined]
                if isinstance(message, SystemMessage):
                    self._enqueue_log(session_id, "Claude session initialized", "info", seq)

                elif isinstance(message, AssistantMessage):
                    self._process_assistant_message(session_id, message, seq)

                elif isinstance(message, UserMessage):
                    self._process_user_message(session_id, message, seq)

                elif isinstance(message, ResultMessage):
                    self._process_result_message(session_id, message, seq)
                    break

                # StreamEvent, TaskStartedMessage etc. are logged but not
                # forwarded as transcript events (they are internal SDK bookkeeping).
        except asyncio.CancelledError:
            log.info("claude_consumer_cancelled", session_id=session_id)
        except Exception as exc:
            # SDK ≤0.0.25 throws MessageParseError on unknown event types like
            # rate_limit_event.  Swallow it and retry the message stream so the
            # session can continue rather than crash.
            if MessageParseError is not None and isinstance(exc, MessageParseError):
                log.warning(
                    "claude_unknown_message_type",
                    session_id=session_id,
                    error=str(exc),
                )
                # Re-enter the consumer — the SDK may have more messages.
                await self._consume_messages(session_id, client)
                return

            stderr_snippet = self._read_session_stderr(session_id)
            log.error(
                "claude_consumer_error",
                session_id=session_id,
                stderr_tail=stderr_snippet[:500] if stderr_snippet else "",
                exc_info=True,
            )
            error_msg = f"Claude SDK session error: {exc}"
            if stderr_snippet:
                error_msg += f"\n{stderr_snippet}"
            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.error,
                    payload={"message": error_msg},
                ),
            )
        finally:
            # Sentinel to signal end of stream
            if queue is not None:
                queue.put_nowait(None)

    def _process_user_message(
        self,
        session_id: str,
        message: object,
        seq: list[int],
    ) -> None:
        """Handle a UserMessage — extract ToolResultBlocks for telemetry/transcript."""
        from claude_code_sdk import ToolResultBlock

        content = getattr(message, "content", None)
        job_id = self._session_to_job.get(session_id)

        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    self._process_tool_result_block(session_id, block, seq, job_id)
        elif isinstance(content, str) and content.strip() and job_id:
            # Human / operator follow-up message
            self._schedule_db_write(
                self._db_write("increment", job_id=job_id, operator_messages=1)
            )

    def _process_assistant_message(
        self,
        session_id: str,
        message: object,
        seq: list[int],
    ) -> None:
        """Translate an AssistantMessage's content blocks into SessionEvents."""
        from claude_code_sdk import TextBlock, ToolResultBlock, ToolUseBlock

        content_blocks = getattr(message, "content", []) or []
        model = getattr(message, "model", "") or ""
        job_id = self._session_to_job.get(session_id)

        # Each AssistantMessage starts a new turn for grouping
        self._current_turn_id = str(uuid.uuid4())

        # Turn counting is deferred to ResultMessage.num_turns for accuracy
        # (the SDK streams many AssistantMessages per actual API turn).
        if job_id:
            turn_num = self._turn_counters.get(job_id, 0) + 1
            self._turn_counters[job_id] = turn_num

        # Lock in the main model from the first AssistantMessage that carries one
        if job_id and model and job_id not in self._job_main_models:
            self._job_main_models[job_id] = model
            self._schedule_db_write(self._db_write("set_model", job_id=job_id, model=model))

            # Model downgrade/mismatch detection (mirrors CopilotAdapter behaviour)
            if not self._model_verified.get(job_id):
                self._model_verified[job_id] = True
                requested = self._requested_models.get(job_id, "")
                if requested and normalize_model_name(model) != normalize_model_name(requested):
                    log.error(
                        "model_mismatch",
                        requested=requested,
                        actual=model,
                        job_id=job_id,
                    )
                    self._enqueue(
                        session_id,
                        SessionEvent(
                            kind=SessionEventKind.model_downgraded,
                            payload={
                                "requested_model": requested,
                                "actual_model": model,
                            },
                        ),
                    )
                else:
                    log.info("model_confirmed", model=model, job_id=job_id)

        for block in content_blocks:
            if isinstance(block, TextBlock):
                text = block.text or ""
                if not text.strip():
                    continue
                if job_id:
                    self._schedule_db_write(
                        self._db_write(
                            "increment",
                            job_id=job_id,
                            agent_messages=1,
                        )
                    )
                self._enqueue(
                    session_id,
                    SessionEvent(
                        kind=SessionEventKind.transcript,
                        payload={
                            "role": "agent",
                            "content": text,
                            "turn_id": self._current_turn_id,
                        },
                    ),
                )

            elif isinstance(block, ToolUseBlock):
                self._process_tool_use_block(session_id, block, model, seq, job_id)

            elif isinstance(block, ToolResultBlock):
                self._process_tool_result_block(session_id, block, seq, job_id)

    def _process_tool_use_block(
        self,
        session_id: str,
        block: object,
        model: str,
        seq: list[int],
        job_id: str | None,
    ) -> None:
        """Handle a ToolUseBlock — emit tool_running transcript + log + record start time."""
        tool_name = getattr(block, "name", "") or "tool"
        tool_id = getattr(block, "id", "") or str(uuid.uuid4())
        tool_input = getattr(block, "input", None)

        # Serialize tool arguments
        args_str: str | None = None
        if isinstance(tool_input, dict):
            try:
                args_str = json.dumps(tool_input)
            except Exception:
                args_str = str(tool_input)

        # Record start time for duration calculation
        self._tool_start_times[tool_id] = time.monotonic()

        # Synthesize a turn_id for grouping (one per AssistantMessage stream)
        if not self._current_turn_id:
            self._current_turn_id = str(uuid.uuid4())
        turn_id = self._current_turn_id

        # Buffer for the completion event
        self._tool_call_buffer[tool_id] = {
            "tool_name": tool_name,
            "tool_args": args_str or "",
            "turn_id": turn_id,
        }

        if tool_name not in _HIDDEN_TOOLS:
            from backend.services.tool_formatters import format_tool_display, format_tool_display_full

            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.transcript,
                    payload={
                        "role": "tool_running",
                        "content": tool_name,
                        "tool_name": tool_name,
                        "tool_args": args_str,
                        "turn_id": turn_id,
                        "tool_display": format_tool_display(tool_name, args_str),
                        "tool_display_full": format_tool_display_full(tool_name, args_str),
                    },
                ),
            )
            self._enqueue_log(session_id, f"Tool started: {tool_name}", "debug", seq)

    def _process_tool_result_block(
        self,
        session_id: str,
        block: object,
        seq: list[int],
        job_id: str | None,
    ) -> None:
        """Handle a ToolResultBlock — emit transcript + telemetry."""
        tool_use_id = getattr(block, "tool_use_id", "") or ""
        content = getattr(block, "content", "")
        is_error = getattr(block, "is_error", False)

        # Resolve tool name + args from the buffer populated by _process_tool_use_block
        buffered = self._tool_call_buffer.pop(tool_use_id, {})
        tool_name = buffered.get("tool_name", "tool")
        tool_args_str = buffered.get("tool_args") or None
        turn_id = buffered.get("turn_id") or None

        # Calculate duration
        start = self._tool_start_times.pop(tool_use_id, time.monotonic())
        duration_ms = (time.monotonic() - start) * 1000

        # Extract text from content (can be str or list of content blocks)
        result_text = ""
        if isinstance(content, str):
            result_text = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if hasattr(part, "text"):
                    parts.append(part.text)
                else:
                    parts.append(str(part))
            result_text = "\n".join(parts)

        success = not is_error
        tool_issue = None
        if not success:
            from backend.services.tool_formatters import extract_tool_issue

            tool_issue = extract_tool_issue(result_text) or "Tool reported an issue"

        if tool_name not in _HIDDEN_TOOLS:
            from backend.services.tool_formatters import format_tool_display, format_tool_display_full

            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.transcript,
                    payload={
                        "role": "tool_call",
                        "content": tool_name,
                        "tool_name": tool_name,
                        "tool_args": tool_args_str,
                        "tool_result": result_text,
                        "tool_success": success,
                        "tool_issue": tool_issue,
                        "turn_id": turn_id,
                        "tool_display": format_tool_display(
                            tool_name,
                            tool_args_str,
                            tool_result=result_text or None,
                            tool_success=success,
                        ),
                        "tool_display_full": format_tool_display_full(
                            tool_name,
                            tool_args_str,
                            tool_result=result_text or None,
                            tool_success=success,
                        ),
                        "tool_duration_ms": int(duration_ms),
                    },
                ),
            )
            self._enqueue_log(
                session_id,
                f"Tool {'completed' if success else 'failed'}: {tool_name}",
                "info" if success else "warn",
                seq,
            )

        # Telemetry
        if job_id:
            from backend.services import telemetry as tel
            from backend.services.tool_classifier import classify_tool, extract_file_paths, extract_tool_target

            attrs: dict[str, str | bool] = {
                "job_id": job_id,
                "sdk": "claude",
                "tool_name": tool_name,
                "success": bool(success),
            }
            tel.tool_duration.record(duration_ms, attrs)

            # Tool classification
            category = classify_tool(tool_name)
            target = extract_tool_target(tool_name, tool_args_str)
            current_phase = self._current_phases.get(job_id, "agent_reasoning")
            turn_num = self._turn_counters.get(job_id, 0)

            # Retry detection
            from backend.services.retry_tracker import RetryTracker

            if job_id not in self._retry_trackers:
                self._retry_trackers[job_id] = RetryTracker()
            retry_result = self._retry_trackers[job_id].record(tool_name, target, 0, success)

            # Result size
            result_size = len(result_text.encode("utf-8", errors="replace")) if result_text else None

            # File access tracking
            file_rw_increment = {"file_read_count": 0, "file_write_count": 0}
            if category in ("file_read", "file_write"):
                paths = extract_file_paths(tool_name, tool_args_str)
                access_type = "write" if category == "file_write" else "read"
                if access_type == "read":
                    file_rw_increment["file_read_count"] = 1
                else:
                    file_rw_increment["file_write_count"] = 1
                for fpath in paths:
                    self._schedule_db_write(
                        self._db_write(
                            "record_file_access",
                            job_id=job_id,
                            file_path=fpath,
                            access_type=access_type,
                            turn_number=turn_num,
                        )
                    )

                # Emit file_changed events for successful writes so the runtime
                # service can trigger diff recalculation (mirrors CopilotAdapter's
                # session.workspace_file_changed handling).
                if category == "file_write" and success:
                    for fpath in paths:
                        self._enqueue(
                            session_id,
                            SessionEvent(
                                kind=SessionEventKind.file_changed,
                                payload={"path": fpath},
                            ),
                        )

            self._schedule_db_write(
                self._db_write(
                    "increment",
                    job_id=job_id,
                    tool_call_count=1,
                    tool_failure_count=0 if success else 1,
                    total_tool_duration_ms=int(duration_ms),
                    retry_count=1 if retry_result.is_retry else 0,
                    **file_rw_increment,
                )
            )

            job_start = self._job_start_times.get(job_id, time.monotonic())
            offset = time.monotonic() - job_start
            self._schedule_db_write(
                self._db_write(
                    "insert_span",
                    job_id=job_id,
                    span_type="tool",
                    name=tool_name,
                    started_at=round(offset, 2),
                    duration_ms=duration_ms,
                    attrs={
                        "success": success,
                        **({
                            "error_snippet": result_text[:500],
                        } if not success and result_text else {}),
                    },
                    tool_category=category,
                    tool_target=target,
                    turn_number=turn_num,
                    execution_phase=current_phase,
                    is_retry=retry_result.is_retry,
                    retries_span_id=retry_result.prior_failure_span_id,
                    tool_args_json=tool_args_str,
                    result_size_bytes=result_size,
                )
            )

    def _process_result_message(
        self,
        session_id: str,
        message: object,
        seq: list[int],
    ) -> None:
        """Handle the final ResultMessage — extract cost/usage and emit done."""
        job_id = self._session_to_job.get(session_id)
        result_text = getattr(message, "result", "") or ""
        total_cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
        usage = getattr(message, "usage", {}) or {}
        duration_ms = getattr(message, "duration_ms", 0) or 0
        is_error = getattr(message, "is_error", False)

        input_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
        output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0
        cache_read = usage.get("cache_read_input_tokens", 0) if isinstance(usage, dict) else 0
        cache_write = usage.get("cache_creation_input_tokens", 0) if isinstance(usage, dict) else 0

        # Telemetry — note: model is not on ResultMessage, so we use the main model.
        if job_id:
            from backend.services import telemetry as tel

            model = self._job_main_models.get(job_id, "")
            attrs = {"job_id": job_id, "sdk": "claude", "model": model}
            tel.tokens_input.add(int(input_tokens), attrs)
            tel.tokens_output.add(int(output_tokens), attrs)
            tel.tokens_cache_read.add(int(cache_read), attrs)
            tel.tokens_cache_write.add(int(cache_write), attrs)
            tel.cost_usd.add(float(total_cost_usd), attrs)
            tel.llm_duration.record(float(duration_ms), {**attrs, "is_subagent": False})

            num_turns = getattr(message, "num_turns", 0) or 1
            self._schedule_db_write(
                self._db_write(
                    "increment",
                    job_id=job_id,
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                    cache_read_tokens=int(cache_read),
                    cache_write_tokens=int(cache_write),
                    total_cost_usd=float(total_cost_usd),
                    total_llm_duration_ms=int(duration_ms),
                    llm_call_count=int(num_turns),
                    total_turns=int(num_turns),
                )
            )

            turn_num = self._turn_counters.get(job_id, 0)
            current_phase = self._current_phases.get(job_id, "agent_reasoning")

            job_start = self._job_start_times.get(job_id, time.monotonic())
            offset = time.monotonic() - job_start
            self._schedule_db_write(
                self._db_write(
                    "insert_span",
                    job_id=job_id,
                    span_type="llm",
                    name=model or "claude",
                    started_at=round(offset, 2),
                    duration_ms=float(duration_ms),
                    attrs={
                        "input_tokens": int(input_tokens),
                        "output_tokens": int(output_tokens),
                        "cache_read_tokens": int(cache_read),
                        "cache_write_tokens": int(cache_write),
                        "cost": float(total_cost_usd),
                        "is_subagent": False,
                    },
                    turn_number=turn_num,
                    execution_phase=current_phase,
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                    cache_read_tokens=int(cache_read),
                    cache_write_tokens=int(cache_write),
                    cost_usd=float(total_cost_usd),
                )
            )

        self._enqueue_log(
            session_id,
            f"Session complete (cost=${total_cost_usd:.4f}, {input_tokens}+{output_tokens} tokens)",
            "info",
            seq,
        )

        if is_error:
            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.error,
                    payload={"message": "Claude session ended with error", "result": result_text},
                ),
            )
        else:
            self._enqueue(
                session_id,
                SessionEvent(kind=SessionEventKind.done, payload={"result": result_text}),
            )

    # ------------------------------------------------------------------
    # AgentAdapterInterface implementation
    # ------------------------------------------------------------------

    async def create_session(self, config: SessionConfig) -> str:
        from claude_code_sdk import ClaudeCodeOptions, ClaudeSDKClient

        session_id = str(uuid.uuid4())
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        self._queues[session_id] = queue

        if config.job_id:
            self._session_to_job[session_id] = config.job_id
            self._job_start_times.setdefault(config.job_id, time.monotonic())
            if config.model:
                self._requested_models[config.job_id] = config.model

        # Capture Claude subprocess stderr for diagnostics on failure
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="claude_stderr_", suffix=".log")
        stderr_file = os.fdopen(stderr_fd, "w")
        self._stderr_files[session_id] = stderr_path

        # Build options
        options = ClaudeCodeOptions(
            cwd=config.workspace_path,
            model=config.model,
            permission_mode=_PERMISSION_MODE_MAP.get(config.permission_mode, "default"),  # type: ignore[arg-type]
            can_use_tool=self._build_can_use_tool(config, session_id),
            append_system_prompt=CODEPLANE_SYSTEM_PROMPT,
            extra_args={"debug-to-stderr": None},
            debug_stderr=stderr_file,
        )

        # MCP servers from CodePlane config
        if config.mcp_servers:
            mcp_config: dict[str, dict[str, Any]] = {}
            for name, srv in config.mcp_servers.items():
                entry: dict[str, Any] = {
                    "type": "stdio",
                    "command": srv.command,
                    "args": srv.args,
                }
                if srv.env:
                    entry["env"] = srv.env
                mcp_config[name] = entry
            options.mcp_servers = mcp_config  # type: ignore[assignment]

        # Resume support
        if config.resume_sdk_session_id:
            options.resume = config.resume_sdk_session_id

        # Create client and connect — the SDK requires an AsyncIterable prompt
        # when can_use_tool is set (streaming mode).
        try:
            client = ClaudeSDKClient(options)
            await client.connect(_prompt_to_stream(config.prompt))
        except Exception:
            if options.resume:
                # Resume failed — fall back to a fresh session (mirrors CopilotAdapter behaviour)
                log.warning(
                    "claude_session_resume_failed_creating_new",
                    resume_id=options.resume,
                    exc_info=True,
                )
                options.resume = None
                try:
                    client = ClaudeSDKClient(options)
                    await client.connect(_prompt_to_stream(config.prompt))
                except Exception:
                    log.error("claude_session_create_failed", exc_info=True)
                    self._cleanup_session(session_id)
                    raise
            else:
                log.error("claude_session_create_failed", exc_info=True)
                self._cleanup_session(session_id)
                raise

        self._clients[session_id] = client

        # Start background consumer
        task = asyncio.create_task(
            self._consume_messages(session_id, client),
            name=f"claude-consumer-{session_id[:8]}",
        )
        self._consumer_tasks[session_id] = task

        log.info("claude_session_created", session_id=session_id)
        return session_id

    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        queue = self._queues.get(session_id)
        if queue is None:
            log.error("claude_stream_no_queue", session_id=session_id)
            yield SessionEvent(
                kind=SessionEventKind.error,
                payload={"message": "No queue for session"},
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
        client = self._clients.get(session_id)
        if client is None:
            log.warning("claude_send_no_session", session_id=session_id)
            return
        try:
            # Start a new turn on the existing session
            await client.query(message)
        except Exception:
            log.warning("claude_send_message_failed", session_id=session_id, exc_info=True)

    async def interrupt_session(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        try:
            await client.interrupt()
        except Exception:
            log.warning("claude_interrupt_failed", session_id=session_id, exc_info=True)

    def pause_tools(self, session_id: str) -> None:
        self._paused_sessions.add(session_id)

    def resume_tools(self, session_id: str) -> None:
        self._paused_sessions.discard(session_id)

    async def abort_session(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        try:
            await client.interrupt()
            await client.disconnect()
        except Exception:
            log.warning("claude_abort_failed", session_id=session_id, exc_info=True)
        finally:
            self._cleanup_session(session_id)

    async def complete(self, prompt: str) -> str | None:
        """Single-turn completion using the Claude Agent SDK."""
        from claude_code_sdk import (
            AssistantMessage,
            ClaudeCodeOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeCodeOptions(
            max_turns=1,
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Glob", "Grep"],
        )

        collected: list[str] = []
        try:

            async def _run_query() -> None:
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        for block in getattr(message, "content", []) or []:
                            if isinstance(block, TextBlock):
                                text = block.text
                                if text:
                                    collected.append(text)
                    elif isinstance(message, ResultMessage):
                        result = getattr(message, "result", "")
                        if result:
                            collected.append(result)
                        break

            await asyncio.wait_for(_run_query(), timeout=180)
        except TimeoutError:
            log.warning("claude_complete_timeout", prompt_len=len(prompt))
        except Exception:
            log.error("claude_complete_failed", prompt_len=len(prompt), exc_info=True)
            return None
        return "\n".join(collected)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _prompt_to_stream(prompt: str) -> Any:  # noqa: ANN401
    """Wrap a string prompt as an async iterable for Claude SDK streaming mode.

    The generator **must** remain alive after yielding the initial prompt.
    When the generator returns, the SDK's ``stream_input`` calls
    ``transport.end_input()`` which closes stdin to the Claude subprocess.
    With stdin closed the SDK can no longer write control-protocol responses
    (tool permission results) back to the subprocess, so the first tool call
    hangs forever waiting for a permission response that will never arrive.
    """
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": "default",
    }
    # Keep the stream open so stdin is not closed.
    # The anyio task running stream_input will be cancelled when the
    # session disconnects — that is the normal cleanup path.
    # Use a bare Future — it suspends until cancelled, and correctly
    # propagates CancelledError under both asyncio and anyio.
    await asyncio.get_running_loop().create_future()


def _summarize_tool_input(tool_name: str, input_data: dict[str, Any]) -> str:
    """Build a short human-readable summary of a tool call for approval display."""
    if tool_name == "Bash":
        return str(input_data.get("command", ""))[:_TOOL_SUMMARY_MAX]
    if tool_name in ("Edit", "Write"):
        return str(input_data.get("file_path", "") or input_data.get("path", ""))
    if tool_name == "Read":
        return str(input_data.get("file_path", "") or input_data.get("path", ""))
    if tool_name == "WebFetch":
        return str(input_data.get("url", ""))[:_TOOL_SUMMARY_MAX]
    if tool_name == "WebSearch":
        return str(input_data.get("query", ""))[:_TOOL_SUMMARY_MAX]
    try:
        return json.dumps(input_data, default=str)[:_TOOL_SUMMARY_FALLBACK]
    except Exception:
        return str(input_data)[:_TOOL_SUMMARY_FALLBACK]
