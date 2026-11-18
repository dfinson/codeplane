"""Trust and policy at real Haystack component entry, without owning its scheduler."""

from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from agentmesh.integrations.haystack import TrustedPipeline

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError, encode_arguments

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

    def wrap(name: str, component: Any, method: str) -> None:
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
            verification = trust.verify_stage(name, dict(bound.arguments), stage=method)
            if verification["verified"] is not True:
                raise NativeGateDeniedError("Haystack component trust was not verified")
            return NativeCall(
                f"haystack.{name}.{method}",
                dict(bound.arguments),
                definition=original.__doc__ or "Caller-owned Haystack component",
                fingerprint=fingerprint,
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
                observe("component_started", call, arguments=call.arguments)
                try:
                    result = holder.call(*args, **kwargs)
                except BaseException as exc:
                    observe("component_failed", call, error_type=type(exc).__name__, success=False)
                    raise
                observe("component_completed", call, success=True)
                return result

            return gate.complete_sync(call, invoke)

        @wraps(original)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            call = prepare(args, kwargs)
            if not await gate.allow(call):
                raise NativeGateDeniedError("Haystack component denied before invocation")

            async def invoke() -> Any:
                observe("component_started", call, arguments=call.arguments)
                try:
                    result = await holder.call(*args, **kwargs)
                except BaseException as exc:
                    observe("component_failed", call, error_type=type(exc).__name__, success=False)
                    raise
                observe("component_completed", call, success=True)
                return result

            return await gate.complete(call, invoke)

        gate.patch(component, method, asynchronous if inspect.iscoroutinefunction(original) else synchronous)

    for name, component in components:
        gate.claim(component)
        wrap(name, component, "run")
        if hasattr(component, "run_async"):
            wrap(name, component, "run_async")
