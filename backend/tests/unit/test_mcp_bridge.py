import asyncio
import socket
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jsonschema
import pytest
import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from sse_starlette.sse import AppStatus, _get_shutdown_state

from backend.models.session_event import EventMetadata
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.native_gates import GateAttachment, NativeBinding
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.services.adapters.profile_bridges.mcp import McpTarget, install

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.services.adapters.profile_bridges.mcp import McpObservation


@pytest.fixture
async def mcp_peer():
    previous_exit = AppStatus.should_exit
    AppStatus.should_exit = False
    peer = FastMCP("CodePlane fixture", json_response=True)
    calls = []
    entered, finished = asyncio.Event(), asyncio.Event()

    @peer.tool()
    async def read_value(value: str, ctx: Context) -> str:
        calls.append((value, ctx.request_id))
        if value == "wait":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        return value

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(peer.streamable_http_app(), log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        schema = peer._tool_manager.get_tool("read_value").parameters
        yield f"http://127.0.0.1:{port}/mcp", schema, calls, entered, finished
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
        AppStatus.should_exit = True
        async with asyncio.timeout(2):
            while _get_shutdown_state().watcher_started:
                await asyncio.sleep(0.01)
        AppStatus.should_exit = previous_exit


def _target(endpoint, schema, observations):
    from agentmesh.identity import AgentIdentity
    from agentmesh.integrations.ai_card import AICard, AICardService

    identity = AgentIdentity.create(name="mcp-peer", sponsor="fixture@example.test", capabilities=["tool:read"])
    card = AICard.from_identity(identity, services=[AICardService(protocol="mcp", url=endpoint)])
    card.expires_at = datetime.now(UTC) + timedelta(hours=1)
    card.sign(identity)
    return McpTarget(
        endpoint,
        "project",
        card.model_dump_json().encode(),
        str(identity.did),
        identity.public_key,
        {"read_value": schema},
        observations.append,
    )


async def test_real_mcp_session_schema_gate_identity_correlation_and_cancellation(mcp_peer):
    endpoint, schema, calls, entered, finished = mcp_peer
    observations: list[McpObservation] = []
    receipts = []

    class Policy:
        execution_epoch = "epoch"
        allowed = False

        async def authorize_permission(self, invocation):
            return self.allowed

    async def current():
        return True

    async def report(call, started, succeeded):
        receipts.append((call, started, succeeded))

    policy = Policy()
    target = _target(endpoint, schema, observations)
    binding = NativeBinding(
        family="mcp",
        execution=ExecutionIdentity("job", "native:token", "epoch", "token"),
        policy=policy,
        metadata=EventMetadata(execution_epoch="epoch", execution_token="token"),
        workspace="/fixture",
        protected_paths=(),
        is_current=current,
        on_result=report,
    )
    raw_records: list[Mapping[str, Any]] = []
    binding._observation = raw_records.append
    attachment = GateAttachment(binding, target)
    install(attachment, target)
    try:
        with pytest.raises(jsonschema.ValidationError):
            await target.call_tool("read_value", {"value": "a", "unexpected": "must-not-be-dropped"})
        with pytest.raises(NativeGateDeniedError):
            await target.call_tool("read_value", {"value": "a"})
        assert calls == [] and receipts == []
        policy.allowed = True
        result = await target.call_tool("read_value", {"value": "actual-handler"})
        assert not result.isError and result.content[0].text == "actual-handler"
        assert calls == [("actual-handler", observations[0].request_id)]
        assert receipts[0][1:] == (True, True)
        pending = asyncio.create_task(target.call_tool("read_value", {"value": "wait"}))
        await asyncio.wait_for(entered.wait(), 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await asyncio.wait_for(finished.wait(), 5)
        assert observations[-1].outcome == "cancel-acknowledged"
        assert receipts[-1][1:] == (None, None)
        assert raw_records[-1]["outcome"] == "cancel-acknowledged"
    finally:
        attachment.detach()
    with pytest.raises(PermissionError, match="attached"):
        await target.call_tool("read_value", {"value": "detached"})


async def test_mcp_peer_identity_must_match_key_and_independent_endpoint_before_transport(mcp_peer):
    endpoint, schema, calls, _, _ = mcp_peer
    target = _target(endpoint, schema, [])
    target.endpoint += "/changed"

    class Binding:
        execution = ExecutionIdentity("job", "native:token", "epoch", "token")

    with pytest.raises(ValueError, match="endpoints"):
        install(GateAttachment(Binding(), target), target)
    assert calls == []
