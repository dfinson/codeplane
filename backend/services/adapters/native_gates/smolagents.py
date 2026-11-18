"""Wrap the existing ToolCallingAgent's pre-tool boundary, never step callbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._optional import load_sdk
from .binding import ExecutionEvidence, NativeCall, encode_arguments

if TYPE_CHECKING:
    from ._optional import SmolToolCallingAgent
    from .installation import GateAttachment

ToolCallingAgent = load_sdk("smolagents").ToolCallingAgent


def install(gate: GateAttachment, agent: SmolToolCallingAgent) -> None:
    if not isinstance(agent, ToolCallingAgent):
        raise TypeError("Only smolagents ToolCallingAgent has this qualified pre-tool boundary")
    original = agent.execute_tool_call

    def execute(tool_name: str, arguments: dict[str, str] | str) -> Any:
        resolved = agent._substitute_state_variables(arguments)
        available = {**agent.tools, **agent.managed_agents}
        tool = available.get(tool_name)
        tracked = tool is not None and gate.track_callable(tool, "forward")

        def fingerprint() -> str:
            actual = {**agent.tools, **agent.managed_agents}.get(tool_name)
            return encode_arguments(
                {
                    "tool": id(actual),
                    "forward": id(getattr(actual, "forward", None)),
                    "arguments": agent._substitute_state_variables(arguments),
                }
            )

        call = NativeCall(
            tool_name,
            resolved if isinstance(resolved, dict) else {"input": resolved},
            fingerprint=fingerprint,
            evidence=ExecutionEvidence(False if tracked else None),
        )
        snapshot = encode_arguments(resolved)
        if not gate.allow_sync(call):
            return "[BLOCKED] Denied by CodePlane"
        if encode_arguments(agent._substitute_state_variables(arguments)) != snapshot:
            return "[BLOCKED] Denied by CodePlane: state arguments changed"
        return gate.complete_sync(call, lambda: original(tool_name, arguments))

    gate.patch(agent, "execute_tool_call", execute)
