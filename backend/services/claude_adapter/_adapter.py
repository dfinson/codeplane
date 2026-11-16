"""ClaudeAdapter class — bridges the Claude Agent SDK into CodePlane."""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.events import EventKind
from backend.services.adapters.agent_adapter import CODEPLANE_SYSTEM_PROMPT, CompletionResult
from backend.services.adapters.base_adapter import (
    COMPLETION_TIMEOUT_S,
    BaseAgentAdapter,
    PermissionDecision,
)
from backend.services.auth.permission_policy import PermissionRequest
from backend.services.claude_adapter._helpers import (
    _kill_sdk_subprocess,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import SessionConfig
    from backend.models.events import SessionEvent
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService

log = structlog.get_logger()


class ClaudeAdapter(BaseAgentAdapter):
    """Wraps the Claude Agent SDK (Python) behind the adapter interface.

    Each session is backed by a ``ClaudeSDKClient`` instance that maintains
    conversation context.  A background asyncio task consumes the SDK's
    async message iterator and builds native ``traceforge.SessionEvent``
    objects (via the shared base ``_emit_tf`` helper) onto a queue that
    ``stream_events()`` yields from.
    """

    _source_framework = "claude"

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
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._requested_models: dict[str, str] = {}
        self._scout_models: dict[str, str] = {}
        self._model_verified: dict[str, bool] = {}
        # Stderr capture files for debugging failed sessions
        self._stderr_files: dict[str, str] = {}
        self._stderr_file_objects: dict[str, Any] = {}  # session_id → open file
        # Tool-call buffer: tool_use_id -> {job_id, tool_name, arguments, start}
        # Populated on ToolUseBlock, drained on ToolResultBlock to compute
        # duration_ms and carry start-time context (re-homed from EventPipeline).
        self._tool_buf: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_session(self, session_id: str) -> None:
        task = self._consumer_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
        stderr_fobj = self._stderr_file_objects.pop(session_id, None)
        if stderr_fobj:
            with contextlib.suppress(OSError):
                stderr_fobj.close()
        stderr_path = self._stderr_files.pop(session_id, None)
        if stderr_path:
            with contextlib.suppress(OSError):
                os.unlink(stderr_path)
        # Claude-specific model tracking
        job_id = self._session_to_job.get(session_id)
        self._scout_models.pop(session_id, None)
        if job_id and self.get_session_kind(session_id) != "preflight":
            self._requested_models.pop(job_id, None)
            self._model_verified.pop(job_id, None)
            self._log_seqs.pop(job_id, None)
            for cid in [c for c, v in self._tool_buf.items() if v.get("job_id") == job_id]:
                self._tool_buf.pop(cid, None)
        super()._cleanup_session_state(session_id)

    def _read_session_stderr(self, session_id: str) -> str:
        """Read captured stderr from the Claude subprocess."""
        path = self._stderr_files.get(session_id)
        if not path:
            return ""
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return ""

    # ------------------------------------------------------------------
    # Permission callback builder
    # ------------------------------------------------------------------

    def _build_can_use_tool(self, config: SessionConfig, session_id: str) -> Any:  # noqa: ANN401
        """Build the ``can_use_tool`` callback for the Claude SDK.

        Wraps the base adapter's ``_evaluate_permission`` to return SDK-
        specific PermissionResultAllow / PermissionResultDeny objects.
        """
        from claude_code_sdk import PermissionResultAllow, PermissionResultDeny

        # Map Claude tool names to permission_policy kinds
        _claude_tool_kind: dict[str, str] = {
            "Bash": "shell",
            "Edit": "write",
            "Write": "write",
            "Read": "read",
            "MultiEdit": "write",
            "Glob": "read",
            "Grep": "read",
            "ToolSearch": "read",
            "WebFetch": "read",
            "WebSearch": "read",
        }

        async def _can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            context: object,
        ) -> PermissionResultAllow | PermissionResultDeny:
            job_id = self._session_to_job.get(session_id)
            tool_kind = _claude_tool_kind.get(tool_name, "custom-tool")
            full_cmd = str(input_data.get("command", "")) if tool_name == "Bash" else None
            file_name = str(input_data.get("file_path", "") or input_data.get("path", "")) or None
            if config.session_kind == "preflight":
                from backend.services.tools.preflight_curator import scout_read_allowed

                if not scout_read_allowed(
                    config.workspace_path,
                    tool_kind,
                    tool_name,
                    [
                        value
                        for key in (("file_path", "path", "pattern") if tool_name == "Glob" else ("file_path", "path"))
                        if isinstance(value := input_data.get(key), str) and value
                    ],
                ):
                    return PermissionResultDeny(message="Scout tools are limited to workspace reads and searches")
            decision = await self._evaluate_permission(
                session_id,
                job_id,
                PermissionRequest(
                    kind=tool_kind,
                    workspace_path=config.workspace_path,
                    full_command_text=full_cmd,
                    file_name=file_name,
                    path=file_name,
                ),
                tool_name=tool_name,
                tool_input=input_data,
            )
            if decision == PermissionDecision.allow:
                return PermissionResultAllow()
            return PermissionResultDeny(message="Blocked by CodePlane policy")

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
        from claude_code_sdk.types import StreamEvent

        # Guard against SDK message-parse failures for unknown event types
        # (e.g. rate_limit_event in SDK ≤0.0.25).
        try:
            from claude_code_sdk._errors import MessageParseError
        except ImportError:
            MessageParseError = None  # type: ignore[assignment,misc]  # noqa: N806

        queue = self._queues.get(session_id)
        if queue is None:
            return

        done = False
        parse_error_retries = 0
        max_parse_error_retries = 5
        try:
            while not done:
                try:
                    async for message in client.receive_messages():  # type: ignore[attr-defined]
                        parse_error_retries = 0  # reset on successful message
                        try:
                            if isinstance(message, SystemMessage):
                                job_id = self._session_to_job.get(session_id)
                                if job_id and self.get_session_kind(session_id) != "preflight":
                                    self._emit_tf(
                                        session_id,
                                        job_id,
                                        EventKind.message_assistant,
                                        {"content": "Claude session initialized"},
                                    )

                            elif isinstance(message, AssistantMessage):
                                await self._process_assistant_message(session_id, message)

                            elif isinstance(message, UserMessage):
                                await self._process_user_message(session_id, message)

                            elif isinstance(message, ResultMessage):
                                await self._process_result_message(session_id, message)
                                done = True
                                break

                            elif isinstance(message, StreamEvent):
                                await self._process_stream_event(session_id, message)
                        except Exception:
                            log.warning(
                                "claude_pipeline_event_error",
                                session_id=session_id,
                                message_type=type(message).__name__,
                                exc_info=True,
                            )

                    else:
                        # Iterator exhausted without ResultMessage — session ended
                        if self.get_session_kind(session_id) == "preflight":
                            self._emit_tf(
                                session_id,
                                self._session_to_job.get(session_id) or session_id,
                                EventKind.job_failed,
                                {"message": "Claude Scout ended without a final context brief"},
                            )
                        done = True
                except Exception as exc:
                    if MessageParseError is not None and isinstance(exc, MessageParseError):
                        parse_error_retries += 1
                        log.warning(
                            "claude_unknown_message_type",
                            session_id=session_id,
                            error=str(exc),
                            retry=parse_error_retries,
                        )
                        if parse_error_retries >= max_parse_error_retries:
                            log.error(
                                "claude_parse_error_retry_limit",
                                session_id=session_id,
                                retries=parse_error_retries,
                            )
                            self._emit_tf(
                                session_id,
                                self._session_to_job.get(session_id) or session_id,
                                EventKind.job_failed,
                                {
                                    "message": (
                                        f"Claude SDK: too many consecutive parse errors ({parse_error_retries})"
                                    ),
                                },
                            )
                            done = True
                        else:
                            continue
                        continue

                    stderr_snippet = self._read_session_stderr(session_id)
                    log.error(
                        "claude_consumer_error",
                        session_id=session_id,
                        stderr_tail=stderr_snippet if stderr_snippet else "",
                        exc_info=True,
                    )
                    job_id = self._session_to_job.get(session_id)
                    error_msg = f"Claude SDK session error: {exc}"
                    if stderr_snippet:
                        error_msg += f"\n{stderr_snippet}"
                    self._emit_tf(
                        session_id,
                        job_id or session_id,
                        EventKind.job_failed,
                        {"message": error_msg},
                    )
                    done = True
        except asyncio.CancelledError:
            log.info("claude_consumer_cancelled", session_id=session_id)
        finally:
            # Sentinel to signal end of stream
            if queue is not None:
                queue.put_nowait(None)

    async def _process_user_message(
        self,
        session_id: str,
        message: object,
    ) -> None:
        """Handle a UserMessage — extract ToolResultBlocks for telemetry/transcript."""
        from claude_code_sdk import ToolResultBlock

        content = getattr(message, "content", None)
        job_id = self._session_to_job.get(session_id)

        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    await self._process_tool_result_block(session_id, block, job_id)
        elif isinstance(content, str) and content.strip() and job_id:
            self._emit_tf(session_id, job_id, EventKind.message_user, {"content": content})

    async def _process_assistant_message(
        self,
        session_id: str,
        message: object,
    ) -> None:
        """Translate an AssistantMessage's content blocks into pipeline events."""
        from claude_code_sdk import TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

        content_blocks = getattr(message, "content", []) or []
        model = getattr(message, "model", "") or ""
        job_id = self._session_to_job.get(session_id)
        is_scout = self.get_session_kind(session_id) == "preflight"

        # Lock in the main model from the first AssistantMessage that carries one
        if is_scout and model:
            self._scout_models[session_id] = model
        elif job_id and model:
            requested = self._requested_models.get(job_id, "")
            if not self._model_verified.get(job_id):
                self._model_verified[job_id] = True
                self._verify_and_set_model(session_id, job_id, model, requested)

        if not job_id:
            return

        if is_scout:
            texts = [block.text or "" for block in content_blocks if isinstance(block, TextBlock)]
            has_tool_requests = any(isinstance(block, ToolUseBlock) for block in content_blocks)
            if texts or has_tool_requests:
                self._emit_tf(
                    session_id,
                    job_id,
                    EventKind.message_assistant,
                    {"content": "\n".join(texts), "has_tool_requests": has_tool_requests},
                )
        for block in content_blocks:
            if isinstance(block, TextBlock):
                if is_scout:
                    continue
                text = block.text or ""
                if not text.strip():
                    continue
                payload: dict[str, Any] = {"content": text}
                self._emit_tf(session_id, job_id, EventKind.message_assistant, payload)

            elif isinstance(block, ToolUseBlock):
                await self._process_tool_use_block(session_id, block, job_id)

            elif isinstance(block, ToolResultBlock):
                await self._process_tool_result_block(session_id, block, job_id)

            elif isinstance(block, ThinkingBlock):
                thinking = block.thinking or ""
                if thinking.strip():
                    self._emit_tf(session_id, job_id, EventKind.llm_reasoning_chunk, {"content": thinking})

    async def _process_tool_use_block(
        self,
        session_id: str,
        block: object,
        job_id: str | None,
    ) -> None:
        """Handle a ToolUseBlock — buffer start-time and emit tool.call.started."""
        tool_name = getattr(block, "name", "") or "tool"
        tool_id = getattr(block, "id", "") or str(uuid.uuid4())
        tool_input = getattr(block, "input", None)
        arguments: Any = tool_input if isinstance(tool_input, dict) else None

        if not job_id:
            return

        self._tool_buf[tool_id] = {
            "job_id": job_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "start": time.monotonic(),
        }
        self._emit_tf(
            session_id,
            job_id,
            EventKind.tool_call_started,
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "tool_call_id": tool_id,
                "content": tool_name,
            },
        )

    async def _process_tool_result_block(
        self,
        session_id: str,
        block: object,
        job_id: str | None,
    ) -> None:
        """Handle a ToolResultBlock — pair with buffered start and emit tool.call.completed."""
        tool_use_id = getattr(block, "tool_use_id", "") or ""
        content = getattr(block, "content", "")
        is_error = getattr(block, "is_error", False)

        result_text = self._extract_result_text(content)

        if not job_id:
            return

        buffered = self._tool_buf.pop(tool_use_id, {})
        resolved_name = str(buffered.get("tool_name", "tool"))

        # Correct false failures for file-edit tools
        success = not is_error
        if not success:
            from backend.services.tool_formatters import correct_edit_success

            success = correct_edit_success(resolved_name, success, result_text)

        start = buffered.get("start")
        duration_ms = (time.monotonic() - start) * 1000.0 if start else None
        self._emit_tf(
            session_id,
            job_id,
            EventKind.tool_call_completed,
            {
                "tool_name": resolved_name,
                "arguments": buffered.get("arguments"),
                "result": result_text,
                "success": success,
                "tool_call_id": tool_use_id,
            },
            duration_ms=duration_ms,
        )

    async def _process_result_message(
        self,
        session_id: str,
        message: object,
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

        if job_id:
            model = (
                self._scout_models.get(session_id, "")
                if self.get_session_kind(session_id) == "preflight"
                else self._job_main_models.get(job_id, "")
            )
            num_turns = getattr(message, "num_turns", 0) or 1

            self._emit_tf(
                session_id,
                job_id,
                EventKind.telemetry_usage,
                {
                    "model": model,
                    "input_tokens": int(input_tokens),
                    "output_tokens": int(output_tokens),
                    "cache_read_tokens": int(cache_read),
                    "cache_write_tokens": int(cache_write),
                    "cost_usd": float(total_cost_usd),
                    "duration_ms": float(duration_ms),
                    "is_subagent": False,
                    "advance_turn": True,
                    "num_turns": int(num_turns),
                },
            )

        if is_error:
            self._emit_tf(
                session_id,
                job_id or session_id,
                EventKind.job_failed,
                {"message": "Claude session ended with error", "result": result_text},
            )
        elif self.get_session_kind(session_id) == "preflight":
            if getattr(message, "result", None) is None:
                self._emit_tf(
                    session_id,
                    job_id or session_id,
                    EventKind.job_failed,
                    {"message": "Claude Scout ended without a final context brief"},
                )
                return
            self._emit_tf(
                session_id,
                job_id or session_id,
                EventKind.session_ended,
                {"result": getattr(message, "result", None)},
            )
        # On success no explicit event is emitted; the ``done=True`` break in
        # ``_consume_messages`` triggers the queue sentinel that ends the stream.

    async def _process_stream_event(
        self,
        session_id: str,
        message: object,
    ) -> None:
        """Handle a StreamEvent — extract partial tool output and emit deltas.

        The Claude SDK's ``include_partial_messages=True`` mode emits
        ``StreamEvent`` objects that wrap raw Anthropic API deltas.
        """
        parent_id = getattr(message, "parent_tool_use_id", None) or ""
        event = getattr(message, "event", None)
        if not event or not isinstance(event, dict):
            return

        job_id = self._session_to_job.get(session_id)
        if not job_id:
            return

        # Extract text delta from various Anthropic API stream event shapes
        chunk = ""
        event_type = event.get("type", "")
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")

            # Thinking deltas — emit as streaming thinking chunk
            if delta_type == "thinking_delta":
                thinking_chunk = delta.get("thinking", "")
                if thinking_chunk:
                    self._emit_tf(
                        session_id,
                        job_id,
                        EventKind.llm_reasoning_chunk,
                        {"content": thinking_chunk},
                        partial=True,
                    )
                return

            chunk = delta.get("text", "") or delta.get("partial_json", "") or ""
        elif event_type == "content_block_start":
            block = event.get("content_block", {})
            chunk = block.get("text", "")

        if not chunk or not parent_id:
            return

        self._emit_tf(
            session_id,
            job_id,
            EventKind.tool_result_chunk,
            {"tool_call_id": parent_id, "content": chunk},
            partial=True,
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
            self.set_job_id(session_id, config.job_id)
            if config.session_kind == "preflight":
                self._scout_models[session_id] = config.model or ""
            elif config.model:
                self._requested_models[config.job_id] = config.model
        if config.session_kind != "job":
            self.set_session_kind(session_id, config.session_kind)

        # Capture Claude subprocess stderr for diagnostics on failure
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="claude_stderr_", suffix=".log")
        stderr_file = os.fdopen(stderr_fd, "w")
        self._stderr_files[session_id] = stderr_path
        self._stderr_file_objects[session_id] = stderr_file

        # Sidecar/preflight sessions define their own identity — skip the
        # main-agent CODEPLANE_SYSTEM_PROMPT entirely.
        system_prompt = config.system_prompt_override or CODEPLANE_SYSTEM_PROMPT

        # Workspace memory — curated context hidden from transcript
        if config.memory_context:
            system_prompt = system_prompt + "\n\n## Workspace Memory\n\n" + config.memory_context

        # Build options
        options = ClaudeCodeOptions(
            cwd=config.workspace_path,
            model=config.model,
            permission_mode="default",  # Always use callback — policy router handles all decisions
            can_use_tool=self._build_can_use_tool(config, session_id),
            append_system_prompt=system_prompt,
            extra_args={"debug-to-stderr": None},
            debug_stderr=stderr_file,
            include_partial_messages=True,
        )

        # Session-level constraints
        if config.max_turns is not None:
            options.max_turns = config.max_turns
        if config.disallowed_tools:
            options.disallowed_tools = list(config.disallowed_tools)

        # MCP servers from CodePlane config
        mcp_config: dict[str, Any] = {}
        if config.mcp_servers:
            for name, srv in config.mcp_servers.items():
                entry: dict[str, Any] = {
                    "type": "stdio",
                    "command": srv.command,
                    "args": srv.args,
                }
                if srv.env:
                    entry["env"] = srv.env
                mcp_config[name] = entry

        if mcp_config:
            options.mcp_servers = mcp_config

        # Resume support
        if config.resume_sdk_session_id:
            options.resume = config.resume_sdk_session_id

        # Create client and connect — the SDK requires an AsyncIterable prompt
        # when can_use_tool is set (streaming mode).
        client = None
        try:
            try:
                client = ClaudeSDKClient(options)
                await client.connect(_prompt_to_stream(config.prompt))
            except Exception:
                if not options.resume:
                    raise
                log.warning(
                    "claude_session_resume_failed_creating_new",
                    resume_id=options.resume,
                    exc_info=True,
                )
                _kill_sdk_subprocess(client)
                options.resume = None
                client = ClaudeSDKClient(options)
                await client.connect(_prompt_to_stream(config.prompt))
        except BaseException:
            log.error("claude_session_create_failed", exc_info=True)
            _kill_sdk_subprocess(client)
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
            from traceforge.types import EventMetadata

            from backend.models.events import new_event

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
            # Cancel the consumer task first — this ensures the SDK subprocess
            # is no longer being read before we disconnect.
            consumer = self._consumer_tasks.get(session_id)
            if consumer and not consumer.done():
                consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await consumer

            # Kill the CLI subprocess using raw OS signals.
            #
            # We MUST NOT call client.disconnect(), transport.close(), or
            # ANY method on anyio-owned objects.  The SDK's Query holds an
            # anyio task-group whose cancel-scope was entered in a different
            # asyncio task.  Touching ANY anyio object (streams, Process,
            # etc.) from this task triggers cross-task cancel-scope
            # propagation that injects CancelledError into the entire
            # SQLAlchemy connection pool.
            #
            # os.kill(SIGTERM) + os.waitpid are pure OS calls that bypass
            # anyio entirely.  The subprocess is already dead or dying;
            # we just reap it so it doesn't become a zombie.
            _kill_sdk_subprocess(self._clients.get(session_id))
            self._cleanup_session(session_id)

    async def send_message(self, session_id: str, message: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            log.warning("claude_send_no_session", session_id=session_id)
            return
        try:
            # Start a new turn on the existing session
            await client.query(message)
        except (OSError, RuntimeError):
            log.warning("claude_send_message_failed", session_id=session_id, exc_info=True)

    async def interrupt_session(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        try:
            await client.interrupt()
        except (OSError, RuntimeError):
            log.warning("claude_interrupt_failed", session_id=session_id, exc_info=True)

    async def abort_session(self, session_id: str) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        try:
            await client.interrupt()
        except (OSError, RuntimeError, asyncio.CancelledError):
            log.warning("claude_abort_interrupt_failed", session_id=session_id, exc_info=True)

        # Kill subprocess with raw OS signals — see stream_events comment.
        _kill_sdk_subprocess(client)
        self._cleanup_session(session_id)

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        """Single-turn completion using the Claude Agent SDK."""
        from claude_code_sdk import (
            AssistantMessage,
            ClaudeCodeOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        from backend.services.adapters.agent_adapter import CompletionResult

        options = ClaudeCodeOptions(
            max_turns=1,
            model=model or "claude-haiku-4-5",
            permission_mode="bypassPermissions",
            allowed_tools=[],
        )

        collected: list[str] = []
        result_meta: dict[str, object] = {}
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
                        # Capture usage/cost from the result message
                        usage = getattr(message, "usage", {}) or {}
                        if isinstance(usage, dict):
                            result_meta["input_tokens"] = usage.get("input_tokens", 0)
                            result_meta["output_tokens"] = usage.get("output_tokens", 0)
                        result_meta["cost_usd"] = getattr(message, "total_cost_usd", 0.0) or 0.0
                        result_meta["model"] = getattr(message, "model", "") or ""
                        break

            await asyncio.wait_for(_run_query(), timeout=COMPLETION_TIMEOUT_S)
        except TimeoutError:
            log.warning("claude_complete_timeout", prompt_len=len(prompt))
        except (OSError, RuntimeError):
            log.error("claude_complete_failed", prompt_len=len(prompt), exc_info=True)
            return CompletionResult()
        return CompletionResult(
            text="\n".join(collected),
            input_tokens=int(result_meta.get("input_tokens", 0) or 0),  # type: ignore[call-overload]
            output_tokens=int(result_meta.get("output_tokens", 0) or 0),  # type: ignore[call-overload]
            cost_usd=float(result_meta.get("cost_usd", 0.0) or 0.0),  # type: ignore[arg-type]
            model=str(result_meta.get("model", "") or ""),
        )


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
