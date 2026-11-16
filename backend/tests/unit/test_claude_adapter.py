"""Unit tests for backend.services.claude_adapter — ClaudeAdapter.

All claude_code_sdk imports are mocked so the tests run without the SDK installed.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.domain import (
    MCPServerConfig,
    SessionConfig,
)
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, new_event
from backend.services.adapters.base_adapter import PermissionDecision

if TYPE_CHECKING:
    from pathlib import Path

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


class _FakeThinkingBlock:
    def __init__(self, thinking: str = "", signature: str = "") -> None:
        self.thinking = thinking
        self.signature = signature


class _FakeStreamEvent:
    def __init__(
        self,
        uuid: str = "",
        session_id: str = "",
        event: dict[str, Any] | None = None,
        parent_tool_use_id: str | None = None,
    ) -> None:
        self.uuid = uuid
        self.session_id = session_id
        self.event = event or {}
        self.parent_tool_use_id = parent_tool_use_id


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
    mod.ThinkingBlock = _FakeThinkingBlock
    mod.SystemMessage = _FakeSystemMessage
    mod.UserMessage = _FakeUserMessage
    mod.AssistantMessage = _FakeAssistantMessage
    mod.ResultMessage = _FakeResultMessage
    mod.ClaudeCodeOptions = _FakeClaudeCodeOptions
    mod.ClaudeSDKClient = _FakeClaudeSDKClient
    mod.StreamEvent = _FakeStreamEvent
    mod.query = AsyncMock()
    return mod


# Inject the fake module before importing the adapter.
# Force-set (not setdefault) because other test files in the suite may have
# already imported the real claude_code_sdk by the time this module loads.
_fake_sdk = _build_fake_sdk_module()
sys.modules["claude_code_sdk"] = _fake_sdk

# Also provide the _errors submodule so ``from claude_code_sdk._errors import``
# works inside _consume_messages.
_fake_errors = ModuleType("claude_code_sdk._errors")
_fake_errors.MessageParseError = type("MessageParseError", (Exception,), {})
sys.modules["claude_code_sdk._errors"] = _fake_errors

# Provide the types submodule so ``from claude_code_sdk.types import StreamEvent``
# works inside _consume_messages.
_fake_types = ModuleType("claude_code_sdk.types")
_fake_types.StreamEvent = _FakeStreamEvent
sys.modules["claude_code_sdk.types"] = _fake_types

# If the adapter was already imported with the real SDK, purge it so it
# re-imports with our fakes above.
for _key in list(sys.modules):
    if _key.startswith("backend.services.claude_adapter"):
        del sys.modules[_key]

from backend.services.adapters.base_adapter import BaseAgentAdapter  # noqa: E402
from backend.services.claude_adapter import (  # noqa: E402
    _HIDDEN_TOOLS,
    ClaudeAdapter,
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
    }
    defaults.update(overrides)
    return SessionConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests: module-level helpers
# ---------------------------------------------------------------------------


class TestBuildPermissionDescription:
    def test_bash_command(self) -> None:
        assert "ls -la" in BaseAgentAdapter._build_permission_description("shell", "Bash", {"command": "ls -la"}, None)

    def test_edit_file_path(self) -> None:
        result = BaseAgentAdapter._build_permission_description(
            "write",
            "Edit",
            {"file_path": "/tmp/foo.py"},
            None,
        )
        assert "/tmp/foo.py" in result

    def test_write_file_path(self) -> None:
        result = BaseAgentAdapter._build_permission_description(
            "write",
            "Write",
            {"path": "/tmp/bar.py"},
            None,
        )
        assert "/tmp/bar.py" in result

    def test_read_file_path(self) -> None:
        result = BaseAgentAdapter._build_permission_description(
            "read",
            "Read",
            {"file_path": "readme.md"},
            None,
        )
        assert "readme.md" in result

    def test_webfetch_url(self) -> None:
        result = BaseAgentAdapter._build_permission_description(
            "url",
            "WebFetch",
            {"url": "https://example.com"},
            None,
        )
        assert "example.com" in result

    def test_websearch_query(self) -> None:
        result = BaseAgentAdapter._build_permission_description(
            "url",
            "WebSearch",
            {"query": "python async"},
            None,
        )
        assert "python async" in result

    def test_fallback_json(self) -> None:
        result = BaseAgentAdapter._build_permission_description("custom-tool", "CustomTool", {"a": 1}, None)
        assert "1" in result

    def test_fallback_truncation(self) -> None:
        result = BaseAgentAdapter._build_permission_description("custom-tool", "CustomTool", {"x": "a" * 200}, None)
        assert len(result) <= 250


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
        event = None

        adapter._enqueue(sid, event)

        assert q.get_nowait() is event

    def test_enqueue_missing_queue_noop(self, adapter: ClaudeAdapter) -> None:
        adapter._enqueue("nonexistent", None)


# ---------------------------------------------------------------------------
# Tests: permission callback
# ---------------------------------------------------------------------------


class TestBuildCanUseTool:
    """Test _build_can_use_tool callback against the action policy router.

    All permission decisions now route through the policy router.  These tests
    mock ``_evaluate_permission`` to verify the SDK callback translates
    PermissionDecision → PermissionResultAllow / PermissionResultDeny.
    """

    @pytest.mark.asyncio
    async def test_policy_router_allow(self, adapter: ClaudeAdapter) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        adapter._session_to_job["sess-1"] = "job-1"
        config = _make_config()
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Bash", {"command": "make test"}, None)
        assert isinstance(result, _FakePermissionResultAllow)

    @pytest.mark.asyncio
    async def test_policy_router_deny(self, adapter: ClaudeAdapter) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.deny)
        adapter._session_to_job["sess-1"] = "job-1"
        config = _make_config()
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Edit", {"file_path": "/tmp/foo"}, None)
        assert isinstance(result, _FakePermissionResultDeny)

    @pytest.mark.asyncio
    async def test_no_policy_router_denies(self, adapter: ClaudeAdapter) -> None:
        """Without a policy router, the callback denies by default."""
        adapter._session_to_job["sess-1"] = "job-1"
        config = _make_config()
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, _FakePermissionResultDeny)

    @pytest.mark.asyncio
    async def test_paused_session_denies_all_tools(self, adapter: ClaudeAdapter) -> None:
        """When a session is paused, all tool calls are immediately denied."""
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.deny)
        adapter._session_to_job["sess-1"] = "job-1"
        config = _make_config()
        callback = adapter._build_can_use_tool(config, "sess-1")

        adapter.pause_tools("sess-1")

        for tool in ("Read", "Bash", "Edit", "Glob"):
            result = await callback(tool, {}, None)
            assert isinstance(result, _FakePermissionResultDeny), f"{tool} should be denied while paused"

    @pytest.mark.asyncio
    async def test_trusted_job_auto_approves(self) -> None:
        approval_svc = MagicMock()
        approval_svc.is_trusted = MagicMock(return_value=True)

        adapter = ClaudeAdapter(approval_service=approval_svc)
        adapter._session_to_job["sess-1"] = "job-1"
        # Trust bypass is inside _evaluate_permission, so mock the whole chain
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)

        config = _make_config()
        callback = adapter._build_can_use_tool(config, "sess-1")

        result = await callback("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, _FakePermissionResultAllow)


class TestScoutPermissions:
    async def test_recipe_capability_cannot_be_waived_by_general_permission_allow(self, adapter, tmp_path) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        config = _make_config(workspace_path=str(tmp_path), execution_capabilities=frozenset({"read", "shell"}))
        callback = adapter._build_can_use_tool(config, "session")
        result = await callback("Bash", {"command": "echo changed > file"}, None)
        assert isinstance(result, _FakePermissionResultDeny)
        adapter._evaluate_permission.assert_not_awaited()
        result = await callback("Read", {"file_path": "file"}, None)
        assert isinstance(result, _FakePermissionResultAllow)
        adapter._evaluate_permission.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "input_data"),
        [
            ("Read", {"file_path": "src.py"}),
            ("Glob", {"pattern": "**/*.py", "path": "."}),
            ("Grep", {"pattern": "target", "path": "."}),
            ("Glob", {"pattern": "**/*.py"}),
            ("Grep", {"pattern": "target"}),
        ],
    )
    async def test_scout_read_search_reaches_policy_and_sdk_allow(
        self, adapter: ClaudeAdapter, tmp_path: Path, tool_name: str, input_data: dict[str, Any]
    ) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        callback = adapter._build_can_use_tool(
            _make_config(workspace_path=str(tmp_path), session_kind="preflight"), "scout"
        )
        result = await callback(tool_name, input_data, None)
        assert isinstance(result, _FakePermissionResultAllow)
        adapter._evaluate_permission.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "input_data"),
        [
            ("Write", {"file_path": "src.py", "content": "replacement"}),
            ("Edit", {"file_path": "src.py", "old_string": "old", "new_string": "new"}),
            ("MultiEdit", {"file_path": "src.py", "edits": []}),
            ("Bash", {"command": "cat src.py", "read_only": True}),
            ("PowerShell", {"command": "Get-Content src.py", "read_only": True}),
            ("WebFetch", {"url": "https://example.test"}),
            ("WebSearch", {"query": "repository source"}),
            ("Task", {"prompt": "Read the repository", "read_only": True}),
            ("Agent", {"prompt": "Read the repository", "read_only": True}),
            ("mcp__repo__read", {"file_path": "src.py", "read_only": True}),
            ("ToolSearch", {"query": "mcp repository"}),
            ("FutureReader", {"file_path": "src.py", "read_only": True}),
            ("Read", {}),
            ("Read", {"file_path": "..\\outside.py"}),
            ("Grep", {"path": "..", "pattern": "target"}),
            ("Read", {"file_path": "src.py", "path": "..\\outside.py"}),
            ("Read", {"file_path": "https://example.test/source.py"}),
        ],
    )
    async def test_scout_denies_unsafe_requests_before_general_allow(
        self, adapter: ClaudeAdapter, tmp_path: Path, tool_name: str, input_data: dict[str, Any]
    ) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        callback = adapter._build_can_use_tool(
            _make_config(workspace_path=str(tmp_path), session_kind="preflight"), "scout"
        )
        result = await callback(tool_name, input_data, None)
        assert isinstance(result, _FakePermissionResultDeny)
        adapter._evaluate_permission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scout_denies_absolute_sibling_path(self, adapter: ClaudeAdapter, tmp_path: Path) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        callback = adapter._build_can_use_tool(
            _make_config(workspace_path=str(tmp_path), session_kind="preflight"), "scout"
        )
        result = await callback("Read", {"file_path": str(tmp_path.parent / "other" / "src.py")}, None)
        assert isinstance(result, _FakePermissionResultDeny)
        adapter._evaluate_permission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scout_read_does_not_override_policy_denial(self, adapter: ClaudeAdapter, tmp_path: Path) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.deny)
        callback = adapter._build_can_use_tool(
            _make_config(workspace_path=str(tmp_path), session_kind="preflight"), "scout"
        )
        result = await callback("Read", {"file_path": "src.py"}, None)
        assert isinstance(result, _FakePermissionResultDeny)
        adapter._evaluate_permission.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("session_kind", ["job", "sidecar"])
    async def test_scout_guard_does_not_restrict_other_sessions(
        self, adapter: ClaudeAdapter, tmp_path: Path, session_kind: str
    ) -> None:
        adapter._evaluate_permission = AsyncMock(return_value=PermissionDecision.allow)
        callback = adapter._build_can_use_tool(
            _make_config(workspace_path=str(tmp_path), session_kind=session_kind), "ordinary"
        )
        result = await callback("Bash", {"command": "cat src.py"}, None)
        assert isinstance(result, _FakePermissionResultAllow)
        adapter._evaluate_permission.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scout_sdk_configuration_keeps_reads_and_excludes_mutations(
        self, adapter: ClaudeAdapter, tmp_path: Path
    ) -> None:
        from backend.services.tools.preflight_curator import _DISALLOWED_BUILTIN_TOOLS

        clients: list[_FakeClaudeSDKClient] = []

        def capture_client(options: Any) -> _FakeClaudeSDKClient:
            client = _FakeClaudeSDKClient(options)
            client._messages = [_FakeResultMessage(result="brief")]
            clients.append(client)
            return client

        config = _make_config(
            workspace_path=str(tmp_path),
            model="utility-model",
            session_kind="preflight",
            disallowed_tools=_DISALLOWED_BUILTIN_TOOLS,
        )
        with patch.dict(sys.modules["claude_code_sdk"].__dict__, {"ClaudeSDKClient": capture_client}):
            session_id = await adapter.create_session(config)
        try:
            assert adapter.get_session_kind(session_id) == "preflight"
            assert adapter._session_to_job[session_id] == config.job_id
            options = clients[0].options
            assert options.model == "utility-model"
            assert options.cwd == str(tmp_path)
            excluded = set(options.disallowed_tools)
            assert {"Bash", "Write", "Edit", "MultiEdit", "Task", "WebFetch", "WebSearch"} <= excluded
            assert not excluded.intersection({"Read", "Glob", "Grep"})
            assert callable(options.can_use_tool)
        finally:
            consumer = adapter._consumer_tasks.get(session_id)
            if consumer is not None:
                await consumer
            adapter._cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Tests: message processing
# ---------------------------------------------------------------------------


class TestProcessAssistantMessage:
    @pytest.mark.parametrize("role", ["job", "preflight"])
    @pytest.mark.parametrize("requested_model", ["", "requested-model"])
    async def test_attempt_model_is_recorded_from_assistant_metadata(self, adapter, role, requested_model) -> None:
        sid = "actual-session"
        adapter._queues[sid] = asyncio.Queue()
        adapter.set_job_id(sid, "job-1")
        adapter.set_session_kind(sid, role)
        adapter.set_session_metadata(sid, _make_config(attempt_id="attempt", execution_epoch="epoch"))
        adapter._requested_models[sid] = requested_model
        adapter._job_main_models["job-1"] = "job-default"
        await adapter._process_assistant_message(sid, _FakeAssistantMessage(model="reported-model"))
        observed = adapter._queues[sid].get_nowait()
        assert observed.kind == EventKind.model_observed
        assert observed.payload == {
            "requested_model": requested_model or None, "actual_model": "reported-model",
            "model_match": False if requested_model else None,
        }
        assert observed.metadata.attempt_id == "attempt"
        assert adapter.get_reported_model(sid) == "reported-model"
        assert adapter._job_main_models["job-1"] == "job-default"

    @pytest.mark.asyncio
    async def test_scout_text_after_tool_request_is_not_a_final_response(self, adapter: ClaudeAdapter) -> None:
        sid = "scout-tool-request"
        adapter._queues[sid] = asyncio.Queue()
        adapter.set_job_id(sid, "job-1")
        adapter.set_session_kind(sid, "preflight")
        message = _FakeAssistantMessage(
            content=[_FakeToolUseBlock(name="Read"), _FakeTextBlock("Let me inspect the implementation.")]
        )
        await adapter._process_assistant_message(sid, message)
        messages = []
        while not adapter._queues[sid].empty():
            event = adapter._queues[sid].get_nowait()
            if event is not None and event.kind == EventKind.message_assistant:
                messages.append(event)
        assert len(messages) == 1
        assert messages[0].payload.get("has_tool_requests") is True

    @pytest.mark.asyncio
    async def test_text_block_emits_transcript(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        msg = _FakeAssistantMessage(content=[_FakeTextBlock("Hello world")])

        await adapter._process_assistant_message(sid, msg)

        event = adapter._queues[sid].get_nowait()
        assert event is not None
        assert event.kind in TRANSCRIPT_KINDS
        assert event.kind == EventKind.message_assistant
        assert event.payload["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_empty_text_block_skipped(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        msg = _FakeAssistantMessage(content=[_FakeTextBlock("   ")])

        await adapter._process_assistant_message(sid, msg)

        assert adapter._queues[sid].empty()

    @pytest.mark.asyncio
    async def test_tool_use_block_records_start_time(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        tool_block = _FakeToolUseBlock(name="Bash", id="tool-1")
        msg = _FakeAssistantMessage(content=[tool_block])

        await adapter._process_assistant_message(sid, msg)

        assert (sid, "tool-1") in adapter._tool_buf

    @pytest.mark.asyncio
    async def test_todo_write_emits_transcript(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        tool_block = _FakeToolUseBlock(name="TodoWrite", id="tool-2")
        msg = _FakeAssistantMessage(content=[tool_block])

        await adapter._process_assistant_message(sid, msg)

        # TodoWrite should now be emitted as a transcript event (no longer hidden)
        assert (sid, "tool-2") in adapter._tool_buf
        assert not adapter._queues[sid].empty()

    @pytest.mark.asyncio
    async def test_tool_result_block_emits_transcript(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        # Pre-populate tool metadata (simulates on_tool_start)
        adapter._tool_buf[sid, "tool-1"] = {
            "job_id": "job-1",
            "tool_name": "Bash",
            "arguments": None,
            "start": time.monotonic() - 0.5,
        }

        result_block = _FakeToolResultBlock(tool_use_id="tool-1", content="output here", is_error=False)
        msg = _FakeAssistantMessage(content=[result_block])

        await adapter._process_assistant_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind in TRANSCRIPT_KINDS]
        assert len(transcript_events) >= 1
        assert transcript_events[0].payload["result"] == "output here"
        assert transcript_events[0].payload["success"] is True

    @pytest.mark.asyncio
    async def test_tool_result_error(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

        result_block = _FakeToolResultBlock(tool_use_id="tool-err", content="error msg", is_error=True)
        msg = _FakeAssistantMessage(content=[result_block])

        await adapter._process_assistant_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind in TRANSCRIPT_KINDS]
        assert len(transcript_events) >= 1
        assert transcript_events[0].payload["success"] is False

    @pytest.mark.asyncio
    async def test_tool_result_list_content(self, adapter: ClaudeAdapter) -> None:
        """ToolResultBlock.content can be a list of objects with .text attrs."""
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

        part1 = SimpleNamespace(text="line1")
        part2 = SimpleNamespace(text="line2")
        result_block = _FakeToolResultBlock(tool_use_id="tool-list", content=[part1, part2])
        msg = _FakeAssistantMessage(content=[result_block])

        await adapter._process_assistant_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind in TRANSCRIPT_KINDS]
        assert transcript_events[0].payload["result"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_tool_result_list_content_no_text_attr(self, adapter: ClaudeAdapter) -> None:
        """Content list items without .text should be stringified."""
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

        result_block = _FakeToolResultBlock(tool_use_id="tool-x", content=[42, "raw"])
        msg = _FakeAssistantMessage(content=[result_block])

        await adapter._process_assistant_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        transcript_events = [e for e in events if e is not None and e.kind in TRANSCRIPT_KINDS]
        assert "42" in transcript_events[0].payload["result"]

    @pytest.mark.asyncio
    async def test_no_content_blocks(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._schedule_db_write = lambda coro: coro.close()
        msg = _FakeAssistantMessage(content=None)

        await adapter._process_assistant_message(sid, msg)

        assert adapter._queues[sid].empty()

    @pytest.mark.asyncio
    async def test_thinking_block_emits_reasoning(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        msg = _FakeAssistantMessage(content=[_FakeThinkingBlock("I need to think about this")])

        await adapter._process_assistant_message(sid, msg)

        event = adapter._queues[sid].get_nowait()
        assert event is not None
        assert event.kind in TRANSCRIPT_KINDS
        assert event.kind == EventKind.llm_reasoning_chunk
        assert event.metadata.partial is False
        assert event.payload["content"] == "I need to think about this"

    @pytest.mark.asyncio
    async def test_empty_thinking_block_skipped(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        msg = _FakeAssistantMessage(content=[_FakeThinkingBlock("   ")])

        await adapter._process_assistant_message(sid, msg)

        assert adapter._queues[sid].empty()


class TestProcessResultMessage:
    @pytest.mark.asyncio
    async def test_successful_result(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._schedule_db_write = lambda coro: coro.close()

        msg = _FakeResultMessage(
            result="All done",
            total_cost_usd=0.05,
            usage={"input_tokens": 100, "output_tokens": 50},
            duration_ms=1200,
            is_error=False,
        )

        await adapter._process_result_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        assert events == []

    @pytest.mark.asyncio
    async def test_error_result(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._schedule_db_write = lambda coro: coro.close()

        msg = _FakeResultMessage(result="Something broke", is_error=True)

        await adapter._process_result_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        error_events = [e for e in events if e is not None and e.kind == EventKind.job_failed]
        assert len(error_events) == 1
        assert "error" in error_events[0].payload["message"].lower()

    @pytest.mark.asyncio
    async def test_result_with_job_records_telemetry(self) -> None:
        adapter = ClaudeAdapter()
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

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

        await adapter._process_result_message(sid, msg)

        event = adapter._queues[sid].get_nowait()
        assert event.kind == EventKind.telemetry_usage
        assert event.payload["input_tokens"] == 10
        assert event.payload["output_tokens"] == 5
        assert event.payload["cache_read_tokens"] == 2
        assert event.payload["cache_write_tokens"] == 1
        assert event.payload["cost_usd"] == 0.01

    @pytest.mark.asyncio
    async def test_result_empty_usage_dict(self, adapter: ClaudeAdapter) -> None:
        """usage=None or non-dict should default to 0."""
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._schedule_db_write = lambda coro: coro.close()

        msg = _FakeResultMessage(result="ok", usage=None)
        await adapter._process_result_message(sid, msg)

        events = []
        while not adapter._queues[sid].empty():
            events.append(adapter._queues[sid].get_nowait())

        assert events == []


# ---------------------------------------------------------------------------
# Tests: _process_stream_event (thinking deltas + tool output deltas)
# ---------------------------------------------------------------------------


class TestProcessStreamEvent:
    @pytest.mark.asyncio
    async def test_thinking_delta_emits_reasoning_delta(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

        stream_event = _FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "Let me consider..."},
            },
            parent_tool_use_id=None,
        )

        await adapter._process_stream_event(sid, stream_event)

        event = adapter._queues[sid].get_nowait()
        assert event is not None
        assert event.kind in TRANSCRIPT_KINDS
        assert event.kind == EventKind.llm_reasoning_chunk
        assert event.metadata.partial is True
        assert event.payload["content"] == "Let me consider..."

    @pytest.mark.asyncio
    async def test_empty_thinking_delta_skipped(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

        stream_event = _FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": ""},
            },
        )

        await adapter._process_stream_event(sid, stream_event)

        assert adapter._queues[sid].empty()

    @pytest.mark.asyncio
    async def test_text_delta_emits_tool_output_delta(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        parent_id = "tool-1"
        stream_event = _FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"text": "output line"},
            },
            parent_tool_use_id=parent_id,
        )

        await adapter._process_stream_event(sid, stream_event)

        event = adapter._queues[sid].get_nowait()
        assert event is not None
        assert event.kind == EventKind.tool_result_chunk
        assert event.payload["content"] == "output line"

    @pytest.mark.asyncio
    async def test_no_event_dict_returns_early(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

        stream_event = _FakeStreamEvent(event=None)

        await adapter._process_stream_event(sid, stream_event)

        assert adapter._queues[sid].empty()


# ---------------------------------------------------------------------------
# Tests: consume_messages background task
# ---------------------------------------------------------------------------


class TestConsumeMessages:
    @pytest.mark.asyncio
    async def test_system_message_emits_log(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()

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
        # SystemMessage emits an agent transcript through the pipeline
        transcript_events = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert any("initialized" in (e.payload.get("content", "") or "") for e in transcript_events)

    @pytest.mark.asyncio
    async def test_result_message_ends_stream(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        adapter._schedule_db_write = lambda coro: coro.close()

        client = _FakeClaudeSDKClient()
        client._messages = [_FakeResultMessage(result="final")]

        await adapter._consume_messages(sid, client)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        # Last item is sentinel None
        assert events[-1] is None
        real_events = [e for e in events if e is not None]
        assert real_events == []

    @pytest.mark.asyncio
    async def test_exception_emits_error_event(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        adapter._schedule_db_write = lambda coro: coro.close()

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
        error_events = [e for e in real_events if e.kind == EventKind.job_failed]
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

        e1 = new_event(session_id="job-test", kind=EventKind.message_assistant, payload={"content": "hi"})
        e2 = new_event(session_id="job-test", kind=EventKind.log_line_emitted, payload={"message": "done"})
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
        assert collected[0].kind == EventKind.job_failed

    @pytest.mark.asyncio
    async def test_sentinel_terminates_stream(self, adapter: ClaudeAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        # Only sentinel — simulates process exit with no prior events
        q.put_nowait(None)

        collected = []
        async for event in adapter.stream_events(sid):
            collected.append(event)

        assert len(collected) == 0


# ---------------------------------------------------------------------------
# Tests: send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_to_existing_session(self, adapter: ClaudeAdapter) -> None:
        client = _FakeClaudeSDKClient()
        adapter._clients["sess-1"] = client

        await adapter.send_message("sess-1", "follow up")

    @pytest.mark.asyncio
    async def test_send_to_missing_session(self, adapter: ClaudeAdapter) -> None:
        from backend.services.completers.copilot_steer import NativeDeliveryError

        with pytest.raises(NativeDeliveryError) as failure:
            await adapter.send_message("nonexistent", "hello")
        assert not failure.value.uncertain

    @pytest.mark.asyncio
    async def test_send_handles_exception(self, adapter: ClaudeAdapter) -> None:
        client = MagicMock()
        client.query = AsyncMock(side_effect=RuntimeError("send failed"))
        adapter._clients["sess-1"] = client

        from backend.services.completers.copilot_steer import NativeDeliveryError

        with pytest.raises(NativeDeliveryError) as failure:
            await adapter.send_message("sess-1", "oops")
        assert failure.value.uncertain


# ---------------------------------------------------------------------------
# Tests: abort_session
# ---------------------------------------------------------------------------


class TestAbortSession:
    @pytest.mark.asyncio
    async def test_abort_existing_session(self, adapter: ClaudeAdapter) -> None:
        client = _FakeClaudeSDKClient()
        adapter._clients["sess-1"] = client
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

        assert result.text is not None
        assert "Hello" in result.text
        assert "World" in result.text

    @pytest.mark.asyncio
    async def test_complete_handles_exception(self, adapter: ClaudeAdapter) -> None:
        async def _exploding_query(**kwargs: Any):
            raise RuntimeError("boom")
            yield  # noqa: E501

        from backend.services.adapters.agent_adapter import UtilityCompletionError

        with (
            patch.dict(sys.modules["claude_code_sdk"].__dict__, {"query": _exploding_query}),
            pytest.raises(UtilityCompletionError, match="failed"),
        ):
            await adapter.complete("test")

    async def test_empty_timeout_and_tool_output_fail_explicitly(self, adapter) -> None:
        from backend.services.adapters.agent_adapter import UtilityCompletionError
        from backend.services.adapters.completion_context import CompletionContext, completion_context

        async def oversized(**kwargs):
            yield _FakeAssistantMessage(content=[_FakeTextBlock("too many bytes")])
            yield _FakeResultMessage(result="")

        with (
            patch.dict(sys.modules["claude_code_sdk"].__dict__, {"query": oversized}),
            completion_context(CompletionContext(max_output_bytes=4)),
            pytest.raises(UtilityCompletionError) as failure,
        ):
            await adapter.complete("test")
        assert failure.value.reason == "output_limit"

        async def empty(**kwargs):
            yield _FakeResultMessage(result="")

        with (
            patch.dict(sys.modules["claude_code_sdk"].__dict__, {"query": empty}),
            pytest.raises(UtilityCompletionError) as failure,
        ):
            await adapter.complete("test")
        assert failure.value.reason == "empty_response"


# ---------------------------------------------------------------------------
# Tests: create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    async def test_cancelled_deferred_connect_releases_owned_client_and_state(self, adapter) -> None:
        client = _FakeClaudeSDKClient()
        client.connect = AsyncMock(side_effect=asyncio.CancelledError())
        with (
            patch.dict(sys.modules["claude_code_sdk"].__dict__, {"ClaudeSDKClient": lambda opts: client}),
            patch("backend.services.claude_adapter._adapter._kill_sdk_subprocess") as release,
            pytest.raises(asyncio.CancelledError),
        ):
            await adapter.create_session(_make_config(defer_start=True))
        release.assert_called_once_with(client)
        assert not adapter._queues and not adapter._clients
        assert not adapter._stderr_files and not adapter._stderr_file_objects

    async def test_deferred_attempt_connects_without_a_prompt(self, adapter) -> None:
        fake_client = _FakeClaudeSDKClient()
        fake_client.connect = AsyncMock()
        fake_client.query = AsyncMock()
        fake_client._messages = []
        with patch.dict(sys.modules["claude_code_sdk"].__dict__, {"ClaudeSDKClient": lambda opts: fake_client}):
            session_id = await adapter.create_session(_make_config(defer_start=True))
        fake_client.connect.assert_awaited_once_with(None)
        fake_client.query.assert_not_awaited()
        await adapter.send_message(session_id, "pinned attempt prompt")
        fake_client.query.assert_awaited_once_with("pinned attempt prompt")
        await adapter.abort_session(session_id)

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
    async def test_same_job_parallel_sessions_keep_tool_pairing_and_cleanup_isolated(self, adapter) -> None:
        for session in ("first", "second"):
            adapter._queues[session] = asyncio.Queue()
            adapter.set_job_id(session, "job")
        for session, name in (("first", "Read"), ("second", "Grep")):
            block = _FakeToolUseBlock(id="shared", name=name)
            block.input = {"path": session}
            await adapter._process_assistant_message(
                session,
                _FakeAssistantMessage(content=[block]),
            )
        adapter._cleanup_session("first")
        assert ("second", "shared") in adapter._tool_buf
        await adapter._process_assistant_message(
            "second", _FakeAssistantMessage(content=[_FakeToolResultBlock(tool_use_id="shared", content="result")])
        )
        events = []
        while not adapter._queues["second"].empty():
            events.append(adapter._queues["second"].get_nowait())
        completed = [event for event in events if event.kind == EventKind.tool_call_completed]
        assert len(completed) == 1
        assert completed[0].payload["tool_name"] == "Grep"
        assert completed[0].payload["arguments"]["path"] == "second"

    @pytest.mark.asyncio
    async def test_tool_result_records_telemetry_with_job(self) -> None:
        adapter = ClaudeAdapter()
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"
        adapter._schedule_db_write = lambda coro: coro.close()
        adapter._tool_buf[sid, "tool-1"] = {
            "job_id": "job-1",
            "tool_name": "Bash",
            "arguments": None,
            "start": time.monotonic() - 1.0,
        }

        await adapter._process_tool_result_block(
            sid,
            _FakeToolResultBlock(tool_use_id="tool-1", content="ok"),
            "job-1",
        )

        event = adapter._queues[sid].get_nowait()
        assert event.kind == EventKind.tool_call_completed
        assert event.metadata.duration_ms is not None

    @pytest.mark.asyncio
    async def test_tool_result_no_job_skips_telemetry(self) -> None:
        adapter = ClaudeAdapter()
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()
        adapter._schedule_db_write = lambda coro: coro.close()

        await adapter._process_tool_result_block(
            sid,
            _FakeToolResultBlock(tool_use_id="tool-2", content="ok"),
            None,
        )

        assert adapter._queues[sid].empty()
