"""Unit tests for backend.services.claude_adapter — ClaudeAdapter.

All claude_code_sdk imports are mocked so the tests run without the SDK installed.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.domain import (
    MCPServerConfig,
    PermissionMode,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)

# ---------------------------------------------------------------------------
# Fake claude_code_sdk types (injected before the adapter is imported)
# ---------------------------------------------------------------------------


class _FakePermissionResultAllow:
    pass


class _FakePermissionResultDeny:
    def __init__(self, message: str = "") -> None:
        self.message = message


class _FakeTextBlock:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, name: str = "Bash", id: str = "") -> None:
        self.name = name
        self.id = id or str(uuid.uuid4())


class _FakeToolResultBlock:
    def __init__(
        self,
        tool_use_id: str = "",
        content: Any = "",
        is_error: bool = False,
    ) -> None:
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class _FakeSystemMessage:
    pass


class _FakeUserMessage:
    def __init__(self, content: Any = None, parent_tool_use_id: str | None = None) -> None:
        self.content = content
        self.parent_tool_use_id = parent_tool_use_id


class _FakeAssistantMessage:
    def __init__(self, content: list[Any] | None = None, model: str = "claude-sonnet-4-20250514") -> None:
        self.content = content or []
        self.model = model


class _FakeResultMessage:
    def __init__(
        self,
        result: str = "",
        total_cost_usd: float = 0.0,
        usage: dict[str, int] | None = None,
        duration_ms: int = 0,
        is_error: bool = False,
    ) -> None:
        self.result = result
        self.total_cost_usd = total_cost_usd
        self.usage = usage or {}
        self.duration_ms = duration_ms
        self.is_error = is_error


class _FakeClaudeCodeOptions:
    resume: Any = None
    mcp_servers: Any = None

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeTransport:
    """Fake transport for _FakeClaudeSDKClient."""

    def __init__(self) -> None:
        self._closed = False

    async def close(self) -> None:
        self._closed = True


class _FakeClaudeSDKClient:
    """Fake client whose receive_messages() yields injected messages."""

    def __init__(self, options: Any = None) -> None:
        self.options = options
        self._messages: list[Any] = []
        self.connected = False
        self._interrupted = False
        self._disconnected = False
        self._transport = _FakeTransport()

    async def connect(self, prompt_stream: Any) -> None:
        self.connected = True

    async def receive_messages(self):  # noqa: ANN201
        for msg in self._messages:
            yield msg

    async def query(self, message: str) -> None:
        pass

    async def interrupt(self) -> None:
        self._interrupted = True

    async def disconnect(self) -> None:
        self._disconnected = True


def _build_fake_sdk_module() -> ModuleType:
    """Build a fake ``claude_code_sdk`` module with all types the adapter imports."""
    mod = ModuleType("claude_code_sdk")
    mod.PermissionResultAllow = _FakePermissionResultAllow
    mod.PermissionResultDeny = _FakePermissionResultDeny
    mod.TextBlock = _FakeTextBlock
    mod.ToolUseBlock = _FakeToolUseBlock
    mod.ToolResultBlock = _FakeToolResultBlock
    mod.SystemMessage = _FakeSystemMessage
    mod.UserMessage = _FakeUserMessage
    mod.AssistantMessage = _FakeAssistantMessage
    mod.ResultMessage = _FakeResultMessage
    mod.ClaudeCodeOptions = _FakeClaudeCodeOptions
    mod.ClaudeSDKClient = _FakeClaudeSDKClient
    mod.query = AsyncMock()
    return mod


# Inject the fake module before importing the adapter
_fake_sdk = _build_fake_sdk_module()
sys.modules.setdefault("claude_code_sdk", _fake_sdk)

from backend.services.claude_adapter import (  # noqa: E402
    _HIDDEN_TOOLS,
    _PERMISSION_MODE_MAP,
    ClaudeAdapter,
    _summarize_tool_input,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter() -> ClaudeAdapter:
    return ClaudeAdapter()


@pytest.fixture()
def adapter_with_services() -> ClaudeAdapter:
    approval = MagicMock()
    approval.is_trusted = MagicMock(return_value=False)
    event_bus = MagicMock()
    return ClaudeAdapter(approval_service=approval, event_bus=event_bus)


def _make_config(**overrides: Any) -> SessionConfig:
    defaults: dict[str, Any] = {
        "workspace_path": "/tmp/workspace",
        "prompt": "hello",
        "job_id": "job-1",
        "permission_mode": PermissionMode.full_auto,
    }
    defaults.update(overrides)
    return SessionConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests: module-level helpers
# ---------------------------------------------------------------------------


class TestSummarizeToolInput:
    def test_bash_command(self) -> None:
        assert _summarize_tool_input("Bash", {"command": "ls -la"}) == "ls -la"

    def test_edit_file_path(self) -> None:
        assert _summarize_tool_input("Edit", {"file_path": "/tmp/foo.py"}) == "/tmp/foo.py"

    def test_write_file_path(self) -> None:
        assert _summarize_tool_input("Write", {"path": "/tmp/bar.py"}) == "/tmp/bar.py"

    def test_read_file_path(self) -> None:
        assert _summarize_tool_input("Read", {"file_path": "readme.md"}) == "readme.md"

    def test_webfetch_url(self) -> None:
        assert _summarize_tool_input("WebFetch", {"url": "https://example.com"}) == "https://example.com"

    def test_websearch_query(self) -> None:
        assert _summarize_tool_input("WebSearch", {"query": "python async"}) == "python async"

    def test_fallback_json(self) -> None:
        result = _summarize_tool_input("CustomTool", {"a": 1})
        assert "1" in result

    def test_fallback_truncation(self) -> None:
        result = _summarize_tool_input("CustomTool", {"x": "a" * 200})
        assert len(result) <= 120


class TestPermissionModeMap:
    def test_auto_maps_to_bypass(self) -> None:
        assert _PERMISSION_MODE_MAP[PermissionMode.full_auto] == "bypassPermissions"

    def test_read_only_maps_to_plan(self) -> None:
        assert _PERMISSION_MODE_MAP[PermissionMode.observe_only] == "plan"

    def test_approval_required_maps_to_default(self) -> None:
        assert _PERMISSION_MODE_MAP[PermissionMode.review_and_approve] == "default"


class TestHiddenTools:
    def test_no_hidden_tools(self) -> None:
        assert len(_HIDDEN_TOOLS) == 0

    def test_todo_write_not_hidden(self) -> None:
        assert "TodoWrite" not in _HIDDEN_TOOLS


# ---------------------------------------------------------------------------
# Tests: ClaudeAdapter internals
# ---------------------------------------------------------------------------


class TestCleanupSession:
    def test_cleanup_removes_all_state(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._clients[sid] = MagicMock()
        adapter._session_to_job[sid] = "job-1"
        task = MagicMock()
        task.done.return_value = False
        adapter._consumer_tasks[sid] = task

        adapter._cleanup_session(sid)

        assert sid not in adapter._queues
        assert sid not in adapter._clients
        assert sid not in adapter._session_to_job
        assert sid not in adapter._consumer_tasks
        task.cancel.assert_called_once()

    def test_cleanup_skips_done_task(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-2"
        task = MagicMock()
        task.done.return_value = True
        adapter._consumer_tasks[sid] = task

        adapter._cleanup_session(sid)

        task.cancel.assert_not_called()

    def test_cleanup_missing_session_noop(self, adapter: ClaudeAdapter) -> None:
        adapter._cleanup_session("nonexistent")  # should not raise


class TestEnqueue:
    def test_enqueue_adds_to_queue(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        event = SessionEvent(kind=SessionEventKind.done, payload={})

        adapter._enqueue(sid, event)

        assert q.get_nowait() is event

    def test_enqueue_missing_queue_noop(self, adapter: ClaudeAdapter) -> None:
        adapter._enqueue("nonexistent", SessionEvent(kind=SessionEventKind.done, payload={}))


class TestEnqueueLog:
    def test_enqueue_log_increments_seq(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        seq = [0]

        adapter._enqueue_log(sid, "hello", "info", seq)

        assert seq[0] == 1
        event = q.get_nowait()
        assert event is not None
        assert event.kind == SessionEventKind.log
        assert event.payload["message"] == "hello"
        assert event.payload["level"] == "info"
        assert event.payload["seq"] == 1

    def test_enqueue_log_no_seq(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        adapter._enqueue_log(sid, "msg", "warn")

        event = q.get_nowait()
        assert event is not None
        assert event.payload["seq"] == 0


# ---------------------------------------------------------------------------
# Tests: permission callback
# ---------------------------------------------------------------------------


class TestBuildCanUseTool:
    @pytest.mark.asyncio
    async def test_auto_mode_approves(self, adapter: ClaudeAdapter) -> None:
        config = _make_config(permission_mode=PermissionMode.full_auto)
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Bash", {"command": "rm -rf /"}, None)

        assert isinstance(result, _FakePermissionResultAllow)

    @pytest.mark.asyncio
    async def test_read_only_allows_read_tools(self, adapter: ClaudeAdapter) -> None:
        config = _make_config(permission_mode=PermissionMode.observe_only)
        callback = adapter._build_can_use_tool(config, "sess-1")

        for tool in ("Read", "Glob", "Grep", "WebSearch", "WebFetch", "ToolSearch"):
            result = await callback(tool, {}, None)
            assert isinstance(result, _FakePermissionResultAllow), f"{tool} should be allowed"

    @pytest.mark.asyncio
    async def test_read_only_denies_write_tools(self, adapter: ClaudeAdapter) -> None:
        config = _make_config(permission_mode=PermissionMode.observe_only)
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Edit", {"file_path": "/tmp/foo"}, None)

        assert isinstance(result, _FakePermissionResultDeny)

    @pytest.mark.asyncio
    async def test_approval_required_auto_approves_reads(self, adapter: ClaudeAdapter) -> None:
        config = _make_config(permission_mode=PermissionMode.review_and_approve)
        callback = adapter._build_can_use_tool(config, "sess-1")

        for tool in ("Read", "Glob", "Grep"):
            result = await callback(tool, {}, None)
            assert isinstance(result, _FakePermissionResultAllow)

    @pytest.mark.asyncio
    async def test_approval_required_no_infra_approves(self, adapter: ClaudeAdapter) -> None:
        """When no approval service is configured, fall back to auto-approve."""
        config = _make_config(permission_mode=PermissionMode.review_and_approve)
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Bash", {"command": "ls"}, None)

        assert isinstance(result, _FakePermissionResultAllow)

    @pytest.mark.asyncio
    async def test_paused_session_denies_all_tools(self, adapter: ClaudeAdapter) -> None:
        """When a session is paused, all tool calls are immediately denied."""
        config = _make_config(permission_mode=PermissionMode.full_auto)
        callback = adapter._build_can_use_tool(config, "sess-1")

        adapter.pause_tools("sess-1")

        # Even read tools should be denied while paused
        for tool in ("Read", "Bash", "Edit", "Glob"):
            result = await callback(tool, {}, None)
            assert isinstance(result, _FakePermissionResultDeny), f"{tool} should be denied while paused"

        # After resuming, tools work again
        adapter.resume_tools("sess-1")
        result = await callback("Read", {}, None)
        assert isinstance(result, _FakePermissionResultAllow)

    @pytest.mark.asyncio
    async def test_approval_required_routes_to_operator_approved(self) -> None:
        approval_svc = MagicMock()
        approval_svc.is_trusted = MagicMock(return_value=False)
        approval_request = MagicMock()
        approval_request.id = "req-1"
        approval_svc.create_request = AsyncMock(return_value=approval_request)
        approval_svc.wait_for_resolution = AsyncMock(return_value="approved")

        adapter = ClaudeAdapter(approval_service=approval_svc)
        adapter._session_to_job["sess-1"] = "job-1"
        config = _make_config(permission_mode=PermissionMode.review_and_approve)
        callback = adapter._build_can_use_tool(config, "sess-1")

        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues["sess-1"] = q

        result = await callback("Bash", {"command": "echo hi"}, None)

        assert isinstance(result, _FakePermissionResultAllow)
        # Check approval_request event was enqueued
        event = q.get_nowait()
        assert event is not None
        assert event.kind == SessionEventKind.approval_request
        assert event.payload["approval_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_approval_required_routes_to_operator_denied(self) -> None:
        approval_svc = MagicMock()
        approval_svc.is_trusted = MagicMock(return_value=False)
        approval_request = MagicMock()
        approval_request.id = "req-2"
        approval_svc.create_request = AsyncMock(return_value=approval_request)
        approval_svc.wait_for_resolution = AsyncMock(return_value="denied")

        adapter = ClaudeAdapter(approval_service=approval_svc)
        adapter._session_to_job["sess-1"] = "job-1"
        adapter._queues["sess-1"] = asyncio.Queue()

        config = _make_config(permission_mode=PermissionMode.review_and_approve)
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Edit", {"file_path": "/x"}, None)

        assert isinstance(result, _FakePermissionResultDeny)

    @pytest.mark.asyncio
    async def test_trusted_job_auto_approves(self) -> None:
        approval_svc = MagicMock()
        approval_svc.is_trusted = MagicMock(return_value=True)

        adapter = ClaudeAdapter(approval_service=approval_svc)
        adapter._session_to_job["sess-1"] = "job-1"

        config = _make_config(permission_mode=PermissionMode.review_and_approve)
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Bash", {"command": "rm -rf /"}, None)

        assert isinstance(result, _FakePermissionResultAllow)


# ---------------------------------------------------------------------------
# Tests: message processing
# ---------------------------------------------------------------------------


class TestProcessAssistantMessage:
    def test_text_block_emits_transcript(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        msg = _FakeAssistantMessage(content=[_FakeTextBlock("Hello world")])

        adapter._process_assistant_message(sid, msg, [0])

        event = adapter._queues[sid].get_nowait()
        assert event is not None
        assert event.kind == SessionEventKind.transcript
        assert event.payload["role"] == "agent"
        assert event.payload["content"] == "Hello world"

    def test_empty_text_block_skipped(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        msg = _FakeAssistantMessage(content=[_FakeTextBlock("   ")])

        adapter._process_assistant_message(sid, msg, [0])

        assert adapter._queues[sid].empty()

    def test_tool_use_block_records_start_time(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        tool_block = _FakeToolUseBlock(name="Bash", id="tool-1")
        msg = _FakeAssistantMessage(content=[tool_block])

        adapter._process_assistant_message(sid, msg, [0])

        assert "tool-1" in adapter._tool_start_times

    @patch("backend.services.tool_formatters.format_tool_display_full", return_value="TodoWrite")
    @patch("backend.services.tool_formatters.format_tool_display", return_value="Update todo list")
    def test_todo_write_emits_transcript(
        self,
        mock_fmt: MagicMock,
        mock_fmt_full: MagicMock,
        adapter: ClaudeAdapter,
    ) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        tool_block = _FakeToolUseBlock(name="TodoWrite", id="tool-2")
        msg = _FakeAssistantMessage(content=[tool_block])

        adapter._process_assistant_message(sid, msg, [0])

        # TodoWrite should now be emitted as a transcript event (no longer hidden)
        assert "tool-2" in adapter._tool_start_times
        assert not adapter._queues[sid].empty()

    @patch("backend.services.tool_formatters.format_tool_display", return_value="Bash: ls")
    def test_tool_result_block_emits_transcript(self, mock_fmt: MagicMock, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._tool_start_times["tool-1"] = time.monotonic() - 0.5

        result_block = _FakeToolResultBlock(tool_use_id="tool-1", content="output here", is_error=False)
        msg = _FakeAssistantMessage(content=[result_block])

        adapter._process_assistant_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind == SessionEventKind.transcript]
        assert len(transcript_events) == 1
        assert transcript_events[0].payload["tool_result"] == "output here"
        assert transcript_events[0].payload["tool_success"] is True

    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: failed")
    def test_tool_result_error(self, mock_fmt: MagicMock, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        result_block = _FakeToolResultBlock(tool_use_id="tool-err", content="error msg", is_error=True)
        msg = _FakeAssistantMessage(content=[result_block])

        adapter._process_assistant_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind == SessionEventKind.transcript]
        assert len(transcript_events) == 1
        assert transcript_events[0].payload["tool_success"] is False

    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: ok")
    def test_tool_result_list_content(self, mock_fmt: MagicMock, adapter: ClaudeAdapter) -> None:
        """ToolResultBlock.content can be a list of objects with .text attrs."""
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        part1 = SimpleNamespace(text="line1")
        part2 = SimpleNamespace(text="line2")
        result_block = _FakeToolResultBlock(tool_use_id="tool-list", content=[part1, part2])
        msg = _FakeAssistantMessage(content=[result_block])

        adapter._process_assistant_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind == SessionEventKind.transcript]
        assert transcript_events[0].payload["tool_result"] == "line1\nline2"

    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: ok")
    def test_tool_result_list_content_no_text_attr(self, mock_fmt: MagicMock, adapter: ClaudeAdapter) -> None:
        """Content list items without .text should be stringified."""
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        result_block = _FakeToolResultBlock(tool_use_id="tool-x", content=[42, "raw"])
        msg = _FakeAssistantMessage(content=[result_block])

        adapter._process_assistant_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind == SessionEventKind.transcript]
        assert "42" in transcript_events[0].payload["tool_result"]

    def test_no_content_blocks(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        msg = _FakeAssistantMessage(content=None)

        adapter._process_assistant_message(sid, msg, [0])

        assert adapter._queues[sid].empty()


class TestProcessResultMessage:
    def test_successful_result(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        msg = _FakeResultMessage(
            result="All done",
            total_cost_usd=0.05,
            usage={"input_tokens": 100, "output_tokens": 50},
            duration_ms=1200,
            is_error=False,
        )

        adapter._process_result_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        done_events = [e for e in events if e is not None and e.kind == SessionEventKind.done]
        assert len(done_events) == 1
        assert done_events[0].payload["result"] == "All done"

        log_events = [e for e in events if e is not None and e.kind == SessionEventKind.log]
        assert any("$0.0500" in e.payload["message"] for e in log_events)

    def test_error_result(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        msg = _FakeResultMessage(result="Something broke", is_error=True)

        adapter._process_result_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        error_events = [e for e in events if e is not None and e.kind == SessionEventKind.error]
        assert len(error_events) == 1
        assert "error" in error_events[0].payload["message"].lower()

    def test_result_with_job_records_telemetry(self) -> None:
        adapter = ClaudeAdapter()
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"

        msg = _FakeResultMessage(
            result="ok",
            total_cost_usd=0.01,
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 1,
            },
        )

        with (
            patch("backend.services.telemetry.tokens_input") as mock_in,
            patch("backend.services.telemetry.tokens_output") as mock_out,
            patch("backend.services.telemetry.tokens_cache_read") as mock_cr,
            patch("backend.services.telemetry.tokens_cache_write") as mock_cw,
            patch("backend.services.telemetry.cost_usd") as mock_cost,
            patch("backend.services.telemetry.llm_duration") as mock_dur,
        ):
            adapter._process_result_message(sid, msg, [0])

            attrs = {"job_id": "job-1", "sdk": "claude", "model": ""}
            mock_in.add.assert_called_once_with(10, attrs)
            mock_out.add.assert_called_once_with(5, attrs)
            mock_cr.add.assert_called_once_with(2, attrs)
            mock_cw.add.assert_called_once_with(1, attrs)
            mock_cost.add.assert_called_once_with(0.01, attrs)
            mock_dur.record.assert_called_once_with(0.0, {**attrs, "is_subagent": False})

    def test_result_empty_usage_dict(self, adapter: ClaudeAdapter) -> None:
        """usage=None or non-dict should default to 0."""
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        msg = _FakeResultMessage(result="ok", usage=None)
        adapter._process_result_message(sid, msg, [0])

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        log_events = [e for e in events if e is not None and e.kind == SessionEventKind.log]
        # Should contain "0+0 tokens"
        assert any("0+0" in e.payload.get("message", "") for e in log_events)


# ---------------------------------------------------------------------------
# Tests: consume_messages background task
# ---------------------------------------------------------------------------


class TestConsumeMessages:
    @pytest.mark.asyncio
    async def test_system_message_emits_log(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        client = _FakeClaudeSDKClient()
        client._messages = [
            _FakeSystemMessage(),
            _FakeResultMessage(result="done"),
        ]

        await adapter._consume_messages(sid, client)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        # Filter out sentinel None
        events = [e for e in events if e is not None]
        log_events = [e for e in events if e is not None and e.kind == SessionEventKind.log]
        assert any("initialized" in e.payload.get("message", "") for e in log_events)

    @pytest.mark.asyncio
    async def test_result_message_ends_stream(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        client = _FakeClaudeSDKClient()
        client._messages = [_FakeResultMessage(result="final")]

        await adapter._consume_messages(sid, client)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        # Last item is sentinel None
        assert events[-1] is None
        real_events = [e for e in events if e is not None]
        done_events = [e for e in real_events if e.kind == SessionEventKind.done]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_exception_emits_error_event(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        client = MagicMock()

        async def _exploding():
            raise RuntimeError("boom")
            yield  # noqa: E501 — make it a generator

        client.receive_messages = _exploding

        await adapter._consume_messages(sid, client)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        real_events = [e for e in events if e is not None]
        error_events = [e for e in real_events if e.kind == SessionEventKind.error]
        assert len(error_events) == 1
        assert "error" in error_events[0].payload["message"].lower()

    @pytest.mark.asyncio
    async def test_no_queue_returns_immediately(self, adapter: ClaudeAdapter) -> None:
        client = _FakeClaudeSDKClient()
        # No queue registered for this session
        await adapter._consume_messages("nonexistent", client)


# ---------------------------------------------------------------------------
# Tests: stream_events
# ---------------------------------------------------------------------------


class TestStreamEvents:
    @pytest.mark.asyncio
    async def test_yields_events_until_sentinel(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        e1 = SessionEvent(kind=SessionEventKind.transcript, payload={"role": "agent", "content": "hi"})
        e2 = SessionEvent(kind=SessionEventKind.done, payload={})
        q.put_nowait(e1)
        q.put_nowait(e2)
        q.put_nowait(None)  # sentinel

        collected = []
        async for event in adapter.stream_events(sid):
            collected.append(event)

        assert len(collected) == 2
        assert collected[0] is e1
        assert collected[1] is e2

    @pytest.mark.asyncio
    async def test_no_queue_yields_error(self, adapter: ClaudeAdapter) -> None:
        collected = []
        async for event in adapter.stream_events("nonexistent"):
            collected.append(event)

        assert len(collected) == 1
        assert collected[0].kind == SessionEventKind.error

    @pytest.mark.asyncio
    async def test_timeout_yields_error(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        # Patch wait_for to immediately timeout
        with patch("asyncio.wait_for", side_effect=TimeoutError):
            collected = []
            async for event in adapter.stream_events(sid):
                collected.append(event)

            assert any(e.kind == SessionEventKind.error for e in collected)


# ---------------------------------------------------------------------------
# Tests: send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_to_existing_session(self, adapter: ClaudeAdapter) -> None:
        client = _FakeClaudeSDKClient()
        adapter._clients["sess-1"] = client  # type: ignore[assignment]

        await adapter.send_message("sess-1", "follow up")

    @pytest.mark.asyncio
    async def test_send_to_missing_session(self, adapter: ClaudeAdapter) -> None:
        await adapter.send_message("nonexistent", "hello")  # should not raise

    @pytest.mark.asyncio
    async def test_send_handles_exception(self, adapter: ClaudeAdapter) -> None:
        client = MagicMock()
        client.query = AsyncMock(side_effect=RuntimeError("send failed"))
        adapter._clients["sess-1"] = client

        await adapter.send_message("sess-1", "oops")  # should not raise


# ---------------------------------------------------------------------------
# Tests: abort_session
# ---------------------------------------------------------------------------


class TestAbortSession:
    @pytest.mark.asyncio
    async def test_abort_existing_session(self, adapter: ClaudeAdapter) -> None:
        client = _FakeClaudeSDKClient()
        adapter._clients["sess-1"] = client  # type: ignore[assignment]
        adapter._queues["sess-1"] = asyncio.Queue()
        adapter._session_to_job["sess-1"] = "job-1"

        await adapter.abort_session("sess-1")

        assert client._interrupted
        # subprocess kill is via raw os.kill — no SDK methods called
        assert "sess-1" not in adapter._clients

    @pytest.mark.asyncio
    async def test_abort_missing_session(self, adapter: ClaudeAdapter) -> None:
        await adapter.abort_session("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_abort_handles_exception(self, adapter: ClaudeAdapter) -> None:
        client = MagicMock()
        client.interrupt = AsyncMock(side_effect=RuntimeError("fail"))
        client.disconnect = AsyncMock()
        adapter._clients["sess-1"] = client

        await adapter.abort_session("sess-1")

        assert "sess-1" not in adapter._clients


# ---------------------------------------------------------------------------
# Tests: complete (single-turn)
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_collects_text_from_assistant(self, adapter: ClaudeAdapter) -> None:
        async def _fake_query(**kwargs: Any):
            yield _FakeAssistantMessage(content=[_FakeTextBlock("Hello")])
            yield _FakeResultMessage(result="World")

        with patch.dict(sys.modules["claude_code_sdk"].__dict__, {"query": _fake_query}):
            result = await adapter.complete("test prompt")

        assert result is not None
        assert "Hello" in result
        assert "World" in result

    @pytest.mark.asyncio
    async def test_complete_handles_exception(self, adapter: ClaudeAdapter) -> None:
        async def _exploding_query(**kwargs: Any):
            raise RuntimeError("boom")
            yield  # noqa: E501

        with patch.dict(sys.modules["claude_code_sdk"].__dict__, {"query": _exploding_query}):
            result = await adapter.complete("test")

        assert result is None


# ---------------------------------------------------------------------------
# Tests: create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session_success(self, adapter: ClaudeAdapter) -> None:
        config = _make_config()
        fake_client = _FakeClaudeSDKClient()
        fake_client._messages = [_FakeResultMessage(result="done")]

        with patch.dict(
            sys.modules["claude_code_sdk"].__dict__,
            {"ClaudeSDKClient": lambda opts: fake_client},
        ):
            session_id = await adapter.create_session(config)

        assert session_id in adapter._queues
        assert session_id in adapter._clients
        assert adapter._session_to_job[session_id] == "job-1"

    @pytest.mark.asyncio
    async def test_create_session_with_mcp_servers(self, adapter: ClaudeAdapter) -> None:
        config = _make_config(
            mcp_servers={
                "my-server": MCPServerConfig(command="node", args=["server.js"], env={"KEY": "val"}),
            },
        )
        captured_opts = []

        def _capture_client(opts: Any) -> _FakeClaudeSDKClient:
            captured_opts.append(opts)
            c = _FakeClaudeSDKClient(opts)
            c._messages = [_FakeResultMessage(result="done")]
            return c

        with patch.dict(
            sys.modules["claude_code_sdk"].__dict__,
            {"ClaudeSDKClient": _capture_client},
        ):
            await adapter.create_session(config)

        assert hasattr(captured_opts[0], "mcp_servers")
        mcp = captured_opts[0].mcp_servers
        assert "my-server" in mcp
        assert mcp["my-server"]["command"] == "node"
        assert mcp["my-server"]["env"] == {"KEY": "val"}

    @pytest.mark.asyncio
    async def test_create_session_with_resume(self, adapter: ClaudeAdapter) -> None:
        config = _make_config(resume_sdk_session_id="prev-session")
        captured_opts = []

        def _capture_client(opts: Any) -> _FakeClaudeSDKClient:
            captured_opts.append(opts)
            c = _FakeClaudeSDKClient(opts)
            c._messages = [_FakeResultMessage(result="done")]
            return c

        with patch.dict(
            sys.modules["claude_code_sdk"].__dict__,
            {"ClaudeSDKClient": _capture_client},
        ):
            await adapter.create_session(config)

        assert captured_opts[0].resume == "prev-session"

    @pytest.mark.asyncio
    async def test_create_session_connect_failure_cleans_up(self, adapter: ClaudeAdapter) -> None:
        config = _make_config()

        class _FailingClient(_FakeClaudeSDKClient):
            async def connect(self, prompt_stream: Any) -> None:
                raise ConnectionError("SDK down")

        with (
            patch.dict(
                sys.modules["claude_code_sdk"].__dict__,
                {"ClaudeSDKClient": lambda opts: _FailingClient()},
            ),
            pytest.raises(ConnectionError),
        ):
            await adapter.create_session(config)

        # All state should be cleaned up
        assert len(adapter._clients) == 0
        assert len(adapter._queues) == 0


# ---------------------------------------------------------------------------
# Tests: tool result telemetry with job_id
# ---------------------------------------------------------------------------


class TestToolResultTelemetry:
    @patch("backend.services.tool_formatters.format_tool_display", return_value="Bash: ls")
    def test_tool_result_records_telemetry_with_job(self, mock_fmt: MagicMock) -> None:
        adapter = ClaudeAdapter()
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._tool_start_times["tool-1"] = time.monotonic() - 1.0

        with patch("backend.services.telemetry.tool_duration") as mock_tool_dur:
            adapter._process_tool_result_block(
                sid,
                _FakeToolResultBlock(tool_use_id="tool-1", content="ok"),
                [0],
                "job-1",
            )

            mock_tool_dur.record.assert_called_once()
            call_args = mock_tool_dur.record.call_args
            assert call_args[0][1]["job_id"] == "job-1"

    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: ok")
    def test_tool_result_no_job_skips_telemetry(self, mock_fmt: MagicMock) -> None:
        adapter = ClaudeAdapter()
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        with patch("backend.services.telemetry.tool_duration") as mock_tool_dur:
            adapter._process_tool_result_block(
                sid,
                _FakeToolResultBlock(tool_use_id="tool-2", content="ok"),
                [0],
                None,
            )

            mock_tool_dur.record.assert_not_called()
