"""Release regressions through pinned SDKs and real persisted Runtime ownership."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.profile_bridges.autogen import AutoGenTarget
from backend.services.adapters.profile_bridges.google_adk import GoogleADKTarget
from backend.tests.native_gates.qualify_autogen import Request, RoutedAgent, core
from backend.tests.native_gates.qualify_google_adk import (
    BaseLlm,
    BasePlugin,
    adk,
    sessions,
    types,
)
from backend.tests.native_gates.qualify_google_adk import (
    make_target as make_adk_target,
)
from backend.tests.native_gates.qualify_llamaindex import make_engine
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

if TYPE_CHECKING:
    from pydantic import BaseModel

    class BaseAgent(BaseModel):
        name: str
else:
    BaseAgent = load_sdk("google.adk.agents.base_agent").BaseAgent


async def target_for(family, body):
    if family == "google-adk":
        target, invoke, _ = await make_adk_target(body)
        return target, invoke
    if family == "llamaindex":
        target, _ = make_engine(body)
        return target, lambda: target.engine.aquery("local")
    tool = load_sdk("autogen_core.tools").FunctionTool(body, "Inert local function")

    class Agent(RoutedAgent):
        @core.message_handler
        async def request(self, message: Request, ctx: Any) -> Any:
            return await tool.run_json({"value": message.value}, ctx.cancellation_token, call_id="native-tool")

    runtime = core.SingleThreadedAgentRuntime()
    await Agent.register(runtime, "local", lambda: Agent("original"))

    async def invoke_autogen():
        runtime.start()
        try:
            return await runtime.send_message(
                Request("local"), core.AgentId("local", "default"), message_id="native-message"
            )
        finally:
            await runtime.stop()

    return AutoGenTarget(runtime, (tool,)), invoke_autogen


@pytest.mark.timeout(25)
@pytest.mark.parametrize("family", ["autogen", "llamaindex", "google-adk"])
@pytest.mark.parametrize("change", ["cancel", "revoke", "snapshot"])
async def test_real_queued_native_leaf_rechecks_ownership_and_snapshot(family, change, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    target, invoke = await target_for(family, body)
    loop = asyncio.get_running_loop()
    if family == "google-adk":
        pool = load_sdk("google.adk.utils._sync_runner")._get_tool_thread_pool(1)
        previous = None
    else:
        await asyncio.to_thread(lambda: None)
        previous = vars(loop)["_default_executor"]
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qualified-queued-native")
        loop.set_default_executor(pool)
    occupied, approved = asyncio.Event(), asyncio.Event()
    release = threading.Event()

    def occupy():
        loop.call_soon_threadsafe(occupied.set)
        assert release.wait(15)

    blocker = pool.submit(occupy)
    await asyncio.wait_for(occupied.wait(), 2)
    approvals_seen = 0

    async def approve(event):
        nonlocal approvals_seen
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")
            approvals_seen += 1
            if approvals_seen == (3 if family == "google-adk" else 2):
                approved.set()

    host._event_bus.subscribe(approve)
    task = None
    try:
        task = await host.start_native(job.id, family=family, target=target, invoke=invoke)
        await asyncio.wait_for(approved.wait(), 5)
        async with asyncio.timeout(5):
            while not pool._work_queue.qsize():
                await asyncio.sleep(0.001)
        assert not calls
        (execution,) = host._native_executions.for_job(job.id)
        if change == "cancel":
            task.cancel()
            await asyncio.wait_for(execution.gates.binding.wait_revoked(), 2)
        elif change == "revoke":
            await host.detach_native(execution.identity)
        elif family == "autogen":
            target.tools[0]._description = "changed after approval"
        elif family == "llamaindex":
            target.tools[0].metadata.description = "changed after approval"
        else:
            target.tools[0].name = "changed_after_approval"
        assert not task.done() and host._native_executions.for_job(job.id)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not calls, "A previously queued native body ran after revocation or snapshot replacement"
        receipts = [event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result"]
        assert any(event.payload["started"] is False and event.payload["succeeded"] is False for event in receipts)
    finally:
        release.set()
        await asyncio.wrap_future(blocker)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if previous is not None:
            loop.set_default_executor(previous)
            pool.shutdown(wait=True)
        if family == "google-adk":
            await target.runner.close()


@pytest.mark.parametrize("explicit_override", [False, True])
async def test_llamaindex_preserves_explicit_native_async_conversion(explicit_override, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    calls = []

    def original(value: str) -> str:
        calls.append("original")
        return value

    def other(value: str) -> str:
        calls.append("explicit")
        return value

    conversion = load_sdk("llama_index.core.tools.function_tool").sync_to_async
    target, _ = make_engine(original, async_fn=conversion(other) if explicit_override else None)
    selected = target.tools[0]._async_fn

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    await (
        await host.start_native(
            job.id, family="llamaindex", target=target, invoke=lambda: target.engine.aquery("local")
        )
    )
    assert calls == (["explicit"] if explicit_override else ["original"])
    assert target.tools[0]._async_fn is selected


@pytest.mark.parametrize("boundary", ["model", "agent"])
async def test_adk_generator_entry_failure_is_known_local_execution(boundary, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    entered = []

    async def fail():
        entered.append(boundary)
        raise RuntimeError("Actual local generator failed before its first yield")

    class FailingModel(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield await fail()

    class FailingAgent(BaseAgent):
        async def _run_async_impl(self, ctx):
            yield await fail()

    model = FailingModel(model="inert")
    agent = adk.Agent(name="local", model=model) if boundary == "model" else FailingAgent(name="local")
    service = sessions.InMemorySessionService()
    session = await service.create_session(app_name="qualification", user_id="local", session_id="native-session")
    runner = adk.Runner(agent=agent, app_name="qualification", session_service=service)
    target = GoogleADKTarget(runner, (), (model,) if boundary == "model" else ())

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)

    async def invoke():
        return [
            event
            async for event in runner.run_async(
                user_id="local",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="local")]),
            )
        ]

    task = await host.start_native(job.id, family="google-adk", target=target, invoke=invoke)
    try:
        with pytest.raises(RuntimeError, match="before its first yield"):
            await task
        assert entered == [boundary]
        receipts = [event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result"]
        assert len(receipts) == (2 if boundary == "model" else 1)
        assert all(event.payload["started"] is True and event.payload["succeeded"] is False for event in receipts)
        assert (await governance._ledger.job_totals(job.id)).executed_calls == len(receipts)
    finally:
        await runner.close()


@pytest.mark.parametrize("recover", [False, True])
async def test_adk_real_tool_error_has_one_correlated_canonical_failure(recover, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        raise RuntimeError("local tool failure")

    target, invoke = await target_for("google-adk", body)

    class Recovery(BasePlugin):
        async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
            return {"recovered": True} if recover else None

    sibling = Recovery(name="local-recovery")
    target.runner.plugin_manager.register_plugin(sibling)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    task = await host.start_native(job.id, family="google-adk", target=target, invoke=invoke)
    try:
        if recover:
            assert await task
        else:
            with pytest.raises(RuntimeError, match="local tool failure"):
                await task
        stored = await events_for(host, job.id)
        control = [
            event
            for event in stored
            if event.kind == "execution.native.tool_result"
            and event.payload.get("native_tool_call_id") == "native-function"
        ]
        assert len(control) == 1 and control[0].payload["started"] is True and control[0].payload["succeeded"] is False
        canonical = [
            event
            for event in stored
            if event.metadata.integration_binding_id is not None
            and event.kind.startswith("tool.call.")
            and event.payload.get("tool_call_id") == "native-function"
        ]
        assert [event.kind for event in canonical] == ["tool.call.failed"]
        assert canonical[0].payload["error"] == "local tool failure"
        assert canonical[0].metadata.run_id
        assert calls == ["local"] and target.runner.plugin_manager.plugins == [sibling]
    finally:
        await target.runner.close()


@pytest.mark.parametrize("family", ["autogen", "llamaindex", "google-adk"])
async def test_native_batch_receipts_never_alias_app_binding_to_native_run(family, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()

    def body(value: str) -> str:
        return value

    target, invoke = await target_for(family, body)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    try:
        await (await host.start_native(job.id, family=family, target=target, invoke=invoke))
        receipts = [event for event in await events_for(host, job.id) if event.kind == "integration.batch.accepted"]
        assert receipts
        for event in receipts:
            native = (event.raw_event or {}).get("records", [])
            expected = native[0].get("run_id") if native else None
            assert event.metadata.run_id == expected
            assert event.metadata.integration_binding_id.startswith("native-observation-")
    finally:
        if family == "google-adk":
            await target.runner.close()


@pytest.mark.parametrize("fail", [False, True])
async def test_llama_runtime_pairs_app_operation_without_fabricating_native_tool_id(fail, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        if fail:
            raise RuntimeError("local failure")
        return value

    target, invoke = await target_for("llamaindex", body)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    task = await host.start_native(job.id, family="llamaindex", target=target, invoke=invoke)
    if fail:
        with pytest.raises(RuntimeError, match="local failure"):
            await task
    else:
        assert str(await task) == "local"
    canonical = [
        event
        for event in await events_for(host, job.id)
        if event.metadata.integration_binding_id is not None and event.kind.startswith("tool.call.")
    ]
    assert len(canonical) == 1
    assert canonical[0].kind == ("tool.call.failed" if fail else "tool.call.completed")
    assert canonical[0].payload["app_operation_id"]
    assert canonical[0].payload["arguments"]["kwargs"] == {"value": "local"}
    assert "tool_call_id" not in canonical[0].payload
    assert "_codeplane_pairing" not in canonical[0].payload
    assert calls == ["local"] and (await governance._ledger.job_totals(job.id)).executed_calls == 2
