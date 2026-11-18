"""FunctionTool.on_invoke_tool is the gate, not an input guardrail or trace hook."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agents import Agent, FunctionTool

from .binding import NativeCall, NativeGateDeniedError
from .readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from .installation import GateAttachment


def install(gate: GateAttachment, agent: Agent[Any]) -> None:
    if not isinstance(agent, Agent):
        raise TypeError("OpenAI Agents gating requires the original Agent")
    tools = tuple(agent.tools)
    if any(not isinstance(tool, FunctionTool) for tool in tools):
        raise UnsupportedNativeHookError("Server-hosted tools have no local FunctionTool enforcement boundary")
    for tool in tools:
        assert isinstance(tool, FunctionTool)
        gate.claim(tool)

        def wrap(original: Any, native_tool: FunctionTool) -> Any:
            async def invoke(ctx: Any, input_str: str) -> Any:
                arguments = json.loads(input_str)
                if not isinstance(arguments, dict):
                    raise ValueError("Native FunctionTool arguments must be a JSON object")
                call = NativeCall(
                    native_tool.name,
                    arguments,
                    ctx.tool_call_id,
                    definition=native_tool.description,
                )
                if not await gate.allow(call):
                    raise NativeGateDeniedError("Denied by CodePlane")
                return await gate.complete(call, lambda: original(ctx, input_str))

            return invoke

        gate.patch(tool, "on_invoke_tool", wrap(tool.on_invoke_tool, tool))
