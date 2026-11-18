"""FunctionTool.on_invoke_tool is the gate, not an input guardrail or trace hook."""

from __future__ import annotations

import json
from inspect import getclosurevars, isfunction
from typing import TYPE_CHECKING, Any

from ._optional import load_sdk
from .binding import NativeCall, NativeGateDeniedError, encode_arguments
from .readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from ._optional import AgentTools
    from ._optional import FunctionTool as FunctionToolBoundary
    from .installation import GateAttachment

_agents = load_sdk("agents")
Agent, FunctionTool = _agents.Agent, _agents.FunctionTool


def _execution_probe(original: Any) -> Any:
    """Read the pinned decorator's provenance; never alter its closure or policy."""
    if not isfunction(original) or original.__module__ != "agents.tool" or original.__name__ != "_on_invoke_tool":
        return lambda error: None
    captured = getclosurevars(original).nonlocals
    implementation = captured.get("_on_invoke_tool_impl")
    if not isfunction(implementation):
        return lambda error: None
    function = getclosurevars(implementation).nonlocals.get("the_func")
    code = getattr(function, "__code__", None)
    error_handler = captured.get("failure_error_function")
    exceptions = load_sdk("agents.exceptions")

    def probe(error: BaseException | None) -> bool | None:
        if error is None:
            return True if error_handler is None else None
        if code is None:
            return None
        seen: set[int] = set()
        pending = [error]
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            traceback = current.__traceback__
            while traceback is not None:
                if traceback.tb_frame.f_code is code:
                    return True
                traceback = traceback.tb_next
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
        return False if isinstance(error, exceptions.ModelBehaviorError) else None

    return probe


def install(gate: GateAttachment, agent: AgentTools) -> None:
    if not isinstance(agent, Agent):
        raise TypeError("OpenAI Agents gating requires the original Agent")
    tools = tuple(agent.tools)
    if any(not isinstance(tool, FunctionTool) for tool in tools):
        raise UnsupportedNativeHookError("Server-hosted tools have no local FunctionTool enforcement boundary")
    for tool in tools:
        assert isinstance(tool, FunctionTool)
        gate.claim(tool)

        def wrap(original: Any, native_tool: FunctionToolBoundary) -> Any:
            probe = _execution_probe(original)

            async def invoke(ctx: Any, input_str: str) -> Any:
                arguments = json.loads(input_str)
                if not isinstance(arguments, dict):
                    raise ValueError("Native FunctionTool arguments must be a JSON object")
                call = NativeCall(
                    native_tool.name,
                    arguments,
                    ctx.tool_call_id,
                    definition=native_tool.description,
                    fingerprint=lambda: encode_arguments(
                        {
                            "tool": id(native_tool),
                            "invoke": id(native_tool.on_invoke_tool),
                            "name": native_tool.name,
                            "description": native_tool.description,
                            "call_id": ctx.tool_call_id,
                            "tool_name": ctx.tool_name,
                            "tool_arguments": ctx.tool_arguments,
                        }
                    ),
                    execution_probe=probe,
                )
                if not await gate.allow(call):
                    raise NativeGateDeniedError("Denied by CodePlane")
                return await gate.complete(call, lambda: original(ctx, input_str))

            return invoke

        gate.patch(tool, "on_invoke_tool", wrap(tool.on_invoke_tool, tool))
