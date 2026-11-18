"""Actual ADK Runner/plugin/model/tool lifecycle with an inert local BaseLlm."""

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import pytest

from backend.services.adapters.native_gates import GateAttachment
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.adapters.profile_bridges.google_adk import GoogleADKTarget, install
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

adk = load_sdk("google.adk")
types = load_sdk("google.genai.types")
models = load_sdk("google.adk.models.llm_response")
tools = load_sdk("google.adk.tools.function_tool")
sessions = load_sdk("google.adk.sessions")
run_config = load_sdk("google.adk.agents.run_config")
if TYPE_CHECKING:
    from pydantic import BaseModel

    from backend.services.adapters.native_gates._optional import ADKPlugin as BasePlugin

    class BaseLlm(BaseModel):
        model: str
else:
    BaseLlm = load_sdk("google.adk.models.base_llm").BaseLlm
    BasePlugin = load_sdk("google.adk.plugins.base_plugin").BasePlugin


async def make_target(body, usage="positive"):
    requests = []
    tool = tools.FunctionTool(body)

    class LocalModel(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            requests.append(llm_request)
            if len(requests) == 1:
                yield models.LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    id="native-function", name=tool.name, args={"value": "local"}
                                )
                            )
                        ],
                    ),
                    usage_metadata=None
                    if usage == "missing"
                    else types.GenerateContentResponseUsageMetadata(
                        prompt_token_count=0 if usage == "zero" else 1, candidates_token_count=0
                    ),
                )
            else:
                yield models.LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text="local-result")]),
                    usage_metadata=None
                    if usage == "missing"
                    else types.GenerateContentResponseUsageMetadata(
                        prompt_token_count=0 if usage == "zero" else 3, candidates_token_count=0
                    ),
                )

    model = LocalModel(model="inert-local")
    agent = adk.Agent(name="local_agent", model=model, tools=[tool])
    service = sessions.InMemorySessionService()
    session = await service.create_session(app_name="qualification", user_id="local", session_id="native-session")
    runner = adk.Runner(agent=agent, app_name="qualification", session_service=service)
    target = GoogleADKTarget(runner, (tool,), (model,))

    async def invoke():
        return [
            event
            async for event in runner.run_async(
                user_id="local",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="local request")]),
                run_config=run_config.RunConfig(tool_thread_pool_config=run_config.ToolThreadPoolConfig(max_workers=1)),
            )
        ]

    return target, invoke, requests


@pytest.mark.parametrize("deny_stage", [None, "run", "model", "tool"])
async def test_adk_actual_native_before_callbacks_and_tool_entry(deny_stage, binding_factory):
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    target, invoke, requests = await make_target(body)
    gates, decision, _ = binding_factory("google-adk")
    original_authorize = decision.authorize_permission
    records: list[dict[str, Any]] = []
    receipts = []

    async def authorize(invocation):
        decision.allow = invocation.tool_name != {
            "run": "google-adk.run",
            "model": "google-adk.model",
            "tool": "body",
        }.get(deny_stage)
        return await original_authorize(invocation)

    decision.authorize_permission = authorize
    gates.binding._observation = records.append

    async def usage(operation_id, result):
        return None

    async def result(call, started, succeeded):
        receipts.append((call.name, started, succeeded))

    gates.binding._usage = usage
    gates.binding._on_result = result
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    install(attachment, target)
    try:
        with pytest.raises(UnsupportedNativeHookError, match="acknowledged native binding"):
            target.runner.run_live()
        events = await invoke()
        assert events and (len(calls) == (1 if deny_stage is None else 0))
        if deny_stage is None:
            assert len(requests) == 2
            assert ("body", True, True) in receipts
            assert any(raw.get("tool_call_id") == "native-function" for raw in records)
        elif deny_stage in {"run", "model"}:
            assert not requests
    finally:
        attachment.detach()
        await target.runner.close()
    assert not any(plugin.name.startswith("codeplane-") for plugin in target.runner.plugin_manager.plugins)


@pytest.mark.parametrize("change", ["none", "revoke", "model-request", "tool-arguments"])
async def test_adk_pending_and_postapproval_sibling_mutation_fences(change, binding_factory):
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    target, invoke, requests = await make_target(body)
    sibling_calls = []

    class Sibling(BasePlugin):
        async def before_model_callback(self, *, callback_context, llm_request):
            sibling_calls.append("model")
            if change == "model-request":
                llm_request.contents.append(types.Content(role="user", parts=[types.Part(text="changed")]))

        async def before_tool_callback(self, *, tool, tool_args, tool_context):
            sibling_calls.append("tool")
            if change == "tool-arguments":
                tool_args["value"] = "changed"

    sibling = Sibling(name="retained-sibling")
    target.runner.plugin_manager.register_plugin(sibling)
    gates, decision, _ = binding_factory("google-adk")
    decision.pending = True
    gates.binding._observation = lambda record: None

    async def usage(operation_id, result):
        return None

    gates.binding._usage = usage
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    install(attachment, target)
    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(decision.entered.wait(), 2)
        assert not calls and not requests and not task.done()
        if change == "revoke":
            gates.cancel()
        decision.release.set()
        if change in {"model-request", "tool-arguments"}:
            await asyncio.gather(task, return_exceptions=True)
            assert not calls
        else:
            await task
            assert calls == ([] if change == "revoke" else ["local"])
    finally:
        decision.release.set()
        await asyncio.gather(task, return_exceptions=True)
        attachment.detach()
        assert target.runner.plugin_manager.plugins == [sibling]
        await target.runner.close()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("usage", ["positive", "zero", "missing"])
async def test_adk_runtime_canonical_events_usage_and_cancellation(cancel, usage, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        entered.set()
        assert finish.wait(10)
        return value

    target, invoke, _ = await make_target(body, usage)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    if not cancel:
        finish.set()
    task = await host.start_native(job.id, family="google-adk", target=target, invoke=invoke)
    try:
        if cancel:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.wait({task}, timeout=0.1)
            assert not task.done() and host._native_executions.for_job(job.id)
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await target.runner.close()
    assert calls == ["local"]
    stored = await events_for(host, job.id)
    canonical = [event for event in stored if event.metadata.integration_binding_id is not None]
    assert any(event.payload.get("tool_call_id") == "native-function" for event in canonical)
    assert any(event.metadata.run_id for event in canonical if event.kind == "tool.call.completed")
    usages = [event for event in stored if event.kind == "execution.native.usage"]
    assert len(usages) == (0 if usage == "missing" else 1 if cancel else 2)
    assert sum(
        int(item["amount"]) for event in usages for item in event.payload["usage"] if item["unit"] == "input_tokens"
    ) == (0 if usage != "positive" else 1 if cancel else 4)
    assert all(
        item["producer"] == "google-adk" and item["role"] == "primary"
        for event in usages
        for item in event.payload["usage"]
    )
    assert (await governance._ledger.job_totals(job.id)).executed_calls == (3 if cancel else 4)
    assert not any(plugin.name.startswith("codeplane-") for plugin in target.runner.plugin_manager.plugins)
