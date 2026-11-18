"""LangChain's execution wrappers enforce; callbacks remain observational."""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from inspect import signature
from threading import Lock
from typing import TYPE_CHECKING, Any

from ._optional import load_sdk
from .binding import NativeCall, encode_arguments

if TYPE_CHECKING:
    from ._optional import LangChainTool
    from .installation import GateAttachment

RunnableConfig = load_sdk("langchain_core.runnables").RunnableConfig
_tools = load_sdk("langchain_core.tools")
BaseTool, ToolException = _tools.BaseTool, _tools.ToolException


def install(gate: GateAttachment, tool: LangChainTool) -> None:
    if not isinstance(tool, BaseTool):
        raise TypeError("LangChain gating requires the original BaseTool instance")
    call_id: ContextVar[str | None] = ContextVar("native_langchain_call", default=None)
    delegated: ContextVar[tuple[str, list[bool], Lock] | None] = ContextVar("native_langchain_delegation", default=None)
    original_run, original_arun = tool._run, tool._arun

    def call(args: tuple[Any, ...], kwargs: dict[str, Any], manager: Any) -> NativeCall:
        return NativeCall(
            tool.name,
            {"args": args, "kwargs": kwargs},
            call_id.get(),
            str(manager.run_id) if manager else None,
            str(manager.parent_run_id) if manager and manager.parent_run_id else None,
            tool.description,
        )

    def forwarded(original: Any, kwargs: dict[str, Any], config: Any, manager: Any) -> dict[str, Any]:
        values = dict(kwargs)
        parameters = signature(original).parameters
        if "config" in parameters:
            values["config"] = config
        if "run_manager" in parameters:
            values["run_manager"] = manager
        return values

    def guarded_run(*args: Any, config: dict[str, Any] | None = None, run_manager: Any = None, **kwargs: Any) -> Any:
        native_call = call(args, kwargs, run_manager)
        permit = delegated.get()
        authorized = False
        if permit is not None:
            snapshot, remaining, lock = permit
            with lock:
                if remaining and snapshot == encode_arguments(native_call.arguments):
                    remaining.pop()
                    authorized = gate.active
        if not authorized and not gate.allow_sync(native_call):
            raise ToolException("Denied by CodePlane")

        def invoke() -> Any:
            return original_run(*args, **forwarded(original_run, kwargs, config, run_manager))

        return invoke() if authorized else gate.complete_sync(native_call, invoke)

    async def guarded_arun(
        *args: Any, config: dict[str, Any] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        native_call = call(args, kwargs, run_manager)
        if not await gate.allow(native_call):
            raise ToolException("Denied by CodePlane")
        token = delegated.set((encode_arguments(native_call.arguments), [True], Lock()))
        try:
            return await gate.complete(
                native_call, lambda: original_arun(*args, **forwarded(original_arun, kwargs, config, run_manager))
            )
        finally:
            delegated.reset(token)

    # LangChain discovers config by exact type annotation, not its parameter name.
    guarded_run.__annotations__["config"] = RunnableConfig
    guarded_arun.__annotations__["config"] = RunnableConfig
    gate.patch(tool, "_run", guarded_run)
    native_async = tool.coroutine is not None if hasattr(tool, "coroutine") else type(tool)._arun is not BaseTool._arun
    if native_async:
        gate.patch(tool, "_arun", guarded_arun)

    run, arun = tool.run, tool.arun

    @wraps(run)
    def with_id(*args: Any, **kwargs: Any) -> Any:
        token = call_id.set(kwargs.get("tool_call_id"))
        try:
            return run(*args, **kwargs)
        finally:
            call_id.reset(token)

    @wraps(arun)
    async def with_async_id(*args: Any, **kwargs: Any) -> Any:
        token = call_id.set(kwargs.get("tool_call_id"))
        try:
            return await arun(*args, **kwargs)
        finally:
            call_id.reset(token)

    gate.patch(tool, "run", with_id)
    gate.patch(tool, "arun", with_async_id)
