from __future__ import annotations

import asyncio
import json
import socket
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.models.domain import ApprovalResolution
from backend.models.events import EventKind
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.services.adapters.profile_bridges.a2a import (
    A2ADeliveryUnknownError,
    A2ARequestRefusedError,
    A2ATarget,
)
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

if TYPE_CHECKING:
    from backend.models.session_event import SessionEvent


@pytest.fixture
async def a2a_peer():
    app = FastAPI()
    calls: list[dict[str, Any]] = []
    state = {
        "value": "input-required",
        "drop": False,
        "refuse_cancel": False,
        "wrong_task": False,
        "stream_mode": "normal",
    }
    hold = asyncio.Event()
    hold.set()

    def task():
        return {
            "kind": "task",
            "id": "native-task",
            "contextId": "native-context",
            "status": {"state": state["value"]},
        }

    @app.post("/a2a")
    async def accept(request: Request):
        body = await request.json()
        calls.append(body)
        assert body["jsonrpc"] == "2.0"
        method = body["method"]
        if state["drop"]:

            async def missing_result():
                yield b'{"jsonrpc":"2.0","id":'

            return StreamingResponse(missing_result(), media_type="application/json")
        if method == "tasks/cancel":
            if state["refuse_cancel"]:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32002, "message": "refused"}}
                )
            state["value"] = "canceled"
        elif method in {"message/send", "message/stream"} and body["params"]["message"].get("taskId"):
            assert body["params"]["message"]["contextId"] == "native-context"
            state["value"] = "working"
        if method == "message/stream":

            async def stream():
                mode = state["stream_mode"]
                if mode in {
                    "message",
                    "malformed-message",
                    "malformed-parts",
                    "partial-json",
                    "partial-frame",
                    "no-result",
                }:
                    result = {
                        "kind": "message",
                        "messageId": "native-reply",
                        "role": "agent",
                        "parts": [{"kind": "text", "text": "Immediate source reply"}],
                    }
                    if mode == "malformed-message":
                        result.pop("messageId")
                    elif mode == "malformed-parts":
                        result["parts"] = [{"kind": "text", "text": 1}]
                    document = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
                    if mode == "no-result":
                        yield b": heartbeat\r\n\r\n"
                    elif mode == "partial-json":
                        yield b"data: " + document[:-1] + b"\r\n\r\n"
                    elif mode == "partial-frame":
                        yield b"data: " + document + b"\r\n"
                    else:
                        yield b"data: " + document + b"\r\n\r\n"
                    return
                results = [
                    task(),
                    {
                        "kind": "artifact-update",
                        "taskId": "wrong-task" if state["wrong_task"] else "native-task",
                        "contextId": "native-context",
                        "artifact": {
                            "artifactId": "native-artifact",
                            "parts": [{"kind": "text", "text": "source result"}],
                        },
                    },
                    {
                        "kind": "status-update",
                        "taskId": "native-task",
                        "contextId": "native-context",
                        "status": {"state": state["value"]},
                        "final": True,
                    },
                ]
                if mode == "task-eof":
                    results = results[:1]
                for result in results:
                    yield (
                        b"data: "
                        + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
                        + b"\r\n\r\n"
                    )
                    await hold.wait()

            return StreamingResponse(stream(), media_type="text/event-stream")
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": task()})

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    pending = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/a2a", calls, state, hold
    finally:
        hold.set()
        server.should_exit = True
        await asyncio.wait_for(pending, 5)
        listener.close()


def target_for(endpoint):
    from agentmesh.identity import AgentIdentity
    from agentmesh.integrations.a2a import A2AAgentCard, A2ATrustProvider

    local = AgentIdentity.create(name="local-client", sponsor="fixture@example.test", capabilities=[])
    peer_identity = AgentIdentity.create(name="local-peer", sponsor="fixture@example.test", capabilities=["delegate"])
    card = A2AAgentCard.from_identity(peer_identity, endpoint)
    card.ai_card.expires_at = datetime.now(UTC) + timedelta(hours=1)
    card.ai_card.sign(peer_identity)
    return A2ATarget(
        card,
        A2ATrustProvider(local),
        "project",
        str(peer_identity.did),
        peer_identity.public_key,
        endpoint,
    )


def message(identifier, **extra):
    return {
        "kind": "message",
        "messageId": identifier,
        "role": "user",
        "parts": [{"kind": "text", "text": "Inert local request"}],
        **extra,
    }


async def test_real_runtime_a2a_submit_stream_get_state_input_cancel_and_history(composed_runtime, a2a_peer):
    host, approvals, governance, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    target = target_for(endpoint)
    job = await create()

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)

    async def invoke():
        observed = await target.send_message(message("message-1"), stream=True)
        assert [result["kind"] for result in observed] == ["task", "artifact-update", "status-update"]
        assert (await target.get_task("native-task"))["status"]["state"] == "input-required"
        await target.send_message(message("message-2", taskId="native-task", contextId="native-context"))
        with pytest.raises(ValueError, match="input-required"):
            await target.send_message(message("message-3", taskId="native-task", contextId="native-context"))
        state["refuse_cancel"] = True
        with pytest.raises(A2ARequestRefusedError) as refused:
            await target.cancel_task("native-task")
        assert refused.value.code == -32002
        state["refuse_cancel"] = False
        return await target.cancel_task("native-task")

    execution = await host.start_native(job.id, family="a2a", target=target, invoke=invoke)
    result = await asyncio.wait_for(execution, 15)
    assert result["status"]["state"] == "canceled"
    assert [call["method"] for call in calls] == [
        "message/stream",
        "tasks/get",
        "message/send",
        "tasks/cancel",
        "tasks/cancel",
    ]
    history = await events_for(host, job.id)
    source = [event for event in history if event.kind == "integration.remote.observed"]
    assert any(event.payload["result"].get("contextId") == "native-context" for event in source)
    assert any(event.payload["result"].get("artifact", {}).get("artifactId") == "native-artifact" for event in source)
    assert all(event.raw_event is not None and event.metadata.integration_binding_id for event in source)
    assert any(event.kind == "integration.request.refused" for event in history)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 4
    with pytest.raises(PermissionError, match="attached"):
        await target.get_task("native-task")


@pytest.mark.parametrize("reason", ["denied", "wrong-key", "endpoint"])
async def test_a2a_identity_and_preflight_precede_actual_transport(composed_runtime, a2a_peer, reason):
    host, approvals, governance, create = composed_runtime
    endpoint, calls, _, _ = a2a_peer
    target = target_for(endpoint)
    if reason == "wrong-key":
        target.public_key = target_for(endpoint).public_key
    elif reason == "endpoint":
        target.endpoint += "/wrong"

    async def deny(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.rejected)

    host._event_bus.subscribe(deny)
    job = await create()
    task = await host.start_native(
        job.id, family="a2a", target=target, invoke=lambda: target.send_message(message("m"))
    )
    with pytest.raises((NativeGateDeniedError, ValueError)):
        await asyncio.wait_for(task, 10)
    assert calls == [] and (await governance._ledger.job_totals(job.id)).executed_calls == 0


async def test_a2a_response_loss_is_not_retried_or_promoted_to_remote_admission(composed_runtime, a2a_peer):
    host, approvals, governance, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    state["drop"] = True
    target = target_for(endpoint)

    async def approve(event: SessionEvent):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()

    async def invoke():
        with pytest.raises(A2ADeliveryUnknownError):
            await target.send_message(message("uncertain"))
        with pytest.raises(A2ADeliveryUnknownError, match="Reconcile"):
            await target.send_message(message("no-blind-retry"))
        return "uncertain outcome retained"

    task = await host.start_native(job.id, family="a2a", target=target, invoke=invoke)
    await asyncio.wait_for(task, 10)
    assert len(calls) == 1
    history = await events_for(host, job.id)
    assert not any(event.kind == "integration.remote.observed" for event in history)
    assert any(event.payload.get("outcome") == "delivery_unknown" for event in history)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


async def test_a2a_known_uncertain_request_requires_get_reconciliation(composed_runtime, a2a_peer):
    host, approvals, _, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    target = target_for(endpoint)

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()

    async def invoke():
        await target.send_message(message("first"))
        state["drop"] = True
        with pytest.raises(A2ADeliveryUnknownError):
            await target.send_message(message("lost", taskId="native-task", contextId="native-context"))
        with pytest.raises(A2ADeliveryUnknownError, match="Reconcile"):
            await target.send_message(message("blocked", taskId="native-task", contextId="native-context"))
        state["drop"] = False
        await target.get_task("native-task")
        return await target.send_message(message("after-reconcile", taskId="native-task", contextId="native-context"))

    task = await host.start_native(job.id, family="a2a", target=target, invoke=invoke)
    await asyncio.wait_for(task, 10)
    assert [call["method"] for call in calls] == ["message/send", "message/send", "tasks/get", "message/send"]


async def test_a2a_new_stream_reconciles_the_task_learned_before_response_loss(composed_runtime, a2a_peer):
    host, approvals, _, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    state["stream_mode"] = "task-eof"
    target = target_for(endpoint)

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()

    async def invoke():
        with pytest.raises(A2ADeliveryUnknownError):
            await target.send_message(message("new-stream"), stream=True)
        with pytest.raises(A2ADeliveryUnknownError, match="Reconcile"):
            await target.send_message(message("blocked", taskId="native-task", contextId="native-context"))
        reconciled = await target.get_task("native-task")
        assert reconciled["status"]["state"] == "input-required"
        return await target.send_message(message("resumed", taskId="native-task", contextId="native-context"))

    task = await host.start_native(job.id, family="a2a", target=target, invoke=invoke)
    result = await asyncio.wait_for(task, 10)
    assert result[0]["id"] == "native-task"
    assert [call["method"] for call in calls] == ["message/stream", "tasks/get", "message/send"]
    history = await events_for(host, job.id)
    lost = next(event for event in history if event.payload.get("outcome") == "delivery_unknown")
    assert lost.payload["task_id"] == "native-task"


@pytest.mark.parametrize(
    "mode", ["message", "malformed-message", "malformed-parts", "partial-json", "partial-frame", "no-result"]
)
async def test_a2a_message_only_stream_requires_a_complete_valid_result(composed_runtime, a2a_peer, mode):
    host, approvals, governance, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    state["stream_mode"] = mode
    target = target_for(endpoint)

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()
    task = await host.start_native(
        job.id, family="a2a", target=target, invoke=lambda: target.send_message(message("immediate"), stream=True)
    )
    if mode == "message":
        results = await asyncio.wait_for(task, 10)
        assert results == (
            {
                "kind": "message",
                "messageId": "native-reply",
                "role": "agent",
                "parts": [{"kind": "text", "text": "Immediate source reply"}],
            },
        )
    else:
        with pytest.raises(A2ADeliveryUnknownError):
            await asyncio.wait_for(task, 10)
    assert len(calls) == 1
    history = await events_for(host, job.id)
    observed = [event for event in history if event.kind == "integration.remote.observed"]
    if mode == "message":
        assert len(observed) == 1 and observed[0].payload["result"]["messageId"] == "native-reply"
        assert any(event.payload.get("outcome") == "responded" for event in history)
        assert not any(event.payload.get("outcome") == "delivery_unknown" for event in history)
    else:
        assert observed == []
        assert any(event.payload.get("outcome") == "delivery_unknown" for event in history)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == int(mode == "message")


async def test_a2a_get_cannot_clear_uncertainty_when_the_lost_submission_had_no_task_id(composed_runtime, a2a_peer):
    host, approvals, _, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    state["stream_mode"] = "no-result"
    target = target_for(endpoint)

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()

    async def invoke():
        with pytest.raises(A2ADeliveryUnknownError):
            await target.send_message(message("unbound-stream"), stream=True)
        await target.get_task("native-task")
        with pytest.raises(A2ADeliveryUnknownError, match="Reconcile"):
            await target.send_message(message("unproven-resubmit"))
        return "still unconfirmed"

    task = await host.start_native(job.id, family="a2a", target=target, invoke=invoke)
    assert await asyncio.wait_for(task, 10) == "still unconfirmed"
    assert [call["method"] for call in calls] == ["message/stream", "tasks/get"]


async def test_a2a_stream_cannot_attach_another_tasks_updates(composed_runtime, a2a_peer):
    host, approvals, _, create = composed_runtime
    endpoint, calls, state, _ = a2a_peer
    state["wrong_task"] = True
    target = target_for(endpoint)

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()
    task = await host.start_native(
        job.id,
        family="a2a",
        target=target,
        invoke=lambda: target.send_message(message("stream"), stream=True),
    )
    with pytest.raises(A2ADeliveryUnknownError):
        await asyncio.wait_for(task, 10)
    assert len(calls) == 1
    retained = [
        event.payload["result"]
        for event in await events_for(host, job.id)
        if event.kind == "integration.remote.observed"
    ]
    assert all(result.get("taskId") != "wrong-task" for result in retained)


async def test_a2a_small_stream_updates_persist_before_close_and_local_cancel_is_not_remote_abort(
    composed_runtime,
    a2a_peer,
):
    host, approvals, _, create = composed_runtime
    endpoint, calls, state, hold = a2a_peer
    hold.clear()
    state["value"] = "working"
    target = target_for(endpoint)

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    job = await create()
    task = await host.start_native(
        job.id,
        family="a2a",
        target=target,
        invoke=lambda: target.send_message(message("long-stream"), stream=True),
    )
    try:
        async with asyncio.timeout(5):
            while not any(event.kind == "integration.remote.observed" for event in await events_for(host, job.id)):
                await asyncio.sleep(0.01)
        assert not task.done()
        await host.cancel(job.id)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert state["value"] == "working"
        assert [call["method"] for call in calls] == ["message/stream"]
        history = await events_for(host, job.id)
        assert any(event.payload.get("outcome") == "local-stop-requested" for event in history)
    finally:
        hold.set()
        await asyncio.gather(task, return_exceptions=True)
