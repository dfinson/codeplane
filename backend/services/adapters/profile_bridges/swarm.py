"""Original Swarm dispatch with pre-call policy and post-return handoff trust.

Qualified against openai/swarm@6af0b4caf37dca4526dfd98e9fbd8ce36e7eeb22,
distribution 0.1.0, with openai 2.38.0. No trust-only run substitute is used.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from agentmesh.integrations.swarm import HandoffVerifier

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError, encode_arguments
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from backend.services.adapters.native_gates import GateAttachment

sdk = load_sdk("swarm")
native_types = load_sdk("swarm.types")


@dataclass
class SwarmTarget:
    client: Any
    agents: tuple[Any, ...]
    verifier: HandoffVerifier


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, SwarmTarget) or not isinstance(target.client, sdk.Swarm):
        raise TypeError("Swarm requires the original installed Swarm client")
    if not isinstance(target.verifier, HandoffVerifier) or not target.agents:
        raise TypeError("Swarm requires its actual handoff verifier and registered native agents")
    if any(not isinstance(agent, sdk.Agent) for agent in target.agents):
        raise TypeError("Swarm requires original Agent instances")
    agents = tuple(target.agents)
    identities = {id(agent): agent for agent in agents}
    names = {id(agent): agent.name for agent in agents}
    if len(identities) != len(agents) or len({agent.name for agent in agents}) != len(agents):
        raise ValueError("Swarm agent identities must be distinct")
    if any(agent.name not in target.verifier._identities for agent in target.agents):
        raise ValueError("Register each Swarm peer with the upstream verifier before attachment")
    client, verifier = target.client, target.verifier
    gate.claim(client)
    dispatch: ContextVar[tuple[Any, list[Any]] | None] = ContextVar("swarm_dispatch", default=None)
    inside: ContextVar[bool] = ContextVar("swarm_function", default=False)

    def observe(kind: str, **fields: Any) -> None:
        gate.binding.observe({"type": kind, "timestamp": datetime.now(UTC).isoformat(), **fields})

    def require_current() -> None:
        if threading.get_ident() == gate.binding._loop_thread:
            raise RuntimeError("Swarm synchronous invocation requires the Runtime-owned worker")
        gate.validate()
        if any(agent.name != names[id(agent)] for agent in agents):
            raise NativeGateDeniedError("Swarm agent identity changed during its execution")
        if not asyncio.run_coroutine_threadsafe(gate.binding.current(), gate.binding.loop).result():
            raise NativeGateDeniedError("Swarm invocation is no longer admitted")

    def wrap(agent: Any, function: Any) -> Any:
        if not inspect.isfunction(function) or inspect.iscoroutinefunction(function):
            raise UnsupportedNativeHookError("Swarm 0.1.0 requires synchronous Python function tools")
        signature = inspect.signature(function)
        holder = SimpleNamespace(call=function)
        gate.track_callable(holder, "call")

        @wraps(function)
        def guarded(context_variables: dict[str, Any] | None = None, **kwargs: Any) -> Any:
            require_current()
            context = context_variables if context_variables is not None else {}
            arguments = dict(kwargs)
            if "context_variables" in signature.parameters:
                arguments["context_variables"] = context
            signature.bind(**arguments)
            source_call = None
            native_dispatch = dispatch.get()
            if not inside.get() and native_dispatch is not None and native_dispatch[0] is agent:
                pending = native_dispatch[1]
                index = next(
                    (index for index, call in enumerate(pending) if call.function.name == function.__name__), None
                )
                if index is not None:
                    source_call = pending.pop(index)
            call = NativeCall(
                function.__name__,
                {"arguments": arguments, "context_variables": context},
                source_call.id if source_call is not None else None,
                definition=function.__doc__ or "Caller-owned Swarm function",
                fingerprint=lambda: encode_arguments(
                    {
                        "arguments": arguments,
                        "context": context,
                        "source_agent": agent.name,
                        "peers": {
                            peer.name: asdict(verifier._identities[peer.name])
                            for peer in agents
                            if peer.name in verifier._identities
                        },
                        "allowed": sorted(verifier.policy.allowed_targets),
                        "blocked": sorted(verifier.policy.blocked_targets),
                        "minimum": verifier.policy.min_trust_score,
                        "sensitive_minimum": verifier.policy.sensitive_trust_score,
                        "sensitive_keys": sorted(verifier.policy.sensitive_context_keys),
                    }
                ),
            )
            if not gate.allow_sync(call):
                raise NativeGateDeniedError("Swarm function denied before invocation")

            def invoke() -> Any:
                scope = inside.set(True)
                observe(
                    "tool_started",
                    agent_id=agent.name,
                    operation_id=call.operation_id,
                    tool_call_id=call.tool_call_id,
                    tool_name=function.__name__,
                    arguments=arguments,
                )
                try:
                    result = holder.call(**arguments)
                    if any(item.name != names[id(item)] for item in agents):
                        raise NativeGateDeniedError("Swarm agent identity changed during its function call")
                    peer = (
                        result
                        if isinstance(result, sdk.Agent)
                        else (result.agent if isinstance(result, native_types.Result) else None)
                    )
                    if peer is not None:
                        if id(peer) not in identities or identities[id(peer)] is not peer:
                            raise NativeGateDeniedError("Returned Swarm peer is not an explicitly bound agent")
                        transferred = {
                            **context,
                            **(result.context_variables if isinstance(result, native_types.Result) else {}),
                        }
                        verifier.verify_handoff(agent.name, peer.name, transferred)
                        observe(
                            "handoff_verified",
                            operation_id=call.operation_id,
                            source_agent=agent.name,
                            target_agent=peer.name,
                            source_did=verifier._identities[agent.name].did,
                            target_did=verifier._identities[peer.name].did,
                        )
                    rendered = (
                        {"agent": result.name}
                        if isinstance(result, sdk.Agent)
                        else {
                            "value": result.value,
                            "agent": peer.name if peer is not None else None,
                            "context_variables": result.context_variables,
                        }
                        if isinstance(result, native_types.Result)
                        else str(result)
                    )
                    observe(
                        "tool_completed",
                        agent_id=agent.name,
                        operation_id=call.operation_id,
                        tool_call_id=call.tool_call_id,
                        tool_name=function.__name__,
                        result=rendered,
                        success=True,
                    )
                    return result
                except BaseException as exc:
                    observe(
                        "tool_failed",
                        agent_id=agent.name,
                        operation_id=call.operation_id,
                        tool_call_id=call.tool_call_id,
                        tool_name=function.__name__,
                        error_type=type(exc).__name__,
                        success=False,
                    )
                    raise
                finally:
                    inside.reset(scope)

            return gate.complete_sync(call, invoke)

        return guarded

    for agent in agents:
        gate.claim(agent)
        if len({function.__name__ for function in agent.functions}) != len(agent.functions):
            raise ValueError("Swarm function names must be distinct within an agent")
        gate.patch(agent, "functions", [wrap(agent, function) for function in agent.functions])

    handle = client.handle_tool_calls

    @wraps(handle)
    def handle_scoped(
        tool_calls: list[Any], functions: list[Any], context_variables: dict[str, Any], debug: bool
    ) -> Any:
        require_current()
        agent = next((agent for agent in agents if agent.functions is functions), None)
        if agent is None:
            raise NativeGateDeniedError("Swarm dispatch has no bound source agent")
        token = dispatch.set((agent, list(tool_calls)))
        try:
            return handle(tool_calls, functions, context_variables, debug)
        finally:
            dispatch.reset(token)

    gate.patch(client, "handle_tool_calls", handle_scoped)
    completion = client.get_chat_completion

    @wraps(completion)
    def observed_completion(*args: Any, **kwargs: Any) -> Any:
        require_current()
        bound = inspect.signature(completion).bind(*args, **kwargs)
        bound.apply_defaults()
        agent = bound.arguments["agent"]
        if id(agent) not in identities:
            raise NativeGateDeniedError("Swarm model request has no bound source agent")
        response = completion(*args, **kwargs)

        def report(value: Any, kind: str) -> None:
            observe(kind, agent_id=agent.name, response=value.model_dump(mode="json", exclude_none=True))
            if value.usage is not None:
                asyncio.run_coroutine_threadsafe(
                    gate.binding.supplied_usage(value.id, value), gate.binding.loop
                ).result()

        if not bound.arguments["stream"]:
            report(response, "model_completed")
            return response

        def chunks() -> Any:
            try:
                for chunk in response:
                    require_current()
                    report(chunk, "model_chunk")
                    if chunk.choices:
                        yield chunk
            finally:
                response.close()

        return chunks()

    gate.patch(client, "get_chat_completion", observed_completion)
