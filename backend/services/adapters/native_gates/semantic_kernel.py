"""Native AUTO_FUNCTION_INVOCATION filter, before the next handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._optional import load_sdk
from .binding import NativeCall

if TYPE_CHECKING:
    from ._optional import KernelFilters
    from .installation import GateAttachment

Kernel = load_sdk("semantic_kernel").Kernel
FilterTypes = load_sdk("semantic_kernel.filters").FilterTypes
FunctionResult = load_sdk("semantic_kernel.functions").FunctionResult


def install(gate: GateAttachment, kernel: KernelFilters) -> None:
    if not isinstance(kernel, Kernel):
        raise TypeError("Semantic Kernel gating requires the original Kernel")

    async def before(context: Any, next: Any) -> None:
        call = NativeCall(
            context.function.fully_qualified_name,
            context.arguments if context.arguments is not None else {},
            context.function_call_content.id,
            definition=context.function.description or "",
        )
        if not await gate.allow(call):
            context.function_result = FunctionResult(function=context.function.metadata, value="Denied by CodePlane")
            context.terminate = True
            return
        await gate.complete(call, lambda: next(context), outcome=lambda _: None if context.terminate else True)

    kernel.add_filter(FilterTypes.AUTO_FUNCTION_INVOCATION, before)
    gate.cleanup(lambda: kernel.remove_filter(FilterTypes.AUTO_FUNCTION_INVOCATION, filter_id=id(before)))
