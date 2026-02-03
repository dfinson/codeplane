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
        # Handle new dot-namespaced format
        if name.startswith("git."):
            features.add("git_ops")
        elif name.startswith("refactor."):
            features.add("refactoring")
        elif name.startswith("session."):
            features.add("session_management")
        elif name.startswith("testing."):
            features.add("testing")
        elif name.startswith("lint."):
            features.add("linting")
        elif name.startswith("index."):
            features.add("indexing")
        elif name.startswith("files."):
            features.add("file_ops")
        elif name.startswith("meta."):
            features.add("introspection")

    return sorted(features)


# =============================================================================
# Parameter Models
# =============================================================================


class CapabilitiesParams(BaseParams):
    """Parameters for meta.capabilities (none required)."""

    pass


class WorkflowsParams(BaseParams):
    """Parameters for meta.workflows (none required)."""

    pass


class OperationsParams(BaseParams):
    """Parameters for meta.operations."""

    path: str | None = None
    success_only: bool = False
    limit: int = 50


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register(
    "meta.capabilities", "List server capabilities and available tools", CapabilitiesParams
)
async def meta_capabilities(ctx: AppContext, _params: CapabilitiesParams) -> dict[str, Any]:
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

    features = _derive_features(tool_names)

    return {
        "version": _get_version(),
        "server": "codeplane",
        "tool_count": len(tool_list),
        "tools": tool_list,
        "index_status": index_status,
        "features": features,
        "summary": f"{len(tool_list)} tools, {len(features)} feature domains",
    }


@registry.register("meta.workflows", "Get common tool workflow patterns", WorkflowsParams)
async def meta_workflows(_ctx: AppContext, _params: WorkflowsParams) -> dict[str, Any]:
    """Return common workflow patterns for tool usage."""
    workflows = [
        {
            "name": "code_review",
            "description": "Review code changes before commit",
            "steps": ["git.status", "git.diff", "lint.check", "testing.run"],
        },
        {
            "name": "refactor_symbol",
            "description": "Safely rename a symbol across codebase",
            "steps": ["refactor.rename", "refactor.inspect", "refactor.apply"],
        },
        {
            "name": "explore_codebase",
            "description": "Understand repository structure",
            "steps": ["index.map", "index.search", "files.read"],
        },
        {
            "name": "fix_and_commit",
            "description": "Edit files, lint, test, and commit",
            "steps": ["files.edit", "lint.check", "testing.run", "git.commit"],
        },
    ]
    return {
        "workflows": workflows,
        "summary": f"{len(workflows)} workflow patterns",
    }


@registry.register(
    "meta.operations", "Query recent mutation operations for debugging", OperationsParams
)
async def meta_operations(_ctx: AppContext, params: OperationsParams) -> dict[str, Any]:
    """Query recent operations from the ledger."""
    from codeplane.mcp.ledger import get_ledger

    ledger = get_ledger()
    ops = ledger.list_operations(
        path=params.path,
        success_only=params.success_only,
        limit=min(params.limit, 200),
    )

    return {
        "operations": [
            {
                "op_id": op.op_id,
                "tool": op.tool,
                "success": op.success,
                "timestamp": op.timestamp,
                "path": op.path,
                "action": op.action,
                "error_code": op.error_code,
                "error_message": op.error_message,
            }
            for op in ops
        ],
        "count": len(ops),
        "summary": f"{len(ops)} recent operations",
    }
