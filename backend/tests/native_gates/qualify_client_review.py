"""Regressions for retained client outcomes and actual async callable objects."""

import asyncio

import pytest

from backend.services.adapters.native_gates.binding import NativeBinding, NativeGateDeniedError
from backend.services.adapters.profile_bridges import _client_calls
from backend.tests.native_gates.qualify_client_profiles import FAMILIES, approve_requests, client_target
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("failure", [False, True])
async def test_actual_local_function_emits_known_invocation_outcome(family, failure, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    calls = []

    def body(value):
        calls.append(value)
        if failure:
            raise ValueError("actual local function failure")
        # A false business value does not mean that invocation itself failed.
        return False

    async with client_target(family, body) as producer:

        async def invoke():
            return await producer.target.call_function("local", {"value": "local-body"}, tool_call_id="actual-call")

        task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
        if failure:
            with pytest.raises(ValueError, match="actual local function failure"):
                await task
        else:
            assert await task is False
        assert calls == ["local-body"] and producer.calls == []
    events = await events_for(host, job.id)
    outcomes = [event for event in events if event.kind in {"tool.call.completed", "tool.call.failed"}]
    assert len(outcomes) == 1 and outcomes[0].payload["success"] is not failure


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("failure", ["normalization", "observation"])
async def test_sdk_return_survives_local_diagnostics_failure(synchronous, failure, composed_runtime, monkeypatch):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    if failure == "normalization":
        original = _client_calls.canonical_record

        def broken(value):
            if "response" in value:
                raise ValueError("injected local normalization failure")
            return original(value)

        monkeypatch.setattr(_client_calls, "canonical_record", broken)
    else:
        original_record = _client_calls.record

        def broken_record(gate, kind, call, **fields):
            if kind == "response":
                raise ValueError("injected local observation failure")
            return original_record(gate, kind, call, **fields)

        monkeypatch.setattr(_client_calls, "record", broken_record)
    async with client_target("anthropic-client", lambda value: value, synchronous=synchronous) as producer:

        async def invoke():
            try:
                return await producer.invoke()
            except _client_calls.ClientReconciliationError:
                with pytest.raises(NativeGateDeniedError):
                    await producer.invoke()
                raise

        task = await host.start_native(job.id, family="anthropic-client", target=producer.target, invoke=invoke)
        errors = await asyncio.gather(task, return_exceptions=True)
        error: object = errors[0]
        assert isinstance(error, (ValueError, RuntimeError))
        assert len(producer.calls) == 1
    events = await events_for(host, job.id)
    receipt = next(event for event in events if event.kind == "execution.native.tool_result")
    assert receipt.payload["started"] is True and receipt.payload["succeeded"] is True
    usage = [event for event in events if event.kind == "execution.native.usage"]
    assert len(usage) == 1 and {item["amount"] for item in usage[0].payload["usage"]} == {"0", "7"}
    outcome = next(event for event in events if event.payload.get("outcome") == "local-reconciliation-required")
    assert outcome.payload["response_received"] is True and outcome.payload["response_complete"] is True
    assert outcome.payload["native_response_id"] == "message-native"
    assert outcome.payload["native_request_id"] == "request-native"
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


@pytest.mark.parametrize("event_type", ["chunk", "trace"])
async def test_bedrock_header_receipt_is_not_erased_by_stream_observation_failure(
    event_type, composed_runtime, monkeypatch
):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    observe = NativeBinding.observe

    def broken(binding, record):
        if record["type"] == "stream_event" and event_type in record["event"]:
            raise ValueError("injected stream observation failure")
        return observe(binding, record)

    monkeypatch.setattr(NativeBinding, "observe", broken)
    async with client_target("bedrock-client", lambda value: value) as producer:
        task = await host.start_native(job.id, family="bedrock-client", target=producer.target, invoke=producer.invoke)
        errors = await asyncio.gather(task, return_exceptions=True)
        error: object = errors[0]
        assert isinstance(error, (ValueError, RuntimeError)) and len(producer.calls) == 1
    events = await events_for(host, job.id)
    receipt = next(event for event in events if event.kind == "execution.native.tool_result")
    assert receipt.payload["started"] is True and receipt.payload["succeeded"] is None
    outcome = next(event for event in events if event.payload.get("outcome") == "local-reconciliation-required")
    assert outcome.payload["response_received"] is True and outcome.payload["response_complete"] is False
    assert outcome.payload["native_request_id"] == "request-native"
    assert outcome.payload["native_session_id"] == "session-native"
    usage = [event for event in events if event.kind == "execution.native.usage"]
    assert len(usage) == int(event_type == "trace")
    if usage:
        assert {item["amount"] for item in usage[0].payload["usage"]} == {"0", "7"}
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


@pytest.mark.parametrize("mode", ["positive", "deny", "cancel"])
async def test_async_callable_object_runs_inside_the_real_governed_lifetime(mode, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals, allow=mode != "deny")
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    if mode != "cancel":
        release.set()

    class AsyncBody:
        async def __call__(self, value):
            calls.append(value)
            entered.set()
            await release.wait()
            return f"result:{value}"

    async with client_target("anthropic-client", AsyncBody()) as producer:

        async def invoke():
            return await producer.target.call_function("local", {"value": "actual-body"}, tool_call_id="supplied-call")

        task = await host.start_native(job.id, family="anthropic-client", target=producer.target, invoke=invoke)
        try:
            if mode == "positive":
                assert await task == "result:actual-body"
                assert calls == ["actual-body"]
            elif mode == "deny":
                with pytest.raises(NativeGateDeniedError):
                    await task
                assert calls == []
            else:
                waiter = asyncio.create_task(entered.wait())
                done, _ = await asyncio.wait((task, waiter), timeout=5, return_when=asyncio.FIRST_COMPLETED)
                try:
                    assert waiter in done and entered.is_set(), f"Callable never entered; task_done={task.done()}"
                    (execution,) = host._native_executions.for_job(job.id)
                    await host.detach_native(execution.identity)
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
                    assert task.cancelled() and calls == ["actual-body"]
                finally:
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    receipts = [event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result"]
    if mode == "deny":
        assert receipts == []
    else:
        assert len(receipts) == 1 and receipts[0].payload["started"] is True
        assert receipts[0].payload["succeeded"] is (mode == "positive")
        observed = [
            event
            for event in await events_for(host, job.id)
            if event.kind in {"tool.call.completed", "tool.call.failed"}
        ]
        assert len(observed) == 1
        assert observed[0].payload.get("success") is (True if mode == "positive" else None)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == int(mode != "deny")


@pytest.mark.parametrize("synchronous_boundary", [False, True])
@pytest.mark.parametrize("family", FAMILIES)
async def test_runtime_operator_input_reaches_the_next_actual_sdk_request(
    family, synchronous_boundary, composed_runtime
):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    ready, boundary = asyncio.Event(), asyncio.Event()
    requests = {
        "openai-client": {"thread_id": "thread-native", "assistant_id": "assistant-native"},
        "anthropic-client": {"model": "inert", "max_tokens": 8, "messages": []},
        "gemini-client": {"model": "inert", "config": {"automatic_function_calling": {"disable": True}}},
        "mistral-client": {"model": "inert", "messages": []},
        "bedrock-client": {
            "agentId": "agent-native",
            "agentAliasId": "alias-native",
            "sessionId": "session-native",
            "enableTrace": True,
        },
    }
    async with client_target(family, lambda value: value, synchronous=synchronous_boundary) as producer:

        async def invoke():
            ready.set()
            await boundary.wait()
            if synchronous_boundary:
                return await asyncio.to_thread(producer.target.next_turn_sync, **requests[family])
            return await producer.target.next_turn(**requests[family])

        task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
        try:
            await asyncio.wait_for(ready.wait(), 5)
            content = f"Operator input for {family} through Runtime"
            assert await host.send_operator_message(job.id, content)
            before = await events_for(host, job.id)
            queued = next(event for event in before if event.kind == "execution.native.input_queued")
            assert producer.calls == []
            assert not any(event.kind == "execution.native.input_consumed" for event in before)
            boundary.set()
            assert await asyncio.wait_for(task, 10) is not None
            body = producer.calls[0][2]
            if family == "openai-client":
                assert body["content"] == content and body["role"] == "user"
                assert producer.calls[0][1].endswith("/threads/thread-native/messages")
                assert producer.calls[1][1].endswith("/threads/thread-native/runs")
            elif family == "gemini-client":
                assert body["contents"][-1] == {"role": "user", "parts": [{"text": content}]}
            elif family == "bedrock-client":
                assert body["inputText"] == content
            else:
                assert body["messages"][-1] == {"role": "user", "content": content}
        finally:
            boundary.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    events = await events_for(host, job.id)
    delivered = next(event for event in events if event.kind == "execution.native.input_consumed")
    assert delivered.payload["message_id"] == queued.payload["message_id"]
    assert delivered.payload["native_ids"]
    assert not any(event.kind == "execution.native.input_failed" for event in events)


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("control", ["detach", "cancel"])
@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("normal", "acknowledged"),
        ("cancel_requested", "cancel-requested"),
        ("cancel_loss", "delivery-unknown"),
    ],
)
async def test_runtime_controls_send_the_original_openai_cancel_after_revoke(
    synchronous, control, mode, status, composed_runtime
):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    ready, finish = asyncio.Event(), asyncio.Event()
    async with client_target("openai-client", lambda value: value, mode=mode, synchronous=synchronous) as producer:

        async def invoke():
            value = await producer.invoke()
            ready.set()
            await finish.wait()
            return value

        task = await host.start_native(job.id, family="openai-client", target=producer.target, invoke=invoke)
        try:
            await asyncio.wait_for(ready.wait(), 5)
            (execution,) = host._native_executions.for_job(job.id)
            if control == "detach":
                assert await host.detach_native(execution.identity) == "cancel-requested"
            else:
                await host.cancel(job.id)
            assert not execution.gates.binding.active
            assert [call[1] for call in producer.calls] == [
                "/v1/threads/thread-native/runs",
                "/v1/threads/thread-native/runs/run-native/cancel",
            ]
            await host._native_executions.cancel_controls(execution)
            assert len(producer.calls) == 2, "Uncertain native stop must not be retried automatically"
            events = await events_for(host, job.id)
            evidence = next(event for event in events if event.kind == "execution.native.stop_result")
            assert evidence.payload["status"] == status
            assert evidence.payload["native_ids"][0]["run_id"] == "run-native"
            assert evidence.payload["native_ids"][0]["thread_id"] == "thread-native"
            assert not execution.owner.done(), "Remote stop evidence must not fabricate local terminal state"
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failure", ["usage", "none"])
async def test_supplied_usage_failure_retains_the_known_sdk_response(failure, composed_runtime, monkeypatch):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    actual = NativeBinding.supplied_usage

    async def supplied(binding, operation_id, value):
        if failure == "usage":
            raise ValueError("injected local usage receipt failure")
        return await actual(binding, operation_id, value)

    monkeypatch.setattr(NativeBinding, "supplied_usage", supplied)
    async with client_target("anthropic-client", lambda value: value) as producer:
        task = await host.start_native(
            job.id, family="anthropic-client", target=producer.target, invoke=producer.invoke
        )
        if failure == "usage":
            with pytest.raises(_client_calls.ClientReconciliationError) as caught:
                await task
            assert caught.value.response_received and caught.value.response_complete
            assert caught.value.response.id == "message-native"
            assert caught.value.identities["request_id"] == "request-native"
        else:
            assert (await task).id == "message-native"
        assert len(producer.calls) == 1
    receipt = next(event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result")
    assert receipt.payload["started"] is True and receipt.payload["succeeded"] is True


@pytest.mark.parametrize("cancel", [False, True])
async def test_sync_function_returned_awaitable_remains_governed(cancel, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def body(value):
        calls.append(value)
        entered.set()
        await finish.wait()
        return value

    def factory(value):
        return body(value)

    async with client_target("anthropic-client", factory) as producer:

        async def invoke():
            return await producer.target.call_function("local", {"value": "actual awaited body"})

        task = await host.start_native(job.id, family="anthropic-client", target=producer.target, invoke=invoke)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert not task.done()
            assert not any(event.kind == "tool.call.completed" for event in await events_for(host, job.id))
            if cancel:
                (execution,) = host._native_executions.for_job(job.id)
                await host.detach_native(execution.identity)
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                finish.set()
                assert await task == "actual awaited body"
            assert calls == ["actual awaited body"]
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failure", ["denial", "observation"])
async def test_operator_delivery_receipt_tracks_actual_sdk_consumption_on_failure(
    failure, composed_runtime, monkeypatch
):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals, allow=failure != "denial")
    ready = asyncio.Event()
    record = _client_calls.record

    def observe(gate, kind, call, **fields):
        if failure == "observation" and kind == "response":
            raise ValueError("injected observation failure after actual input consumption")
        return record(gate, kind, call, **fields)

    monkeypatch.setattr(_client_calls, "record", observe)
    async with client_target("anthropic-client", lambda value: value) as producer:

        async def invoke():
            ready.set()
            return await producer.target.next_turn(model="inert", max_tokens=8, messages=[])

        task = await host.start_native(job.id, family="anthropic-client", target=producer.target, invoke=invoke)
        try:
            await asyncio.wait_for(ready.wait(), 5)
            assert await host.send_operator_message(job.id, "Actual operator input, not a hard-coded SDK followup")
            with pytest.raises(
                NativeGateDeniedError if failure == "denial" else _client_calls.ClientReconciliationError
            ):
                await task
            assert len(producer.calls) == int(failure == "observation")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    events = await events_for(host, job.id)
    consumed = [event for event in events if event.kind == "execution.native.input_consumed"]
    assert len(consumed) == int(failure == "observation")
    state = next(
        event
        for event in events
        if event.kind
        in {
            "execution.native.input_failed",
            "execution.native.input_processing_failed",
        }
    )
    assert state.payload["delivery_status"] == ("consumed" if failure == "observation" else "unconfirmed")
