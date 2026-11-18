"""Actual native gates must not starve the ledger on their own executor."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

if TYPE_CHECKING:

    class FunctionToolsetBase:
        def __init__(self, tools: list[Any]) -> None: ...

        async def call_tool(self, name: str, tool_args: Any, ctx: Any, tool: Any) -> Any: ...
else:
    FunctionToolsetBase = load_sdk("pydantic_ai.toolsets.function").FunctionToolset


async def test_pydantic_final_leaf_checks_actual_delegated_values(composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    sdk = load_sdk("pydantic_ai")
    models = load_sdk("pydantic_ai.models.test")
    calls = []

    def body(value: str, /) -> str:
        """A real supported positional-only native tool."""
        calls.append(value)
        return value

    class ChangedArguments(FunctionToolsetBase):
        async def call_tool(self, name, tool_args, ctx, tool):
            tool_args["value"] = "changed after approval"
            return await super().call_tool(name, tool_args, ctx, tool)

    target = sdk.Agent(models.TestModel(), toolsets=[ChangedArguments([body])])

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    task = await host.start_native(job.id, family="pydantic_ai", target=target, invoke=lambda: target.run("local"))
    with pytest.raises(PermissionError, match="callable arguments differ"):
        await task
    assert not calls
    results = [event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result"]
    assert len(results) == 1 and results[0].payload["started"] is False


async def test_pydantic_positional_argument_consumption_preserves_approved_entry(composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    sdk = load_sdk("pydantic_ai")
    models = load_sdk("pydantic_ai.models.test")
    calls = []

    def body(value: str, /) -> str:
        """A real supported positional-only native tool."""
        calls.append(value)
        return value

    target = sdk.Agent(models.TestModel(), tools=[body])

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    await (await host.start_native(job.id, family="pydantic_ai", target=target, invoke=lambda: target.run("local")))
    assert len(calls) == 1
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


@pytest.mark.timeout(25)
@pytest.mark.parametrize("family", ["langgraph", "pydantic_ai"])
@pytest.mark.parametrize("change", ["cancel", "snapshot"])
async def test_existing_native_queued_leaf_is_fenced_at_body_entry(family, change, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    calls = []

    def body(value: str = "local") -> str:
        """Inert actual native synchronous function."""
        calls.append(value)
        return value

    if family == "langgraph":
        tools = load_sdk("langchain_core.tools")
        graph = load_sdk("langgraph.graph")
        prebuilt = load_sdk("langgraph.prebuilt")
        messages = load_sdk("langchain_core.messages")
        tool = tools.StructuredTool.from_function(body)
        target = prebuilt.ToolNode([tool])
        builder = graph.StateGraph(graph.MessagesState)
        builder.add_node("tools", target)
        builder.add_edge(graph.START, "tools")
        builder.add_edge("tools", graph.END)
        runnable = builder.compile()

        async def invoke_graph():
            return await runnable.ainvoke(
                {
                    "messages": [
                        messages.AIMessage(content="", tool_calls=[{"id": "native", "name": "body", "args": {}}])
                    ]
                }
            )

        invoke = invoke_graph

        def mutate():
            tool.description = "changed after approval"
    else:
        pydantic = load_sdk("pydantic_ai")
        test = load_sdk("pydantic_ai.models.test")
        target = pydantic.Agent(test.TestModel(), tools=[body])

        async def invoke_agent():
            return await target.run("local")

        invoke = invoke_agent

        def mutate():
            target._function_toolset.tools["body"].function_schema.function = lambda value="local": value

    loop = asyncio.get_running_loop()
    occupied, approved = asyncio.Event(), asyncio.Event()
    release = threading.Event()

    def occupy():
        loop.call_soon_threadsafe(occupied.set)
        assert release.wait(15)

    if family == "pydantic_ai":
        anyio_threads = load_sdk("anyio.to_thread")
        limiter = anyio_threads.current_default_thread_limiter()
        previous_limit = limiter.total_tokens
        limiter.total_tokens = 1
        blocker = asyncio.create_task(anyio_threads.run_sync(occupy))

        def queued_anyio():
            return limiter.statistics().tasks_waiting

        queued = queued_anyio
    else:
        await asyncio.to_thread(lambda: None)
        previous = vars(loop)["_default_executor"]
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qualified-existing-native")
        loop.set_default_executor(pool)
        blocker = asyncio.ensure_future(asyncio.wrap_future(pool.submit(occupy)))
        queued = pool._work_queue.qsize
    await asyncio.wait_for(occupied.wait(), 2)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")
            approved.set()

    host._event_bus.subscribe(approve)
    task = None
    try:
        task = await host.start_native(job.id, family=family, target=target, invoke=invoke)
        await asyncio.wait_for(approved.wait(), 5)
        async with asyncio.timeout(5):
            while not queued():
                await asyncio.sleep(0.001)
        if change == "cancel":
            (execution,) = host._native_executions.for_job(job.id)
            task.cancel()
            await asyncio.wait_for(execution.gates.binding.wait_revoked(), 2)
        else:
            mutate()
        assert not calls and not task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not calls
        results = [event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result"]
        assert len(results) == 1 and results[0].payload["started"] is False
    finally:
        release.set()
        await blocker
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if family == "pydantic_ai":
            limiter.total_tokens = previous_limit
        else:
            loop.set_default_executor(previous)
            pool.shutdown(wait=True)


@pytest.mark.timeout(12)
@pytest.mark.parametrize("synchronous", [True, False], ids=["explicit-sync", "async-fallback"])
@pytest.mark.parametrize("cancel", [False, True], ids=["complete", "cancel"])
async def test_real_native_gate_progress_with_one_default_executor_worker(synchronous, cancel, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    tools = load_sdk("langchain_core.tools")
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(lambda: None)
    previous = vars(loop)["_default_executor"]
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qualified-native-default")
    loop.set_default_executor(pool)
    entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    calls = []

    def body() -> str:
        """A real native synchronous function under an actual persisted policy."""
        calls.append("body")
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(5)
            return "actual-result"
        finally:
            finished.set()

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    tool = tools.StructuredTool.from_function(body)
    task = None
    try:
        await host.require_governance_admission()
        if not cancel:
            release.set()
        task = await host.start_native(
            job.id,
            family="langchain",
            target=tool,
            invoke=partial(tool.invoke if synchronous else tool.ainvoke, {}),
            synchronous=synchronous,
        )
        await asyncio.wait_for(entered.wait(), 3)
        if cancel:
            task.cancel()
            task.cancel()
            await asyncio.wait({task}, timeout=0.1)
            assert not task.done() and not finished.is_set()
            assert host._native_executions.for_job(job.id)
            assert job.id in host._legacy_runner_slots
            assert not any(event.kind == "execution.native.released" for event in await events_for(host, job.id))
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == "actual-result"
        assert calls == ["body"] and finished.is_set()
        assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
        assert host._native_executions.for_job(job.id) == ()
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        loop.set_default_executor(previous)
        pool.shutdown(wait=True)
