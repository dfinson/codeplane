"""Original LlamaIndex query/chat and FunctionTool boundaries, not response guards."""

from __future__ import annotations

import asyncio
import sys
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass
from functools import wraps
from inspect import getclosurevars
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import (
    ExecutionEvidence,
    NativeCall,
    NativeGateDeniedError,
    encode_arguments,
)
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from backend.services.adapters.native_gates._optional import CallbackHandler
    from backend.services.adapters.native_gates.installation import GateAttachment
else:
    CallbackHandler = load_sdk("llama_index.core.callbacks.base_handler").BaseCallbackHandler

BaseQueryEngine = load_sdk("llama_index.core.base.base_query_engine").BaseQueryEngine
BaseChatEngine = load_sdk("llama_index.core.chat_engine.types").BaseChatEngine
FunctionTool = load_sdk("llama_index.core.tools").FunctionTool
_responses = load_sdk("llama_index.core.base.response.schema")
_usage: ContextVar[list[tuple[str, Any]] | None] = ContextVar("llamaindex_usage", default=None)


def _trace_identity() -> dict[str, Any]:
    result = {"native_span_id": load_sdk("llama_index.core.instrumentation.span").active_span_id.get()}
    # A supplied OTel context already loaded its API. Do not initialize a
    # provider or require an optional exporter merely to observe callbacks.
    trace = sys.modules.get("opentelemetry.trace")
    if trace is not None:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            result.update(otel_trace_id=f"{context.trace_id:032x}", otel_span_id=f"{context.span_id:016x}")
    return result


@dataclass
class LlamaIndexTarget:
    engine: Any
    tools: tuple[Any, ...]


def _arguments(value: Any, *, observation: bool = False) -> Any:
    if observation and isinstance(value, BaseException):
        return {"error_type": f"{type(value).__module__}.{type(value).__qualname__}", "message": str(value)}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, type) and issubclass(value, BaseModel):
        return value.model_json_schema()
    if is_dataclass(value) and not isinstance(value, type):
        return _arguments(asdict(value), observation=observation)
    if isinstance(value, dict):
        return {key: _arguments(item, observation=observation) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_arguments(item, observation=observation) for item in value]
    return value


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, LlamaIndexTarget) or not isinstance(target.engine, (BaseQueryEngine, BaseChatEngine)):
        raise TypeError("LlamaIndex requires the original query/chat engine and FunctionTools")
    engine = target.engine
    if any(not isinstance(tool, FunctionTool) or tool.requires_context for tool in target.tools):
        raise UnsupportedNativeHookError("Only context-free original LlamaIndex FunctionTools are qualified")
    gate.claim(engine)
    callbacks = getattr(engine, "callback_manager", None)
    if callbacks is None:
        raise UnsupportedNativeHookError("The LlamaIndex engine must expose its native callback manager")
    _observe_callbacks(gate, callbacks)
    methods = ("query", "aquery") if isinstance(engine, BaseQueryEngine) else ("chat", "achat")
    for name in methods:
        _request(gate, engine, name)
    for name in ("stream_chat", "astream_chat"):
        if hasattr(engine, name):

            def unsupported(*args: Any, **kwargs: Any) -> Any:
                raise UnsupportedNativeHookError("LlamaIndex streaming has no qualified retained-consumption binding")

            gate.patch(engine, name, unsupported)
    for tool in target.tools:
        _tool(gate, tool)


def _request(gate: GateAttachment, engine: Any, method: str) -> None:
    original = getattr(engine, method)
    entry = SimpleNamespace(invoke=original)
    gate.track_callable(entry, "invoke")

    def call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> NativeCall:
        return NativeCall(
            f"llamaindex.{method}",
            {"args": _arguments(args), "kwargs": _arguments(kwargs)},
            fingerprint=lambda: encode_arguments({"args": _arguments(args), "kwargs": _arguments(kwargs)}),
            evidence=ExecutionEvidence(False),
        )

    def observe(result: Any) -> None:
        gate.binding.observe({"type": "response", "operation": method, "response": str(result)})

    async def require_complete_response(result: Any) -> None:
        if isinstance(result, _responses.AsyncStreamingResponse):
            await result.response_gen.aclose()
            raise UnsupportedNativeHookError("LlamaIndex async streaming query results require retained consumption")
        if isinstance(result, _responses.StreamingResponse):
            result.response_gen.close()
            raise UnsupportedNativeHookError("LlamaIndex streaming query results require retained consumption")

    @wraps(original)
    def synchronous(*args: Any, **kwargs: Any) -> Any:
        invocation = call(args, kwargs)
        if not gate.allow_sync(invocation):
            raise NativeGateDeniedError("LlamaIndex request denied before query/chat invocation")
        supplied: list[tuple[str, Any]] = []
        token = _usage.set(supplied)
        try:

            def invoke() -> Any:
                result = entry.invoke(*args, **kwargs)
                asyncio.run_coroutine_threadsafe(require_complete_response(result), gate.binding.loop).result()
                return result

            result = gate.complete_sync(invocation, invoke)
            observe(result)
            return result
        finally:
            try:
                for event_id, response in supplied:
                    asyncio.run_coroutine_threadsafe(
                        gate.binding.supplied_usage(f"{invocation.operation_id}:{event_id}", response),
                        gate.binding.loop,
                    ).result()
            finally:
                _usage.reset(token)

    @wraps(original)
    async def asynchronous(*args: Any, **kwargs: Any) -> Any:
        invocation = call(args, kwargs)
        if not await gate.allow(invocation):
            raise NativeGateDeniedError("LlamaIndex request denied before query/chat invocation")
        supplied: list[tuple[str, Any]] = []
        token = _usage.set(supplied)
        try:

            async def invoke() -> Any:
                result = await entry.invoke(*args, **kwargs)
                await require_complete_response(result)
                return result

            result = await gate.complete(invocation, invoke)
            observe(result)
            return result
        finally:
            try:
                for event_id, response in supplied:
                    await gate.binding.supplied_usage(f"{invocation.operation_id}:{event_id}", response)
            finally:
                _usage.reset(token)

    gate.patch(engine, method, asynchronous if asyncio.iscoroutinefunction(original) else synchronous)


def _tool(gate: GateAttachment, tool: Any) -> None:
    gate.claim(tool)
    original_call, original_acall = tool.call, tool.acall
    original_function = tool._fn
    async_fn = tool._async_fn
    gate.track_callable(tool, "_fn")
    conversion = load_sdk("llama_index.core.tools.function_tool").sync_to_async
    converted = conversion(original_function)
    if getattr(async_fn, "__code__", None) is converted.__code__:
        selected = getclosurevars(async_fn).nonlocals.get("fn")
        if selected is original_function:
            async_fn = conversion(tool._fn)
        elif callable(selected):
            entry = SimpleNamespace(invoke=selected)
            gate.track_callable(entry, "invoke")
            async_fn = conversion(entry.invoke)
        else:
            raise UnsupportedNativeHookError("LlamaIndex async conversion has no qualified original callable")
    else:
        entry = SimpleNamespace(invoke=async_fn)
        gate.track_callable(entry, "invoke")
        async_fn = entry.invoke

    async def retained(*args: Any, **kwargs: Any) -> Any:
        return await retain_task(asyncio.create_task(async_fn(*args, **kwargs)))

    gate.patch(tool, "_async_fn", retained)

    def call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> NativeCall:
        def actual() -> dict[str, Any]:
            return {
                "args": _arguments(args),
                "kwargs": _arguments({**tool._field_defaults, **tool.partial_params, **kwargs}),
                "tool_schema": tool.metadata.to_openai_tool(),
                "fn": id(tool._fn),
                "async_fn": id(tool._async_fn),
            }

        return NativeCall(
            tool.metadata.get_name(),
            actual(),
            definition=tool.metadata.description,
            fingerprint=lambda: encode_arguments(actual()),
            evidence=ExecutionEvidence(False),
        )

    def emit(kind: str, invocation: NativeCall, result: Any = None) -> None:
        gate.binding.observe(
            {
                "type": kind,
                "tool_name": invocation.name,
                "arguments": invocation.arguments,
                # FunctionTool supplies no native call ID. The local operation ID
                # remains explicitly app-owned instead of impersonating a vendor ID.
                "operation_id": invocation.operation_id,
                "result": str(result) if result is not None else None,
            }
        )

    def synchronous(*args: Any, **kwargs: Any) -> Any:
        invocation = call(args, kwargs)
        if not gate.allow_sync(invocation):
            raise NativeGateDeniedError("LlamaIndex function denied before invocation")
        emit("tool_started", invocation)
        try:
            result = gate.complete_sync(invocation, lambda: original_call(*args, **kwargs))
        except BaseException:
            emit("tool_failed", invocation)
            raise
        emit("tool_completed", invocation, result)
        return result

    async def asynchronous(*args: Any, **kwargs: Any) -> Any:
        invocation = call(args, kwargs)
        if not await gate.allow(invocation):
            raise NativeGateDeniedError("LlamaIndex function denied before invocation")
        emit("tool_started", invocation)
        try:
            result = await gate.complete(invocation, lambda: original_acall(*args, **kwargs))
        except BaseException:
            emit("tool_failed", invocation)
            raise
        emit("tool_completed", invocation, result)
        return result

    gate.patch(tool, "call", synchronous)
    gate.patch(tool, "acall", asynchronous)


def _observe_callbacks(gate: GateAttachment, manager: Any) -> None:
    class Observer(CallbackHandler):
        def on_event_start(
            self, event_type: Any, payload: Any = None, event_id: str = "", parent_id: str = "", **kwargs: Any
        ) -> str:
            gate.binding.observe(
                {
                    "type": "callback_started",
                    "native_kind": str(event_type.value),
                    "event_id": event_id or None,
                    "parent_id": parent_id or None,
                    "payload": _arguments(payload, observation=True),
                    **_trace_identity(),
                }
            )
            return event_id

        def on_event_end(self, event_type: Any, payload: Any = None, event_id: str = "", **kwargs: Any) -> None:
            gate.binding.observe(
                {
                    "type": "callback_completed",
                    "native_kind": str(event_type.value),
                    "event_id": event_id or None,
                    "payload": _arguments(payload, observation=True),
                    **_trace_identity(),
                }
            )
            if event_type.value == "llm" and payload is not None and (supplied := _usage.get()) is not None:
                response = payload.get("response") or payload.get("completion")
                if response is not None and event_id and not any(identity == event_id for identity, _ in supplied):
                    supplied.append((event_id, response))

        def start_trace(self, trace_id: str | None = None) -> None:
            gate.binding.observe({"type": "trace_started", "trace_label": trace_id})

        def end_trace(self, trace_id: str | None = None, trace_map: Any = None) -> None:
            gate.binding.observe({"type": "trace_completed", "trace_label": trace_id, "trace_map": trace_map})

    observer = Observer([], [])
    manager.add_handler(observer)

    def remove() -> None:
        manager.handlers[:] = [item for item in manager.handlers if item is not observer]

    gate.cleanup(remove)
