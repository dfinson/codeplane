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
from backend.services.agent_adapter import AgentAdapterInterface

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from backend.services.approval_service import ApprovalService
    from backend.services.event_bus import EventBus

log = structlog.get_logger()

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
    ) -> None:
        self._queues: dict[str, asyncio.Queue[SessionEvent | None]] = {}
        self._clients: dict[str, object] = {}  # session_id → ClaudeSDKClient
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_to_job: dict[str, str] = {}
        self._tool_start_times: dict[str, float] = {}
        self._approval_service = approval_service
        self._event_bus = event_bus

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_session(self, session_id: str) -> None:
        self._clients.pop(session_id, None)
        self._queues.pop(session_id, None)
        self._session_to_job.pop(session_id, None)
        task = self._consumer_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

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
            mode = config.permission_mode
            job_id = self._session_to_job.get(session_id)

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
                proposed_action=json.dumps(input_data, default=str)[:2000],
            )

            # Emit approval_request event
            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.approval_request,
                    payload={
                        "description": description,
                        "proposed_action": json.dumps(input_data, default=str)[:2000],
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
        )

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

                elif isinstance(message, ResultMessage):
                    self._process_result_message(session_id, message, seq)
                    break

                # UserMessage, StreamEvent, TaskStartedMessage etc. are logged but not
                # forwarded as transcript events (they are internal SDK bookkeeping).
        except asyncio.CancelledError:
            log.info("claude_consumer_cancelled", session_id=session_id)
        except Exception:
            log.error("claude_consumer_error", session_id=session_id, exc_info=True)
            self._enqueue(
                session_id,
                SessionEvent(
                    kind=SessionEventKind.error,
                    payload={"message": "Claude SDK session error"},
                ),
            )
        finally:
            # Sentinel to signal end of stream
            if queue is not None:
                queue.put_nowait(None)

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

        for block in content_blocks:
            if isinstance(block, TextBlock):
                text = block.text or ""
                if not text.strip():
                    continue
                self._enqueue(
                    session_id,
                    SessionEvent(
                        kind=SessionEventKind.transcript,
                        payload={"role": "agent", "content": text},
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
        """Handle a ToolUseBlock — emit tool start log + record start time."""
        tool_name = getattr(block, "name", "") or "tool"
        tool_id = getattr(block, "id", "") or str(uuid.uuid4())

        # Record start time for duration calculation
        self._tool_start_times[tool_id] = time.monotonic()

        if tool_name not in _HIDDEN_TOOLS:
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

        # Resolve tool name from the parent ToolUseBlock if we can
        # (Claude SDK doesn't directly link result→name, but we can look
        # up from the preceding blocks in the same message)
        tool_name = "tool"
        tool_args_str: str | None = None

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

        if tool_name not in _HIDDEN_TOOLS:
            from backend.services.tool_formatters import format_tool_display

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
                        "tool_display": format_tool_display(
                            tool_name,
                            tool_args_str,
                            tool_result=result_text or None,
                            tool_success=success,
                        ),
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
            from backend.services.telemetry import collector as tel

            tel.record_tool_call(
                job_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=success,
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

        # Telemetry — note: model is not on ResultMessage, so we pass empty string.
        # Model info is available on AssistantMessage instead.
        if job_id:
            from backend.services.telemetry import collector as tel

            tel.record_llm_usage(
                job_id,
                model="",
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                cache_read_tokens=int(cache_read),
                cache_write_tokens=int(cache_write),
                cost=float(total_cost_usd),
                duration_ms=float(duration_ms),
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

        # Build options
        options = ClaudeCodeOptions(
            cwd=config.workspace_path,
            model=config.model,
            permission_mode=_PERMISSION_MODE_MAP.get(config.permission_mode, "default"),  # type: ignore[arg-type]
            can_use_tool=self._build_can_use_tool(config, session_id),
            append_system_prompt=(
                "You are running inside CodePlane, a headless non-interactive orchestration "
                "framework. There is no human at a terminal. Do not enter plan mode or "
                "pause to present a plan for review. Proceed directly with task execution."
            ),
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
        client = self._clients.get(session_id)
        if client is None:
            log.warning("claude_send_no_session", session_id=session_id)
            return
        try:
            # Start a new turn on the existing session
            await client.query(message)  # type: ignore[attr-defined]
        except Exception:
            log.warning("claude_send_message_failed", session_id=session_id, exc_info=True)

    async def abort_session(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        try:
            await client.interrupt()  # type: ignore[attr-defined]
            await client.disconnect()  # type: ignore[attr-defined]
        except Exception:
            log.warning("claude_abort_failed", session_id=session_id, exc_info=True)
        finally:
            self._cleanup_session(session_id)

    async def complete(self, prompt: str) -> str:
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
        except Exception:
            log.error("claude_complete_failed", exc_info=True)
            return ""
        return "\n".join(collected)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _prompt_to_stream(prompt: str) -> Any:  # noqa: ANN401
    """Wrap a string prompt as an async iterable for Claude SDK streaming mode."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": "default",
    }


def _summarize_tool_input(tool_name: str, input_data: dict[str, Any]) -> str:
    """Build a short human-readable summary of a tool call for approval display."""
    if tool_name == "Bash":
        return str(input_data.get("command", ""))[:200]
    if tool_name in ("Edit", "Write"):
        return str(input_data.get("file_path", "") or input_data.get("path", ""))
    if tool_name == "Read":
        return str(input_data.get("file_path", "") or input_data.get("path", ""))
    if tool_name == "WebFetch":
        return str(input_data.get("url", ""))[:200]
    if tool_name == "WebSearch":
        return str(input_data.get("query", ""))[:200]
    # Fallback: first 120 chars of JSON
    try:
        return json.dumps(input_data, default=str)[:120]
    except Exception:
        return str(input_data)[:120]
