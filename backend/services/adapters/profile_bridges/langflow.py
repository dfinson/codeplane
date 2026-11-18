"""Attach trust and policy to the original Langflow LFX host's output methods."""

from __future__ import annotations

import inspect
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from agentmesh.integrations.langflow import TrustGatedFlow

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError, encode_arguments

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment

host_graph = load_sdk("lfx.graph.graph.base")
host_component = load_sdk("lfx.custom.custom_component.component")
native_data = load_sdk("lfx.schema.data")


@dataclass
class LangflowTarget:
    flow: Any
    trust: TrustGatedFlow


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, LangflowTarget) or not isinstance(target.flow, host_graph.Graph):
        raise TypeError("Langflow requires its actual caller-owned LFX host graph")
    flow, trust = target.flow, target.trust
    if not isinstance(trust, TrustGatedFlow) or trust.verifier.on_failure != "block":
        raise ValueError("Langflow requires upstream TrustVerificationComponent with on_failure=block")
    nodes = tuple((vertex.id, vertex.custom_component) for vertex in flow.vertices)
    if not nodes or any(not isinstance(component, host_component.Component) for _, component in nodes):
        raise ValueError("Langflow requires instantiated host components, not exported trust configuration")
    if {name for name, _ in nodes} != set(trust._flow_nodes):
        raise ValueError("Register each actual Langflow node with its upstream identity component")
    gate.claim(flow)
    gate.claim(trust)
    gate.patch(trust.verifier, "audit_logging", False)

    def parents(name: str) -> list[str]:
        return sorted({edge.source_id for edge in flow.edges if edge.target_id == name})

    def topology() -> dict[str, Any]:
        return {
            "flow_id": flow.flow_id,
            "nodes": [(vertex.id, id(vertex.custom_component)) for vertex in flow.vertices],
            "edges": [
                (edge.source_id, edge.target_id, edge.source_handle.model_dump(), edge.target_handle.model_dump())
                for edge in flow.edges
            ],
            "identities": {name: asdict(identity) for name, identity in trust._flow_nodes.items()},
            "minimum": trust.verifier.min_trust_score,
            "sensitive_minimum": trust.verifier.sensitive_trust_score,
            "sensitive_keys": sorted(trust.verifier.sensitive_data_keys),
            "capabilities": trust.verifier.required_capabilities,
            "failure": trust.verifier.on_failure,
        }

    attached = topology()

    def wrap(
        name: str,
        component: Any,
        method: str,
        active: ContextVar[tuple[str, NativeCall] | None],
        delegates: dict[str, str],
    ) -> None:
        original = getattr(component, method, None)
        if not callable(original):
            raise TypeError("Langflow output must have an actual callable handler")
        signature = inspect.signature(original)
        holder = SimpleNamespace(call=original)
        gate.track_callable(holder, "call")

        def inputs() -> dict[str, Any]:
            def plain(value: Any) -> Any:
                if isinstance(value, native_data.Data):
                    return {
                        "native_type": f"{type(value).__module__}.{type(value).__qualname__}",
                        "value": value.model_dump(mode="json"),
                    }
                if isinstance(value, dict):
                    return {key: plain(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [plain(item) for item in value]
                return value

            return {key: plain(getattr(component, key)) for key in component._inputs}

        def fingerprint() -> str:
            return encode_arguments({"topology": topology(), "inputs": inputs()})

        def prepare() -> NativeCall:
            gate.validate()
            current = topology()
            changed = [key for key, value in attached.items() if current[key] != value]
            if component.get_id() != name:
                changed.append("node_identity")
            if changed:
                raise NativeGateDeniedError(f"Langflow configuration changed after attachment: {', '.join(changed)}")
            arguments = inputs()
            for source in parents(name) or [name]:
                if trust.verify_connection(source, name, arguments) is not True:
                    raise NativeGateDeniedError("Langflow connection trust was not verified")
            return NativeCall(
                f"langflow.{name}.{method}",
                arguments,
                run_id=flow._run_id or None,
                definition=original.__doc__ or "Caller-owned Langflow component output",
                fingerprint=fingerprint,
            )

        def observe(kind: str, call: NativeCall, **fields: Any) -> None:
            gate.binding.observe(
                {
                    "type": kind,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation_id": call.operation_id,
                    "tool_call_id_source": "codeplane-operation",
                    "flow_id": flow.flow_id,
                    "run_id": call.run_id,
                    "node_id": name,
                    "parent_nodes": parents(name),
                    "component_did": trust._flow_nodes[name].did,
                    "method": method,
                    **fields,
                }
            )

        @wraps(original)
        def synchronous(*args: Any, **kwargs: Any) -> Any:
            signature.bind(*args, **kwargs)
            call = prepare()
            if not gate.allow_sync(call):
                raise NativeGateDeniedError("Langflow component denied before invocation")

            def invoke() -> Any:
                token = active.set((method, call))
                observe("component_started", call, arguments=call.arguments)
                try:
                    result = holder.call(*args, **kwargs)
                except BaseException as exc:
                    observe("component_failed", call, error_type=type(exc).__name__, success=False)
                    raise
                finally:
                    active.reset(token)
                observe("component_completed", call, success=True)
                return result

            return gate.complete_sync(call, invoke)

        @wraps(original)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            signature.bind(*args, **kwargs)
            parent = active.get()
            if parent is not None and delegates.get(parent[0]) == method:
                await gate.require_entry(parent[1])
                token = active.set(None)
                try:
                    return await holder.call(*args, **kwargs)
                finally:
                    active.reset(token)
            call = prepare()
            if not await gate.allow(call):
                raise NativeGateDeniedError("Langflow component denied before invocation")

            async def invoke() -> Any:
                token = active.set((method, call))
                observe("component_started", call, arguments=call.arguments)
                try:
                    result = await holder.call(*args, **kwargs)
                except BaseException as exc:
                    observe("component_failed", call, error_type=type(exc).__name__, success=False)
                    raise
                finally:
                    active.reset(token)
                observe("component_completed", call, success=True)
                return result

            return await gate.complete(call, invoke)

        gate.patch(component, method, asynchronous if inspect.iscoroutinefunction(original) else synchronous)

    def guard_dispatch(component: Any, selectors: dict[str, str], delegates: dict[str, str]) -> None:
        original = component._get_output_result

        @wraps(original)
        async def dispatch(output: Any) -> Any:
            gate.validate()
            if not gate.active or not await gate.binding.current():
                raise NativeGateDeniedError("Langflow output dispatch is no longer admitted")
            gate.validate()
            method = selectors.get(output.name)
            if method is None or output.method != method:
                raise NativeGateDeniedError("Langflow output selector changed after attachment")
            if getattr(getattr(component, method), "__lfx_async_delegate__", None) != delegates.get(method):
                raise NativeGateDeniedError("Langflow async delegation changed after attachment")
            return await original(output)

        gate.patch(component, "_get_output_result", dispatch)

    for name, component in nodes:
        gate.claim(component)
        methods = {output.method for output in component._outputs_map.values()}
        if not methods or None in methods:
            raise ValueError("Langflow requires concrete output handlers")
        delegates = {}
        for method in methods:
            delegate = getattr(getattr(component, method), "__lfx_async_delegate__", None)
            if delegate is not None:
                if not isinstance(delegate, str) or not inspect.iscoroutinefunction(getattr(component, delegate, None)):
                    raise ValueError("Langflow async delegation must identify an actual coroutine handler")
                delegates[method] = delegate
        active: ContextVar[tuple[str, NativeCall] | None] = ContextVar("langflow_component_call", default=None)
        for method in methods | set(delegates.values()):
            wrap(name, component, method, active, delegates)
        guard_dispatch(component, {output.name: output.method for output in component._outputs_map.values()}, delegates)
