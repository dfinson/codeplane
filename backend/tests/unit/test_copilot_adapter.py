"""Unit tests for backend.services.copilot_adapter — CopilotAdapter.

All ``copilot`` SDK imports are mocked so the tests run without the SDK installed.
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
    PermissionMode,
    SessionConfig,
    SessionEvent,
    SessionEventKind,
)

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

    async def send(self, payload: dict[str, Any]) -> None:
        self._send_calls.append(payload)

    async def abort(self) -> None:
        self._aborted = True

    def fire_event(self, event: _FakeSdkSessionEvent) -> None:
        """Helper to simulate SDK firing an event."""
        for cb in self._callbacks:
            cb(event)


class _FakeCopilotClient:
    """Mimics ``copilot.CopilotClient``."""

    def __init__(self) -> None:
        self._sessions: list[_FakeCopilotSession] = []

    async def create_session(self, config: Any) -> _FakeCopilotSession:
        session = _FakeCopilotSession()
        self._sessions.append(session)
        return session

    async def resume_session(self, session_id: str, config: Any) -> _FakeCopilotSession:
        session = _FakeCopilotSession(session_id=session_id)
        self._sessions.append(session)
        return session


def _build_fake_copilot_module() -> ModuleType:
    mod = ModuleType("copilot")
    mod.CopilotClient = _FakeCopilotClient
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
        "permission_mode": PermissionMode.auto,
    }
    defaults.update(overrides)
    return SessionConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests: CopilotAdapter internals
# ---------------------------------------------------------------------------


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

        e1 = SessionEvent(kind=SessionEventKind.transcript, payload={"role": "agent", "content": "hi"})
        e2 = SessionEvent(kind=SessionEventKind.done, payload={})
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
        assert collected[0].kind == SessionEventKind.error

    @pytest.mark.asyncio
    async def test_timeout_yields_error(self, adapter: CopilotAdapter) -> None:
        sid = "sess-1"
        adapter._queues[sid] = asyncio.Queue()

        with patch("asyncio.wait_for", side_effect=TimeoutError):
            collected = []
            async for event in adapter.stream_events(sid):
                collected.append(event)

            assert any(e.kind == SessionEventKind.error for e in collected)

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
        assert session._send_calls[0]["prompt"] == "follow up"

    @pytest.mark.asyncio
    async def test_send_to_missing_session(self, adapter: CopilotAdapter) -> None:
        await adapter.send_message("nonexistent", "hello")  # should not raise


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
        session.abort = AsyncMock(side_effect=RuntimeError("abort fail"))
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
            async def resume_session(self, session_id: str, config: Any) -> _FakeCopilotSession:
                raise RuntimeError("session expired")

        fake_client = _FailingResumeClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        # Fell back to create_session, so ID won't match the resume ID
        assert session_id in adapter._sessions

    @pytest.mark.asyncio
    async def test_create_session_send_failure_cleans_up(self, adapter: CopilotAdapter) -> None:
        config = _make_config()

        class _FailingSendSession(_FakeCopilotSession):
            async def send(self, payload: dict[str, Any]) -> None:
                raise RuntimeError("send failed")

        class _FailClient(_FakeCopilotClient):
            async def create_session(self, cfg: Any) -> _FailingSendSession:
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
        return session_id, queue, session

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
        session.fire_event(_FakeSdkSessionEvent("assistant.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind == SessionEventKind.transcript]
        assert len(transcripts) == 1
        assert transcripts[0].payload["role"] == "agent"
        assert transcripts[0].payload["content"] == "Hello from agent"

    @pytest.mark.asyncio
    async def test_empty_assistant_message_skipped(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="   ", title=None, turn_id=None)
        session.fire_event(_FakeSdkSessionEvent("assistant.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind == SessionEventKind.transcript]
        assert len(transcripts) == 0

    @pytest.mark.asyncio
    async def test_user_message_emits_transcript(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="user says hi", message=None)
        session.fire_event(_FakeSdkSessionEvent("user.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind == SessionEventKind.transcript]
        assert len(transcripts) == 1
        assert transcripts[0].payload["role"] == "operator"

    @pytest.mark.asyncio
    async def test_system_notification_user_message_skipped(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(content="<system_notification>done</system_notification>", message=None)
        session.fire_event(_FakeSdkSessionEvent("user.message", data))

        events = self._drain_queue(queue)
        transcripts = [e for e in events if e.kind == SessionEventKind.transcript]
        assert len(transcripts) == 0

    @pytest.mark.asyncio
    async def test_task_complete_emits_done(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        session.fire_event(_FakeSdkSessionEvent("session.task_complete", data))

        events = self._drain_queue(queue)
        done_events = [e for e in events if e.kind == SessionEventKind.done]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_session_error_emits_error_and_sentinel(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        session.fire_event(_FakeSdkSessionEvent("session.error", data))

        # Drain including None sentinel
        all_items = []
        while not queue.empty():
            all_items.append(queue.get_nowait())

        real_events = [e for e in all_items if e is not None]
        error_events = [e for e in real_events if e.kind == SessionEventKind.error]
        assert len(error_events) == 1
        # Sentinel should be present
        assert None in all_items

    @pytest.mark.asyncio
    async def test_session_idle_emits_done(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        session.fire_event(_FakeSdkSessionEvent("session.idle", data))

        events = self._drain_queue(queue)
        done_events = [e for e in events if e.kind == SessionEventKind.done]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_session_shutdown_emits_done(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        session.fire_event(_FakeSdkSessionEvent("session.shutdown", data))

        events = self._drain_queue(queue)
        done_events = [e for e in events if e.kind == SessionEventKind.done]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_file_changed_emits_file_changed(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(file_path="/tmp/workspace/main.py")
        session.fire_event(_FakeSdkSessionEvent("session.workspace_file_changed", data))

        events = self._drain_queue(queue)
        fc = [e for e in events if e.kind == SessionEventKind.file_changed]
        assert len(fc) == 1

    @pytest.mark.asyncio
    async def test_unknown_event_type_ignored(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData()
        session.fire_event(_FakeSdkSessionEvent("internal.something", data))

        events = self._drain_queue(queue)
        # Only log events (from tool started etc.) might appear, no mapped event
        mapped = [
            e for e in events if e.kind in (SessionEventKind.transcript, SessionEventKind.done, SessionEventKind.error)
        ]
        assert len(mapped) == 0


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
        return session_id, queue, session

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
        )

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("assistant.usage", data))

            mock_tel.record_llm_usage.assert_called_once_with(
                "job-tel",
                model="gpt-4o",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=10,
                cache_write_tokens=5,
                cost=0.002,
                duration_ms=1500.0,
            )

    @pytest.mark.asyncio
    async def test_assistant_usage_model_mismatch(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-mismatch", model="gpt-4o")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]

        data = _FakeEventData(
            model="gpt-3.5-turbo",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.0001,
            duration=100,
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("assistant.usage", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        downgraded = [e for e in events if e.kind == SessionEventKind.model_downgraded]
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

        data = _FakeEventData(
            model="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.001,
            duration=200,
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("assistant.usage", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        downgraded = [e for e in events if e.kind == SessionEventKind.model_downgraded]
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

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("tool.execution_start", start_data))

            # Verify start time recorded
            assert "tc-1" in adapter._tool_start_times
            assert "tc-1" in adapter._tool_call_buffer

            # Now fire completion
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
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", complete_data))

            mock_tel.record_tool_call.assert_called_once()
            call_args = mock_tel.record_tool_call.call_args
            assert call_args[1]["tool_name"] == "bash" or call_args[0][1] == "bash"

    @pytest.mark.asyncio
    @patch("backend.services.tool_formatters.format_tool_display", return_value="bash: ls")
    async def test_tool_execution_complete_emits_transcript(self, mock_fmt: MagicMock, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        # Buffer the tool start
        adapter._tool_call_buffer["tc-2"] = {
            "tool_name": "bash",
            "tool_args": '{"command": "ls"}',
            "turn_id": "t1",
            "tool_intent": "",
            "tool_title": "",
        }
        adapter._tool_start_times["tc-2"] = time.monotonic() - 0.5

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

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", complete_data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        transcripts = [
            e for e in events if e.kind == SessionEventKind.transcript and e.payload.get("role") == "tool_call"
        ]
        assert len(transcripts) == 1
        assert transcripts[0].payload["tool_name"] == "bash"
        assert transcripts[0].payload["tool_success"] is True

    @pytest.mark.asyncio
    async def test_report_intent_tool_skipped_from_transcript(self, adapter: CopilotAdapter) -> None:
        """report_intent tool calls should not appear in transcript."""
        sid, queue, session = await self._setup_session_with_job(adapter)

        adapter._tool_call_buffer["tc-ri"] = {
            "tool_name": "report_intent",
            "tool_args": '{"intent": "testing"}',
            "turn_id": "t1",
            "tool_intent": "",
            "tool_title": "",
        }
        adapter._tool_start_times["tc-ri"] = time.monotonic()

        complete_data = _FakeEventData(
            tool_call_id="tc-ri",
            tool_name="report_intent",
            mcp_tool_name=None,
            success=True,
            result=None,
            partial_output=None,
            turn_id="t1",
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", complete_data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        transcripts = [
            e for e in events if e.kind == SessionEventKind.transcript and e.payload.get("role") == "tool_call"
        ]
        assert len(transcripts) == 0

    @pytest.mark.asyncio
    async def test_context_changed(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(current_tokens=5000)

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("session.context_changed", data))

            mock_tel.record_context_change.assert_called_once_with("job-tel", current_tokens=5000)

    @pytest.mark.asyncio
    async def test_compaction_complete(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(pre_compaction_tokens=10000, post_compaction_tokens=3000)

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("session.compaction_complete", data))

            mock_tel.record_compaction.assert_called_once_with("job-tel", pre_tokens=10000, post_tokens=3000)
            # Also records a context change for the post-compaction token count
            mock_tel.record_context_change.assert_called_with("job-tel", current_tokens=3000)

    @pytest.mark.asyncio
    async def test_session_truncation(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(token_limit=128000)

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("session.truncation", data))

            mock_tel.record_context_change.assert_called_once_with("job-tel", window_size=128000)

    @pytest.mark.asyncio
    async def test_session_model_change(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(new_model="gpt-4o-mini")

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("session.model_change", data))

            mock_tel.set_main_model.assert_called_once_with("job-tel", "gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_assistant_message_records_telemetry(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(content="hello", title=None, turn_id=None)

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("assistant.message", data))

            mock_tel.record_message.assert_called_once_with("job-tel", role="agent")

    @pytest.mark.asyncio
    async def test_user_message_records_telemetry(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session_with_job(adapter)

        data = _FakeEventData(content="user prompt", message=None)

        with patch("backend.services.telemetry.collector") as mock_tel:
            session.fire_event(_FakeSdkSessionEvent("user.message", data))

            mock_tel.record_message.assert_called_once_with("job-tel", role="operator")


# ---------------------------------------------------------------------------
# Tests: Log event emission from SDK events
# ---------------------------------------------------------------------------


class TestLogEvents:
    @pytest.mark.asyncio
    async def _setup_session(self, adapter: CopilotAdapter) -> tuple[str, asyncio.Queue[SessionEvent | None], Any]:
        config = _make_config(job_id="job-log")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        return session_id, adapter._queues[session_id], adapter._sessions[session_id]

    def _drain_queue(self, q: asyncio.Queue[SessionEvent | None]) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        while not q.empty():
            e = q.get_nowait()
            if e is not None:
                events.append(e)
        return events

    @pytest.mark.asyncio
    async def test_tool_start_emits_log(self, adapter: CopilotAdapter) -> None:
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

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        assert any("Tool started" in e.payload.get("message", "") for e in log_events)

    @pytest.mark.asyncio
    async def test_tool_complete_emits_log(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        adapter._tool_start_times["tc-log"] = time.monotonic()
        adapter._tool_call_buffer["tc-log"] = {
            "tool_name": "grep",
            "tool_args": "",
            "turn_id": "",
            "tool_intent": "",
            "tool_title": "",
        }

        data = _FakeEventData(
            tool_call_id="tc-log",
            tool_name="grep",
            mcp_tool_name=None,
            success=True,
            result=None,
            partial_output=None,
            turn_id=None,
        )

        with (
            patch("backend.services.telemetry.collector"),
            patch("backend.services.tool_formatters.format_tool_display", return_value="grep: ok"),
        ):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        assert any("Tool completed" in e.payload.get("message", "") for e in log_events)

    @pytest.mark.asyncio
    async def test_tool_complete_failed_emits_warn(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        adapter._tool_start_times["tc-fail"] = time.monotonic()
        adapter._tool_call_buffer["tc-fail"] = {
            "tool_name": "bash",
            "tool_args": "",
            "turn_id": "",
            "tool_intent": "",
            "tool_title": "",
        }

        data = _FakeEventData(
            tool_call_id="tc-fail",
            tool_name="bash",
            mcp_tool_name=None,
            success=False,
            result=None,
            partial_output=None,
            turn_id=None,
        )

        with (
            patch("backend.services.telemetry.collector"),
            patch("backend.services.tool_formatters.format_tool_display", return_value="bash: failed"),
        ):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        failed_logs = [e for e in log_events if "failed" in e.payload.get("message", "")]
        assert len(failed_logs) >= 1
        assert any(e.payload.get("level") == "warn" for e in failed_logs)

    @pytest.mark.asyncio
    async def test_assistant_usage_log_normal(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.01,
            duration=500,
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("assistant.usage", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        assert any("LLM call" in e.payload.get("message", "") for e in log_events)

    @pytest.mark.asyncio
    async def test_assistant_usage_model_mismatch_error_log(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-mm-log", model="gpt-4o")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]

        data = _FakeEventData(
            model="gpt-3.5-turbo",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=0.001,
            duration=100,
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("assistant.usage", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        log_events = [e for e in events if e.kind == SessionEventKind.log]
        mismatch_logs = [e for e in log_events if "MISMATCH" in e.payload.get("message", "")]
        assert len(mismatch_logs) >= 1
        assert mismatch_logs[0].payload["level"] == "error"

    @pytest.mark.asyncio
    async def test_compaction_log(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(pre_compaction_tokens=8000, post_compaction_tokens=2000)

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("session.compaction_complete", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        assert any("compacted" in e.payload.get("message", "").lower() for e in log_events)

    @pytest.mark.asyncio
    async def test_model_change_log(self, adapter: CopilotAdapter) -> None:
        sid, queue, session = await self._setup_session(adapter)

        data = _FakeEventData(new_model="gpt-4o-mini")

        with patch("backend.services.telemetry.collector") as mock_tel:
            mock_tel.get.return_value = MagicMock()
            session.fire_event(_FakeSdkSessionEvent("session.model_change", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        assert any("Model changed" in e.payload.get("message", "") for e in log_events)

    @pytest.mark.asyncio
    async def test_mcp_tool_display_name(self, adapter: CopilotAdapter) -> None:
        """MCP tools should display as server/tool_name in logs."""
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

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))

        events = self._drain_queue(queue)
        log_events = [e for e in events if e.kind == SessionEventKind.log]
        assert any("github/search_code" in e.payload.get("message", "") for e in log_events)

        # Also verify it was buffered correctly
        assert adapter._tool_call_buffer["tc-mcp"]["tool_name"] == "github/search_code"


# ---------------------------------------------------------------------------
# Tests: complete (single-turn)
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_collects_response(self, adapter: CopilotAdapter) -> None:
        fake_session = _FakeCopilotSession()
        collected_content = "Generated response"

        class _FakeCompleteClient:
            async def create_session(self, config: Any) -> _FakeCopilotSession:
                return fake_session

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

        assert result is not None
        assert collected_content in result

    @pytest.mark.asyncio
    async def test_complete_handles_exception(self, adapter: CopilotAdapter) -> None:
        class _FailingClient:
            async def create_session(self, config: Any) -> None:
                raise RuntimeError("boom")

        with patch("copilot.CopilotClient", return_value=_FailingClient()):
            result = await adapter.complete("test")

        assert result is None

    @pytest.mark.asyncio
    async def test_complete_timeout(self, adapter: CopilotAdapter) -> None:
        fake_session = _FakeCopilotSession()

        class _TimeoutClient:
            async def create_session(self, config: Any) -> _FakeCopilotSession:
                return fake_session

        with (
            patch("copilot.CopilotClient", return_value=_TimeoutClient()),
            patch("asyncio.wait_for", side_effect=TimeoutError),
        ):
            result = await adapter.complete("test prompt")

        assert result == ""


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

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))

        buf = adapter._tool_call_buffer["tc-desc"]
        assert buf["tool_intent"] == "List files"

    @pytest.mark.asyncio
    async def test_arguments_string_buffered(self, adapter: CopilotAdapter) -> None:
        """When arguments is already a string, it should be stored as-is."""
        config = _make_config(job_id="job-str")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        session = adapter._sessions[session_id]

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

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))

        buf = adapter._tool_call_buffer["tc-str"]
        assert buf["tool_args"] == '{"path": "/tmp/foo"}'
        assert buf["tool_intent"] == "Read a file"
        assert buf["tool_title"] == "read_file"

    @pytest.mark.asyncio
    async def test_arguments_none_buffered_as_empty(self, adapter: CopilotAdapter) -> None:
        config = _make_config(job_id="job-none")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        session = adapter._sessions[session_id]

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

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_start", data))

        buf = adapter._tool_call_buffer["tc-none"]
        assert buf["tool_args"] == ""


# ---------------------------------------------------------------------------
# Tests: tool_execution_complete result extraction edge cases
# ---------------------------------------------------------------------------


class TestToolResultExtraction:
    @pytest.mark.asyncio
    @patch("backend.services.tool_formatters.format_tool_display", return_value="tool: ok")
    async def test_partial_output_fallback(self, mock_fmt: MagicMock, adapter: CopilotAdapter) -> None:
        """When result.content is empty, partial_output should be used."""
        config = _make_config(job_id="job-partial")
        fake_client = _FakeCopilotClient()

        with patch("copilot.CopilotClient", return_value=fake_client):
            session_id = await adapter.create_session(config)

        queue = adapter._queues[session_id]
        session = adapter._sessions[session_id]

        adapter._tool_call_buffer["tc-po"] = {
            "tool_name": "bash",
            "tool_args": "",
            "turn_id": "",
            "tool_intent": "",
            "tool_title": "",
        }
        adapter._tool_start_times["tc-po"] = time.monotonic()

        data = _FakeEventData(
            tool_call_id="tc-po",
            tool_name="bash",
            mcp_tool_name=None,
            success=True,
            result=SimpleNamespace(content=None),
            partial_output="partial result text",
            turn_id=None,
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        transcripts = [
            e for e in events if e.kind == SessionEventKind.transcript and e.payload.get("role") == "tool_call"
        ]
        assert len(transcripts) == 1
        assert transcripts[0].payload["tool_result"] == "partial result text"

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

        adapter._tool_call_buffer["tc-sr"] = {
            "tool_name": "grep",
            "tool_args": "",
            "turn_id": "",
            "tool_intent": "",
            "tool_title": "",
        }
        adapter._tool_start_times["tc-sr"] = time.monotonic()

        data = _FakeEventData(
            tool_call_id="tc-sr",
            tool_name="grep",
            mcp_tool_name=None,
            success=True,
            result=SimpleNamespace(content="plain string result"),
            partial_output=None,
            turn_id=None,
        )

        with patch("backend.services.telemetry.collector"):
            session.fire_event(_FakeSdkSessionEvent("tool.execution_complete", data))

        events = []
        while not queue.empty():
            e = queue.get_nowait()
            if e is not None:
                events.append(e)

        transcripts = [
            e for e in events if e.kind == SessionEventKind.transcript and e.payload.get("role") == "tool_call"
        ]
        assert len(transcripts) == 1
        assert transcripts[0].payload["tool_result"] == "plain string result"


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
        config = _make_config(permission_mode=PermissionMode.auto)

        approval = MagicMock()
        approval.id = "apr-1"
        approval_service.create_request = AsyncMock(return_value=approval)
        approval_service.wait_for_resolution = AsyncMock(return_value="approved")

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard HEAD"),
            self._invocation(),
            config,
        )
        assert result.kind == "approved"
        approval_service.create_request.assert_called_once()
        call_kwargs = approval_service.create_request.call_args.kwargs
        assert call_kwargs["requires_explicit_approval"] is True

    @pytest.mark.asyncio
    async def test_git_reset_hard_cannot_be_bypassed_by_trust(self) -> None:
        """Trust grant must NOT bypass the git reset --hard block."""
        adapter, approval_service = self._make_adapter_with_approval()
        approval_service.is_trusted = MagicMock(return_value=True)  # trusted job
        config = _make_config(permission_mode=PermissionMode.auto)

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
        assert result.kind == "approved"
        approval_service.create_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_git_reset_hard_rejected_by_operator(self) -> None:
        adapter, approval_service = self._make_adapter_with_approval()
        config = _make_config(permission_mode=PermissionMode.auto)

        approval = MagicMock()
        approval.id = "apr-2"
        approval_service.create_request = AsyncMock(return_value=approval)
        approval_service.wait_for_resolution = AsyncMock(return_value="rejected")

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard HEAD"),
            self._invocation(),
            config,
        )
        assert result.kind == "denied-interactively-by-user"

    @pytest.mark.asyncio
    async def test_git_reset_hard_denied_when_no_infra(self) -> None:
        adapter = CopilotAdapter(approval_service=None)
        config = _make_config(permission_mode=PermissionMode.auto)

        result = await adapter._handle_permission_request(
            self._make_request("git reset --hard HEAD"),
            {"session_id": ""},
            config,
        )
        assert result.kind == "denied-interactively-by-user"

    @pytest.mark.asyncio
    async def test_normal_shell_in_auto_mode_not_affected(self) -> None:
        """Regular shell commands in auto mode still go through normal path."""
        adapter, approval_service = self._make_adapter_with_approval()
        config = _make_config(permission_mode=PermissionMode.auto)

        result = await adapter._handle_permission_request(
            self._make_request("git status"),
            self._invocation(),
            config,
        )
        # In auto mode, non-git-reset-hard commands are approved without hitting approval service
        assert result.kind == "approved"
        approval_service.create_request.assert_not_called()
