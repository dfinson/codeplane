"""Real registered run boundaries consume operator input across the eight SDK gates."""

import asyncio

import pytest

from backend.models.domain import StateConflictError
from backend.services.adapters.native_gates._optional import load_sdk
from backend.tests.native_gates.producers import make_producer
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

MODES = [
    "langchain-async",
    "langgraph-async",
    "crewai-sync",
    "semantic_kernel-async",
    "maf-async",
    "smolagents-sync",
    "pydantic_ai-async",
    "openai_agents-async",
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cancel_before_boundary", [False, True])
async def test_operator_input_reaches_actual_native_run_boundary(mode, cancel_before_boundary, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    producer = make_producer(mode)
    ready, boundary = asyncio.Event(), asyncio.Event()
    observed = []

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)

    async def receive(message):
        producer.input_text = message.content
        result = await producer.invoke()
        if mode.startswith("pydantic_ai"):
            messages = load_sdk("pydantic_ai.messages")
            assert any(
                isinstance(part, messages.UserPromptPart) and part.content == message.content
                for native in result.all_messages()
                for part in getattr(native, "parts", ())
            )
        else:
            assert producer.calls[-1] == message.content
        observed.append(message.content)
        native_ids = {}
        call_id = getattr(result, "tool_call_id", None)
        if isinstance(call_id, str):
            native_ids["tool_call_id"] = call_id
        await host.acknowledge_native_input(message, native_ids)
        return result

    async def invoke():
        ready.set()
        await boundary.wait()
        await host.consume_native_input()
        await host.consume_native_input()
        return "two native runs"

    task = await host.start_native(
        job.id, family=mode.split("-")[0], target=producer.target, invoke=invoke, input_handler=receive
    )
    try:
        await asyncio.wait_for(ready.wait(), 5)
        context = await host.get_current_execution_context(job.id)
        assert await host.send_operator_message(job.id, "first operator instruction")
        assert await host.send_operator_message(job.id, "second operator instruction")
        queued = [event for event in await events_for(host, job.id) if event.kind == "execution.native.input_queued"]
        assert len(queued) == 2 and not observed
        assert not any(event.kind == "execution.native.input_consumed" for event in await events_for(host, job.id))
        if cancel_before_boundary:
            await host.cancel_job(job.id)
            assert not task.done()
            boundary.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 15)
        else:
            boundary.set()
            assert await asyncio.wait_for(task, 15) == "two native runs"
    finally:
        boundary.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    events = await events_for(host, job.id)
    consumed = [event for event in events if event.kind == "execution.native.input_consumed"]
    undelivered = [event for event in events if event.kind == "execution.native.input_undelivered"]
    if cancel_before_boundary:
        assert not observed and not producer.calls and not consumed
        terminal, status = undelivered, "undelivered"
    else:
        assert observed == ["first operator instruction", "second operator instruction"]
        assert not undelivered
        terminal, status = consumed, "consumed"
    assert [event.payload["message_id"] for event in terminal] == [event.payload["message_id"] for event in queued]
    assert all(event.payload["delivery_status"] == status for event in terminal)
    for event in queued + terminal:
        assert event.payload["execution"]["job_id"] == job.id
        assert event.payload["execution"]["execution_token"] == context.execution_token
        assert event.payload["execution"] == queued[0].payload["execution"]
        assert event.payload["admission_id"] == queued[0].payload["admission_id"]
    with pytest.raises(StateConflictError):
        await host.send_operator_message(job.id, "must not become a managed resume")
