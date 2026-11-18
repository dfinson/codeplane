import asyncio
from typing import TYPE_CHECKING

import pytest

from backend.models.domain import ApprovalResolution
from backend.models.events import EventKind, SessionEvent
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for
from backend.tests.unit.test_mcp_bridge import _target
from backend.tests.unit.test_mcp_bridge import mcp_peer as mcp_peer
from backend.tests.unit.test_native_bridge_runtime import local_flowise as local_flowise
from backend.tests.unit.test_native_bridge_runtime import pending_bridge, target_for

if TYPE_CHECKING:
    from backend.services.adapters.profile_bridges.mcp import McpObservation


async def test_real_runtime_mcp_revocation_sends_native_cancel_while_work_is_shielded(composed_runtime, mcp_peer):
    host, approvals, _, create = composed_runtime
    endpoint, schema, calls, entered, finished = mcp_peer
    observed: list[McpObservation] = []
    target = _target(endpoint, schema, observed)
    job = await create()
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def approval(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(approval)
    task = await host.start_native(
        job.id,
        family="mcp",
        target=target,
        invoke=lambda: target.call_tool("read_value", {"value": "wait"}),
    )
    pending = await asyncio.wait_for(requested.get(), 10)
    assert calls == []
    await approvals.resolve(pending.payload["approval_id"], ApprovalResolution.approved)
    await asyncio.wait_for(entered.wait(), 5)
    await host.cancel(job.id)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert finished.is_set()
    assert observed[-1].outcome == "cancel-acknowledged"
    assert observed[-1].request_id == calls[-1][1]
    assert host._native_executions.for_job(job.id) == ()
    history = await events_for(host, job.id)
    uncertain = next(event for event in history if event.kind == "integration.request.unknown")
    assert uncertain.payload["request_id"] == calls[-1][1]
    assert uncertain.payload["outcome"] == "cancel-acknowledged"
    assert uncertain.metadata.integration_binding_id is not None
    assert uncertain.raw_event["operation_id"] == observed[-1].operation_id
    assert (
        next(event for event in history if event.kind == "execution.native.bound").payload["observation_pin"]["id"]
        == "mcp"
    )


async def test_real_flowise_approval_cannot_release_replaced_transport_credentials(composed_runtime, local_flowise):
    _, approvals, _, _ = composed_runtime
    endpoint, calls, _, _ = local_flowise
    target = target_for(endpoint)
    _, task, requested = await pending_bridge(composed_runtime, target)
    target.client.api_key = "replacement-fixture-key"
    await approvals.resolve(requested.payload["approval_id"], ApprovalResolution.approved)
    with pytest.raises(NativeGateDeniedError):
        await asyncio.wait_for(task, 10)
    assert calls == []


async def test_real_flowise_runtime_retains_request_response_source_and_remote_session(composed_runtime, local_flowise):
    host, approvals, governance, _ = composed_runtime
    endpoint, calls, _, _ = local_flowise
    target = target_for(endpoint)
    job, task, requested = await pending_bridge(composed_runtime, target)
    await approvals.resolve(requested.payload["approval_id"], ApprovalResolution.approved)
    assert (await asyncio.wait_for(task, 10))["sessionId"] == "native-local-session"
    history = await events_for(host, job.id)
    completed = next(event for event in history if event.kind == "integration.request.completed")
    started = next(event for event in history if event.kind == "integration.request.started")
    assert completed.payload["operation_id"] == started.payload["operation_id"]
    assert completed.payload["remote_session_id"] == "native-local-session"
    assert completed.payload["result"]["text"] == "local result"
    assert started.payload["arguments"]["question"] == "question"
    assert completed.metadata.integration_binding_id == started.metadata.integration_binding_id
    assert completed.metadata.execution_token is not None and completed.raw_event is not None
    assert len(calls) == 1 and (await governance._ledger.job_totals(job.id)).executed_calls == 1


async def test_real_mcp_runtime_retains_native_request_and_tool_result_without_duplicate_accrual(
    composed_runtime,
    mcp_peer,
):
    host, approvals, governance, create = composed_runtime
    endpoint, schema, calls, _, _ = mcp_peer
    observed: list[McpObservation] = []
    target = _target(endpoint, schema, observed)
    job = await create()

    async def approve(event):
        if event.kind == EventKind.approval_requested:
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)
    task = await host.start_native(
        job.id,
        family="mcp",
        target=target,
        invoke=lambda: target.call_tool("read_value", {"value": "persisted-result"}),
    )
    result = await asyncio.wait_for(task, 10)
    assert not result.isError and len(calls) == 1
    history = await events_for(host, job.id)
    completed = next(event for event in history if event.kind == "tool.call.completed")
    assert completed.payload["request_id"] == calls[0][1]
    assert completed.payload["operation_id"] == observed[0].operation_id
    assert completed.payload["protocol_session_id"]
    assert completed.payload["success"] is True
    assert completed.payload["result"]["content"][0]["text"] == "persisted-result"
    assert completed.raw_event is not None and completed.metadata.integration_binding_id is not None
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    await governance.observe(completed)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
