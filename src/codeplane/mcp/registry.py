"""Tool registry for MCP server.

Provides decorator-based tool registration with Pydantic param validation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext

T = TypeVar("T", bound=BaseModel)

# Handler signature: (ctx, validated_params) -> dict
HandlerFn = Callable[["AppContext", Any], Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    """Specification for a registered tool."""

    name: str
    handler: HandlerFn
    description: str
    params_model: type[BaseModel]


class ToolRegistry:
    """Registry for MCP tools with decorator-based registration."""

    _instance: ToolRegistry | None = None
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def __new__(cls) -> ToolRegistry:
        """Singleton pattern for global registry."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
        return cls._instance

    def register(
        self,
        name: str,
        description: str,
        params_model: type[BaseModel],
    ) -> Callable[[HandlerFn], HandlerFn]:
        """Decorator to register a tool handler.

        Usage:
            @registry.register("git_status", "Get repo status", GitStatusParams)
            async def git_status(ctx: AppContext, params: GitStatusParams) -> dict:
                ...
        """

        def decorator(fn: HandlerFn) -> HandlerFn:
            self._tools[name] = ToolSpec(
                name=name,
                handler=fn,
                description=description,
                params_model=params_model,
            )
            return fn

        return decorator

    def get_all(self) -> list[ToolSpec]:
        """Get all registered tool specs."""
        return list(self._tools.values())

    def get(self, name: str) -> ToolSpec | None:
        """Get a specific tool spec by name."""
        return self._tools.get(name)

    def clear(self) -> None:
        """Clear all registrations (for testing)."""
        self._tools.clear()


# Global registry instance
registry = ToolRegistry()
