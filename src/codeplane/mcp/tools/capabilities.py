"""Capabilities MCP tool - server introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class CapabilitiesParams(BaseParams):
    """Parameters for capabilities (none required)."""

    pass


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("capabilities", "List server capabilities and available tools", CapabilitiesParams)
async def capabilities(ctx: AppContext, params: CapabilitiesParams) -> dict[str, Any]:
    """Return server capabilities, available tools, and index status."""
    # Get all registered tools
    all_tools = registry.get_all()
    tool_list = [
        {
            "name": spec.name,
            "description": spec.description,
        }
        for spec in all_tools
    ]

    # Get index status if available
    index_status: dict[str, Any] = {}
    try:
        epoch = ctx.coordinator.get_current_epoch()
        index_status["current_epoch"] = epoch
        index_status["initialized"] = ctx.coordinator._initialized
    except Exception:
        index_status["initialized"] = False

    return {
        "version": "0.1.0",
        "server": "codeplane",
        "tool_count": len(tool_list),
        "tools": tool_list,
        "index_status": index_status,
        "features": [
            "lexical_search",
            "symbol_search",
            "reference_search",
            "git_ops",
            "mutation",
            "refactoring",
            "session_management",
            "map_repo",
        ],
    }
