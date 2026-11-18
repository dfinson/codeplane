import asyncio
from typing import TYPE_CHECKING

import pytest

from backend.models.domain import ApprovalResolution
from backend.models.events import EventKind, SessionEvent
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
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
