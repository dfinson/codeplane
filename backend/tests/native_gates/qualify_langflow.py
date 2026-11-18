"""Real Langflow host graph execution with inert, caller-owned component bodies."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest
from agentmesh.integrations.langflow import TrustGatedFlow

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.profile_bridges.langflow import LangflowTarget
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

if TYPE_CHECKING:
    from lfx.custom.custom_component.component import Component  # type: ignore[import-not-found]
else:
    Component = load_sdk("lfx.custom.custom_component.component").Component
Graph = load_sdk("lfx.graph.graph.base").Graph
Output = load_sdk("lfx.io").Output
StrInput = load_sdk("lfx.io").StrInput
async_helpers = load_sdk(Component._get_output_result.__globals__["async_delegate_target"].__module__)


class InertComponent(Component):
    display_name = "Inert qualification component"
    inputs = [StrInput(name="value", value="source-input", input_types=["Text"])]
    outputs = [Output(name="result", display_name="Result", method="produce")]

    def produce(self) -> str:
        self.calls.append(self.get_id())
        self.entered.set()
        assert self.finish.wait(10)
        if self.fail:
            raise ValueError("inert component failure")
        return f"{self.value}:handled"


class AsyncInertComponent(Component):
    display_name = "Inert async qualification component"
    inputs = InertComponent.inputs
    outputs = InertComponent.outputs

    async def produce(self) -> str:
        self.calls.append(self.get_id())
        self.entered.set()
        while not self.finish.is_set():
            await asyncio.sleep(0.01)
        if self.fail:
            raise ValueError("inert component failure")
        return f"{self.value}:handled"


class DelegatedInertComponent(InertComponent):
    @async_helpers.delegates_to("produce_async")
    def produce(self) -> str:
        return str(async_helpers.run_until_complete(self.produce_async()))

    async def produce_async(self) -> str:
        self.calls.append(self.get_id())
        self.entered.set()
        while not self.finish.is_set():
            await asyncio.sleep(0.01)
        if self.fail:
            raise ValueError("inert component failure")
        return f"{self.value}:handled"


class NativeValuesComponent(Component):
    display_name = "Native message and data"
    inputs = [load_sdk("lfx.io").MessageInput(name="message"), load_sdk("lfx.io").DataInput(name="payload")]
    outputs = [Output(name="result", display_name="Result", method="produce")]

    def produce(self) -> str:
        self.seen.append((self.message, self.payload))
        return f"{self.message.get_text()}:{self.payload.get_text()}"


@pytest.mark.parametrize("outcome", ["allow", "mutate-message", "mutate-data"])
async def test_real_langflow_native_values_are_pinned_without_replacing_objects(composed_runtime, outcome):
    host, approvals, _, create = composed_runtime
    job = await create()
    component = NativeValuesComponent()
    component.seen = []
    component.set(
        message=load_sdk("lfx.schema.message").Message(text="native-message"),
        payload=load_sdk("lfx.schema.data").Data(data={"text": "native-data"}),
    )
    graph = Graph(start=component, end=component, flow_id="native-value-flow")
    trust = TrustGatedFlow()
    trust.register_node(component.get_id(), trust_score=0.9)
    approved = []

    async def approve(event):
        if event.kind == "permission.requested":
            approved.append((component.message, component.payload))
            if outcome == "mutate-message":
                component.message.text = "changed"
            elif outcome == "mutate-data":
                component.payload.data["text"] = "changed"
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)

    async def invoke():
        return [result async for result in graph.async_start()]

    task = await host.start_native(job.id, family="langflow", target=LangflowTarget(graph, trust), invoke=invoke)
    if outcome == "allow":
        await task
        assert component._outputs_map["result"].value == "native-message:native-data"
        assert component.seen[0][0] is approved[0][0] and component.seen[0][1] is approved[0][1]
    else:
        with pytest.raises(Exception, match="changed|denied"):
            await task
        assert component.seen == []
    assert len(approved) == 1


@pytest.mark.parametrize("outcome", ["allow", "deny", "untrusted", "error", "cancel", "mutated", "selector-change"])
@pytest.mark.parametrize("component_type", [InertComponent, AsyncInertComponent, DelegatedInertComponent])
async def test_real_langflow_graph_connection_and_handler(composed_runtime, outcome, component_type):
    host, approvals, governance, create = composed_runtime
    job = await create()
    calls: list[str] = []
    source, destination = component_type(), component_type()
    for component in (source, destination):
        component.calls = calls
        component.entered, component.finish = threading.Event(), threading.Event()
        component.fail = component is destination and outcome == "error"
        if component is source or outcome != "cancel":
            component.finish.set()
    destination.set(value=source.produce)
    flow = Graph(start=source, end=destination, flow_id="actual-flow-id")
    trust = TrustGatedFlow()
    trust.register_node(source.get_id(), trust_score=0.9)
    identity = trust.register_node(destination.get_id(), trust_score=0.1 if outcome == "untrusted" else 0.9)
    original = destination.produce
    decisions = []

    async def approve(event):
        if event.kind == "permission.requested":
            decisions.append(event)
            if len(decisions) == 2 and outcome == "mutated":
                destination.value = "changed-after-preflight"
            if len(decisions) == 1 and outcome == "selector-change":
                destination._outputs_map["result"].method = "unbound"
                destination.unbound = lambda: calls.append(destination.get_id())
            await approvals.resolve(
                event.payload["approval_id"], "rejected" if len(decisions) == 2 and outcome == "deny" else "approved"
            )

    host._event_bus.subscribe(approve)

    async def invoke():
        return [result async for result in flow.async_start()]

    task = await host.start_native(
        job.id,
        family="langflow",
        target=LangflowTarget(flow, trust),
        invoke=invoke,
    )
    try:
        if outcome == "cancel":
            assert await asyncio.to_thread(destination.entered.wait, 8)
            task.cancel()
            await asyncio.wait({task}, timeout=0.05)
            assert not task.done()
            destination.finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome in {"deny", "untrusted", "error", "mutated", "selector-change"}:
            with pytest.raises(Exception) as failure:
                await task
            assert any(word in str(failure.value).lower() for word in ("denied", "trust", "changed", "failure"))
        else:
            await task
            assert destination._outputs_map["result"].value == "source-input:handled:handled"
        assert calls == (
            [source.get_id(), destination.get_id()] if outcome in {"allow", "error", "cancel"} else [source.get_id()]
        )
        assert destination.produce == original and flow.vertex_map[destination.get_id()].custom_component is destination
        assert trust.verifier.audit_logging is True
        observed = [
            event for event in await events_for(host, job.id) if event.metadata.integration_binding_id is not None
        ]
        if destination.get_id() in calls:
            terminal = next(
                event
                for event in observed
                if event.kind in {"tool.call.completed", "tool.call.failed"}
                and event.payload["node_id"] == destination.get_id()
            )
            assert terminal.payload["parent_nodes"] == [source.get_id()]
            assert terminal.payload["component_did"] == identity.did
            assert terminal.payload["flow_id"] == "actual-flow-id"
        assert (await governance._ledger.job_totals(job.id)).executed_calls == len(calls)
    finally:
        destination.finish.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_exported_trust_configuration_is_not_a_langflow_run(composed_runtime):
    host, _, _, create = composed_runtime
    job = await create()
    trust = TrustGatedFlow()
    trust.register_node("configuration-only")
    task = await host.start_native(
        job.id,
        family="langflow",
        target=LangflowTarget(trust.to_langflow_config(), trust),
        invoke=lambda: None,
        synchronous=True,
    )
    with pytest.raises(TypeError, match="actual caller-owned"):
        await task


async def test_langflow_sync_delegate_counts_one_actual_component_operation(composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    component = DelegatedInertComponent()
    component.calls = []
    component.entered, component.finish = threading.Event(), threading.Event()
    component.finish.set()
    component.fail = False
    flow = Graph(start=component, end=component, flow_id="caller-owned-flow")
    trust = TrustGatedFlow()
    trust.register_node(component.get_id(), trust_score=0.9)
    decisions = []

    async def approve(event):
        if event.kind == "permission.requested":
            decisions.append(event)
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    task = await host.start_native(
        job.id,
        family="langflow",
        target=LangflowTarget(flow, trust),
        invoke=lambda: component.produce(),
        synchronous=True,
    )
    assert await task == "source-input:handled"
    assert component.calls == [component.get_id()] and len(decisions) == 1
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
