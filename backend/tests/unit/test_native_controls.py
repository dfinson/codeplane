"""Shared Runtime control routing, separate from individual SDK delivery qualification."""

import asyncio

import pytest

from backend.models.domain import StateConflictError
from backend.services.adapters.native_gates.controls import ControlPermit, StopEvidence
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for
from backend.tests.unit.test_native_bridge_runtime import target_for


async def test_runtime_operator_input_is_queued_then_exactly_acknowledged(composed_runtime):
    host, _, _, create = composed_runtime
    job = await create()
    target = target_for("http://127.0.0.1:1")
    ready, boundary, consumed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received = []

    async def handler(message):
        received.append(message)
        await host.acknowledge_native_input(message, {})
        consumed.set()
        return message.content

    async def invoke():
        ready.set()
        await boundary.wait()
        return await host.consume_native_input()

    task = await host.start_native(job.id, family="flowise", target=target, invoke=invoke, input_handler=handler)
    await asyncio.wait_for(ready.wait(), 5)
    context = await host.get_current_execution_context(job.id)
    assert context.execution_token is not None
    assert await host.send_operator_message(job.id, "actual operator input")
    before = await events_for(host, job.id)
    queued = next(event for event in before if event.kind == "execution.native.input_queued")
    assert queued.payload["delivery_status"] == "queued" and not consumed.is_set()
    assert not any(event.kind == "execution.native.input_consumed" for event in before)
    boundary.set()
    assert await asyncio.wait_for(task, 5) == "actual operator input"
    events = await events_for(host, job.id)
    delivered = next(event for event in events if event.kind == "execution.native.input_consumed")
    assert delivered.payload["message_id"] == queued.payload["message_id"] == received[0].id
    assert received[0].execution.execution_token == context.execution_token
    with pytest.raises(StateConflictError, match="original caller-owned"):
        await host.send_message(job.id, "cannot resume as a managed session")


@pytest.mark.parametrize("cancel", ["detach", "job", "owner"])
async def test_stop_permit_requires_runtime_intent_and_cannot_reopen_normal_work(cancel, composed_runtime):
    host, _, _, create = composed_runtime
    job = await create()
    target = target_for("http://127.0.0.1:1")
    ready, finish = asyncio.Event(), asyncio.Event()
    stopped = asyncio.Event()
    seen = []

    async def invoke():
        (execution,) = host._native_executions.for_job(job.id)
        binding = execution.gates.binding
        gate = await execution.gates.attach(target)

        async def stop(permit):
            await binding.require_control(permit)
            assert not binding.active
            seen.append(permit)
            stopped.set()
            return StopEvidence("acknowledged", source="local-handler")

        gate.cleanup(binding.controls.register(stop_handler=stop))
        ready.set()
        await finish.wait()

    task = await host.start_native(job.id, family="flowise", target=target, invoke=invoke)
    try:
        await asyncio.wait_for(ready.wait(), 5)
        (execution,) = host._native_executions.for_job(job.id)
        binding = execution.gates.binding
        with pytest.raises(StateConflictError, match="cancellation request"):
            await binding.controls.stop()
        with pytest.raises(StateConflictError, match="exact binding"):
            await binding.require_control(ControlPermit("fabricated", execution.identity))
        if cancel == "detach":
            await host.detach_native(execution.identity)
        elif cancel == "job":
            await host.cancel_job(job.id)
        else:
            task.cancel()
        await asyncio.wait_for(stopped.wait(), 5)
        assert len(seen) == 1
        await host._native_executions.cancel_controls(execution)
        assert len(seen) == 1 and not binding.active
        with pytest.raises(StateConflictError, match="exact binding"):
            await binding.require_control(seen[0])
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
    events = await events_for(host, job.id)
    assert any(
        event.kind == "execution.native.stop_result" and event.payload["status"] == "acknowledged" for event in events
    )


async def test_stop_is_reserved_before_pin_await_and_final_drain_fences_new_admission(composed_runtime, monkeypatch):
    host, _, _, create = composed_runtime
    job = await create()
    target = target_for("http://127.0.0.1:1")
    ready, finish, checking, release = (asyncio.Event() for _ in range(4))
    calls = []

    async def invoke():
        (execution,) = host._native_executions.for_job(job.id)
        binding = execution.gates.binding
        gate = await execution.gates.attach(target)

        async def stop(permit):
            await binding.require_control(permit)
            calls.append(permit.operation_id)
            return StopEvidence("acknowledged", source="local-handler")

        gate.cleanup(binding.controls.register(stop_handler=stop))
        ready.set()
        await finish.wait()

    task = await host.start_native(job.id, family="flowise", target=target, invoke=invoke)
    control = draining = None
    try:
        await asyncio.wait_for(ready.wait(), 5)
        (execution,) = host._native_executions.for_job(job.id)
        binding = execution.gates.binding
        actual = binding._control_retained

        async def paused():
            checking.set()
            await release.wait()
            return await actual()

        monkeypatch.setattr(binding, "_control_retained", paused)
        control = asyncio.create_task(host.detach_native(execution.identity))
        await asyncio.wait_for(checking.wait(), 5)
        draining = asyncio.create_task(binding.drain_controls())
        await asyncio.wait({draining}, timeout=0.05)
        assert not draining.done(), "Finalization missed an already-admitted stop awaiting its pin"
        release.set()
        await control
        await draining
        assert len(calls) == 1
        with pytest.raises(StateConflictError, match="clos"):
            await binding.controls.stop(refresh=True)
    finally:
        release.set()
        await asyncio.gather(*(item for item in (control, draining) if item is not None), return_exceptions=True)
        finish.set()
        await task
