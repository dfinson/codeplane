"""Pinned original LlamaIndex query/chat, callback and local FunctionTool paths."""

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import pytest

from backend.services.adapters.native_gates import GateAttachment
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.adapters.profile_bridges.llamaindex import LlamaIndexTarget, install
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

query_sdk = load_sdk("llama_index.core.base.base_query_engine")
callbacks = load_sdk("llama_index.core.callbacks")
tools = load_sdk("llama_index.core.tools")
responses = load_sdk("llama_index.core.base.response.schema")
llm_types = load_sdk("llama_index.core.base.llms.types")
if TYPE_CHECKING:

    class BaseQueryEngine:
        callback_manager: Any

        def __init__(self, callback_manager: Any) -> None: ...
        def query(self, query: str) -> Any: ...
        async def aquery(self, query: str) -> Any: ...
else:
    BaseQueryEngine = query_sdk.BaseQueryEngine


def make_engine(body, usage=None, async_fn=None):
    tool = tools.FunctionTool.from_defaults(fn=body, async_fn=async_fn)
    manager = callbacks.CallbackManager([])

    class Engine(BaseQueryEngine):
        def _query(self, bundle):
            with self.callback_manager.event(callbacks.CBEventType.QUERY, event_id="native-query"):
                result = tool.call(value=bundle.query_str)
                self.usage()
                return responses.Response(response=result.content)

        async def _aquery(self, bundle):
            with self.callback_manager.event(callbacks.CBEventType.QUERY, event_id="native-query"):
                result = await tool.acall(value=bundle.query_str)
                self.usage()
                return responses.Response(response=result.content)

        def usage(self):
            with self.callback_manager.event(callbacks.CBEventType.LLM, event_id="native-llm") as event:
                event.on_end(
                    payload={
                        "completion": llm_types.CompletionResponse(
                            text="local",
                            additional_kwargs=usage
                            if usage is not None
                            else {"prompt_tokens": 2, "completion_tokens": 0},
                        )
                    }
                )

        def _get_prompt_modules(self):
            return {}

    engine = Engine(manager)
    return LlamaIndexTarget(engine, (tool,)), manager


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("allow", [False, True])
async def test_llamaindex_original_query_and_function_boundaries(asynchronous, allow, binding_factory):
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    target, manager = make_engine(body)
    sibling = callbacks.LlamaDebugHandler(print_trace_on_end=False)
    manager.add_handler(sibling)
    gates, decision, _ = binding_factory("llamaindex")
    decision.allow = allow
    records: list[dict[str, Any]] = []
    receipts = []
    gates.binding._observation = records.append

    async def usage(operation_id, result):
        return None

    async def receipt(call, started, succeeded):
        receipts.append((started, succeeded))

    gates.binding._usage = usage
    gates.binding._on_result = receipt
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    install(attachment, target)
    try:
        pending = target.engine.aquery("local") if asynchronous else asyncio.to_thread(target.engine.query, "local")
        if allow:
            trace = load_sdk("opentelemetry.trace")
            span = trace.NonRecordingSpan(trace.SpanContext(trace_id=123, span_id=456, is_remote=False))
            with trace.use_span(span):
                assert str(await pending) == "local"
            assert calls == ["local"] and receipts == [(True, True), (True, True)]
            assert any(raw.get("event_id") == "native-query" for raw in records)
            assert any(raw.get("otel_trace_id") == f"{123:032x}" for raw in records)
            assert any(raw.get("native_span_id") for raw in records)
        else:
            with pytest.raises(PermissionError):
                await pending
            assert not calls and not receipts
    finally:
        attachment.detach()
    assert manager.handlers == [sibling]
    assert target.tools[0]._fn is body


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("allow", [False, True])
async def test_original_llamaindex_chat_without_remote_inference(asynchronous, allow, binding_factory):
    chat = load_sdk("llama_index.core.chat_engine.simple")
    memory = load_sdk("llama_index.core.memory")
    mock = load_sdk("llama_index.core.llms.mock")
    manager = callbacks.CallbackManager([])
    llm = mock.MockLLM(max_tokens=2, callback_manager=manager)
    history = memory.ChatMemoryBuffer.from_defaults(token_limit=100, tokenizer_fn=lambda value: value.split())
    engine = chat.SimpleChatEngine(llm, history, [], callback_manager=manager)
    target = LlamaIndexTarget(engine, ())
    gates, decision, _ = binding_factory("llamaindex")
    decision.allow = allow
    observed: list[dict[str, Any]] = []
    gates.binding._observation = observed.append

    async def usage(operation_id, result):
        return None

    gates.binding._usage = usage
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    install(attachment, target)
    try:
        invocation = engine.achat("local") if asynchronous else asyncio.to_thread(engine.chat, "local")
        if allow:
            assert str(await invocation) == "text text"
            assert len(decision.invocations) == 1
            assert observed
        else:
            with pytest.raises(PermissionError):
                await invocation
            assert not history.get_all()
    finally:
        attachment.detach()


@pytest.mark.parametrize("change", ["none", "partial-arguments", "revoke"])
async def test_llamaindex_tool_pending_fences_actual_partial_arguments(change, binding_factory):
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        return value

    target, _ = make_engine(body)
    gates, decision, _ = binding_factory("llamaindex")
    decision.pending = True
    gates.binding._observation = lambda record: None
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    install(attachment, target)
    target.tools[0].partial_params["value"] = "approved"
    task = asyncio.create_task(target.tools[0].acall())
    try:
        await asyncio.wait_for(decision.entered.wait(), 2)
        assert not calls and not task.done()
        if change == "partial-arguments":
            target.tools[0].partial_params["value"] = "changed"
        elif change == "revoke":
            gates.cancel()
        decision.release.set()
        if change == "none":
            assert (await task).content == "approved"
            assert calls == ["approved"]
        else:
            with pytest.raises(PermissionError):
                await task
            assert not calls
    finally:
        decision.release.set()
        await asyncio.gather(task, return_exceptions=True)
        attachment.detach()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_llamaindex_streaming_query_cannot_escape_retained_execution(asynchronous, binding_factory):
    consumed = []

    def stream():
        consumed.append("sync")
        yield "unconsumed"

    async def astream():
        consumed.append("async")
        yield "unconsumed"

    class Engine(BaseQueryEngine):
        def _query(self, bundle):
            return responses.StreamingResponse(response_gen=stream())

        async def _aquery(self, bundle):
            return responses.AsyncStreamingResponse(response_gen=astream())

        def _get_prompt_modules(self):
            return {}

    engine = Engine(callbacks.CallbackManager([]))
    target = LlamaIndexTarget(engine, ())
    gates, _, _ = binding_factory("llamaindex")
    gates.binding._observation = lambda record: None
    attachment = GateAttachment(gates.binding, target)
    attachment.claim(target)
    install(attachment, target)
    try:
        with pytest.raises(UnsupportedNativeHookError, match="retained consumption"):
            if asynchronous:
                await engine.aquery("local")
            else:
                await asyncio.to_thread(engine.query, "local")
        assert not consumed
    finally:
        attachment.detach()


@pytest.mark.parametrize(
    "cancel,usage,asynchronous",
    [(cancel, usage, True) for cancel in (False, True) for usage in ("positive", "zero", "missing")]
    + [(False, "positive", False), (True, "positive", False)],
)
async def test_llamaindex_persisted_runtime_usage_and_worker_retention(cancel, usage, asynchronous, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def body(value: str) -> str:
        calls.append(value)
        entered.set()
        assert finish.wait(10)
        return value

    target, manager = make_engine(
        body,
        {} if usage == "missing" else {"prompt_tokens": 0 if usage == "zero" else 2, "completion_tokens": 0},
    )

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    if not cancel:
        finish.set()
    task = await host.start_native(
        job.id,
        family="llamaindex",
        target=target,
        invoke=lambda: target.engine.aquery("local") if asynchronous else target.engine.query("local"),
        synchronous=not asynchronous,
    )
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
            assert str(await task) == "local"
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == ["local"] and not manager.handlers
    stored = await events_for(host, job.id)
    canonical = [event for event in stored if event.metadata.integration_binding_id is not None]
    assert any(event.payload.get("native_event_id") == "native-query" for event in canonical)
    assert any((event.raw_event or {}).get("event_id") == "native-llm" for event in canonical)
    usages = [event for event in stored if event.kind == "execution.native.usage"]
    assert len(usages) == (0 if usage == "missing" else 1)
    if usages:
        assert {item["unit"]: item["amount"] for item in usages[0].payload["usage"]} == {
            "input_tokens": "0" if usage == "zero" else "2",
            "output_tokens": "0",
        }
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 2
