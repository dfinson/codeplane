"""Private mechanics for the five fixed native client bridges, not a plugin registry."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from backend.services.adapters.native_gates.binding import (
    ExecutionEvidence,
    NativeCall,
    NativeGateDeniedError,
    encode_arguments,
)
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.services.adapters.native_gates import GateAttachment


@dataclass
class LocalFunction:
    name: str
    invoke: Callable[..., Any]
    description: str = ""


class FunctionDispatcher:
    functions: tuple[LocalFunction, ...]

    async def call_function(
        self, name: str, arguments: dict[str, Any], *, tool_call_id: str | None = None, run_id: str | None = None
    ) -> Any:
        raise PermissionError("Caller-owned functions require a live Runtime attachment")


def plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_unset=True)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Native client arguments require string keys")
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise UnsupportedNativeHookError(f"Unqualified native client argument: {type(value).__name__}")


def record(gate: GateAttachment, kind: str, call: NativeCall, **fields: Any) -> None:
    gate.binding.observe(
        {
            "type": kind,
            "timestamp": datetime.now(UTC).isoformat(),
            "operation_id": call.operation_id,
            "operation": call.name,
            **fields,
        }
    )


def install_functions(gate: GateAttachment, target: FunctionDispatcher) -> None:
    functions = {item.name: item for item in target.functions}
    if len(functions) != len(target.functions) or any(not name for name in functions):
        raise ValueError("Caller-owned function names must be distinct and nonempty")
    for item in functions.values():
        if not gate.track_callable(item, "invoke"):
            raise TypeError("Caller-owned function must be callable")

    async def invoke(
        name: str, arguments: dict[str, Any], *, tool_call_id: str | None = None, run_id: str | None = None
    ) -> Any:
        if name not in functions:
            raise ValueError("Function is not in this binding's configured local inventory")
        function = functions[name]
        encoded = canonical_record(arguments)
        snapshot = decode_json_body(encoded)
        if not isinstance(snapshot, dict):
            raise ValueError("Local function arguments must be a JSON object")

        def fingerprint() -> str:
            return encode_arguments(
                {
                    "arguments": arguments,
                    "functions": [
                        (item.name, id(item), id(item.invoke), item.description) for item in target.functions
                    ],
                }
            )

        call = NativeCall(
            name,
            snapshot,
            tool_call_id=tool_call_id,
            run_id=run_id,
            definition=function.description,
            fingerprint=fingerprint,
            evidence=ExecutionEvidence(False),
        )
        if not await gate.allow(call):
            raise NativeGateDeniedError("Caller-owned function denied before invocation")
        record(
            gate, "function_started", call, tool_name=name, tool_call_id=tool_call_id, run_id=run_id, arguments=snapshot
        )

        async def execute() -> Any:
            if inspect.iscoroutinefunction(function.invoke):
                return await function.invoke(**snapshot)
            return await retain_task(asyncio.create_task(asyncio.to_thread(function.invoke, **snapshot)))

        try:
            result = await gate.complete(call, execute)
        except BaseException:
            record(gate, "function_failed", call, tool_name=name, tool_call_id=tool_call_id, run_id=run_id)
            raise
        record(
            gate,
            "function_completed",
            call,
            tool_name=name,
            tool_call_id=tool_call_id,
            run_id=run_id,
            result=plain(result),
        )
        return result

    gate.patch(target, "call_function", invoke)


@dataclass
class _Outcome:
    value: Any = None
    error: BaseException | None = None
    started: bool | None = None
    succeeded: bool | None = None


def install_request(
    gate: GateAttachment,
    resource: Any,
    method: str,
    operation: str,
    *,
    fingerprint: Callable[[], str],
    validate: Callable[[dict[str, Any]], None],
    response: Callable[[Any], dict[str, Any]],
    usage_key: Callable[[Any], str | None],
    async_method: bool | None = None,
) -> None:
    """Gate the original method, preserve its real result, and retain local cancellation."""
    original = getattr(resource, method)
    is_async = inspect.iscoroutinefunction(original) if async_method is None else async_method

    def synchronous_entry(call: NativeCall, *args: Any, **kwargs: Any) -> Any:
        value = original(*args, **kwargs)
        received(call, value)
        return value

    async def asynchronous_entry(call: NativeCall, *args: Any, **kwargs: Any) -> Any:
        value = await original(*args, **kwargs)
        received(call, value)
        return value

    entry = SimpleNamespace(invoke=asynchronous_entry if is_async else synchronous_entry)
    gate.claim(resource)
    gate.track_callable(entry, "invoke")
    signature = inspect.signature(original)

    def prepare(args: tuple[Any, ...], kwargs: dict[str, Any], state: _Outcome) -> NativeCall:
        bound = dict(signature.bind(*args, **kwargs).arguments)
        validate(bound)
        snapshot = plain(bound)
        decode_json_body(canonical_record(snapshot))
        return NativeCall(
            operation,
            {"request": snapshot, "client": decode_json_body(fingerprint().encode())},
            definition="Native SDK request boundary only; hosted tools and remote outcomes are not local execution.",
            fingerprint=lambda: encode_arguments({"request": plain(bound), "client": fingerprint()}),
            evidence=ExecutionEvidence(False),
            execution_probe=lambda _: state.started,
        )

    def received(call: NativeCall, value: Any) -> None:
        output = response(value)
        decode_json_body(canonical_record(output))
        record(gate, "response", call, **output, local_cancel_requested=not gate.active)

    async def asynchronous(*args: Any, **kwargs: Any) -> Any:
        state = _Outcome(started=False, succeeded=False)
        call = prepare(args, kwargs, state)
        if not await gate.allow(call):
            raise NativeGateDeniedError("Native client request denied before invocation")
        record(gate, "request_started", call, arguments=call.arguments)

        async def work() -> _Outcome:
            try:
                await gate.require_entry(call)
                state.started, state.succeeded = None, None
                value = await entry.invoke(call, *args, **kwargs)
                state.started, state.succeeded, state.value = True, True, value
                key = usage_key(value)
                await gate.binding.supplied_usage(key or call.operation_id, value)
            except BaseException as exc:
                state.error = exc
                if isinstance(exc, NativeGateDeniedError):
                    state.started, state.succeeded = False, False
            return state

        async def execute() -> _Outcome:
            pending = asyncio.create_task(work())
            revoked = asyncio.create_task(gate.binding.wait_revoked())
            interrupted = False
            try:
                done, _ = await asyncio.wait((pending, revoked), return_when=asyncio.FIRST_COMPLETED)
                if pending not in done:
                    interrupted = True
            except asyncio.CancelledError:
                interrupted = True
            finally:
                if interrupted:
                    pending.cancel()
                try:
                    await retain_task(pending)
                except asyncio.CancelledError:
                    interrupted = True
                revoked.cancel()
                await asyncio.gather(revoked, return_exceptions=True)
            if interrupted:
                state.error = asyncio.CancelledError()
            return state

        outcome = await gate.complete(call, execute, outcome=lambda result: result.succeeded)
        if outcome.error is not None:
            record(
                gate,
                "request_outcome",
                call,
                outcome="local-stop-requested"
                if isinstance(outcome.error, asyncio.CancelledError)
                else "delivery-unknown",
                response_received=outcome.started is True,
                error_type=type(outcome.error).__name__,
            )
            raise outcome.error
        return outcome.value

    def synchronous(*args: Any, **kwargs: Any) -> Any:
        state = _Outcome(started=False, succeeded=False)
        call = prepare(args, kwargs, state)
        if not gate.allow_sync(call):
            raise NativeGateDeniedError("Native client request denied before invocation")
        record(gate, "request_started", call, arguments=call.arguments)

        def execute() -> _Outcome:
            try:
                gate.require_entry_sync(call)
                state.started, state.succeeded = None, None
                value = entry.invoke(call, *args, **kwargs)
                state.started, state.succeeded, state.value = True, True, value
                key = usage_key(value)
                asyncio.run_coroutine_threadsafe(
                    gate.binding.supplied_usage(key or call.operation_id, value), gate.binding.loop
                ).result()
            except BaseException as exc:
                state.error = exc
                if isinstance(exc, NativeGateDeniedError):
                    state.started, state.succeeded = False, False
            return state

        outcome = gate.complete_sync(call, execute, outcome=lambda result: result.succeeded)
        if outcome.error is not None:
            record(
                gate,
                "request_outcome",
                call,
                outcome="delivery-unknown",
                response_received=outcome.started is True,
                error_type=type(outcome.error).__name__,
            )
            raise outcome.error
        return outcome.value

    gate.patch(resource, method, asynchronous if is_async else synchronous)


def refuse_stream(*args: Any, **kwargs: Any) -> Any:
    raise UnsupportedNativeHookError("Streaming is not qualified for this client boundary")
