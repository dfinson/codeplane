"""Attach trust and policy to the original Langflow LFX host's output methods."""

from __future__ import annotations

import inspect
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

    def wrap(name: str, component: Any, method: str) -> None:
        original = getattr(component, method, None)
        if not callable(original):
            raise TypeError("Langflow output must have an actual callable handler")
        holder = SimpleNamespace(call=original)
        gate.track_callable(holder, "call")

        def inputs() -> dict[str, Any]:
            return {key: getattr(component, key) for key in component._inputs}

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
            call = prepare()
            if not gate.allow_sync(call):
                raise NativeGateDeniedError("Langflow component denied before invocation")

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
            call = prepare()
            if not await gate.allow(call):
                raise NativeGateDeniedError("Langflow component denied before invocation")

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

    for name, component in nodes:
        gate.claim(component)
        methods = {output.method for output in component._outputs_map.values()}
        if not methods or None in methods:
            raise ValueError("Langflow requires concrete output handlers")
        for method in methods:
            wrap(name, component, method)
