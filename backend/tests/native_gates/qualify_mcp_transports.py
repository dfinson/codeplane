"""Actual MCP stdio/client and authenticated HTTP/server boundaries on real Runtime Jobs."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uvicorn
from agentmesh.identity import AgentIdentity
from agentmesh.integrations.ai_card import AICard, AICardService
from agentmesh.integrations.mcp import TrustGatedMCPServer
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.exceptions import McpError
from sse_starlette.sse import AppStatus, _get_shutdown_state

from backend.services.adapters.profile_bridges.mcp import McpCallerAuthority, McpServerTarget, McpTarget
from backend.services.ingest.mapping import canonical_record
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

if TYPE_CHECKING:
    from backend.services.adapters.profile_bridges.mcp import McpObservation


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original = socket.socket.connect

    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] != "127.0.0.1":
            raise AssertionError("MCP qualification permits only its literal loopback fixture")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")


def card(identity, endpoint):
    value = AICard.from_identity(identity, services=[AICardService(protocol="mcp", url=endpoint)])
    value.expires_at = datetime.now(UTC) + timedelta(hours=1)
    value.sign(identity)
    return value.model_dump_json().encode()


@pytest.mark.parametrize("outcome", ["allow", "deny", "extra-argument", "cancel"])
async def test_real_stdio_operation_uses_runtime_gate_and_native_cancellation(composed_runtime, tmp_path, outcome):
    host, approvals, governance, create = composed_runtime
    job = await create()
    peer = FastMCP("schema")

    @peer.tool()
    async def read_value(value: str, ctx: Context) -> str:
        return value

    identity = AgentIdentity.create(name="stdio-peer", sponsor="fixture@example.test", capabilities=["tool:read"])
    endpoint = "https://stdio-peer.invalid/mcp"
    record = tmp_path / "stdio-calls.jsonl"
    observations: list[McpObservation] = []
    target = McpTarget(
        endpoint,
        "project",
        card(identity, endpoint),
        str(identity.did),
        identity.public_key,
        {"read_value": peer._tool_manager.get_tool("read_value").parameters},
        observations.append,
        stdio=StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).parents[1] / "fixtures" / "mcp_stdio_peer.py")],
            env={"CODEPLANE_MCP_FIXTURE_RECORD": str(record)},
        ),
    )

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "rejected" if outcome == "deny" else "approved")

    host._event_bus.subscribe(approve)
    payload = {"value": "wait" if outcome == "cancel" else "inert"}
    if outcome == "extra-argument":
        payload["extra"] = "must-not-be-dropped"
    task = await host.start_native(
        job.id, family="mcp", target=target, invoke=lambda: target.call_tool("read_value", payload)
    )
    try:
        if outcome == "cancel":
            async with asyncio.timeout(10):
                while not record.exists() or not any(
                    json.loads(line)["kind"] == "entered" for line in record.read_text().splitlines()
                ):
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observations[-1].outcome == "cancel-acknowledged"
        elif outcome in {"deny", "extra-argument"}:
            with pytest.raises(Exception) as rejected:
                await task
            assert type(rejected.value).__name__ in {"NativeGateDeniedError", "ValidationError"}
            assert not record.exists()
        else:
            result = await task
            assert not result.isError and result.content[0].text == "inert"
        rows = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
        if rows:
            assert rows[0]["kind"] == "started"
            assert rows[1]["request_id"] == observations[0].request_id
            assert rows[1]["kind"] == "entered"
            if outcome == "cancel":
                assert rows[-1]["kind"] == "cancelled"
        observed = [
            event for event in await events_for(host, job.id) if event.metadata.integration_binding_id is not None
        ]
        if outcome == "allow":
            assert any(event.kind == "tool.call.completed" for event in observed)
        assert (await governance._ledger.job_totals(job.id)).executed_calls == int(outcome == "allow")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "synchronous,outcome",
    [
        (False, outcome)
        for outcome in (
            "allow",
            "deny",
            "self-asserted",
            "wrong-key",
            "cross-binding",
            "replay",
            "extra-argument",
            "capability",
            "low-trust",
            "mutated",
            "cancel",
            "client-bridge",
            "handler-error",
        )
    ]
    + [(True, outcome) for outcome in ("allow", "deny", "cancel")],
)
async def test_real_mcp_server_requires_scoped_identity_before_native_handler(composed_runtime, synchronous, outcome):
    host, approvals, governance, create = composed_runtime
    job = await create()
    peer = FastMCP("trusted actual server", json_response=True)
    called, decisions = [], []
    entered, finished, release = threading.Event(), threading.Event(), threading.Event()

    if synchronous:

        @peer.tool()
        def read_value(value: str, ctx: Context) -> str:
            called.append((value, str(ctx.request_id)))
            if value == "wait":
                entered.set()
                try:
                    assert release.wait(10)
                finally:
                    finished.set()
            return value
    else:

        @peer.tool()
        async def read_value(value: str, ctx: Context) -> str:
            called.append((value, str(ctx.request_id)))
            if outcome == "handler-error":
                raise ValueError("private-handler-secret")
            if value == "wait":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()
            return value

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
    server_identity = AgentIdentity.create(name="server", sponsor="fixture@example.test", capabilities=[])
    caller = AgentIdentity.create(name="caller", sponsor="fixture@example.test", capabilities=["tool:read"])
    other = AgentIdentity.create(name="wrong-key", sponsor="fixture@example.test", capabilities=["tool:read"])
    native_tool = peer._tool_manager.get_tool("read_value")
    trust = TrustGatedMCPServer(server_identity)
    trust.register_tool("read_value", read_value, input_schema=native_tool.parameters, required_capability="tool:read")
    target = McpServerTarget(
        peer,
        trust,
        endpoint,
        "project",
        card(caller, endpoint),
        str(caller.did),
        caller.public_key,
        () if outcome == "capability" else ("tool:read",),
        100 if outcome == "low-trust" else 900,
    )
    original = native_tool.fn
    previous_exit = AppStatus.should_exit
    AppStatus.should_exit = False
    server = uvicorn.Server(uvicorn.Config(peer.streamable_http_app(), log_level="warning"))
    ready = asyncio.Event()

    async def approve(event):
        if event.kind == "permission.requested":
            decisions.append(event)
            if outcome == "mutated":
                trust._tools["read_value"].min_trust_score = 999
            await approvals.resolve(event.payload["approval_id"], "rejected" if outcome == "deny" else "approved")

    host._event_bus.subscribe(approve)

    async def serve():
        ready.set()
        await server.serve(sockets=[listener])

    task = await host.start_native(job.id, family="mcp", target=target, invoke=serve)
    try:
        async with asyncio.timeout(10):
            await ready.wait()
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        binding, epoch = await target.authority_scope()
        authority = McpCallerAuthority(other if outcome == "wrong-key" else caller, binding, epoch)
        async with (
            streamablehttp_client(endpoint) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            payload = {"value": "wait" if outcome == "cancel" else "inert"}
            if outcome == "extra-argument":
                payload["extra"] = "not-allowed"
            proof = authority.proof(endpoint, "read_value", payload, str(session._request_id))
            if outcome == "wrong-key":
                proof["caller_did"] = str(caller.did)
                proof["signature"] = other.sign(
                    canonical_record({key: value for key, value in proof.items() if key != "signature"})
                )
            if outcome == "cross-binding":
                proof = McpCallerAuthority(caller, "native:other-binding", epoch).proof(
                    endpoint, "read_value", payload, str(session._request_id)
                )
            meta = {"codeplane-authority": proof} if outcome != "self-asserted" else {"caller_did": str(caller.did)}
            client_observations: list[McpObservation] = []
            if outcome == "client-bridge":
                client_job = await create("client-job")
                client = McpTarget(
                    endpoint,
                    "project",
                    card(server_identity, endpoint),
                    str(server_identity.did),
                    server_identity.public_key,
                    {"read_value": native_tool.parameters},
                    client_observations.append,
                    authority=authority,
                )
                pending = await host.start_native(
                    client_job.id, family="mcp", target=client, invoke=lambda: client.call_tool("read_value", payload)
                )
            else:
                pending = asyncio.create_task(session.call_tool("read_value", payload, meta=meta))
            if outcome == "cancel":
                assert await asyncio.to_thread(entered.wait, 5)
                await session.send_notification(
                    types.ClientNotification(
                        types.CancelledNotification(
                            params=types.CancelledNotificationParams(requestId=int(proof["request_id"]))
                        )
                    )
                )
                if synchronous:
                    await asyncio.wait({pending}, timeout=0.05)
                    assert not finished.is_set() and not task.done()
                    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0
                    release.set()
                with pytest.raises(McpError, match="Request cancelled"):
                    await pending
                assert await asyncio.to_thread(finished.wait, 5)
            else:
                result = await pending
                assert result.isError is (outcome not in {"allow", "replay", "client-bridge"})
                assert "private-handler-secret" not in result.model_dump_json()
                if outcome == "replay":
                    session._request_id = int(proof["request_id"])
                    replay = await session.call_tool("read_value", payload, meta=meta)
                    assert replay.isError
            assert len(called) == int(outcome in {"allow", "replay", "cancel", "client-bridge", "handler-error"})
            if called:
                expected_id = client_observations[0].request_id if client_observations else proof["request_id"]
                assert called[0][1] == expected_id
            if outcome in {"self-asserted", "wrong-key", "cross-binding", "extra-argument"}:
                assert decisions == []
        server.should_exit = True
        await task
        assert native_tool.fn is original and trust.audit_all_calls is True
        assert (await governance._ledger.job_totals(job.id)).executed_calls == len(called)
        observed = [
            event for event in await events_for(host, job.id) if event.metadata.integration_binding_id is not None
        ]
        if called:
            assert any(event.payload.get("request_id") == called[0][1] for event in observed)
        if outcome == "cancel" and synchronous:
            terminal = next(event for event in observed if event.kind == "tool.call.completed")
            assert terminal.payload["outcome"] == "responded-after-cancel"
        assert not any("signature" in json.dumps(event.payload) for event in observed)
    finally:
        release.set()
        server.should_exit = True
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        listener.close()
        AppStatus.should_exit = True
        async with asyncio.timeout(2):
            while _get_shutdown_state().watcher_started:
                await asyncio.sleep(0.01)
        AppStatus.should_exit = previous_exit
