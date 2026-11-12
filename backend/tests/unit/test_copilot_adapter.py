"""Unit tests for backend.services.copilot_adapter — CopilotAdapter.

All ``copilot`` SDK imports are mocked so the tests run without the SDK installed.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.domain import (
    SessionConfig,
)
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, new_event
from backend.services.adapters import agent_adapter
from backend.services.completers import copilot_steer

# ---------------------------------------------------------------------------
# Fake copilot SDK types — injected into sys.modules before adapter import
# ---------------------------------------------------------------------------


class _FakePermissionRequest:
    """Mimics ``copilot.PermissionRequest``."""

    def __init__(self, **kwargs: Any) -> None:
        self.kind = SimpleNamespace(value=kwargs.get("kind", "shell"))
        self.file_name = kwargs.get("file_name")
        self.path = kwargs.get("path")
        self.possible_paths = kwargs.get("possible_paths")
        self.full_command_text = kwargs.get("full_command_text")
        self.read_only = kwargs.get("read_only", False)
        self.intention = kwargs.get("intention")
        self.url = kwargs.get("url")
        self.tool_title = kwargs.get("tool_title")
        self.tool_name = kwargs.get("tool_name")


class _FakePermissionRequestResult:
    """Mimics ``copilot.PermissionRequestResult``."""

    def __init__(self, kind: str = "approved") -> None:
        self.kind = kind


class _FakeSdkSessionEvent:
    """Mimics ``copilot.generated.session_events.SessionEvent``."""

    def __init__(self, type_value: str = "log", data: Any = None) -> None:
        self.type = SimpleNamespace(value=type_value) if type_value else None
        self.data = data


class _FakeEventData:
    """Flexible namespace that mimics SDK event data objects (to_dict, attributes)."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class _FakeCopilotSession:
    """Mimics ``copilot.session.CopilotSession``."""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self._callbacks: list[Any] = []
        self._send_calls: list[Any] = []
        self._aborted = False

    def on(self, callback: Any) -> None:
        self._callbacks.append(callback)

    async def send(self, payload: Any, **kwargs: Any) -> None:
        self._send_calls.append(payload)

    async def abort(self) -> None:
        self._aborted = True

    def fire_event(self, event: _FakeSdkSessionEvent) -> None:
        """Helper to simulate SDK firing an event."""
        for cb in self._callbacks:
            cb(event)


class _FakeCopilotClient:
    """Mimics ``copilot.CopilotClient``."""

    def __init__(self, config: Any = None) -> None:
        self._sessions: list[_FakeCopilotSession] = []
        self._config = config

    async def create_session(self, **kwargs: Any) -> _FakeCopilotSession:
        session = _FakeCopilotSession()
        self._sessions.append(session)
        return session

    async def resume_session(self, session_id: str, **kwargs: Any) -> _FakeCopilotSession:
        session = _FakeCopilotSession(session_id=session_id)
        self._sessions.append(session)
        return session


def _build_fake_copilot_module() -> ModuleType:
    mod = ModuleType("copilot")
    mod.CopilotClient = _FakeCopilotClient

    class _FakeSubprocessConfig:
        def __init__(self, *, github_token: str | None = None) -> None:
            self.github_token = github_token

    mod.SubprocessConfig = _FakeSubprocessConfig
    mod.PermissionRequest = _FakePermissionRequest
    mod.PermissionRequestResult = _FakePermissionRequestResult
    return mod


def _build_fake_copilot_types() -> ModuleType:
    mod = ModuleType("copilot.types")

    class _SessionConfig(dict):  # type: ignore[type-arg]
        pass

    class _ResumeSessionConfig(dict):  # type: ignore[type-arg]  # type: ignore[type-arg]
        pass

    mod.SessionConfig = _SessionConfig
    mod.ResumeSessionConfig = _ResumeSessionConfig
    return mod


def _build_fake_copilot_session() -> ModuleType:
    mod = ModuleType("copilot.session")
    mod.CopilotSession = _FakeCopilotSession
    mod.PermissionRequestResult = _FakePermissionRequestResult
    mod.SystemMessageAppendConfig = dict  # adapter only uses it as a type hint
    return mod


def _build_fake_copilot_events() -> ModuleType:
    mod = ModuleType("copilot.generated")
    sub = ModuleType("copilot.generated.session_events")
    sub.SessionEvent = _FakeSdkSessionEvent
    mod.session_events = sub
    return mod


# Inject all fake copilot modules
_fake_copilot = _build_fake_copilot_module()
_fake_copilot_types = _build_fake_copilot_types()
_fake_copilot_session = _build_fake_copilot_session()
_fake_copilot_events = _build_fake_copilot_events()

sys.modules.setdefault("copilot", _fake_copilot)
sys.modules.setdefault("copilot.types", _fake_copilot_types)
sys.modules.setdefault("copilot.session", _fake_copilot_session)
sys.modules.setdefault("copilot.generated", _fake_copilot_events)
sys.modules.setdefault("copilot.generated.session_events", _fake_copilot_events.session_events)

# Fake copilot.client and copilot.jsonrpc — provide SDK exception types
_fake_copilot_client_mod = ModuleType("copilot.client")
_fake_copilot_jsonrpc_mod = ModuleType("copilot.jsonrpc")


class _FakeProcessExitedError(Exception):
    pass


class _FakeJsonRpcError(Exception):
    pass


_fake_copilot_client_mod.ProcessExitedError = _FakeProcessExitedError
_fake_copilot_client_mod.StopError = type("StopError", (Exception,), {})
_fake_copilot_jsonrpc_mod.JsonRpcError = _FakeJsonRpcError
_fake_copilot_jsonrpc_mod.ProcessExitedError = _FakeProcessExitedError
sys.modules.setdefault("copilot.client", _fake_copilot_client_mod)
sys.modules.setdefault("copilot.jsonrpc", _fake_copilot_jsonrpc_mod)

# SDK 0.3.0 moved error types to copilot._jsonrpc
_fake_copilot_jsonrpc_internal_mod = ModuleType("copilot._jsonrpc")
_fake_copilot_jsonrpc_internal_mod.JsonRpcError = _FakeJsonRpcError
_fake_copilot_jsonrpc_internal_mod.ProcessExitedError = _FakeProcessExitedError
sys.modules.setdefault("copilot._jsonrpc", _fake_copilot_jsonrpc_internal_mod)

from backend.services.copilot_adapter import CopilotAdapter  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter() -> CopilotAdapter:
    return CopilotAdapter()


@pytest.fixture()
def adapter_with_services() -> CopilotAdapter:
    approval = MagicMock()
    approval.is_trusted = MagicMock(return_value=False)
    event_bus = MagicMock()
    return CopilotAdapter(approval_service=approval, event_bus=event_bus)


def _make_config(**overrides: Any) -> SessionConfig:
    defaults: dict[str, Any] = {
        "workspace_path": "/tmp/workspace",
        "prompt": "hello world",
        "job_id": "job-1",
    }
    defaults.update(overrides)
    return SessionConfig(**defaults)


def _install_auto_approve_router(adapter: Any, job_id: str = "job-1") -> None:
    """Install a mock policy router that auto-approves everything."""
    from backend.services.adapters.base_adapter import PermissionDecision

    mock_router = MagicMock()
    adapter._policy_router[job_id] = mock_router
    adapter._repo_policies[job_id] = MagicMock()
    adapter._worktree_paths[job_id] = "/tmp/workspace"
    adapter._evaluate_with_policy_router = AsyncMock(return_value=PermissionDecision.allow)


# ---------------------------------------------------------------------------
# Tests: CopilotAdapter internals
# ---------------------------------------------------------------------------


class TestRuntimeContract:
    @staticmethod
    def _client(session_id: str = "runtime-session") -> tuple[MagicMock, _FakeCopilotSession]:
        session = _FakeCopilotSession(session_id)
        client = MagicMock()
        client.create_session = AsyncMock(return_value=session)
        client.resume_session = AsyncMock(return_value=session)
        client.stop = AsyncMock()
        client.force_stop = AsyncMock()
        return client, session

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", ["create", "resume", "send", "cancel"])
    async def test_failed_start_awaits_client_release(self, adapter: CopilotAdapter, stage: str) -> None:
        client, session = self._client()
        config = _make_config(workspace_path=".")
        if stage == "resume":
            config.resume_sdk_session_id = "previous"
            client.resume_session.side_effect = RuntimeError("fixture failure")
        elif stage == "send":
            session.send = AsyncMock(side_effect=RuntimeError("fixture failure"))
        else:
            client.create_session.side_effect = (
                asyncio.CancelledError() if stage == "cancel" else RuntimeError("fixture failure")
            )
        with (
            patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client),
            pytest.raises((RuntimeError, asyncio.CancelledError)),
        ):
            await adapter.create_session(config)
        released = client.stop.await_count + client.force_stop.await_count
        await asyncio.sleep(0)
        assert released > 0, "Startup must await release, including pre-registration failure/cancellation"
        assert not adapter._clients

    @pytest.mark.asyncio
    async def test_resume_reapplies_full_session_constraints(self, adapter: CopilotAdapter) -> None:
        client, _ = self._client()
        config = _make_config(
            workspace_path=".",
            model="primary-model",
            system_prompt_override="Read-only context brief",
            disallowed_tools=["write", "shell"],
            resume_sdk_session_id="previous",
        )
        with patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client):
            sid = await adapter.create_session(config)
        kwargs = client.resume_session.call_args.kwargs
        adapter._cleanup_session(sid)
        await asyncio.sleep(0)
        assert kwargs.get("excluded_tools") == ["write", "shell"]
        assert kwargs.get("system_message") == {"mode": "append", "content": "Read-only context brief"}
        assert "tools" in kwargs and kwargs["tools"] is None
        assert kwargs["model"] == "primary-model"

    @pytest.mark.asyncio
    async def test_late_callback_cannot_repopulate_finalized_session(self, adapter: CopilotAdapter) -> None:
        client, session = self._client()
        with patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client):
            sid = await adapter.create_session(_make_config(workspace_path="."))
        session.fire_event(
            _FakeSdkSessionEvent(
                "tool.execution_start",
                _FakeEventData(
                    tool_call_id="late", tool_name="read", arguments={}, mcp_tool_name=None, mcp_server_name=None
                ),
            )
        )
        adapter._cleanup_session(sid)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not adapter._tool_buf, "A queued callback must not restore state after finalization"

    @pytest.mark.asyncio
    async def test_tool_pairing_and_cleanup_are_session_qualified(self, adapter: CopilotAdapter) -> None:
        first_client, first = self._client("first")
        second_client, second = self._client("second")
        with patch(
            "backend.services.copilot_adapter._adapter.create_copilot_client",
            side_effect=[first_client, second_client],
        ):
            first_id = await adapter.create_session(_make_config(workspace_path="."))
            second_id = await adapter.create_session(_make_config(workspace_path="."))
        for session, tool in [(first, "read"), (second, "grep")]:
            session.fire_event(
                _FakeSdkSessionEvent(
                    "tool.execution_start",
                    _FakeEventData(
                        tool_call_id="shared-id",
                        tool_name=tool,
                        arguments={"tool": tool},
                        mcp_tool_name=None,
                        mcp_server_name=None,
                    ),
                )
            )
        await asyncio.sleep(0)
        adapter._cleanup_session(first_id)
        second.fire_event(
            _FakeSdkSessionEvent(
                "tool.execution_complete",
                _FakeEventData(tool_call_id="shared-id", success=True, result=None),
            )
        )
        await asyncio.sleep(0)
        events = []
        queue = adapter._queues[second_id]
        while not queue.empty():
            events.append(queue.get_nowait())
        adapter._cleanup_session(second_id)
        await asyncio.sleep(0)
        completed = [event for event in events if event and event.kind == EventKind.tool_call_completed]
        assert completed[0].payload["tool_name"] == "grep"
        assert completed[0].payload["arguments"] == {"tool": "grep"}

    @pytest.mark.asyncio
    async def test_utility_denies_permissions_without_exclusion_help(self, adapter: CopilotAdapter) -> None:
        client, session = self._client()
        decisions = []

        async def send(*args: Any, **kwargs: Any) -> None:
            permission = client.create_session.call_args.kwargs["on_permission_request"]
            decisions.append(await permission(_FakePermissionRequest(), {}))
            session.fire_event(_FakeSdkSessionEvent("assistant.message", _FakeEventData(content="text only")))
            session.fire_event(_FakeSdkSessionEvent("session.idle", _FakeEventData()))

        session.send = send
        with (
            patch("copilot.CopilotClient", return_value=client),
            patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client),
        ):
            result = await adapter.complete("hello", excluded_tools=[])
        assert result.text == "text only"
        assert decisions[0].kind == "reject"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["session.error", "abort", "timeout", "create", "send", "empty"])
    async def test_utility_failure_is_typed_not_success(self, adapter: CopilotAdapter, failure: str) -> None:
        client, session = self._client()

        async def send(*args: Any, **kwargs: Any) -> None:
            if failure == "send":
                raise ConnectionError("send failed")
            if failure != "empty":
                session.fire_event(_FakeSdkSessionEvent("assistant.message", _FakeEventData(content="partial")))
            if failure in ("session.error", "abort"):
                session.fire_event(_FakeSdkSessionEvent(failure, _FakeEventData(message="failure", reason="aborted")))
            elif failure == "empty":
                session.fire_event(_FakeSdkSessionEvent("session.idle", _FakeEventData()))

        session.send = send
        if failure == "create":
            client.create_session.side_effect = ConnectionError("create failed")
        error = None
        with (
            patch("copilot.CopilotClient", return_value=client),
            patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client),
            patch("backend.services.copilot_adapter._adapter.COMPLETION_TIMEOUT_S", 0.01),
        ):
            try:
                await adapter.complete("hello")
            except Exception as exc:
                error = exc
        assert error is not None, f"{failure} must not become a successful completion"
        assert type(error).__name__ == "UtilityCompletionError"
        assert getattr(error, "reason", None)
        assert client.stop.await_count + client.force_stop.await_count > 0

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_start_and_drains_ownership(self, adapter: CopilotAdapter) -> None:
        client, _ = self._client()
        entered = asyncio.Event()

        async def create(**kwargs: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        client.create_session.side_effect = create
        with patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client):
            pending = asyncio.create_task(adapter.create_session(_make_config(workspace_path=".")))
            await entered.wait()
            shutdown = getattr(adapter, "shutdown", None)
            if shutdown is not None:
                await asyncio.wait_for(shutdown(), timeout=1)
            completed_by_shutdown = pending.done()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert shutdown is not None, "Adapter needs an awaitable shutdown boundary"
        assert completed_by_shutdown
        assert client.stop.await_count + client.force_stop.await_count > 0
        assert not adapter._clients

    @pytest.mark.asyncio
    async def test_force_stop_is_bounded(self, adapter: CopilotAdapter) -> None:
        client, _ = self._client()
        client.stop.side_effect = TimeoutError()

        async def force_stop() -> None:
            await asyncio.Event().wait()

        client.force_stop.side_effect = force_stop
        timed_out = False
        with patch("backend.services.copilot_adapter._adapter.CLIENT_STOP_TIMEOUT_S", 0.01):
            try:
                await asyncio.wait_for(adapter._stop_client(client), timeout=0.1)
            except TimeoutError:
                timed_out = True
        assert not timed_out, "force_stop must have its own bounded timeout"
        client.force_stop.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stop_error",
        [RuntimeError("stop failed"), ExceptionGroup("disconnect failures", [OSError("transport failed")])],
    )
    async def test_any_sdk_stop_failure_attempts_force_stop(self, adapter: CopilotAdapter, stop_error) -> None:
        client, _ = self._client()
        client.stop.side_effect = stop_error
        escaped = None
        try:
            await adapter._stop_client(client)
        except Exception as exc:
            escaped = exc
        assert escaped is None, "A stop failure must still run forced teardown"
        client.force_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_utility_awaits_client_release(self, adapter: CopilotAdapter) -> None:
        client, _ = self._client()
        client.create_session.side_effect = asyncio.CancelledError()
        with (
            patch("copilot.CopilotClient", return_value=client),
            patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client),
            pytest.raises(asyncio.CancelledError),
        ):
            await adapter.complete("hello")
        released = client.stop.await_count + client.force_stop.await_count
        await asyncio.sleep(0)
        assert released > 0, "Cancelled utilities must await client release"

    @pytest.mark.asyncio
    async def test_late_telemetry_does_not_publish_after_finalize(self, adapter: CopilotAdapter) -> None:
        client, _ = self._client()
        adapter._event_bus = AsyncMock()
        with patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client):
            sid = await adapter.create_session(_make_config(workspace_path="."))
        adapter._cleanup_session(sid)
        await adapter._maybe_broadcast_telemetry("job-1")
        await asyncio.sleep(0)
        adapter._event_bus.publish.assert_not_awaited()


class TestSetJobId:
    def test_set_and_retrieve(self, adapter: CopilotAdapter) -> None:
        adapter.set_job_id("sess-1", "job-42")
        assert adapter._session_to_job["sess-1"] == "job-42"


class TestCleanupSession:
    def test_cleanup_removes_all_state(self, adapter: CopilotAdapter) -> None:
        sid = "sess-1"
        adapter._sessions[sid] = MagicMock()
        adapter._queues[sid] = asyncio.Queue()
        adapter._session_to_job[sid] = "job-1"

        adapter._cleanup_session(sid)

        assert sid not in adapter._sessions
        assert sid not in adapter._queues
        assert sid not in adapter._session_to_job

    def test_cleanup_missing_session_noop(self, adapter: CopilotAdapter) -> None:
        adapter._cleanup_session("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# Tests: stream_events
# ---------------------------------------------------------------------------


class TestStreamEvents:
    @pytest.mark.asyncio
    async def test_yields_events_until_sentinel(self, adapter: CopilotAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q

        e1 = new_event(session_id="job-test", kind=EventKind.message_assistant, payload={"content": "hi"})
        e2 = new_event(session_id="job-test", kind=EventKind.log_line_emitted, payload={"message": "done"})
        q.put_nowait(e1)
        q.put_nowait(e2)
        q.put_nowait(None)

        collected = []
        async for event in adapter.stream_events(sid):
            collected.append(event)

        assert len(collected) == 2
        assert collected[0] is e1
        assert collected[1] is e2

    @pytest.mark.asyncio
    async def test_no_queue_yields_error(self, adapter: CopilotAdapter) -> None:
        collected = []
        async for event in adapter.stream_events("nonexistent"):
            collected.append(event)

        assert len(collected) == 1
        assert collected[0].kind == EventKind.job_failed

    @pytest.mark.asyncio
    async def test_sentinel_terminates_and_cleans_up(self, adapter: CopilotAdapter) -> None:
        """When None sentinel arrives (process exit), stream ends and cleanup runs."""
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        adapter._sessions[sid] = MagicMock()

        # Only a sentinel — simulates process exiting with no prior events
        q.put_nowait(None)

        events: list[SessionEvent] = []
        async for event in adapter.stream_events(sid):
            events.append(event)

        assert len(events) == 0
        # Cleanup still runs via finally
        assert sid not in adapter._sessions

    @pytest.mark.asyncio
    async def test_cleanup_called_on_exit(self, adapter: CopilotAdapter) -> None:
        sid = "sess-1"
        q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        adapter._queues[sid] = q
        adapter._sessions[sid] = MagicMock()
        q.put_nowait(None)

        async for _ in adapter.stream_events(sid):
            pass

        assert sid not in adapter._sessions
        assert sid not in adapter._queues


# ---------------------------------------------------------------------------
# Tests: send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_to_existing_session(self, adapter: CopilotAdapter) -> None:
        session = _FakeCopilotSession()
        adapter._sessions["sess-1"] = session  # type: ignore[assignment]

        await adapter.send_message("sess-1", "follow up")

        assert len(session._send_calls) == 1
        assert session._send_calls[0] == "follow up"

    @pytest.mark.asyncio
    async def test_send_to_missing_session(self, adapter: CopilotAdapter) -> None:
        with pytest.raises(copilot_steer.NativeDeliveryError, match="no longer live") as failure:
            await adapter.send_message("nonexistent", "hello")

        assert failure.value.uncertain is False


# ---------------------------------------------------------------------------
# Tests: abort_session
# ---------------------------------------------------------------------------


class TestAbortSession:
    @pytest.mark.asyncio
    async def test_abort_existing_session(self, adapter: CopilotAdapter) -> None:
        session = _FakeCopilotSession()
        adapter._sessions["sess-1"] = session  # type: ignore[assignment]  # type: ignore[assignment]
        adapter._queues["sess-1"] = asyncio.Queue()
        adapter._session_to_job["sess-1"] = "job-1"

        await adapter.abort_session("sess-1")

        assert session._aborted
        assert "sess-1" not in adapter._sessions

    @pytest.mark.asyncio
    async def test_abort_missing_session(self, adapter: CopilotAdapter) -> None:
        await adapter.abort_session("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_abort_handles_exception(self, adapter: CopilotAdapter) -> None:
        session = MagicMock()
        session.abort = AsyncMock(side_effect=_FakeProcessExitedError("abort fail"))
        adapter._sessions["sess-1"] = session

        await adapter.abort_session("sess-1")

        assert "sess-1" not in adapter._sessions


# ---------------------------------------------------------------------------
# Tests: create_session (SDK event callback / _on_event)
# ---------------------------------------------------------------------------


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session_basic(self, adapter: CopilotAdapter) -> None:
        """Verify a basic session creation wires up queue and session."""
        config = _make_config()
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        assert session_id in adapter._queues
        assert session_id in adapter._sessions
        assert adapter._session_to_job[session_id] == "job-1"

    @pytest.mark.asyncio
    async def test_create_session_with_model(self, adapter: CopilotAdapter) -> None:
        config = _make_config(model="gpt-4o")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        assert session_id  # just verify it returned successfully

    @pytest.mark.asyncio
    async def test_create_session_resume(self, adapter: CopilotAdapter) -> None:
        config = _make_config(resume_sdk_session_id="prev-sdk-session")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        assert session_id == "prev-sdk-session"

    @pytest.mark.asyncio
    async def test_create_session_resume_fallback_on_failure(self, adapter: CopilotAdapter) -> None:
        """When resume fails, should fall back to creating a new session."""
        config = _make_config(resume_sdk_session_id="stale-session")

        class _FailingResumeClient(_FakeCopilotClient):
            async def resume_session(self, session_id: str, **kwargs: Any) -> _FakeCopilotSession:
                raise _FakeJsonRpcError("session expired")

        fake_client = _FailingResumeClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        # Fell back to create_session, so ID won't match the resume ID
        assert session_id in adapter._sessions

    @pytest.mark.asyncio
    async def test_create_session_send_failure_cleans_up(self, adapter: CopilotAdapter) -> None:
        config = _make_config()

        class _FailingSendSession(_FakeCopilotSession):
            async def send(self, payload: Any, **kwargs: Any) -> None:
                raise RuntimeError("send failed")

        class _FailClient(_FakeCopilotClient):
            async def create_session(self, **kwargs: Any) -> _FailingSendSession:
                return _FailingSendSession()

        with (
            patch("copilot.CopilotClient", return_value=_FailClient()),
            pytest.raises(RuntimeError, match="send failed"),
        ):
            await adapter.create_session(config)

        assert len(adapter._sessions) == 0


# ---------------------------------------------------------------------------
# Tests: SDK event translation (_on_event callback)
# ---------------------------------------------------------------------------


class TestOnEventCallback:
    """Tests the _on_event closure created inside create_session.

    We invoke create_session with a fake client, capture the session/queue,
    and fire SDK events through the callback to verify translation.

    The callback now uses create_task, so we must yield to the event loop
    after firing events to let processing complete.
    """

    @pytest.mark.asyncio
    async def _setup_session(
        self,
        adapter: CopilotAdapter,
        config: SessionConfig | None = None,
    ) -> tuple[str, asyncio.Queue[SessionEvent | None], Any]:
        """Create a session and return (session_id, queue, fake_session)."""
        config = config or _make_config()
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        # Prevent pipeline DB writes from failing (no session_factory in tests)
        adapter._schedule_db_write = lambda coro: coro.close()
        return session_id, queue, session

    async def _fire(self, session: Any, event: _FakeSdkSessionEvent) -> None:
        """Fire an SDK event and yield to let the pipeline task execute."""
        session.fire_event(event)
        await asyncio.sleep(0)

    def _drain_queue(self, q: asyncio.Queue[SessionEvent | None]) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        while not q.empty():
            e = q.get_nowait()
            if e is not None:
                events.append(e)
        return events

    @pytest.mark.asyncio
    async def test_assistant_message_emits_transcript(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="Hello from agent", title=None, turn_id=None)
        await self._fire(session, _FakeSdkSessionEvent("assistant.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 1
        assert transcripts[0].kind == EventKind.message_assistant
        assert transcripts[0].payload["content"] == "Hello from agent"

    @pytest.mark.asyncio
    async def test_empty_assistant_message_skipped(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="   ", title=None, turn_id=None)
        await self._fire(session, _FakeSdkSessionEvent("assistant.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 0

    @pytest.mark.asyncio
    async def test_user_message_emits_transcript(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="user says hi", message=None)
        await self._fire(session, _FakeSdkSessionEvent("user.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 1
        assert transcripts[0].kind == EventKind.message_user

    @pytest.mark.asyncio
    async def test_system_notification_user_message_skipped(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="<system_notification>done</system_notification>", message=None)
        await self._fire(session, _FakeSdkSessionEvent("user.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 0

    @pytest.mark.asyncio
    async def test_task_complete_emits_done(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        await self._fire(session, _FakeSdkSessionEvent("session.task_complete", data))

        all_items = []
        while not queue.empty():
            all_items.append(queue.get_nowait())
        assert None in all_items

    @pytest.mark.asyncio
    async def test_session_error_emits_error_and_sentinel(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        await self._fire(session, _FakeSdkSessionEvent("session.error", data))

        # Drain including None sentinel
        all_items = []
        while not queue.empty():
            all_items.append(queue.get_nowait())

        real_events = [e for e in all_items if e is not None]
        error_events = [e for e in real_events if e.kind == EventKind.job_failed]
        assert len(error_events) == 1
        # Sentinel should be present
        assert None in all_items

    @pytest.mark.asyncio
    async def test_session_idle_emits_done(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        await self._fire(session, _FakeSdkSessionEvent("session.idle", data))

        all_items = []
        while not queue.empty():
            all_items.append(queue.get_nowait())
        assert None in all_items

    @pytest.mark.asyncio
    async def test_session_shutdown_emits_done(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(total_premium_requests=None)
        await self._fire(session, _FakeSdkSessionEvent("session.shutdown", data))

        all_items = []
        while not queue.empty():
            all_items.append(queue.get_nowait())
        assert None in all_items

    @pytest.mark.asyncio
    async def test_file_changed_emits_file_changed(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(file_path="/tmp/workspace/main.py")
        await self._fire(session, _FakeSdkSessionEvent("session.workspace_file_changed", data))

        events = self._drain_queue(queue)
        fc = [e for e in events if e.kind == EventKind.file_edited]
        assert len(fc) == 1

    @pytest.mark.asyncio
    async def test_unknown_event_type_ignored(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        await self._fire(session, _FakeSdkSessionEvent("internal.something", data))

        events = self._drain_queue(queue)
        # Only log events (from tool started etc.) might appear, no mapped event
        mapped = [e for e in events if e.kind in TRANSCRIPT_KINDS or e.kind == EventKind.job_failed]
        assert len(mapped) == 0

    @pytest.mark.asyncio
    async def test_reasoning_delta_emits_reasoning_delta(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(delta_content="Let me think...", turn_id=None)
        await self._fire(session, _FakeSdkSessionEvent("assistant.reasoning_delta", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 1
        assert transcripts[0].kind == EventKind.llm_reasoning_chunk
        assert transcripts[0].metadata.partial is True
        assert transcripts[0].payload["content"] == "Let me think..."

    @pytest.mark.asyncio
    async def test_empty_reasoning_delta_skipped(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(delta_content="", turn_id=None)
        await self._fire(session, _FakeSdkSessionEvent("assistant.reasoning_delta", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 0

    @pytest.mark.asyncio
    async def test_reasoning_emits_reasoning(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="Full reasoning block", reasoning_text=None, turn_id=None)
        await self._fire(session, _FakeSdkSessionEvent("assistant.reasoning", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind in TRANSCRIPT_KINDS]
        assert len(transcripts) == 1
        assert transcripts[0].kind == EventKind.llm_reasoning_chunk
        assert transcripts[0].metadata.partial is False
        assert transcripts[0].payload["content"] == "Full reasoning block"


# ---------------------------------------------------------------------------
# Tests: SDK event telemetry extraction
# ---------------------------------------------------------------------------


class TestEventTelemetry:
    @pytest.mark.asyncio
    async def _setup_session_with_job(
        self,
        adapter: CopilotAdapter,
    ) -> tuple[str, asyncio.Queue[SessionEvent | None], Any]:
        config = _make_config(job_id="job-tel")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        # Prevent pipeline DB writes from failing (no session_factory in tests)
        adapter._schedule_db_write = lambda coro: coro.close()
        return session_id, queue, session

    async def _fire(self, session: Any, event: _FakeSdkSessionEvent) -> None:
        """Fire an SDK event and yield to let the pipeline task execute."""
        session.fire_event(event)
        await asyncio.sleep(0)

    def _drain_queue(self, q: asyncio.Queue[SessionEvent | None]) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        while not q.empty():
            e = q.get_nowait()
            if e is not None:
                events.append(e)
        return events

    @pytest.mark.asyncio
    async def test_assistant_usage_records_llm_usage(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            cost=0.002,
            duration=1500,
            quota_snapshots=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("assistant.usage", data))

        events = self._drain_queue(queue)
        usage = [e for e in events if e.kind == EventKind.telemetry_usage]
        assert len(usage) == 1
        assert usage[0].payload == {
            "model": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_write_tokens": 5,
            "cost_usd": 0.002,
            "duration_ms": 1500.0,
            "is_subagent": False,
            "advance_turn": True,
            "num_turns": 1,
        }

    @pytest.mark.asyncio
    async def test_assistant_usage_model_mismatch(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-mismatch", model="gpt-4o")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        data = _FakeEventData(
            model="gpt-3.5-turbo",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.0001,
            duration=100,
            quota_snapshots=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("assistant.usage", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        downgraded = [e for e in events if e.kind == EventKind.model_downgraded]
        assert len(downgraded) == 1
        assert downgraded[0].payload["requested_model"] == "gpt-4o"
        assert downgraded[0].payload["actual_model"] == "gpt-3.5-turbo"

    @pytest.mark.asyncio
    async def test_assistant_usage_model_match(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-match", model="gpt-4o")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        data = _FakeEventData(
            model="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.001,
            duration=200,
            quota_snapshots=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("assistant.usage", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        downgraded = [e for e in events if e.kind == EventKind.model_downgraded]
        assert len(downgraded) == 0

    @pytest.mark.asyncio
    async def test_tool_execution_start_and_complete(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        start_data = _FakeEventData(
            tool_call_id="tc-1",
            tool_name="bash",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments={"command": "ls"},
            turn_id="t1",
            intention=None,
            tool_title=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", start_data))

        buffered = adapter._tool_buf[sid, "tc-1"]
        assert buffered.get("tool_name") == "bash"

        result_obj = SimpleNamespace(content=[SimpleNamespace(text="file1.py")])
        complete_data = _FakeEventData(
            tool_call_id="tc-1",
            tool_name="bash",
            mcp_tool_name=None,
            success=True,
            result=result_obj,
            partial_output=None,
            turn_id="t1",
        )
        await self._fire(session, _FakeSdkSessionEvent("tool.execution_complete", complete_data))

        events = self._drain_queue(queue)
        completed = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(completed) == 1
        assert completed[0].payload["tool_name"] == "bash"
        assert completed[0].metadata.duration_ms is not None

    @pytest.mark.asyncio
    @patch("backend.services.tool_formatters.format_tool_display", return_value="bash: ls")
    async def test_tool_execution_complete_emits_transcript(self, mock_fmt: MagicMock, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        # Fire tool start first to buffer metadata
        start_data = _FakeEventData(
            tool_call_id="tc-2",
            tool_name="bash",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments={"command": "ls"},
            turn_id="t1",
            intention=None,
            tool_title=None,
        )
        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", start_data))

        result_obj = SimpleNamespace(content=[SimpleNamespace(text="output")])
        complete_data = _FakeEventData(
            tool_call_id="tc-2",
            tool_name="bash",
            mcp_tool_name=None,
            success=True,
            result=result_obj,
            partial_output=None,
            turn_id="t1",
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_complete", complete_data))

        events = self._drain_queue(queue)

        transcripts = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(transcripts) == 1
        assert transcripts[0].payload["tool_name"] == "bash"
        assert transcripts[0].payload["success"] is True

    @pytest.mark.asyncio
    async def test_report_intent_tool_emits_tf_tool_events(self, adapter: CopilotAdapter) -> None:
        """report_intent is emitted as TF tool events for the processor to handle."""
        sid, queue, session = await self._setup_session_with_job(adapter)

        # Fire tool start for report_intent
        start_data = _FakeEventData(
            tool_call_id="tc-ri",
            tool_name="report_intent",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments={"intent": "testing"},
            turn_id="t1",
            intention=None,
            tool_title=None,
        )
        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", start_data))

        complete_data = _FakeEventData(
            tool_call_id="tc-ri",
            tool_name="report_intent",
            mcp_tool_name=None,
            success=True,
            result=None,
            partial_output=None,
            turn_id="t1",
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_complete", complete_data))

        events = self._drain_queue(queue)

        completed = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(completed) == 1
        assert completed[0].payload["tool_name"] == "report_intent"
        assert completed[0].payload["arguments"] == {"intent": "testing"}

    @pytest.mark.asyncio
    async def test_context_changed(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(current_tokens=5000)

        with patch("backend.services.analytics.telemetry.context_tokens_gauge") as mock_gauge:
            await self._fire(session, _FakeSdkSessionEvent("session.usage_info", data))

            mock_gauge.set.assert_called_once_with(5000, {"job_id": "job-tel", "sdk": "copilot"})

    @pytest.mark.asyncio
    async def test_compaction_complete(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(pre_compaction_tokens=10000, post_compaction_tokens=3000)

        with (
            patch("backend.services.analytics.telemetry.compactions_counter") as mock_comp,
            patch("backend.services.analytics.telemetry.tokens_compacted") as mock_tc,
            patch("backend.services.analytics.telemetry.context_tokens_gauge") as mock_ctx,
        ):
            await self._fire(session, _FakeSdkSessionEvent("session.compaction_complete", data))

            mock_comp.add.assert_called_once_with(1, {"job_id": "job-tel", "sdk": "copilot"})
            mock_tc.add.assert_called_once_with(7000, {"job_id": "job-tel", "sdk": "copilot"})
            # Also records a context change for the post-compaction token count
            mock_ctx.set.assert_called_with(3000, {"job_id": "job-tel", "sdk": "copilot"})

    @pytest.mark.asyncio
    async def test_session_truncation(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(token_limit=128000)

        with patch("backend.services.analytics.telemetry.context_window_gauge") as mock_gauge:
            await self._fire(session, _FakeSdkSessionEvent("session.truncation", data))

            mock_gauge.set.assert_called_once_with(128000, {"job_id": "job-tel", "sdk": "copilot"})

    @pytest.mark.asyncio
    async def test_session_model_change(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(new_model="gpt-4o-mini")

        await self._fire(session, _FakeSdkSessionEvent("session.model_change", data))

        # Model change is tracked on the adapter
        assert adapter._job_main_models.get("job-tel") == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_assistant_message_records_telemetry(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(content="hello", title=None, turn_id=None)

        await self._fire(session, _FakeSdkSessionEvent("assistant.message", data))

        events = self._drain_queue(queue)
        assert any(e.kind == EventKind.message_assistant and e.payload["content"] == "hello" for e in events)

    @pytest.mark.asyncio
    async def test_user_message_records_telemetry(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(content="user prompt", message=None)

        await self._fire(session, _FakeSdkSessionEvent("user.message", data))

        events = self._drain_queue(queue)
        assert any(e.kind == EventKind.message_user and e.payload["content"] == "user prompt" for e in events)


# ---------------------------------------------------------------------------
# Tests: Log event emission from SDK events
# ---------------------------------------------------------------------------


class TestTfEventEmission:
    @pytest.mark.asyncio
    async def _setup_session(self, adapter: CopilotAdapter) -> tuple[str, asyncio.Queue[SessionEvent | None], Any]:
        config = _make_config(job_id="job-log")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        # Prevent pipeline DB writes from failing
        adapter._schedule_db_write = lambda coro: coro.close()
        return session_id, adapter._queues[session_id], adapter._sessions[session_id]

    async def _fire(self, session: Any, event: _FakeSdkSessionEvent) -> None:
        """Fire an SDK event and yield to let the pipeline task execute."""
        session.fire_event(event)
        await asyncio.sleep(0)

    def _drain_queue(self, q: asyncio.Queue[SessionEvent | None]) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        while not q.empty():
            e = q.get_nowait()
            if e is not None:
                events.append(e)
        return events

    @pytest.mark.asyncio
    async def test_tool_start_emits_tf_event(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(
            tool_call_id="tc-1",
            tool_name="bash",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", data))

        events = self._drain_queue(queue)
        started = [e for e in events if e.kind == EventKind.tool_call_started]
        assert len(started) == 1
        assert started[0].payload["tool_name"] == "bash"

    @pytest.mark.asyncio
    async def test_tool_complete_emits_tf_event(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        # Fire tool start first to buffer metadata
        start_data = _FakeEventData(
            tool_call_id="tc-log",
            tool_name="grep",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )
        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", start_data))

        data = _FakeEventData(
            tool_call_id="tc-log",
            tool_name="grep",
            mcp_tool_name=None,
            success=True,
            result=None,
            partial_output=None,
            turn_id=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_complete", data))

        events = self._drain_queue(queue)
        completed = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(completed) == 1
        assert completed[0].payload["tool_name"] == "grep"
        assert completed[0].payload["success"] is True

    @pytest.mark.asyncio
    async def test_tool_complete_failed_emits_unsuccessful_tf_event(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        # Fire tool start first to buffer metadata
        start_data = _FakeEventData(
            tool_call_id="tc-fail",
            tool_name="bash",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )
        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", start_data))

        data = _FakeEventData(
            tool_call_id="tc-fail",
            tool_name="bash",
            mcp_tool_name=None,
            success=False,
            result=None,
            partial_output=None,
            turn_id=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_complete", data))

        events = self._drain_queue(queue)
        completed = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(completed) == 1
        assert completed[0].payload["success"] is False

    @pytest.mark.asyncio
    async def test_assistant_usage_emits_tf_usage_event(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.01,
            duration=500,
            quota_snapshots=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("assistant.usage", data))

        events = self._drain_queue(queue)
        usage = [e for e in events if e.kind == EventKind.telemetry_usage]
        assert len(usage) == 1
        assert usage[0].payload["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_assistant_usage_model_mismatch_emits_downgrade_event(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-mm-log", model="gpt-4o")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        data = _FakeEventData(
            model="gpt-3.5-turbo",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.001,
            duration=100,
            quota_snapshots=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("assistant.usage", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        downgraded = [e for e in events if e.kind == EventKind.model_downgraded]
        assert len(downgraded) == 1

    @pytest.mark.asyncio
    async def test_compaction_log(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(pre_compaction_tokens=8000, post_compaction_tokens=2000)

        await self._fire(session, _FakeSdkSessionEvent("session.compaction_complete", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == EventKind.log_line_emitted]
        assert any("compacted" in e.payload.get("message", "").lower() for e in log_events)

    @pytest.mark.asyncio
    async def test_model_change_log(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(new_model="gpt-4o-mini")

        await self._fire(session, _FakeSdkSessionEvent("session.model_change", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == EventKind.log_line_emitted]
        assert any("Model changed" in e.payload.get("message", "") for e in log_events)

    @pytest.mark.asyncio
    async def test_mcp_tool_name_uses_server_prefix(self, adapter: CopilotAdapter) -> None:
        """MCP tools should emit server/tool_name as their TF tool name."""
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(
            tool_call_id="tc-mcp",
            tool_name=None,
            mcp_tool_name="search_code",
            mcp_server_name="github",
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )

        await self._fire(session, _FakeSdkSessionEvent("tool.execution_start", data))

        events = self._drain_queue(queue)
        started = [e for e in events if e.kind == EventKind.tool_call_started]
        assert len(started) == 1
        assert started[0].payload["tool_name"] == "github/search_code"

        buffered = adapter._tool_buf[sid, "tc-mcp"]
        assert buffered.get("tool_name") == "github/search_code"


# ---------------------------------------------------------------------------
# Tests: complete (single-turn)
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_collects_response(self, adapter: CopilotAdapter) -> None:
        fake_session = _FakeCopilotSession()
        collected_content = "Generated response"

        class _FakeCompleteClient:
            async def create_session(self, **kwargs: Any) -> _FakeCopilotSession:
                return fake_session

            async def stop(self) -> None:
                pass

        # We need to simulate the on_event callback firing with assistant.message
        original_on = fake_session.on

        def _patched_on(callback: Any) -> None:
            original_on(callback)
            # Immediately fire an assistant message and done
            data = _FakeEventData(content=collected_content)
            callback(_FakeSdkSessionEvent("assistant.message", data))
            callback(_FakeSdkSessionEvent("session.task_complete", _FakeEventData()))

        fake_session.on = _patched_on  # type: ignore[method-assign]

        with patch("copilot.CopilotClient", return_value=_FakeCompleteClient()):
            result = await adapter.complete("test prompt")

        assert result.text is not None
        assert collected_content in result.text

    @pytest.mark.asyncio
    async def test_complete_handles_exception(self, adapter: CopilotAdapter) -> None:
        class _FailingClient:
            async def create_session(self, **kwargs: Any) -> None:
                raise _FakeJsonRpcError("boom")

            async def stop(self) -> None:
                pass

        with (
            patch("copilot.CopilotClient", return_value=_FailingClient()),
            pytest.raises(agent_adapter.UtilityCompletionError) as failure,
        ):
            await adapter.complete("test")

        assert failure.value.reason == "sdk_error"
        assert isinstance(failure.value.__cause__, _FakeJsonRpcError)

    @pytest.mark.asyncio
    async def test_complete_timeout(self, adapter: CopilotAdapter) -> None:
        fake_session = _FakeCopilotSession()

        class _TimeoutClient:
            async def create_session(self, **kwargs: Any) -> _FakeCopilotSession:
                return fake_session

            async def stop(self) -> None:
                pass

        with (
            patch("copilot.CopilotClient", return_value=_TimeoutClient()),
            patch("backend.services.copilot_adapter._adapter.COMPLETION_TIMEOUT_S", 0.01),
            pytest.raises(agent_adapter.UtilityCompletionError) as failure,
        ):
            await adapter.complete("test prompt")

        assert failure.value.reason == "timeout"
        assert fake_session._aborted


# ---------------------------------------------------------------------------
# Tests: tool_execution_start buffering edge cases
# ---------------------------------------------------------------------------


class TestToolStartBuffering:
    @pytest.mark.asyncio
    async def test_arguments_dict_description_extracted(self, adapter: CopilotAdapter) -> None:
        """When arguments is a dict with a 'description' key, it becomes tool_intent."""
        config = _make_config(job_id="job-buf")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        data = _FakeEventData(
            tool_call_id="tc-desc",
            tool_name="bash",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments={"command": "ls", "description": "List files"},
            turn_id="t1",
            intention=None,
            tool_title=None,
        )

        session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))
        await asyncio.sleep(0)

        buf = adapter._tool_buf[session_id, "tc-desc"]
        assert buf.get("intent") == "List files"

    @pytest.mark.asyncio
    async def test_arguments_string_buffered(self, adapter: CopilotAdapter) -> None:
        """When arguments is already a string, it should be stored as-is."""
        config = _make_config(job_id="job-str")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        data = _FakeEventData(
            tool_call_id="tc-str",
            tool_name="read_file",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments='{"path": "/tmp/foo"}',
            turn_id=None,
            intention="Read a file",
            tool_title="read_file",
        )

        session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))
        await asyncio.sleep(0)

        buf = adapter._tool_buf[session_id, "tc-str"]
        assert buf.get("arguments") == '{"path": "/tmp/foo"}'
        assert buf.get("intent") == "Read a file"
        assert buf.get("title") == "read_file"

    @pytest.mark.asyncio
    async def test_arguments_none_buffered_as_empty(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-none")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        data = _FakeEventData(
            tool_call_id="tc-none",
            tool_name="some_tool",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )

        session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))
        await asyncio.sleep(0)

        buf = adapter._tool_buf[session_id, "tc-none"]
        assert buf.get("arguments") is None


# ---------------------------------------------------------------------------
# Tests: tool_execution_complete result extraction edge cases
# ---------------------------------------------------------------------------


class TestToolResultExtraction:
    @pytest.mark.asyncio
    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: ok")
    async def test_empty_result_content_fallback(self, mock_fmt: MagicMock, adapter: CopilotAdapter) -> None:
        """When result.content is empty, tool_result should be empty string."""
        config = _make_config(job_id="job-partial")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        # Fire tool start to buffer metadata
        start_data = _FakeEventData(
            tool_call_id="tc-po",
            tool_name="bash",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )
        session.fire_event(_FakeSdkSessionEvent("tool.execution_start", start_data))
        await asyncio.sleep(0)

        data = _FakeEventData(
            tool_call_id="tc-po",
            tool_name="bash",
            mcp_tool_name=None,
            success=True,
            result=SimpleNamespace(content=None),
            turn_id=None,
        )

        session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", data))
        await asyncio.sleep(0)

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        transcripts = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(transcripts) == 1
        assert transcripts[0].payload["result"] == ""

    @pytest.mark.asyncio
    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: ok")
    async def test_result_string_content(self, mock_fmt: MagicMock, adapter: CopilotAdapter) -> None:
        """When result.content is a plain string (not list), should be stringified."""
        config = _make_config(job_id="job-str-res")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]
        adapter._schedule_db_write = lambda coro: coro.close()

        # Fire tool start to buffer metadata
        start_data = _FakeEventData(
            tool_call_id="tc-sr",
            tool_name="grep",
            mcp_tool_name=None,
            mcp_server_name=None,
            arguments=None,
            turn_id=None,
            intention=None,
            tool_title=None,
        )
        session.fire_event(_FakeSdkSessionEvent("tool.execution_start", start_data))
        await asyncio.sleep(0)

        data = _FakeEventData(
            tool_call_id="tc-sr",
            tool_name="grep",
            mcp_tool_name=None,
            success=True,
            result=SimpleNamespace(content="plain string result"),
            partial_output=None,
            turn_id=None,
        )

        session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", data))
        await asyncio.sleep(0)

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        transcripts = [e for e in events if e.kind == EventKind.tool_call_completed]
        assert len(transcripts) == 1
        assert transcripts[0].payload["result"] == "plain string result"


# ---------------------------------------------------------------------------
# Tests: _handle_permission_request — git reset --hard hard block
# ---------------------------------------------------------------------------


class TestHandlePermissionRequestGitResetHard:
    """_handle_permission_request must block git reset --hard regardless of mode/trust."""

    _SESSION_ID = "test-session-1"

    def _make_adapter_with_approval(self) -> tuple[CopilotAdapter, MagicMock]:
        approval_service = MagicMock()
        approval_service.is_trusted = MagicMock(return_value=False)
        adapter = CopilotAdapter(approval_service=approval_service)
        # Register session→job so job_id is not None inside the handler
        adapter.set_job_id(self._SESSION_ID, "job-1")
        return adapter, approval_service

    def _make_request(self, cmd: str) -> _FakePermissionRequest:
        return _FakePermissionRequest(kind="shell", full_command_text=cmd)

    def _invocation(self) -> dict[str, str]:
        return {"session_id": self._SESSION_ID}

    @pytest.mark.asyncio
    async def test_git_reset_hard_requires_approval_in_auto_mode(self) -> None:
        adapter, approval_service = self._make_adapter_with_approval()
        config = _make_config()

        approval = MagicMock()
        approval.id = "apr-1"
        approval_service.create_request = AsyncMock(return_value=approval)
        approval_service.wait_for_resolution = AsyncMock(return_value="approved")

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard HEAD"),
            self._invocation(),
            config,
        )
        assert result.kind == "approve-once"
        approval_service.create_request.assert_called_once()
        call_kwargs = approval_service.create_request.call_args.kwargs
        assert call_kwargs["requires_explicit_approval"] is True

    @pytest.mark.asyncio
    async def test_git_reset_hard_cannot_be_bypassed_by_trust(self) -> None:
        """Trust grant must NOT bypass the git reset --hard block."""
        adapter, approval_service = self._make_adapter_with_approval()
        approval_service.is_trusted = MagicMock(return_value=True)  # trusted job
        config = _make_config()

        approval = MagicMock()
        approval.id = "apr-trust"
        approval_service.create_request = AsyncMock(return_value=approval)
        approval_service.wait_for_resolution = AsyncMock(return_value="approved")

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard origin/main"),
            self._invocation(),
            config,
        )
        # Still routes to approval even though job is trusted
        assert result.kind == "approve-once"
        approval_service.create_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_git_reset_hard_rejected_by_operator(self) -> None:
        adapter, approval_service = self._make_adapter_with_approval()
        config = _make_config()

        approval = MagicMock()
        approval.id = "apr-2"
        approval_service.create_request = AsyncMock(return_value=approval)
        approval_service.wait_for_resolution = AsyncMock(return_value="rejected")

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard HEAD"),
            self._invocation(),
            config,
        )
        assert result.kind == "reject"

    @pytest.mark.asyncio
    async def test_git_reset_hard_denied_when_no_infra(self) -> None:
        adapter = CopilotAdapter(approval_service=None)
        config = _make_config()

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard HEAD"),
            {"session_id": ""},
            config,
        )
        assert result.kind == "reject"

    @pytest.mark.asyncio
    async def test_normal_shell_in_auto_mode_not_affected(self) -> None:
        """Regular shell commands still go through normal path (policy router)."""
        adapter, approval_service = self._make_adapter_with_approval()
        _install_auto_approve_router(adapter, "job-1")
        config = _make_config()

        result = await adapter._handle_permission_request(
            self._make_request("git status"),
            self._invocation(),
            config,
        )
        # In auto mode, non-git-reset-hard commands are approved without hitting approval service
        assert result.kind == "approve-once"
        approval_service.create_request.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: pause_tools / resume_tools — tool blocking
# ---------------------------------------------------------------------------


class TestPauseTools:
    _SESSION_ID = "test-session-pause"

    @pytest.mark.asyncio
    async def test_paused_session_denies_all_tools(self) -> None:
        """When a session is paused, all permission requests are immediately denied."""
        adapter = CopilotAdapter()
        adapter.set_job_id(self._SESSION_ID, "job-pause")
        adapter.pause_tools(self._SESSION_ID)

        config = _make_config()

        for kind in ("shell", "write", "url"):
            request = _FakePermissionRequest(kind=kind, full_command_text="echo hello")
            result = await adapter._handle_permission_request(
                request,
                {"session_id": self._SESSION_ID},
                config,
            )
            assert result.kind == "reject", f"{kind} should be denied while paused"

    @pytest.mark.asyncio
    async def test_resume_tools_lifts_block(self) -> None:
        """After resume_tools, permission requests go through the normal path."""
        adapter = CopilotAdapter()
        adapter.set_job_id(self._SESSION_ID, "job-pause")
        _install_auto_approve_router(adapter, "job-pause")

        adapter.pause_tools(self._SESSION_ID)
        adapter.resume_tools(self._SESSION_ID)

        config = _make_config()
        request = _FakePermissionRequest(kind="shell", full_command_text="echo hello")
        result = await adapter._handle_permission_request(
            request,
            {"session_id": self._SESSION_ID},
            config,
        )
        assert result.kind == "approve-once"

    def test_cleanup_clears_paused_state(self) -> None:
        """_cleanup_session removes the session from the paused set."""
        adapter = CopilotAdapter()
        adapter.pause_tools(self._SESSION_ID)
        assert self._SESSION_ID in adapter._paused_sessions

        adapter._cleanup_session(self._SESSION_ID)
        assert self._SESSION_ID not in adapter._paused_sessions
