"""Trust and policy at real Haystack component entry, without owning its scheduler."""

from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from importlib.metadata import version
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from agentmesh.integrations.haystack import TrustedPipeline

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError, encode_arguments
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.adapters.native_gates.usage import result_usage

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment

sdk = load_sdk("haystack")


@dataclass
class HaystackTarget:
    pipeline: Any
    trust: TrustedPipeline


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, HaystackTarget) or not isinstance(target.pipeline, sdk.Pipeline):
        raise TypeError("Haystack requires an actual caller-owned Pipeline")
    pipeline, trust = target.pipeline, target.trust
    if not isinstance(trust, TrustedPipeline) or trust._gate.on_failure != "block":
        raise ValueError("Haystack requires its upstream blocking TrustGateComponent")
    components = tuple(pipeline.walk())
    if not components:
        raise ValueError("Haystack requires actual component handlers, not a trust-only pipeline")
    if {name for name, _ in components} != set(trust._components):
        raise ValueError("Each actual Haystack component requires an explicitly registered trust identity")
    gate.claim(pipeline)
    gate.claim(trust)
    gate.patch(trust._gate, "audit_logging", False)

    def fingerprint() -> str:
        return encode_arguments(
            {
                "components": [(name, id(component)) for name, component in pipeline.walk()],
                "edges": sorted(pipeline.graph.edges),
                "identities": {name: asdict(identity) for name, identity in trust._components.items()},
                "gate_identities": {name: asdict(identity) for name, identity in trust._gate._identities.items()},
                "minimum": trust._gate.min_trust_score,
                "capabilities": trust._gate.required_capabilities,
                "failure": trust._gate.on_failure,
            }
        )

    attached = fingerprint()
    messages = load_sdk("haystack.dataclasses")

    def arguments(value: Any) -> Any:
        if isinstance(value, (messages.ChatMessage, messages.Document)):
            return arguments(value.to_dict())
        if isinstance(value, dict):
            return {key: arguments(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [arguments(item) for item in value]
        return value

    def observe_usage(result: Any) -> list[dict[str, Any]]:
        replies = result.get("replies") if isinstance(result, dict) else None
        if not isinstance(replies, list):
            return []
        return [
            {
                "choice_index": reply.meta.get("index"),
                "usage": [
                    amount.model_dump(mode="json") for amount in result_usage("haystack", reply, gate.binding.execution)
                ],
            }
            for reply in replies
            if isinstance(reply, messages.ChatMessage) and reply.meta.get("usage") is not None
        ]

    def capture_responses(component: Any) -> ContextVar[dict[str, Any] | None]:
        responses: ContextVar[dict[str, Any] | None] = ContextVar("haystack_responses", default=None)
        generators = load_sdk("haystack.components.generators.chat")
        if not isinstance(component, generators.OpenAIChatGenerator):
            return responses
        if version("openai") != "2.38.0":
            raise UnsupportedNativeHookError("Haystack OpenAI generator usage requires qualified openai==2.38.0")
        chat = load_sdk("openai.types.chat")
        prepare_api = component._prepare_api_call
        installed: set[tuple[int, str]] = set()

        def capture(value: Any) -> Any:
            pending = responses.get()
            if pending is not None and isinstance(value, chat.ChatCompletion) and value.id:
                pending[value.id] = value
            return value

        def patch_request(resource: Any, method: str, asynchronous: bool) -> None:
            if (id(resource), method) in installed:
                gate.validate()
                return
            original = getattr(resource, method)

            @wraps(original)
            def sync_request(*args: Any, **kwargs: Any) -> Any:
                return capture(original(*args, **kwargs))

            @wraps(original)
            async def async_request(*args: Any, **kwargs: Any) -> Any:
                return capture(await original(*args, **kwargs))

            gate.claim(resource)
            gate.patch(resource, method, async_request if asynchronous else sync_request)
            installed.add((id(resource), method))

        @wraps(prepare_api)
        def prepare(*args: Any, **kwargs: Any) -> Any:
            prepared = prepare_api(*args, **kwargs)
            for client, asynchronous in ((component.client, False), (component.async_client, True)):
                if client is not None:
                    patch_request(client.chat.completions, prepared["openai_endpoint"], asynchronous)
            return prepared

        gate.patch(component, "_prepare_api_call", prepare)
        return responses

    def correlated_usage(pending: dict[str, Any], result: Any) -> tuple[str, Any] | None:
        if not pending:
            return None
        replies = result.get("replies") if isinstance(result, dict) else None
        if len(pending) != 1 or not isinstance(replies, list) or not replies:
            raise ValueError("Haystack result does not identify one native completion")
        response_id, completion = next(iter(pending.items()))
        supplied = completion.usage.model_dump() if completion.usage is not None else None
        for reply in replies:
            if (
                not isinstance(reply, messages.ChatMessage)
                or reply.meta.get("usage") != supplied
                or reply.meta.get("model") != completion.model
            ):
                raise ValueError("Haystack returned usage does not match the actual native completion")
        return response_id, replies[0]

    def wrap(name: str, component: Any, method: str, responses: ContextVar[dict[str, Any] | None]) -> None:
        original = getattr(component, method, None)
        if not callable(original):
            raise TypeError(f"Haystack component {name!r} has no callable {method} handler")
        signature = inspect.signature(original)
        holder = SimpleNamespace(call=original)
        gate.track_callable(holder, "call")

        def prepare(args: tuple[Any, ...], kwargs: dict[str, Any]) -> NativeCall:
            gate.validate()
            if fingerprint() != attached:
                raise NativeGateDeniedError("Haystack graph or trust configuration changed after attachment")
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            supplied = arguments(dict(bound.arguments))
            verification = trust.verify_stage(name, supplied, stage=method)
            if verification["verified"] is not True:
                raise NativeGateDeniedError("Haystack component trust was not verified")
            return NativeCall(
                f"haystack.{name}.{method}",
                supplied,
                definition=original.__doc__ or "Caller-owned Haystack component",
                fingerprint=lambda: encode_arguments(
                    {"configuration": fingerprint(), "arguments": arguments(dict(bound.arguments))}
                ),
            )

        def observe(kind: str, call: NativeCall, **fields: Any) -> None:
            gate.binding.observe(
                {
                    "type": kind,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "component_id": name,
                    "tool_call_id_source": "codeplane-operation",
                    "component_did": trust._components[name].did,
                    "method": method,
                    **fields,
                }
            )

        @wraps(original)
        def synchronous(*args: Any, **kwargs: Any) -> Any:
            call = prepare(args, kwargs)
            if not gate.allow_sync(call):
                raise NativeGateDeniedError("Haystack component denied before invocation")

            def invoke() -> Any:
                pending: dict[str, Any] = {}
                token = responses.set(pending)
                observe("component_started", call, arguments=call.arguments)
                try:
                    result = holder.call(*args, **kwargs)
                except BaseException as exc:
                    observe("component_failed", call, error_type=type(exc).__name__, success=False)
                    raise
                finally:
                    responses.reset(token)
                usage = correlated_usage(pending, result)
                if usage is not None:
                    asyncio.run_coroutine_threadsafe(gate.binding.supplied_usage(*usage), gate.binding.loop).result()
                observe(
                    "component_completed",
                    call,
                    success=True,
                    reported_usage=observe_usage(result),
                    usage_correlation="native-response" if usage is not None else "unresolved",
                )
                return result

            return gate.complete_sync(call, invoke)

        @wraps(original)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            call = prepare(args, kwargs)
            if not await gate.allow(call):
                raise NativeGateDeniedError("Haystack component denied before invocation")

            async def invoke() -> Any:
                pending: dict[str, Any] = {}
                token = responses.set(pending)
                observe("component_started", call, arguments=call.arguments)
                try:
                    result = await holder.call(*args, **kwargs)
                except BaseException as exc:
                    observe("component_failed", call, error_type=type(exc).__name__, success=False)
                    raise
                finally:
                    responses.reset(token)
                usage = correlated_usage(pending, result)
                if usage is not None:
                    await gate.binding.supplied_usage(*usage)
                observe(
                    "component_completed",
                    call,
                    success=True,
                    reported_usage=observe_usage(result),
                    usage_correlation="native-response" if usage is not None else "unresolved",
                )
                return result

            return await gate.complete(call, invoke)

        gate.patch(component, method, asynchronous if inspect.iscoroutinefunction(original) else synchronous)

    for name, component in components:
        gate.claim(component)
        responses = capture_responses(component)
        wrap(name, component, "run", responses)
        if hasattr(component, "run_async"):
            wrap(name, component, "run_async", responses)
