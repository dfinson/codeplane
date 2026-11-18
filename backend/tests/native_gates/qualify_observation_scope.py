"""Real SDK callback admission/drain tests; canonical ingest integration is separate."""

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

from backend.services.adapters.integration_policy import IntegrationDisabledError
from backend.services.adapters.native_gates import observation
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.observation_scope import NativeObservationOwner
from backend.tests.native_gates.producers import make_producer
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime


async def start_observer_runtime(composed_runtime, invoke, append):
    host, _, _, create = composed_runtime
    job = await create()
    agents = load_sdk("agents")
    target = agents.Agent(name="local-observer", tools=[])

    async def work():
        (execution,) = host._native_executions.for_job(job.id)

        async def finish():
            return None

        owner = NativeObservationOwner(
            registry=host._native_executions,
            execution=execution,
            source_id="trace_local",
            append=append,
            finish=finish,
        )
        execution.observation_sources.append(owner)
        await invoke(owner)

    return host, job, await host.start_native(job.id, family="openai_agents", target=target, invoke=work)


async def test_default_profile_refuses_observer_before_sdk_registration(composed_runtime, monkeypatch):
    entered = False

    async def append(raw):
        nonlocal entered
        entered = True
        return ()

    async def invoke(owner):
        composed_runtime[0]._config.runtime.copilot_only = True

        def forbidden(module):
            raise AssertionError("Disabled observation must not import or register native SDK hooks")

        monkeypatch.setattr(observation, "load_sdk", forbidden)
        with pytest.raises(IntegrationDisabledError):
            await observation.subscribe_openai(owner)

    _, _, task = await start_observer_runtime(composed_runtime, invoke, append)
    await task
    assert not entered


@pytest.mark.parametrize("retirement", ["detach", "disable", "cancel-detach"])
async def test_native_callback_drain_prevents_delivery_after_detach(retirement, composed_runtime, monkeypatch):
    tracing = load_sdk("agents.tracing")
    providers = load_sdk("agents.tracing.provider")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "false")
    provider = providers.DefaultTraceProvider()
    if TYPE_CHECKING:
        from backend.services.adapters.native_gates._optional import TracingProcessor as TracingBase
    else:
        TracingBase = tracing.TracingProcessor  # noqa: N806 - real SDK base.
    sibling_calls = []

    class Sibling(TracingBase):
        def on_trace_start(self, trace):
            pass

        def on_trace_end(self, trace):
            sibling_calls.append("trace")

        def on_span_start(self, span):
            pass

        def on_span_end(self, span):
            sibling_calls.append("span")

        def shutdown(self):
            pass

        def force_flush(self):
            pass

    sibling = Sibling()
    provider.register_processor(sibling)
    entered, finish_acceptance, producer_continue, subscribed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    delivered = []
    subscription = None

    async def append(raw):
        entered.set()
        await finish_acceptance.wait()
        delivered.append(raw)
        return ()

    async def invoke(owner):
        nonlocal subscription
        subscription = await observation.subscribe_openai(owner, provider=provider)
        subscribed.set()
        with provider.create_trace("local", trace_id="trace_local") as trace:
            with provider.create_span(tracing.FunctionSpanData("local", "{}", "first"), parent=trace):
                pass
            await producer_continue.wait()
            with provider.create_span(tracing.FunctionSpanData("local", "{}", "second"), parent=trace):
                pass

    host, job, task = await start_observer_runtime(composed_runtime, invoke, append)
    await asyncio.wait_for(subscribed.wait(), 5)
    await asyncio.wait_for(entered.wait(), 5)
    assert subscription is not None
    if retirement == "disable":
        host._config.runtime.copilot_only = True
        retiring = asyncio.create_task(host.reconcile_native_observers())
    else:
        retiring = asyncio.create_task(subscription.detach())
    try:
        await asyncio.sleep(0)
        if retirement == "cancel-detach":
            retiring.cancel()
        await asyncio.wait({retiring}, timeout=0.1)
        assert not retiring.done()
        assert host._native_executions.for_job(job.id)
        finish_acceptance.set()
        await asyncio.gather(retiring, return_exceptions=True)
        assert len(delivered) == 2
        count = len(delivered)
        producer_continue.set()
        await task
        assert len(delivered) == count
        assert provider._multi_processor._processors == (sibling,)
        assert sibling_calls == ["span", "span", "trace"]
        await subscription.detach()
    finally:
        finish_acceptance.set()
        producer_continue.set()
        await asyncio.gather(retiring, task, return_exceptions=True)


async def test_real_crewai_executor_callback_is_drained_after_admission(composed_runtime, monkeypatch):
    events = load_sdk("crewai.events.types.tool_usage_events")
    event_bus = load_sdk("crewai.events").crewai_event_bus
    host, approvals, _, create = composed_runtime
    job = await create()
    producer = make_producer("crewai-sync")
    converting, finish_conversion = threading.Event(), threading.Event()
    producer_continue, subscribed = asyncio.Event(), asyncio.Event()
    original = events.ToolUsageFinishedEvent.to_json
    delivered = []
    subscription = None
    sibling_calls = []
    sibling_done = threading.Event()

    def sibling(source, event):
        sibling_calls.append(event.type)
        if len(sibling_calls) == 2:
            sibling_done.set()

    event_bus.on(events.ToolUsageFinishedEvent)(sibling)

    def paused(event, *args, **kwargs):
        converting.set()
        assert finish_conversion.wait(10)
        return original(event, *args, **kwargs)

    monkeypatch.setattr(events.ToolUsageFinishedEvent, "to_json", paused)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)

    async def work():
        nonlocal subscription
        (execution,) = host._native_executions.for_job(job.id)

        async def append(raw):
            delivered.append(raw)
            return ()

        async def finish():
            return None

        owner = NativeObservationOwner(
            registry=host._native_executions,
            execution=execution,
            source_id=str(producer.target.id),
            append=append,
            finish=finish,
        )
        execution.observation_sources.append(owner)
        subscription = await observation.subscribe_crewai(owner, producer.target)
        subscribed.set()
        await producer.invoke()
        await producer_continue.wait()
        await producer.invoke()

    task = await host.start_native(job.id, family="crewai", target=producer.target, invoke=work)
    await asyncio.wait_for(subscribed.wait(), 5)
    try:
        assert await asyncio.to_thread(converting.wait, 5)
        assert subscription is not None
        retiring = asyncio.create_task(subscription.detach())
        await asyncio.wait({retiring}, timeout=0.1)
        assert not retiring.done()
        finish_conversion.set()
        await asyncio.wait_for(retiring, 5)
        finished = [raw for raw in delivered if raw["type"] == "tool_usage_finished"]
        assert len(finished) == 1
        assert finished[0]["agent_id"] == str(producer.target.agents[0].id)
        assert finished[0]["task_id"] == str(producer.target.tasks[0].id)
        assert sibling in event_bus._sync_handlers[events.ToolUsageFinishedEvent]
        count = len(delivered)
        producer_continue.set()
        await task
        assert await asyncio.to_thread(sibling_done.wait, 5)
        assert len(delivered) == count
    finally:
        finish_conversion.set()
        producer_continue.set()
        await asyncio.gather(task, return_exceptions=True)
        with event_bus._rwlock.w_locked():
            event_type = events.ToolUsageFinishedEvent
            event_bus._sync_handlers[event_type] = event_bus._sync_handlers.get(event_type, frozenset()) - {sibling}
            event_bus._handler_dependencies.get(event_type, {}).pop(sibling, None)
            event_bus._execution_plan_cache.pop(event_type, None)
