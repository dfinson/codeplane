"""Actual AutoGen runtime, intervention and FunctionTool paths without models."""

import asyncio
import threading
from dataclasses import dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import pytest

from backend.services.adapters.native_gates import GateAttachment
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.profile_bridges.autogen import AutoGenTarget, install
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

core = load_sdk("autogen_core")
tools = load_sdk("autogen_core.tools")
MessageDroppedException = load_sdk("autogen_core.exceptions").MessageDroppedException
if TYPE_CHECKING:
    from backend.services.adapters.native_gates._optional import InterventionHandler as DefaultHandler

    class RoutedAgent:
        def __init__(self, description: str) -> None: ...

        @classmethod
        async def register(cls, *args: Any, **kwargs: Any) -> Any: ...
else:
    RoutedAgent = core.RoutedAgent
    DefaultHandler = core.DefaultInterventionHandler


@dataclass
class Request:
    value: str


async def native_fixture(gates):
    calls, sibling_calls, receipts = [], [], []

    def body(value: str) -> str:
        calls.append(value)
        return value

    tool = tools.FunctionTool(body, "Inert original function")

    class Agent(RoutedAgent):
        @core.message_handler
        async def request(self, message: Request, ctx: Any) -> Any:
            return await tool.run_json({"value": message.value}, ctx.cancellation_token, call_id="native-tool-call")

    class Sibling(DefaultHandler):
        async def on_send(self, message, *, message_context, recipient):
            sibling_calls.append(message_context.message_id)
            return message

    sibling = Sibling()
    handlers = [sibling]
    runtime = core.SingleThreadedAgentRuntime(intervention_handlers=handlers)
    await Agent.register(runtime, "local", lambda: Agent("inert"))
    target = AutoGenTarget(runtime, (tool,))
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    original = tool._func

    async def record(call, started, succeeded):
        receipts.append((call.name, call.tool_call_id, started, succeeded))

    gates.binding._on_result = record
    gates.binding._observation = lambda record: None

    async def usage(operation_id, result):
        return None

    gates.binding._usage = usage
    install(attachment, target)
    runtime.start()
    return runtime, attachment, tool, original, calls, sibling_calls, receipts, handlers


@pytest.mark.parametrize("allow", [True, False])
async def test_real_autogen_intervention_and_function_boundary(allow, binding_factory):
    assert version("autogen-core") == "0.7.5"
    gates, decision, _ = binding_factory("autogen")
    decision.allow = allow
    runtime, attachment, tool, original, calls, sibling_calls, receipts, handlers = await native_fixture(gates)
    try:
        if allow:
            result = await runtime.send_message(Request("local"), core.AgentId("local", "default"), message_id="msg-1")
            assert result == "local" and calls == ["local"]
            assert len(decision.invocations) == 2
            assert receipts == [
                ("body", "native-tool-call", True, True),
                ("autogen.send_message", None, True, True),
            ]
            assert sibling_calls == ["msg-1"]
        else:
            with pytest.raises(MessageDroppedException):
                await runtime.send_message(Request("local"), core.AgentId("local", "default"), message_id="msg-1")
            assert not calls and not receipts
        await asyncio.wait_for(runtime.stop_when_idle(), 1)
    finally:
        if runtime._run_context is not None:
            await runtime.stop()
        attachment.detach()
    assert tool._func is original
    assert len(handlers) == 1 and runtime._intervention_handlers is handlers


@pytest.mark.parametrize("change", ["none", "arguments", "revoke"])
async def test_autogen_pending_decision_fences_actual_message(change, binding_factory):
    gates, decision, _ = binding_factory("autogen")
    decision.pending = True
    runtime, attachment, _, _, calls, _, _, _ = await native_fixture(gates)
    message = Request("approved")
    pending = asyncio.create_task(
        runtime.send_message(message, core.AgentId("local", "default"), message_id="pending-message")
    )
    try:
        await asyncio.wait_for(decision.entered.wait(), 2)
        assert not calls and not pending.done()
        if change == "arguments":
            message.value = "changed"
        elif change == "revoke":
            gates.cancel()
        decision.release.set()
        if change == "none":
            assert await pending == "approved"
            assert calls == ["approved"]
        else:
            with pytest.raises(MessageDroppedException):
                await pending
            assert not calls
    finally:
        decision.release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await runtime.stop()
        attachment.detach()


@pytest.mark.parametrize(
    "outcome", ["allow", "sync", "deny-message", "deny-tool", "cancel", "zero-usage", "missing-usage"]
)
async def test_autogen_actual_runtime_policy_history_usage_and_drain(outcome, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    models = load_sdk("autogen_core.models")
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        entered.set()
        assert finish.wait(10)
        return value

    tool = tools.FunctionTool(body, "Inert local function")
    original_function = tool._func

    class Agent(RoutedAgent):
        @core.message_handler
        async def request(self, message: Request, ctx: Any) -> Any:
            result = await tool.run_json({"value": message.value}, ctx.cancellation_token, call_id="native-tool")
            return (
                result
                if outcome == "missing-usage"
                else models.CreateResult(
                    finish_reason="stop",
                    content=result,
                    cached=False,
                    usage=models.RequestUsage(prompt_tokens=0 if outcome == "zero-usage" else 3, completion_tokens=0),
                )
            )

    runtime = core.SingleThreadedAgentRuntime()
    await Agent.register(runtime, "local", lambda: Agent("original"))
    target = AutoGenTarget(runtime, (tool,))
    approval_count = 0

    async def approve(event):
        nonlocal approval_count
        if event.kind == "permission.requested":
            approval_count += 1
            reject = outcome == "deny-message" or (outcome == "deny-tool" and approval_count == 2)
            await approvals.resolve(event.payload["approval_id"], "rejected" if reject else "approved")

    host._event_bus.subscribe(approve)

    async def invoke():
        runtime.start()
        try:
            return await runtime.send_message(
                Request("real-local"), core.AgentId("local", "default"), message_id="native-message"
            )
        finally:
            await runtime.stop()

    if outcome != "cancel":
        finish.set()
    task = await host.start_native(
        job.id,
        family="autogen",
        target=target,
        invoke=(lambda: asyncio.run(invoke())) if outcome == "sync" else invoke,
        synchronous=outcome == "sync",
    )
    try:
        if outcome == "cancel":
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            task.cancel()
            await asyncio.wait({task}, timeout=0.1)
            assert not task.done()
            assert host._native_executions.for_job(job.id)
            assert job.id in host._legacy_runner_slots
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome.startswith("deny"):
            with pytest.raises((MessageDroppedException, PermissionError)):
                await task
            assert not calls
        else:
            result = await task
            assert (result if isinstance(result, str) else result.content) == "real-local"
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
    assert tool._func is original_function and runtime._intervention_handlers is None
    assert len(calls) == (0 if outcome.startswith("deny") else 1)
    stored = await events_for(host, job.id)
    bound = next(event for event in stored if event.kind == "execution.native.bound")
    assert bound.payload["observation_pin"]["id"] == "autogen"
    observed = [event for event in stored if event.metadata.integration_binding_id is not None]
    assert all(event.metadata.run_id is None for event in observed if event.kind.startswith("tool.call."))
    tools_seen = [event for event in observed if event.kind.startswith("tool.call.")]
    if not outcome.startswith("deny"):
        assert tools_seen
        assert all(event.payload.get("tool_call_id") == "native-tool" for event in tools_seen)
        assert all(event.payload["message_id"] == "native-message" for event in tools_seen)
        assert all(event.raw_event is not None for event in tools_seen)
        assert all(
            event.metadata.execution_token == bound.payload["execution"]["execution_token"] for event in tools_seen
        )
    usages = [event for event in stored if event.kind == "execution.native.usage"]
    if outcome in {"allow", "sync", "cancel", "zero-usage"}:
        assert len(usages) == 1
        assert {item["unit"]: item["amount"] for item in usages[0].payload["usage"]} == {
            "input_tokens": "0" if outcome == "zero-usage" else "3",
            "output_tokens": "0",
        }
    else:
        assert not usages
    expected_calls = 0 if outcome == "deny-message" else 1 if outcome == "deny-tool" else 2
    assert (await governance._ledger.job_totals(job.id)).executed_calls == expected_calls
    assert host._native_executions.for_job(job.id) == ()
