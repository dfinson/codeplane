"""Wrap the existing ToolCallingAgent's pre-tool boundary, never step callbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from smolagents import ToolCallingAgent

from .binding import NativeCall, encode_arguments

if TYPE_CHECKING:
    from .installation import GateAttachment


def install(gate: GateAttachment, agent: ToolCallingAgent) -> None:
    if not isinstance(agent, ToolCallingAgent):
        raise TypeError("Only smolagents ToolCallingAgent has this qualified pre-tool boundary")
    original = agent.execute_tool_call

    def execute(tool_name: str, arguments: dict[str, str] | str) -> Any:
        resolved = agent._substitute_state_variables(arguments)
        call = NativeCall(tool_name, resolved if isinstance(resolved, dict) else {"input": resolved})
        snapshot = encode_arguments(resolved)
        if not gate.allow_sync(call):
            return "[BLOCKED] Denied by CodePlane"
        if encode_arguments(agent._substitute_state_variables(arguments)) != snapshot:
            return "[BLOCKED] Denied by CodePlane: state arguments changed"
        return gate.complete_sync(call, lambda: original(tool_name, arguments))

    gate.patch(agent, "execute_tool_call", execute)
