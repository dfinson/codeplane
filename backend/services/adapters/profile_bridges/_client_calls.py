"""Private mechanics for the five fixed native client bridges, not a plugin registry."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
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
    from collections.abc import Awaitable, Callable

    from backend.services.adapters.native_gates import GateAttachment
    from backend.services.adapters.native_gates.controls import ControlPermit, NativeInput, StopEvidence


_turn_message: ContextVar[NativeInput | None] = ContextVar("native_client_turn_message", default=None)


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

    async def next_turn(self, **request: Any) -> Any:
        """Wait inside the original runnable, then send queued operator text in this native request.

        Supply the SDK's request context, not the operator message. OpenAI requires
        thread_id and run-create options. Other clients use their normal generation
        or invoke_agent options, with operator text appended to history or inputText.
        Admission is queued separately from acknowledgment of actual SDK consumption.
        """
        raise PermissionError("Native turn input requires a live Runtime attachment")

    def next_turn_sync(self, **request: Any) -> Any:
        """The same declared boundary for caller-owned synchronous run drivers."""
        raise PermissionError("Native turn input requires a live Runtime attachment")


async def call_native(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await retain_task(asyncio.create_task(asyncio.to_thread(method, *args, **kwargs)))
    return await result if inspect.isawaitable(result) else result


def install_turn_boundary(
    gate: GateAttachment,
    target: FunctionDispatcher,
    *,
    request: Callable[[NativeInput, dict[str, Any]], Awaitable[Any]],
    stop: Callable[[ControlPermit], Awaitable[StopEvidence]] | None = None,
) -> None:
    parameters: ContextVar[dict[str, Any] | None] = ContextVar("native_turn_request", default=None)

    async def input_handler(message: NativeInput) -> Any:
        declared = parameters.get()
        if declared is None:
            raise RuntimeError("The original runnable has not declared its next native request")
        token = _turn_message.set(message)
        try:
            return await request(message, declared)
        finally:
            _turn_message.reset(token)

    async def boundary(**native_request: Any) -> Any:
        snapshot = deepcopy(native_request)
        decode_json_body(canonical_record(plain(snapshot)))
        token = parameters.set(snapshot)
        try:
            return await gate.binding.controls.consume()
        finally:
            parameters.reset(token)

    def synchronous_boundary(**native_request: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is gate.binding.loop:
            raise RuntimeError("Use await next_turn on the Runtime event loop")
        return asyncio.run_coroutine_threadsafe(boundary(**native_request), gate.binding.loop).result()

    gate.cleanup(gate.binding.controls.register(input_handler=input_handler, stop_handler=stop))
    gate.patch(target, "next_turn", boundary)
    gate.patch(target, "next_turn_sync", synchronous_boundary)


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
        if not callable(item.invoke):
            raise TypeError("Caller-owned function must be callable")
        call_method = getattr(item.invoke, "__call__", None)  # noqa: B004 - inspect async protocol, not callability.
        if not inspect.iscoroutinefunction(item.invoke) and inspect.iscoroutinefunction(call_method):
            gate.patch(item, "invoke", call_method)
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
            async def waiting(pending: asyncio.Task[Any]) -> Any:
                revoked = asyncio.create_task(gate.binding.wait_revoked())
                try:
                    done, _ = await asyncio.wait((pending, revoked), return_when=asyncio.FIRST_COMPLETED)
                    if pending not in done:
                        pending.cancel()
                    return await retain_task(pending)
                finally:
                    if not pending.done():
                        pending.cancel()
                        with suppress(asyncio.CancelledError):
                            await retain_task(pending)
                    revoked.cancel()
                    await asyncio.gather(revoked, return_exceptions=True)

            if inspect.iscoroutinefunction(function.invoke):
                result = await waiting(asyncio.create_task(function.invoke(**snapshot)))
            else:
                result = await retain_task(asyncio.create_task(asyncio.to_thread(function.invoke, **snapshot)))
            while inspect.isawaitable(result):

                async def finish(value: Any) -> Any:
                    try:
                        await gate.require_entry(call)
                    except BaseException:
                        if inspect.iscoroutine(value):
                            value.close()
                        elif isinstance(value, asyncio.Future):
                            value.cancel()
                            await asyncio.gather(value, return_exceptions=True)
                        raise
                    token = gate.binding.begin_work(call)
                    try:
                        return await value
                    finally:
                        gate.binding.end_work(token)

                result = await waiting(asyncio.create_task(finish(result)))
            return result

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
    response_received: bool = False
    response_complete: bool = False
    identities: dict[str, Any] = field(default_factory=dict)


class ClientReconciliationError(RuntimeError):
    """The SDK returned evidence, but local accounting/observation did not finish."""

    def __init__(self, state: _Outcome, errors: list[BaseException]) -> None:
        self.response = state.value
        self.response_received = state.response_received
        self.response_complete = state.response_complete
        self.identities = dict(state.identities)
        self.errors = tuple(errors)
        super().__init__(
            "Native client response requires local reconciliation: "
            + ", ".join(type(error).__name__ for error in errors)
        )


def install_request(
    gate: GateAttachment,
    resource: Any,
    method: str,
    operation: str,
    *,
    fingerprint: Callable[[], str],
    validate: Callable[[dict[str, Any]], None],
    response: Callable[[Any], dict[str, Any]],
    identity: Callable[[Any], dict[str, Any]],
    usage_key: Callable[[Any], str | None],
    async_method: bool | None = None,
    streaming_response: bool = False,
    returned: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    """Gate the original method, preserve its real result, and retain local cancellation."""
    original = getattr(resource, method)
    is_async = inspect.iscoroutinefunction(original) if async_method is None else async_method

    async def notify_returned(value: Any) -> None:
        if returned is not None:
            await returned(value)

    async def acknowledge_return(state: _Outcome) -> None:
        message = _turn_message.get()
        if message is not None:
            await gate.binding.controls.consumed(
                message, {key: value for key, value in state.identities.items() if isinstance(value, str) and value}
            )

    def known_return(state: _Outcome, value: Any) -> list[BaseException]:
        state.value, state.started, state.response_received = value, True, True
        state.response_complete = not streaming_response
        state.succeeded = True if state.response_complete else None
        try:
            state.identities = identity(value)
            if any(item is not None and not isinstance(item, str) for item in state.identities.values()):
                raise ValueError("Native response identities must retain their supplied string types")
        except Exception as exc:
            return [exc]
        return []

    def observe_return(call: NativeCall, state: _Outcome, errors: list[BaseException]) -> None:
        try:
            record(gate, "response_received", call, **state.identities, response_complete=state.response_complete)
        except Exception as exc:
            errors.append(exc)
        try:
            output = response(state.value)
            state.response_complete, state.succeeded = True, True
            decode_json_body(canonical_record(output))
            record(gate, "response", call, **output, local_cancel_requested=not gate.active)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
        if errors:
            gate.binding.revoke()
            raise ClientReconciliationError(state, errors)

    def synchronous_entry(call: NativeCall, state: _Outcome, *args: Any, **kwargs: Any) -> Any:
        value = original(*args, **kwargs)
        errors = known_return(state, value)
        try:
            key = usage_key(value)
            asyncio.run_coroutine_threadsafe(
                gate.binding.supplied_usage(key or call.operation_id, value), gate.binding.loop
            ).result()
        except Exception as exc:
            errors.append(exc)
        try:
            asyncio.run_coroutine_threadsafe(acknowledge_return(state), gate.binding.loop).result()
        except Exception as exc:
            errors.append(exc)
        if returned is not None:
            try:
                asyncio.run_coroutine_threadsafe(notify_returned(value), gate.binding.loop).result()
            except Exception as exc:
                errors.append(exc)
        observe_return(call, state, errors)
        return value

    async def asynchronous_entry(call: NativeCall, state: _Outcome, *args: Any, **kwargs: Any) -> Any:
        value = await original(*args, **kwargs)
        errors = known_return(state, value)

        async def reconcile() -> None:
            try:
                key = usage_key(value)
                await gate.binding.supplied_usage(key or call.operation_id, value)
            except Exception as exc:
                errors.append(exc)
            try:
                await acknowledge_return(state)
            except Exception as exc:
                errors.append(exc)
            if returned is not None:
                try:
                    await returned(value)
                except Exception as exc:
                    errors.append(exc)
            observe_return(call, state, errors)

        await retain_task(asyncio.create_task(reconcile()))
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
                await entry.invoke(call, state, *args, **kwargs)
            except BaseException as exc:
                state.error = exc
                if isinstance(exc, NativeGateDeniedError) and not state.response_received:
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
                if interrupted and not isinstance(state.error, ClientReconciliationError):
                    pending.cancel()
                try:
                    await retain_task(pending)
                except asyncio.CancelledError:
                    interrupted = True
                revoked.cancel()
                await asyncio.gather(revoked, return_exceptions=True)
            if interrupted and not isinstance(state.error, ClientReconciliationError):
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
                else "local-reconciliation-required"
                if isinstance(outcome.error, ClientReconciliationError)
                else "delivery-unknown",
                response_received=outcome.response_received,
                response_complete=outcome.response_complete,
                **outcome.identities,
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
                entry.invoke(call, state, *args, **kwargs)
            except BaseException as exc:
                state.error = exc
                if isinstance(exc, NativeGateDeniedError) and not state.response_received:
                    state.started, state.succeeded = False, False
            return state

        outcome = gate.complete_sync(call, execute, outcome=lambda result: result.succeeded)
        if outcome.error is not None:
            record(
                gate,
                "request_outcome",
                call,
                outcome="local-stop-requested"
                if isinstance(outcome.error, asyncio.CancelledError)
                else "local-reconciliation-required"
                if isinstance(outcome.error, ClientReconciliationError)
                else "delivery-unknown",
                response_received=outcome.response_received,
                response_complete=outcome.response_complete,
                **outcome.identities,
                error_type=type(outcome.error).__name__,
            )
            raise outcome.error
        return outcome.value

    gate.patch(resource, method, asynchronous if is_async else synchronous)


def refuse_stream(*args: Any, **kwargs: Any) -> Any:
    raise UnsupportedNativeHookError("Streaming is not qualified for this client boundary")
