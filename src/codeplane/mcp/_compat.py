"""FastMCP v3 compatibility helpers.

FastMCP v3 removed `_tool_manager` from the public API. Tools are now
stored in `local_provider._components` as `FastMCPComponent` instances.
This module provides a typed, synchronous accessor for tool lookups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools.function_tool import FunctionTool


def get_tools_sync(mcp: FastMCP) -> dict[str, FunctionTool]:
    """Get all registered tools as a {name: FunctionTool} dict.

    Provides synchronous access to tool metadata, replacing the
    removed ``mcp._tool_manager._tools`` pattern.
    """
    from fastmcp.tools.function_tool import FunctionTool

    return {
        comp.name: comp
        for comp in mcp.local_provider._components.values()
        if isinstance(comp, FunctionTool)
    }
