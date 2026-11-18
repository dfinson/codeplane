"""Real-SDK regressions for ownership, source fencing, accounting and observation."""

import asyncio
import threading
from functools import partial

import pytest
from pydantic import BaseModel

from backend.models.domain import ApprovalResolution
from backend.models.events import EventKind, SessionEvent
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.usage import result_usage
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


async def approve_next(host, approvals, start):
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(on_event)
    task = await start()
    event = await asyncio.wait_for(requested.get(), 10)
    await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
    return task


@pytest.mark.parametrize("mode", ["async-fallback", "second-cancel", "persistence-error"])
async def test_real_native_worker_outlives_cancellation_until_actual_stop(mode, composed_runtime, monkeypatch):
    tools = load_sdk("langchain_core.tools")
    host, approvals, _, create = composed_runtime
    job = await create()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def body() -> str:
        """A real synchronous tool whose completion is controlled locally."""
        started.set()
        try:
            assert release.wait(10)
            return "finished"
        finally:
            finished.set()

    tool = tools.StructuredTool.from_function(body)
    task = await approve_next(
        host,
        approvals,
        lambda: host.start_native(
            job.id,
            family="langchain",
            target=tool,
            invoke=partial(tool.ainvoke if mode == "async-fallback" else tool.invoke, {}),
            synchronous=mode != "async-fallback",
        ),
    )
    assert await asyncio.to_thread(started.wait, 5)
    retiring, continue_retiring = asyncio.Event(), asyncio.Event()
    original_retire = host._native_executions._retire_approvals
    original_record = host._native_executions._repository.record
    delayed = False

    async def retire(execution):
        nonlocal delayed
        if mode == "second-cancel" and execution.cancel_requested and not delayed:
            delayed = True
            retiring.set()
            await continue_retiring.wait()
        await original_retire(execution)

    async def record(*args, **kwargs):
        if mode == "persistence-error" and args[3] == "cancel_requested":
            raise OSError("Native cancellation receipt unavailable")
        await original_record(*args, **kwargs)

    monkeypatch.setattr(host._native_executions, "_retire_approvals", retire)
    monkeypatch.setattr(host._native_executions._repository, "record", record)
    (execution,) = host._native_executions.for_job(job.id)
    revoked = asyncio.create_task(execution.gates.binding.wait_revoked())
    try:
        task.cancel()
        await asyncio.wait_for(revoked, 5)
        if mode == "second-cancel":
            await asyncio.wait_for(retiring.wait(), 5)
            task.cancel()
            continue_retiring.set()
        await asyncio.wait({task}, timeout=0.25)
        assert not finished.is_set()
        assert not task.done(), "Owning task ended while its real tool thread was still running"
        assert host._native_executions.for_job(job.id), "Live binding was released before the worker"
        assert job.id in host._legacy_runner_slots
        assert not any(event.kind == "execution.native.released" for event in await events_for(host, job.id))
    finally:
        continue_retiring.set()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)


@pytest.mark.parametrize(
    "family,change",
    [
        ("langgraph", "replace"),
        ("semantic_kernel", "replace"),
        ("maf", "replace"),
        ("maf", "mutate-model"),
        ("maf", "replace-function"),
    ],
)
async def test_native_delegated_arguments_and_target_cannot_change_during_approval(family, change, binding_factory):
    calls = []
    captured = []
    gates, decision, _ = binding_factory(family)
    decision.pending = True

    def body(value: str) -> str:
        """Record the exact value the native SDK actually invoked."""
        calls.append(value)
        return value

    if family == "langgraph":
        tools = load_sdk("langchain_core.tools")
        prebuilt = load_sdk("langgraph.prebuilt")
        graph_sdk = load_sdk("langgraph.graph")
        messages = load_sdk("langchain_core.messages")

        async def capture(request, execute):
            captured.append(request)
            return await execute(request)

        target = prebuilt.ToolNode([tools.StructuredTool.from_function(body)], awrap_tool_call=capture)
        graph = graph_sdk.StateGraph(graph_sdk.MessagesState)
        graph.add_node("tools", target)
        graph.add_edge(graph_sdk.START, "tools")
        graph.add_edge("tools", graph_sdk.END)
        runnable = graph.compile()

        async def invoke():
            return await runnable.ainvoke(
                {
                    "messages": [
                        messages.AIMessage(
                            content="",
                            tool_calls=[{"id": "native", "name": "body", "args": {"value": "approved"}}],
                        )
                    ]
                }
            )

        def mutate():
            captured[0].tool_call["args"] = {"value": "changed"}

    elif family == "semantic_kernel":
        kernel = load_sdk("semantic_kernel")
        contents = load_sdk("semantic_kernel.contents")
        functions = load_sdk("semantic_kernel.functions")
        filters = load_sdk("semantic_kernel.filters")
        target = kernel.Kernel()
        target.add_function(plugin_name="local", function=functions.kernel_function(name="body")(body))

        async def capture(context, next):
            captured.append(context)
            await next(context)

        target.add_filter(filters.FilterTypes.AUTO_FUNCTION_INVOCATION, capture)

        async def invoke():
            return await target.invoke_function_call(
                contents.FunctionCallContent(id="native", name="local-body", arguments='{"value":"approved"}'),
                contents.ChatHistory(),
            )

        def mutate():
            captured[0].arguments = functions.KernelArguments(value="changed")

    else:
        maf = load_sdk("agent_framework")
        middleware = load_sdk("agent_framework._middleware")
        from backend.services.adapters.native_gates.maf import MiddlewareSlot

        class Arguments(BaseModel):
            value: str

        function = maf.FunctionTool(name="body", func=body, input_model=Arguments)
        context = maf.FunctionInvocationContext(
            function,
            Arguments(value="approved") if change == "mutate-model" else {"value": "approved"},
        )
        target = MiddlewareSlot([])

        async def invoke():
            async def final(ctx):
                return await ctx.function.invoke(arguments=ctx.arguments, context=ctx, skip_parsing=True)

            return await middleware.FunctionMiddlewarePipeline(*target.middleware).execute(context, final)

        def mutate():
            if change == "mutate-model":
                context.arguments.value = "changed"
            elif change == "replace-function":
                context.function = maf.FunctionTool(name="replacement", func=body)
            else:
                context.arguments = {"value": "changed"}

    await gates.attach(target)
    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(decision.entered.wait(), 5)
        mutate()
        decision.release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not calls, "The SDK executed a different request than the one CodePlane authorized"
    finally:
        decision.release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_actual_trust_rejection_is_not_an_executed_receipt(binding_factory):
    trust = load_sdk("agentmesh.integrations.langchain")
    calls = []
    receipts = []

    def inner(query):
        calls.append(query)
        return query

    tool = trust.TrustVerifiedTool(
        name="trusted",
        description="local",
        agent_did="did:mesh:local",
        trust_store=trust.InMemoryTrustStore(default_score=0),
        inner_fn=inner,
    )
    gates, _, _ = binding_factory("langchain")

    async def record(call, started, succeeded):
        receipts.append((started, succeeded))

    gates.binding._on_result = record
    await gates.attach(tool)
    await asyncio.gather(asyncio.to_thread(tool.invoke, "approved"), return_exceptions=True)
    assert not calls
    assert receipts == [(False, False)]


async def test_cancelled_sync_result_still_records_supplied_native_usage(composed_runtime):
    tools = load_sdk("langchain_core.tools")
    messages = load_sdk("langchain_core.messages")
    host, approvals, _, create = composed_runtime
    job = await create()
    entered, release = threading.Event(), threading.Event()

    def body():
        """Return native result metadata after cancellation was requested."""
        entered.set()
        assert release.wait(10)
        return messages.AIMessage(
            content="local result",
            usage_metadata={"input_tokens": 3, "output_tokens": 0, "total_tokens": 3},
        )

    tool = tools.StructuredTool.from_function(body)
    task = await approve_next(
        host,
        approvals,
        lambda: host.start_native(
            job.id,
            family="langchain",
            target=tool,
            invoke=partial(tool.invoke, {}),
            synchronous=True,
        ),
    )
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    usage = [event for event in await events_for(host, job.id) if event.kind == "execution.native.usage"]
    assert len(usage) == 1
    assert {amount["unit"]: amount["amount"] for amount in usage[0].payload["usage"]} == {
        "input_tokens": "3",
        "output_tokens": "0",
    }


def test_real_crew_output_usage_is_retained_without_invented_usd():
    crew_output = load_sdk("crewai.crews.crew_output")
    usage = load_sdk("crewai.types.usage_metrics")
    result = crew_output.CrewOutput(
        raw="local",
        tasks_output=[],
        token_usage=usage.UsageMetrics(prompt_tokens=5, completion_tokens=2, cached_prompt_tokens=1, total_tokens=7),
    )
    amounts = result_usage("crewai", result, ExecutionIdentity("job", "binding", "epoch", "token"))
    assert {amount.unit: int(amount.amount) for amount in amounts} == {
        "input_tokens": 5,
        "output_tokens": 2,
        "cache_read_tokens": 1,
    }
