"""Actual Haystack pipelines with inert components, not trust-only simulations."""

from __future__ import annotations

import asyncio
import os
import threading

os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "false"

import pytest
from agentmesh.integrations.haystack import TrustedPipeline

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.profile_bridges.haystack import HaystackTarget
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

sdk = load_sdk("haystack")


@pytest.mark.parametrize("outcome", ["allow", "deny", "untrusted", "error", "cancel", "mutated"])
@pytest.mark.parametrize("mode", ["sync-host", "async-host", "async-component"])
async def test_actual_haystack_component_boundary(composed_runtime, outcome, mode):
    host, approvals, governance, create = composed_runtime
    job = await create()
    calls = []
    entered, finish = threading.Event(), threading.Event()

    class Inert:
        @sdk.component.output_types(value=str)
        def run(self, value: str):
            calls.append(value)
            entered.set()
            assert finish.wait(10)
            if outcome == "error":
                raise ValueError("inert handler error")
            return {"value": value + ":handled"}

    if mode == "async-component":

        @sdk.component.output_types(value=str)
        async def run_async(self, value: str):
            calls.append(value)
            entered.set()
            while not finish.is_set():
                await asyncio.sleep(0.01)
            if outcome == "error":
                raise ValueError("inert handler error")
            return {"value": value + ":handled"}

        Inert.run_async = run_async
    component_type = sdk.component(Inert)
    component = component_type()
    original = component.run
    pipeline = sdk.Pipeline()
    pipeline.add_component("actual-node", component)
    trust = TrustedPipeline()
    identity = trust.add_component("actual-node", trust_score=0.1 if outcome == "untrusted" else 0.9)
    target = HaystackTarget(pipeline, trust)

    async def approve(event):
        if event.kind == "permission.requested":
            if outcome == "mutated":
                identity.trust_score = 0.1
            await approvals.resolve(event.payload["approval_id"], "rejected" if outcome == "deny" else "approved")

    host._event_bus.subscribe(approve)
    if outcome != "cancel":
        finish.set()
    task = await host.start_native(
        job.id,
        family="haystack",
        target=target,
        invoke=lambda: (
            pipeline.run({"actual-node": {"value": "original-input"}})
            if mode == "sync-host"
            else pipeline.run_async({"actual-node": {"value": "original-input"}})
        ),
        synchronous=mode == "sync-host",
    )
    try:
        if outcome == "cancel":
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.wait({task}, timeout=0.05)
            assert not task.done()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome in {"deny", "untrusted", "error", "mutated"}:
            with pytest.raises(sdk.core.errors.PipelineRuntimeError):
                await task
        else:
            assert await task == {"actual-node": {"value": "original-input:handled"}}
        assert len(calls) == int(outcome in {"allow", "error", "cancel"})
        assert component.run == original and pipeline.get_component("actual-node") is component
        assert trust._gate.audit_logging is True
        observed = [
            event for event in await events_for(host, job.id) if event.metadata.integration_binding_id is not None
        ]
        if calls:
            terminal = next(event for event in observed if event.kind in {"tool.call.completed", "tool.call.failed"})
            assert terminal.payload["component_id"] == "actual-node"
            assert terminal.payload["component_did"] == identity.did
            assert terminal.payload["success"] is (outcome != "error")
        assert (await governance._ledger.job_totals(job.id)).executed_calls == len(calls)
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_missing_haystack_handler_is_not_completed_pipeline(composed_runtime):
    host, _, _, create = composed_runtime
    job = await create()
    with pytest.raises(ValueError, match="actual component handlers"):
        task = await host.start_native(
            job.id,
            family="haystack",
            target=HaystackTarget(sdk.Pipeline(), TrustedPipeline()),
            invoke=lambda: None,
            synchronous=True,
        )
        await task
