"""Actual Haystack pipelines with inert components, not trust-only simulations."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from functools import partial

import httpx
from pydantic import BaseModel

os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "false"

import pytest
from agentmesh.integrations.haystack import TrustedPipeline

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.profile_bridges.haystack import HaystackTarget
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

sdk = load_sdk("haystack")


@pytest.mark.parametrize(
    "kind",
    ["stream", "tools", "schema", "callback-mutated", "tool-mutated", "schema-mutated", "slotted", "slotted-mutated"],
)
async def test_actual_chat_generator_runtime_handles_and_mutation_fences(composed_runtime, kind):
    host, approvals, _, create = composed_runtime
    job = await create()
    callbacks, tool_calls, requests = [], [], []
    options = {"label": "original"}

    def record(chunk, *, label):
        assert label is options
        callbacks.append(chunk)

    def inert_tool(value: str):
        tool_calls.append(value)
        return value

    class SlottedCallback:
        __slots__ = ("label",)

        def __init__(self):
            self.label = "original"

        def __call__(self, chunk):
            callbacks.append(chunk)

    slotted = SlottedCallback()
    tool = load_sdk("haystack.tools").Tool(
        name="inert_tool",
        description="Inert fixture tool",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        function=inert_tool,
    )

    class Reply(BaseModel):
        answer: str

    runtime = (
        {"streaming_callback": slotted}
        if kind.startswith("slotted")
        else {"streaming_callback": partial(record, label=options)}
        if kind in {"stream", "callback-mutated"}
        else {"tools": [tool]}
        if kind in {"tools", "tool-mutated"}
        else {"generation_kwargs": {"response_format": Reply}}
    )

    def respond(request):
        requests.append(request)
        if kind in {"stream", "slotted"}:
            chunks = [
                {
                    "id": "native-stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant", "content": "inert"}, "finish_reason": None}
                    ],
                },
                {
                    "id": "native-stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
                + b"data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "native-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"answer":"inert"}'},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    generator = load_sdk("haystack.components.generators.chat").OpenAIChatGenerator(
        api_key=load_sdk("haystack.utils").Secret.from_token("fixture-not-a-cloud-credential"),
        model="fixture-model",
        http_client_kwargs={"transport": httpx.MockTransport(respond)},
        max_retries=0,
    )
    pipeline = sdk.Pipeline()
    pipeline.add_component("generator", generator)
    trust = TrustedPipeline()
    trust.add_component("generator", trust_score=0.9)
    decisions = []

    async def approve(event):
        if event.kind == "permission.requested":
            decisions.append(event)
            assert not callbacks and not tool_calls and not requests
            if kind == "callback-mutated":
                options["label"] = "changed"
            elif kind == "slotted-mutated":
                slotted.label = "changed"
            elif kind == "tool-mutated":
                tool.parameters["properties"]["value"]["description"] = "changed"
            elif kind == "schema-mutated":
                Reply.model_fields["answer"].description = "changed"
                Reply.model_rebuild(force=True)
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    task = await host.start_native(
        job.id,
        family="haystack",
        target=HaystackTarget(pipeline, trust),
        invoke=lambda: generator.run(
            messages=[load_sdk("haystack.dataclasses").ChatMessage.from_user("inert")], **runtime
        ),
        synchronous=True,
    )
    try:
        if kind.endswith("mutated"):
            with pytest.raises(Exception, match="changed|denied"):
                await task
            assert not requests and not callbacks
        else:
            result = await task
            assert len(result["replies"]) == len(requests) == 1
            assert bool(callbacks) is (kind in {"stream", "slotted"})
        assert not tool_calls and len(decisions) == 1
    finally:
        await asyncio.gather(task, return_exceptions=True)
        if generator.client is not None:
            generator.client.close()
        if generator.async_client is not None:
            await generator.async_client.close()


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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("usage", ["positive", "zero", "missing"])
async def test_real_chat_generator_response_usage_is_not_multiplied_by_choices(composed_runtime, asynchronous, usage):
    host, approvals, governance, create = composed_runtime
    job = await create()
    generators = load_sdk("haystack.components.generators.chat")
    messages = load_sdk("haystack.dataclasses")
    secret = load_sdk("haystack.utils").Secret
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "actual-native-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {"index": index, "finish_reason": "stop", "message": {"role": "assistant", "content": "inert"}}
                    for index in range(2)
                ],
                "usage": None
                if usage == "missing"
                else {
                    "prompt_tokens": 3 if usage == "positive" else 0,
                    "completion_tokens": 0,
                    "total_tokens": 3 if usage == "positive" else 0,
                },
            },
        )

    generator = generators.OpenAIChatGenerator(
        api_key=secret.from_token("fixture-not-a-cloud-credential"),
        model="fixture-model",
        generation_kwargs={"n": 2},
        http_client_kwargs={"transport": httpx.MockTransport(respond)},
        max_retries=0,
    )
    pipeline = sdk.Pipeline()
    pipeline.add_component("model", generator)
    trust = TrustedPipeline()
    trust.add_component("model", trust_score=0.9)
    original = generator._prepare_api_call

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    inputs = {"model": {"messages": [messages.ChatMessage.from_user("inert")]}}
    task = await host.start_native(
        job.id,
        family="haystack",
        target=HaystackTarget(pipeline, trust),
        invoke=lambda: pipeline.run_async(inputs) if asynchronous else pipeline.run(inputs),
        synchronous=not asynchronous,
    )
    try:
        result = await task
        assert len(result["model"]["replies"]) == 2 and len(requests) == 1
        receipts = [event for event in await events_for(host, job.id) if event.kind == "execution.native.usage"]
        assert len(receipts) == int(usage != "missing")
        if receipts:
            assert receipts[0].payload["operation_id"] == "actual-native-completion"
            assert {amount["unit"]: amount["amount"] for amount in receipts[0].payload["usage"]} == {
                "input_tokens": "3" if usage == "positive" else "0",
                "output_tokens": "0",
            }
            assert all(
                amount["model"] == "fixture-model" and amount["producer"] == "haystack"
                for amount in receipts[0].payload["usage"]
            )
        assert generator._prepare_api_call == original
        assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    finally:
        await asyncio.gather(task, return_exceptions=True)
        if generator.client is not None:
            generator.client.close()
        if generator.async_client is not None:
            await generator.async_client.close()


@pytest.mark.parametrize("value", [True, -1, "3"])
def test_haystack_usage_rejects_untyped_or_invalid_supplied_counts(value):
    from backend.services.adapters.contracts import ExecutionIdentity
    from backend.services.adapters.native_gates.usage import result_usage

    identity = ExecutionIdentity("job", "binding", "epoch", "token")
    raw = {"meta": {"usage": {"prompt_tokens": value}}}
    assert result_usage("haystack", raw, identity) == ()
    message = load_sdk("haystack.dataclasses").ChatMessage.from_assistant("inert", meta=raw["meta"])
    with pytest.raises(ValueError, match="Invalid native client usage"):
        result_usage("haystack", message, identity)


async def test_component_supplied_usage_without_response_identity_stays_local_and_uncorrelated(composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    message = load_sdk("haystack.dataclasses").ChatMessage.from_assistant(
        "inert", meta={"model": "fixture-model", "usage": {"prompt_tokens": 2, "completion_tokens": 0}}
    )

    @sdk.component
    class InertMessages:
        @sdk.component.output_types(replies=list)
        def run(self):
            return {"replies": [message]}

    pipeline = sdk.Pipeline()
    pipeline.add_component("supplied", InertMessages())
    trust = TrustedPipeline()
    trust.add_component("supplied", trust_score=0.9)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    task = await host.start_native(
        job.id,
        family="haystack",
        target=HaystackTarget(pipeline, trust),
        invoke=lambda: pipeline.run({}),
        synchronous=True,
    )
    await task
    events = await events_for(host, job.id)
    assert not any(event.kind == "execution.native.usage" for event in events)
    observed = next(
        event
        for event in events
        if event.kind == "tool.call.completed" and event.metadata.integration_binding_id is not None
    )
    assert observed.payload["usage_correlation"] == "unresolved"
    assert {amount["unit"]: amount["amount"] for amount in observed.payload["reported_usage"][0]["usage"]} == {
        "input_tokens": "2",
        "output_tokens": "0",
    }
