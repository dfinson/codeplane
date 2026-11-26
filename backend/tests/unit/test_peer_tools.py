import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.domain import SessionConfig, StateConflictError
from backend.services.adapters.agent_adapter import AgentAdapterInterface
from backend.services.copilot_adapter._peer_tools import build_peer_tools
from backend.services.runtime.attempt_bindings import AttemptBindings
from backend.services.runtime.service import AgentSession, RuntimeService
from backend.services.runtime.verify import run_followup_turn


@pytest.fixture
def native_sdk_modules():
    # Other adapter tests install SDK stubs globally during collection.
    with patch.dict(sys.modules):
        for name in list(sys.modules):
            if name == "copilot" or name.startswith("copilot."):
                del sys.modules[name]
        yield


async def test_sdk_tool_schema_and_handler_keep_sender_server_bound(native_sdk_modules):
    from copilot.tools import ToolInvocation

    directory = AsyncMock(return_value={"peers": []})
    send = AsyncMock(return_value={"status": "accepted"})
    config = SessionConfig(
        workspace_path="/repo", prompt="Task", peer_directory_handler=directory, peer_message_handler=send
    )
    list_tool, send_tool = build_peer_tools(config)
    assert "senderJobId" not in send_tool.parameters["properties"]
    assert "origin" not in send_tool.parameters["properties"]
    assert not send_tool.skip_permission
    result = await send_tool.handler(
        ToolInvocation(
            arguments={
                "messageId": "unique-id",
                "recipientJobId": "target",
                "content": "Ready",
                "origin": "operator",
                "senderJobId": "spoof",
            }
        )
    )
    assert json.loads(result.text_result_for_llm)["status"] == "accepted"
    send.assert_awaited_once_with("unique-id", "target", "Ready", None, None, None)
    await list_tool.handler(ToolInvocation(arguments={"offset": 50}))
    directory.assert_awaited_once_with(50, None, "")


async def test_strict_adapter_send_enqueues_and_propagates_failures():
    from backend.services.copilot_adapter._adapter import CopilotAdapter

    adapter = CopilotAdapter()
    session = MagicMock()
    session.send = AsyncMock()
    adapter._sessions = {"sdk": session}
    await adapter.send_peer_message("sdk", "note")
    session.send.assert_awaited_once_with("note", mode="enqueue")
    session.send.side_effect = TimeoutError("no response")
    with pytest.raises(TimeoutError):
        await adapter.send_peer_message("sdk", "note")
    with pytest.raises(StateConflictError):
        await adapter.send_peer_message("missing", "note")


async def test_peer_send_preserves_pause_and_rejects_missing_native_session():
    session = AgentSession()
    session._adapter = AsyncMock(spec=AgentAdapterInterface)
    with pytest.raises(StateConflictError):
        await session.send_peer_message("note")
    session._session_id = "sdk"
    session.pause_tools()
    with pytest.raises(StateConflictError):
        await session.send_peer_message("note")
    assert session.tools_paused
    session._adapter.send_peer_message.assert_not_called()
    session._adapter.resume_tools.assert_not_called()


def test_current_peer_session_excludes_paused_gated_and_finished_executions():
    host = MagicMock(spec=RuntimeService)
    task = MagicMock()
    task.done.return_value = False
    task.cancelling.return_value = 0
    target = AgentSession()
    target._session_id = "sdk"
    host._tasks = {"job": task}
    host._agent_sessions = {"job": target}
    host._active_gates = {}
    host._waiting_for_approval = set()
    host._attempt_bindings = AttemptBindings("test")
    host._recipe_controllers = {}
    assert RuntimeService.current_peer_session(host, "job") is target
    target.tools_paused = True
    assert RuntimeService.current_peer_session(host, "job") is None
    target.tools_paused = False
    host._active_gates["job"] = object()
    assert RuntimeService.current_peer_session(host, "job") is None
    host._active_gates.clear()
    task.done.return_value = True
    assert RuntimeService.current_peer_session(host, "job") is None


async def test_each_execution_attempt_rebinds_peer_tools_including_resume_fallback():
    host = MagicMock(spec=RuntimeService)
    host._session_factory = MagicMock()
    host._execution_epoch = "test"
    host._with_peer_callbacks.side_effect = lambda config, job_id, execution: RuntimeService._with_peer_callbacks(
        host, config, job_id, execution
    )
    host.peer_execution_is_current.side_effect = lambda job_id, target: host.current_peer_session(job_id) is target
    captured = []

    async def execute(config, adapter):
        captured.append(config)
        if False:
            yield

    first, replacement = AgentSession(), AgentSession()
    first.execute = execute
    replacement.execute = execute
    config = SessionConfig(workspace_path="/repo", prompt="Task", job_id="job")
    await RuntimeService._execute_session_attempt(host, "job", first, config, None, None)
    await RuntimeService._execute_session_attempt(host, "job", replacement, captured[0], None, None)
    host.current_peer_session.return_value = replacement
    with patch(
        "backend.services.session_coordination.SessionCoordination.send",
        AsyncMock(return_value=MagicMock(model_dump=lambda **kwargs: {"status": "accepted"})),
    ) as send:
        await captured[1].peer_message_handler("id", "target", "Note", None, None, None)
    assert send.call_args.kwargs["sender_execution"] is replacement
    with pytest.raises(StateConflictError):
        await captured[0].peer_directory_handler(0, None, "")


async def test_verification_drops_main_execution_peer_tools():
    host = MagicMock(spec=RuntimeService)
    host._echo_suppress = {}
    host._agent_sessions = {}
    configs = []

    async def execute(self, config, adapter):
        configs.append(config)
        if False:
            yield

    base = SessionConfig(
        workspace_path="/repo",
        prompt="Task",
        peer_directory_handler=AsyncMock(),
        peer_message_handler=AsyncMock(),
    )
    with patch.object(AgentSession, "execute", execute):
        await run_followup_turn(host, "job", "Verify", base, None, None, None)
    assert configs[0].peer_directory_handler is None
    assert configs[0].peer_message_handler is None
