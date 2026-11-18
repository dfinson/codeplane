"""MAF FunctionMiddleware. This is not the M365 transcript/span integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ._optional import load_sdk
from .binding import ExecutionEvidence, NativeCall, encode_arguments

if TYPE_CHECKING:
    from ._optional import FunctionMiddleware
    from .installation import GateAttachment
else:
    FunctionMiddleware = load_sdk("agent_framework").FunctionMiddleware

MiddlewareTermination = load_sdk("agent_framework").MiddlewareTermination


class MiddlewareSlot:
    """Caller-owned middleware list actually supplied to the native agent/run."""

    def __init__(self, middleware: list[Any]) -> None:
        self.middleware = middleware


def install(gate: GateAttachment, slot: MiddlewareSlot) -> None:
    if not isinstance(slot, MiddlewareSlot):
        raise TypeError("MAF gating requires the caller's MiddlewareSlot")

    class CodePlaneMiddleware(FunctionMiddleware):
        async def process(self, context: Any, call_next: Any) -> None:
            def arguments() -> Any:
                value = context.arguments
                return value.model_dump(mode="json") if isinstance(value, BaseModel) else value

            tracked = gate.track_callable(context.function, "func")

            def fingerprint() -> str:
                return encode_arguments(
                    {
                        "function": id(context.function),
                        "func": id(context.function.func),
                        "name": context.function.name,
                        "description": context.function.description,
                        "arguments": arguments(),
                        "kwargs": context.kwargs,
                        "session": id(context.session),
                    }
                )

            call = NativeCall(
                context.function.name,
                arguments(),
                # This version supplies call_id through invocation kwargs, not a context attribute.
                context.kwargs.get("tool_call_id"),
                definition=context.function.description,
                fingerprint=fingerprint,
                evidence=ExecutionEvidence(False if tracked else None),
            )
            if not await gate.allow(call):
                raise MiddlewareTermination("Denied by CodePlane")
            await gate.complete(call, call_next)

    middleware = CodePlaneMiddleware()
    slot.middleware.append(middleware)

    def remove() -> None:
        slot.middleware[:] = [item for item in slot.middleware if item is not middleware]

    gate.cleanup(remove)
