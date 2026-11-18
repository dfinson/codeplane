"""Per-run leaf WrapperToolsets preserve the Agent and its original toolsets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
from pydantic_ai._output import OutputToolset
from pydantic_ai.toolsets import WrapperToolset

from .binding import NativeCall, NativeGateDeniedError

if TYPE_CHECKING:
    from .installation import GateAttachment


def install(gate: GateAttachment, agent: Agent[Any, Any]) -> None:
    if not isinstance(agent, Agent):
        raise TypeError("PydanticAI gating requires the original Agent")
    original = agent._get_toolset

    class CodePlaneToolset(WrapperToolset[Any]):
        async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
            call = NativeCall(
                name,
                tool_args,
                ctx.tool_call_id,
                str(ctx.run_id),
                definition=tool.tool_def.description or "",
            )
            if not await gate.allow(call):
                raise NativeGateDeniedError("Denied by CodePlane")
            original = super().call_tool
            return await gate.complete(call, lambda: original(name, tool_args, ctx, tool))

    def wrap(toolset: Any) -> Any:
        return toolset if isinstance(toolset, (OutputToolset, CodePlaneToolset)) else CodePlaneToolset(toolset)

    def assembled(*args: Any, **kwargs: Any) -> Any:
        # Includes runtime/override/dynamic toolsets, not just construction-time tools.
        return original(*args, **kwargs).visit_and_replace(wrap)

    gate.patch(agent, "_get_toolset", assembled)
