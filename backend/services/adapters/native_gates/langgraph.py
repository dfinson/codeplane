"""Gate the existing ToolNode without replacing the graph or its checkpointer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._optional import load_sdk
from .binding import NativeCall
from .readiness import UnsupportedNativeHookError

if TYPE_CHECKING:
    from ._optional import LangGraphToolNode
    from .installation import GateAttachment

ToolMessage = load_sdk("langchain_core.messages").ToolMessage
ToolNode = load_sdk("langgraph.prebuilt").ToolNode


def install(gate: GateAttachment, node: LangGraphToolNode) -> None:
    if not isinstance(node, ToolNode):
        raise TypeError("LangGraph gating requires the original ToolNode instance")
    sync, asynchronous = node._wrap_tool_call, node._awrap_tool_call
    if sync is not None and asynchronous is None:
        raise UnsupportedNativeHookError("An existing sync-only ToolNode wrapper needs a matching async wrapper")

    def call(request: Any) -> NativeCall:
        return NativeCall(
            request.tool_call["name"],
            request.tool_call["args"],
            request.tool_call["id"],
            definition=request.tool.description if request.tool is not None else "",
        )

    def denied(request: Any) -> Any:
        return ToolMessage(
            content="Denied by CodePlane",
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def outcome(result: Any) -> bool:
        return not (isinstance(result, ToolMessage) and result.status == "error")

    def wrapped(request: Any, execute: Any) -> Any:
        def guarded(actual: Any) -> Any:
            native_call = call(actual)
            if not gate.allow_sync(native_call):
                return denied(actual)
            return gate.complete_sync(native_call, lambda: execute(actual), outcome=outcome)

        return sync(request, guarded) if sync is not None else guarded(request)

    async def awrapped(request: Any, execute: Any) -> Any:
        async def guarded(actual: Any) -> Any:
            native_call = call(actual)
            if not await gate.allow(native_call):
                return denied(actual)
            return await gate.complete(native_call, lambda: execute(actual), outcome=outcome)

        return await asynchronous(request, guarded) if asynchronous is not None else await guarded(request)

    gate.patch(node, "_wrap_tool_call", wrapped)
    gate.patch(node, "_awrap_tool_call", awrapped)
