"""Per-run leaf WrapperToolsets preserve the Agent and its original toolsets."""

from __future__ import annotations

from inspect import signature
from typing import TYPE_CHECKING, Any

from ._optional import load_sdk
from .binding import ExecutionEvidence, NativeCall, NativeGateDeniedError, encode_arguments

if TYPE_CHECKING:
    from ._optional import AgentToolsets, Toolset, WrapperToolset
    from .installation import GateAttachment
else:
    WrapperToolset = load_sdk("pydantic_ai.toolsets").WrapperToolset

Agent = load_sdk("pydantic_ai").Agent
OutputToolset = load_sdk("pydantic_ai._output").OutputToolset
_function_schema = load_sdk("pydantic_ai._function_schema")


def install(gate: GateAttachment, agent: AgentToolsets) -> None:
    if not isinstance(agent, Agent):
        raise TypeError("PydanticAI gating requires the original Agent")
    original = agent._get_toolset

    class CodePlaneToolset(WrapperToolset):
        wrapped: Toolset

        async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
            schema = getattr(getattr(tool, "call_func", None), "__self__", None)
            tracked = isinstance(schema, _function_schema.FunctionSchema) and gate.track_callable(schema, "function")
            entry_check = None
            delegated = tool_args
            if tracked and schema is not None:
                # The SDK consumes positional fields with pop(). Keep the approved
                # request intact and also fence the actual normalized leaf values.
                delegated = dict(tool_args)
                parameters = signature(schema.function)

                def normalized(args: tuple[Any, ...] | list[Any], kwargs: dict[str, Any]) -> str:
                    bound = parameters.bind(*args, **kwargs)
                    values = dict(bound.arguments)
                    if schema.takes_ctx:
                        name = next(iter(parameters.parameters))
                        values[name] = id(values[name])
                    return encode_arguments(values)

                approved_entry = normalized(*schema._call_args(dict(tool_args), ctx))

                def checked_entry(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
                    return normalized(args, kwargs) == approved_entry

                entry_check = checked_entry

            def fingerprint() -> str:
                return encode_arguments(
                    {
                        "wrapped": id(self.wrapped),
                        "tool": id(tool),
                        "call_func": id(getattr(tool, "call_func", None)),
                        "function": id(getattr(schema, "function", None)),
                        "arguments": tool_args,
                        "tool_name": tool.tool_def.name,
                        "description": tool.tool_def.description,
                        "call_id": ctx.tool_call_id,
                        "run_id": str(ctx.run_id),
                    }
                )

            call = NativeCall(
                name,
                tool_args,
                ctx.tool_call_id,
                str(ctx.run_id),
                definition=tool.tool_def.description or "",
                fingerprint=fingerprint,
                evidence=ExecutionEvidence(False if tracked else None),
                entry_check=entry_check,
            )
            if not await gate.allow(call):
                raise NativeGateDeniedError("Denied by CodePlane")
            original = self.wrapped.call_tool
            return await gate.complete(call, lambda: original(name, delegated, ctx, tool))

    def wrap(toolset: Any) -> Any:
        return toolset if isinstance(toolset, (OutputToolset, CodePlaneToolset)) else CodePlaneToolset(toolset)

    def assembled(*args: Any, **kwargs: Any) -> Any:
        # Includes runtime/override/dynamic toolsets, not just construction-time tools.
        return original(*args, **kwargs).visit_and_replace(wrap)

    gate.patch(agent, "_get_toolset", assembled)
