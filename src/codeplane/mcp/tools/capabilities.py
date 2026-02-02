"""Capabilities MCP tool - server introspection."""

from __future__ import annotations

from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


def _get_version() -> str:
    """Get codeplane package version."""
    try:
        return pkg_version("codeplane")
    except Exception:
        return "unknown"


def _derive_features(tool_names: list[str]) -> list[str]:
    """Derive feature categories from registered tool names."""
    features: set[str] = set()

    for name in tool_names:
        if name.startswith("git_"):
            features.add("git_ops")
        elif name.startswith("refactor_"):
            features.add("refactoring")
        elif name.startswith("session_"):
            features.add("session_management")
        elif name.startswith("test_"):
            features.add("testing")
        elif name == "search":
            features.add("search")
        elif name == "atomic_edit_files":
            features.add("mutation")
        elif name == "map_repo":
            features.add("map_repo")
        elif name == "read_files":
            features.add("file_ops")
        elif name == "capabilities":
            features.add("introspection")

    return sorted(features)


# =============================================================================
# Parameter Models
# =============================================================================


class CapabilitiesParams(BaseParams):
    """Parameters for capabilities (none required)."""

    pass


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register(
    "capabilities", "List server capabilities and available tools", CapabilitiesParams
)
async def capabilities(ctx: AppContext, _params: CapabilitiesParams) -> dict[str, Any]:
    """Return server capabilities, available tools, and index status."""
    # Get all registered tools
    all_tools = registry.get_all()
    tool_names = [spec.name for spec in all_tools]
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
        "version": _get_version(),
        "server": "codeplane",
        "tool_count": len(tool_list),
        "tools": tool_list,
        "index_status": index_status,
        "features": _derive_features(tool_names),
    }
