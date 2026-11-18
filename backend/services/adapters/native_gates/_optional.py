"""Narrow typing boundary for optional, version-qualified native SDKs.

Core type checking must not require installing the frameworks. These protocols
describe only the surfaces consumed here. Runtime classes still come from the
real SDK via import_module, after dependency readiness has been checked.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType


def load_sdk(module: str) -> ModuleType:
    return import_module(module)


class LangChainTool(Protocol):
    name: str
    description: str
    coroutine: Callable[..., Awaitable[Any]] | None

    def _run(self, *args: Any, **kwargs: Any) -> Any: ...

    async def _arun(self, *args: Any, **kwargs: Any) -> Any: ...

    def run(self, *args: Any, **kwargs: Any) -> Any: ...

    async def arun(self, *args: Any, **kwargs: Any) -> Any: ...


class LangGraphToolNode(Protocol):
    _wrap_tool_call: Callable[..., Any] | None
    _awrap_tool_call: Callable[..., Awaitable[Any]] | None


class KernelFilters(Protocol):
    def add_filter(self, filter_type: Any, filter: Callable[..., Awaitable[None]]) -> None: ...

    def remove_filter(self, filter_type: Any, *, filter_id: int) -> None: ...


class SmolToolCallingAgent(Protocol):
    tools: dict[str, Any]
    managed_agents: dict[str, Any]

    def execute_tool_call(self, tool_name: str, arguments: dict[str, str] | str) -> Any: ...

    def _substitute_state_variables(self, arguments: dict[str, str] | str) -> Any: ...


class FunctionTool(Protocol):
    name: str
    description: str
    on_invoke_tool: Callable[[Any, str], Awaitable[Any]]


class AgentTools(Protocol):
    tools: list[Any]


class AgentToolsets(Protocol):
    def _get_toolset(self, *args: Any, **kwargs: Any) -> Any: ...


class Toolset(Protocol):
    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any: ...


class WrapperToolset(Toolset, Protocol):
    wrapped: Toolset

    def __init__(self, wrapped: Toolset) -> None:
        self.wrapped = wrapped


class FunctionMiddleware(Protocol):
    async def process(self, context: Any, call_next: Callable[[], Awaitable[None]]) -> None: ...


class NativeExport(Protocol):
    def export(self) -> dict[str, Any] | None: ...


class TracingProcessor(Protocol):
    def on_trace_start(self, trace: NativeExport) -> None: ...

    def on_trace_end(self, trace: NativeExport) -> None: ...

    def on_span_start(self, span: NativeExport) -> None: ...

    def on_span_end(self, span: NativeExport) -> None: ...

    def shutdown(self) -> None: ...

    def force_flush(self) -> None: ...
