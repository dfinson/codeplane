"""Actual pinned SDK clients through inert transports and persisted Runtime ownership."""

import asyncio
import inspect
import io
import json
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.adapters.profile_bridges._client_calls import LocalFunction
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

FAMILIES = ("openai-client", "anthropic-client", "gemini-client", "mistral-client", "bedrock-client")


async def sdk_call(method, *args, **kwargs):
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await retain_task(asyncio.create_task(asyncio.to_thread(method, *args, **kwargs)))


def payload(family):
    if family == "openai-client":
        return {
            "id": "run-native",
            "object": "thread.run",
            "created_at": 1,
            "assistant_id": "assistant-native",
            "thread_id": "thread-native",
            "status": "requires_action",
            "model": "reported-model",
            "instructions": "",
            "tools": [],
            "metadata": {},
            "parallel_tool_calls": False,
            "temperature": 0,
            "top_p": 1,
            "response_format": "auto",
            "tool_choice": "auto",
            "truncation_strategy": {"type": "auto"},
            "required_action": {
                "type": "submit_tool_outputs",
                "submit_tool_outputs": {
                    "tool_calls": [
                        {
                            "id": "tool-native",
                            "type": "function",
                            "function": {"name": "local", "arguments": '{"value":"local-input"}'},
                        }
                    ]
                },
            },
            "usage": None,
        }
    if family == "anthropic-client":
        return {
            "id": "message-native",
            "type": "message",
            "role": "assistant",
            "model": "reported-model",
            "content": [{"type": "tool_use", "id": "tool-native", "name": "local", "input": {"value": "local-input"}}],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 7},
        }
    if family == "gemini-client":
        return {
            "responseId": "response-native",
            "modelVersion": "reported-model",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"functionCall": {"id": "tool-native", "name": "local", "args": {"value": "local-input"}}}
                        ],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 7, "totalTokenCount": 7},
        }
    return {
        "id": "completion-native",
        "object": "chat.completion",
        "created": 1,
        "model": "reported-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tool-native",
                            "type": "function",
                            "function": {"name": "local", "arguments": '{"value":"local-input"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 7, "total_tokens": 7},
    }


def event_message(kind, data):
    header = bytearray()
    for key, value in ((":message-type", "event"), (":event-type", kind), (":content-type", "application/json")):
        name, text = key.encode(), value.encode()
        header += bytes([len(name)]) + name + b"\x07" + struct.pack(">H", len(text)) + text
    body = json.dumps(data).encode()
    prelude = struct.pack(">II", 16 + len(header) + len(body), len(header))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + header + body
    return message + struct.pack(">I", zlib.crc32(message))


@asynccontextmanager
async def client_target(family, body, *, mode="normal", synchronous=False):
    from backend.services.adapters.profile_bridges.anthropic_client import AnthropicClientTarget
    from backend.services.adapters.profile_bridges.bedrock_client import BedrockClientTarget
    from backend.services.adapters.profile_bridges.gemini_client import GeminiClientTarget
    from backend.services.adapters.profile_bridges.mistral_client import MistralClientTarget
    from backend.services.adapters.profile_bridges.openai_client import OpenAIClientTarget

    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    thread_release = threading.Event()
    loop = asyncio.get_running_loop()
    release.set()
    thread_release.set()
    clients = []
    library = load_sdk("httpx2") if family in {"anthropic-client", "mistral-client"} else httpx

    async def transport(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        entered.set()
        if mode in {"blocked", "late"}:
            try:
                await release.wait()
            except asyncio.CancelledError:
                if mode != "late":
                    raise
                await release.wait()
        if mode == "loss":
            raise library.ReadError("Inert response loss", request=request)
        document = payload(family)
        if family == "openai-client" and request.url.path.endswith("/messages"):
            document = {
                "id": "message-followup",
                "object": "thread.message",
                "created_at": 1,
                "thread_id": "thread-native",
                "role": "user",
                "metadata": {},
                "content": [{"type": "text", "text": {"value": "followup", "annotations": []}}],
            }
        if mode == "missing_usage":
            document.pop("usage", None)
            document.pop("usageMetadata", None)
            if family == "mistral-client":
                # The SDK requires the usage object, but its counters may be omitted.
                document["usage"] = {}
        if mode == "invalid_usage":
            document.pop("usage", None)
        if request.url.path.endswith("/cancel"):
            document["status"] = "cancelled"
            document["required_action"] = None
        elif request.url.path.endswith("/submit_tool_outputs") or request.method == "GET":
            document["status"] = "completed"
            document["required_action"] = None
            document["usage"] = {"prompt_tokens": 0, "completion_tokens": 7, "total_tokens": 7}
            if request.method == "GET":
                document["id"] = request.url.path.rsplit("/", 1)[-1]
        return library.Response(
            200, json=document, headers={"x-request-id": "request-native", "request-id": "request-native"}
        )

    tools = (LocalFunction("local", body, "Inert local function"),)
    if family == "bedrock-client":
        boto3, botocore = load_sdk("boto3"), load_sdk("botocore")
        config_type = load_sdk("botocore.config").Config
        aws_response = load_sdk("botocore.awsrequest").AWSResponse
        client = boto3.client(
            "bedrock-agent-runtime",
            region_name="us-east-1",
            endpoint_url="http://local.invalid",
            config=config_type(signature_version=botocore.UNSIGNED, retries={"total_max_attempts": 1}),
        )
        wire = (
            event_message("chunk", {"bytes": "TG9jYWwgYW5zd2Vy"})
            + event_message(
                "trace",
                {
                    "sessionId": "session-native",
                    "agentId": "agent-native",
                    "trace": {
                        "orchestrationTrace": {
                            "modelInvocationOutput": {
                                "traceId": "trace-native",
                                "metadata": {}
                                if mode == "missing_usage"
                                else {"usage": {"inputTokens": 0, "outputTokens": 7}},
                            }
                        }
                    },
                },
            )
            + event_message(
                "returnControl",
                {
                    "invocationId": "tool-native",
                    "invocationInputs": [
                        {
                            "functionInvocationInput": {
                                "actionGroup": "local-group",
                                "function": "local",
                                "parameters": [{"name": "value", "type": "string", "value": "local-input"}],
                            }
                        }
                    ],
                },
            )
        )

        class RawStream(io.BytesIO):
            def stream(self, amt=None):
                while value := self.read(97):
                    yield value

        def send(request):
            calls.append((request.method, request.url, json.loads(request.body)))
            loop.call_soon_threadsafe(entered.set)
            if mode == "loss":
                raise load_sdk("botocore.exceptions").ReadTimeoutError(endpoint_url=request.url)
            assert thread_release.wait(10)
            return aws_response(
                request.url,
                200,
                {
                    "content-type": "application/vnd.amazon.eventstream",
                    "x-amzn-requestid": "request-native",
                    "x-amz-bedrock-agent-session-id": "session-native",
                },
                RawStream(wire),
            )

        client._endpoint.http_session.send = send
        target = BedrockClientTarget(client, tools)

        async def invoke():
            return await retain_task(
                asyncio.create_task(
                    asyncio.to_thread(
                        client.invoke_agent,
                        agentId="agent-native",
                        agentAliasId="alias-native",
                        sessionId="session-native",
                        inputText="local input",
                        enableTrace=True,
                    )
                )
            )
    else:
        async_client = library.AsyncClient(transport=library.MockTransport(transport), trust_env=False)
        sync_client = library.Client(
            transport=library.MockTransport(
                lambda request: asyncio.run_coroutine_threadsafe(transport(request), loop).result()
            ),
            trust_env=False,
        )
        clients.append(async_client)
        clients.append(sync_client)
        if family == "openai-client":
            sdk = load_sdk("openai")
            client_type = sdk.OpenAI if synchronous else sdk.AsyncOpenAI
            client = client_type(
                api_key="inert-local-placeholder",
                base_url="http://local.invalid/v1",
                max_retries=0,
                http_client=sync_client if synchronous else async_client,
            )
            target = OpenAIClientTarget(client, tools)

            async def invoke():
                return await sdk_call(client.beta.threads.runs.create, "thread-native", assistant_id="assistant-native")
        elif family == "anthropic-client":
            sdk = load_sdk("anthropic")
            client_type = sdk.Anthropic if synchronous else sdk.AsyncAnthropic
            client = client_type(
                api_key="inert-local-placeholder",
                base_url="http://local.invalid",
                max_retries=0,
                http_client=sync_client if synchronous else async_client,
            )
            target = AnthropicClientTarget(client, tools)

            async def invoke():
                return await sdk_call(
                    client.messages.create, model="inert", max_tokens=8, messages=[{"role": "user", "content": "local"}]
                )
        elif family == "gemini-client":
            sdk = load_sdk("google.genai")
            client = sdk.Client(
                api_key="inert-local-placeholder",
                http_options=sdk.types.HttpOptions(
                    base_url="http://local.invalid",
                    httpx_async_client=async_client,
                    httpx_client=sync_client,
                    retry_options=sdk.types.HttpRetryOptions(attempts=1),
                ),
            )
            target = GeminiClientTarget(client, tools)

            async def invoke():
                return await sdk_call(
                    client.models.generate_content if synchronous else client.aio.models.generate_content,
                    model="inert",
                    contents="local",
                    config={"automatic_function_calling": {"disable": True}},
                )
        else:
            client = load_sdk("mistralai.client").Mistral(
                api_key="inert-local-placeholder",
                server_url="http://local.invalid",
                async_client=async_client,
                client=sync_client,
                retry_config=None,
            )
            target = MistralClientTarget(client, tools)

            async def invoke():
                return await sdk_call(
                    client.chat.complete if synchronous else client.chat.complete_async,
                    model="inert",
                    messages=[{"role": "user", "content": "local"}],
                )

    try:
        yield SimpleNamespace(
            target=target, invoke=invoke, calls=calls, entered=entered, release=release, thread_release=thread_release
        )
    finally:
        release.set()
        thread_release.set()
        for item in clients:
            if isinstance(item, library.AsyncClient):
                await item.aclose()
            else:
                item.close()
        if family == "bedrock-client":
            client.close()
        if family == "gemini-client":
            await client.aio.aclose()
            client.close()


def approve_requests(host, approvals, *, allow=True):
    seen = []

    async def decide(event):
        if event.kind == "permission.requested":
            seen.append(event)
            await approvals.resolve(event.payload["approval_id"], "approved" if allow else "rejected")

    host._event_bus.subscribe(decide)
    return seen


@pytest.mark.parametrize(
    ("family", "synchronous"),
    [
        *((family, False) for family in FAMILIES),
        *((family, True) for family in FAMILIES[:-1]),
    ],
)
async def test_real_client_request_function_usage_and_durable_identity(family, synchronous, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    seen = approve_requests(host, approvals)
    body_calls = []

    def body(value: str) -> str:
        body_calls.append(value)
        return f"result:{value}"

    async with client_target(family, body, synchronous=synchronous) as producer:

        async def invoke():
            result = await producer.invoke()
            if family == "openai-client":
                native_id = result.required_action.submit_tool_outputs.tool_calls[0].id
                run_id = result.id
            elif family == "anthropic-client":
                native_id, run_id = result.content[0].id, result.id
            elif family == "gemini-client":
                native_id, run_id = result.function_calls[0].id, result.response_id
            elif family == "mistral-client":
                native_id, run_id = result.choices[0].message.tool_calls[0].id, result.id
            else:
                native_id, run_id = result["completion"][-1]["returnControl"]["invocationId"], result["sessionId"]
            await producer.target.call_function(
                "local", {"value": "local-input"}, tool_call_id=native_id, run_id=run_id
            )
            if family == "openai-client":
                result = await sdk_call(
                    producer.target.client.beta.threads.runs.submit_tool_outputs,
                    result.id,
                    thread_id=result.thread_id,
                    tool_outputs=[{"tool_call_id": native_id, "output": "result:local-input"}],
                )
            return result

        task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
        result = await asyncio.wait_for(task, 15)
        assert result is not None and len(producer.calls) == (2 if family == "openai-client" else 1)
    expected_calls = 3 if family == "openai-client" else 2
    assert len(seen) == expected_calls and body_calls == ["local-input"]
    events = await events_for(host, job.id)
    receipts = [event for event in events if event.kind == "execution.native.tool_result"]
    assert len(receipts) == expected_calls and all(event.payload["started"] is True for event in receipts)
    usage = [event for event in events if event.kind == "execution.native.usage"]
    assert len(usage) == 1
    assert {(item["unit"], item["amount"]) for item in usage[0].payload["usage"]} == {
        ("input_tokens", "0"),
        ("output_tokens", "7"),
    }
    assert all(
        item["producer"] == family and item["role"] == "primary" and item["currency"] is None
        for item in usage[0].payload["usage"]
    )
    assert any(
        event.kind == "tool.call.completed" and event.payload["tool_call_id"] == "tool-native" for event in events
    )
    assert any(event.kind == "task.completed" and event.payload.get("response") for event in events)
    if family in {"openai-client", "anthropic-client", "mistral-client", "bedrock-client"}:
        assert all(
            event.payload["native_request_id"] == "request-native" for event in events if event.kind == "task.completed"
        )
    assert (await governance._ledger.job_totals(job.id)).executed_calls == expected_calls
    for event in events:
        await governance.observe(event)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == expected_calls
    assert not host._native_executions.for_job(job.id)


@pytest.mark.parametrize("family", FAMILIES)
async def test_denial_prevents_actual_native_transport(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals, allow=False)
    async with client_target(family, lambda value: value) as producer:
        task = await host.start_native(job.id, family=family, target=producer.target, invoke=producer.invoke)
        with pytest.raises(NativeGateDeniedError):
            await task
        assert producer.calls == []
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("family", FAMILIES)
async def test_response_loss_is_not_retried_or_claimed_executed(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    errors = {
        "openai-client": load_sdk("openai").APIConnectionError,
        "anthropic-client": load_sdk("anthropic").APIConnectionError,
        "gemini-client": httpx.ReadError,
        "mistral-client": load_sdk("httpx2").ReadError,
        "bedrock-client": load_sdk("botocore.exceptions").ReadTimeoutError,
    }
    async with client_target(family, lambda value: value, mode="loss") as producer:
        task = await host.start_native(job.id, family=family, target=producer.target, invoke=producer.invoke)
        with pytest.raises(errors[family]):
            await task
        assert len(producer.calls) == 1
    events = await events_for(host, job.id)
    receipt = next(event for event in events if event.kind == "execution.native.tool_result")
    assert receipt.payload["started"] is None and receipt.payload["succeeded"] is None
    assert any(event.payload.get("outcome") == "delivery-unknown" for event in events)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("family", FAMILIES)
async def test_missing_response_usage_remains_unknown(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    async with client_target(family, lambda value: value, mode="missing_usage") as producer:
        task = await host.start_native(job.id, family=family, target=producer.target, invoke=producer.invoke)
        assert await task is not None
        assert len(producer.calls) == 1
    events = await events_for(host, job.id)
    assert not any(event.kind == "execution.native.usage" for event in events)
    totals = await governance._ledger.job_totals(job.id)
    assert totals.executed_calls == 1 and not totals.verified_usd_present


async def test_mistral_required_usage_object_failure_is_not_hidden(composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    async with client_target("mistral-client", lambda value: value, mode="invalid_usage") as producer:
        task = await host.start_native(job.id, family="mistral-client", target=producer.target, invoke=producer.invoke)
        with pytest.raises(load_sdk("mistralai.client.errors").ResponseValidationError):
            await task
        assert len(producer.calls) == 1
    events = await events_for(host, job.id)
    assert not any(event.kind in {"execution.native.usage", "task.completed"} for event in events)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("family", FAMILIES)
async def test_input_at_next_native_request_boundary_requires_fresh_admission(family, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    decisions = approve_requests(host, approvals)
    async with client_target(family, lambda value: value) as producer:

        async def invoke():
            await producer.invoke()
            client = producer.target.client
            if family == "openai-client":
                return await sdk_call(
                    client.beta.threads.messages.create, "thread-native", role="user", content="followup"
                )
            if family == "anthropic-client":
                return await sdk_call(
                    client.messages.create,
                    model="inert",
                    max_tokens=8,
                    messages=[{"role": "user", "content": "followup"}],
                )
            if family == "gemini-client":
                return await sdk_call(
                    client.aio.models.generate_content,
                    model="inert",
                    contents="followup",
                    config={"automatic_function_calling": {"disable": True}},
                )
            if family == "mistral-client":
                return await sdk_call(
                    client.chat.complete_async, model="inert", messages=[{"role": "user", "content": "followup"}]
                )
            return await sdk_call(
                client.invoke_agent,
                agentId="agent-native",
                agentAliasId="alias-native",
                sessionId="session-native",
                inputText="followup",
            )

        task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
        await task
        assert len(producer.calls) == len(decisions) == 2
        assert "followup" in json.dumps(producer.calls[-1][2])
    events = await events_for(host, job.id)
    receipts = [event for event in events if event.kind == "execution.native.tool_result"]
    assert len(receipts) == 2 and len({event.id for event in receipts}) == 2


@pytest.mark.parametrize("family", FAMILIES)
async def test_local_function_denial_is_separate_from_request_admission(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    decisions = 0

    async def decide(event):
        nonlocal decisions
        if event.kind == "permission.requested":
            decisions += 1
            await approvals.resolve(event.payload["approval_id"], "approved" if decisions == 1 else "rejected")

    host._event_bus.subscribe(decide)
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    async with client_target(family, body) as producer:

        async def invoke():
            await producer.invoke()
            return await producer.target.call_function(
                "local", {"value": "local-input"}, tool_call_id="caller-supplied"
            )

        task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
        with pytest.raises(NativeGateDeniedError):
            await task
        assert len(producer.calls) == 1 and calls == []
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


@pytest.mark.parametrize("family", FAMILIES[:-1])
async def test_unsupported_stream_is_refused_before_transport(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    async with client_target(family, lambda value: value) as producer:
        client = producer.target.client

        async def invoke():
            if family == "openai-client":
                return await client.beta.threads.runs.create(
                    "thread-native", assistant_id="assistant-native", stream=True
                )
            if family == "anthropic-client":
                return await client.messages.create(model="inert", max_tokens=8, messages=[], stream=True)
            if family == "gemini-client":
                return client.aio.models.generate_content_stream(model="inert", contents="local")
            return client.chat.stream_async(model="inert", messages=[])

        task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
        with pytest.raises(UnsupportedNativeHookError):
            await task
        assert producer.calls == []
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("family", FAMILIES)
async def test_client_replacement_while_approval_pending_cannot_dispatch(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    async with client_target(family, lambda value: value) as producer:

        async def replace(event):
            if event.kind == "permission.requested":
                producer.target.client = object()
                await approvals.resolve(event.payload["approval_id"], "approved")

        host._event_bus.subscribe(replace)
        task = await host.start_native(job.id, family=family, target=producer.target, invoke=producer.invoke)
        with pytest.raises(NativeGateDeniedError):
            await task
        assert producer.calls == []
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("family", FAMILIES[:-1])
@pytest.mark.parametrize("late", [False, True])
async def test_cancel_distinguishes_stopped_waiting_from_late_native_response(family, late, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    async with client_target(family, lambda value: value, mode="late" if late else "blocked") as producer:
        producer.release.clear()
        task = await host.start_native(job.id, family=family, target=producer.target, invoke=producer.invoke)
        try:
            await asyncio.wait_for(producer.entered.wait(), 10)
            (execution,) = host._native_executions.for_job(job.id)
            assert await host.detach_native(execution.identity) == "cancel-requested"
            if late:
                await asyncio.wait({task}, timeout=0.1)
                assert not task.done() and host._native_executions.for_job(job.id)
                producer.release.set()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 10)
        finally:
            producer.release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert len(producer.calls) == 1 and task.cancelled()
    events = await events_for(host, job.id)
    receipt = next(event for event in events if event.kind == "execution.native.tool_result")
    assert receipt.payload["started"] is (True if late else None)
    assert receipt.payload["succeeded"] is (True if late else None)
    assert any(event.payload.get("outcome") == "local-stop-requested" for event in events)
    if late:
        assert any(event.kind == "task.completed" and event.payload["local_cancel_requested"] for event in events)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == int(late)


async def test_bedrock_cancel_keeps_sync_transport_owned_until_it_releases(composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    async with client_target("bedrock-client", lambda value: value) as producer:
        producer.thread_release.clear()
        task = await host.start_native(job.id, family="bedrock-client", target=producer.target, invoke=producer.invoke)
        try:
            await asyncio.wait_for(producer.entered.wait(), 10)
            (execution,) = host._native_executions.for_job(job.id)
            assert await host.detach_native(execution.identity) == "cancel-requested"
            await asyncio.wait({task}, timeout=0.1)
            assert not task.done() and host._native_executions.for_job(job.id)
        finally:
            producer.thread_release.set()
            await asyncio.gather(task, return_exceptions=True)
    events = await events_for(host, job.id)
    assert any(
        event.kind == "task.progress" and event.payload["native_session_id"] == "session-native" for event in events
    )
    assert any(event.payload.get("outcome") == "delivery-unknown" for event in events)
    assert not host._native_executions.for_job(job.id)


async def test_openai_cancel_records_returned_state_and_usage_is_not_recounted(composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    approve_requests(host, approvals)
    async with client_target("openai-client", lambda value: value) as producer:

        async def invoke():
            client = producer.target.client
            run = await producer.invoke()
            canceled = await client.beta.threads.runs.cancel(run.id, thread_id=run.thread_id)
            assert canceled.status == "cancelled"
            await client.beta.threads.runs.retrieve("run-usage", thread_id=run.thread_id)
            return await client.beta.threads.runs.retrieve("run-usage", thread_id=run.thread_id)

        task = await host.start_native(job.id, family="openai-client", target=producer.target, invoke=invoke)
        await task
        assert len(producer.calls) == 4
    events = await events_for(host, job.id)
    assert len([event for event in events if event.kind == "execution.native.usage"]) == 1
    assert any(event.payload.get("response", {}).get("status") == "cancelled" for event in events)
    assert all(
        event.payload["native_run_id"] in {"run-native", "run-usage"}
        and event.payload["native_request_id"] == "request-native"
        for event in events
        if event.kind == "task.completed"
    )


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("change", ["revoke", "arguments"])
async def test_queued_local_leaf_rechecks_the_exact_approved_action(family, change, composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    approved = asyncio.Event()
    approvals_seen = 0

    async def decide(event):
        nonlocal approvals_seen
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")
            approvals_seen += 1
            if approvals_seen == 2:
                approved.set()

    host._event_bus.subscribe(decide)
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(lambda: None)
    previous = vars(loop)["_default_executor"]
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="client-leaf-qualification")
    release = threading.Event()
    occupied = asyncio.Event()
    task = None
    blocker = None
    async with client_target(family, body) as producer:
        arguments = {"value": "local-input"}

        async def invoke():
            nonlocal blocker
            await producer.invoke()
            # Bedrock's request itself needs a worker; occupy only after it returns.
            loop.set_default_executor(pool)

            def occupy():
                loop.call_soon_threadsafe(occupied.set)
                assert release.wait(10)

            blocker = pool.submit(occupy)
            await occupied.wait()
            return await producer.target.call_function("local", arguments, tool_call_id="supplied-call")

        try:
            task = await host.start_native(job.id, family=family, target=producer.target, invoke=invoke)
            await asyncio.wait_for(approved.wait(), 10)
            async with asyncio.timeout(5):
                while not pool._work_queue.qsize():
                    await asyncio.sleep(0.001)
            if change == "revoke":
                (execution,) = host._native_executions.for_job(job.id)
                await host.detach_native(execution.identity)
            else:
                arguments["value"] = "changed after approval"
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert calls == []
        finally:
            release.set()
            if blocker is not None:
                await asyncio.wrap_future(blocker)
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            loop.set_default_executor(previous)
            pool.shutdown(wait=True)
    receipts = [event for event in await events_for(host, job.id) if event.kind == "execution.native.tool_result"]
    assert any(event.payload["started"] is False and event.payload["succeeded"] is False for event in receipts)
