"""Describe MCP tool - unified introspection handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from codeplane.mcp.docs import get_tool_documentation
from codeplane.mcp.errors import ERROR_CATALOG, get_error_documentation
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Model
# =============================================================================


class DescribeParams(BaseParams):
    """Parameters for describe tool."""

    action: Literal["tool", "error", "capabilities", "workflows", "operations"]

    # tool action params
    name: str | None = Field(default=None, description="Tool name to describe")

    # error action params
    code: str | None = Field(default=None, description="Error code to describe")

    # operations action params
    path: str | None = None
    success_only: bool = False
    limit: int = 50


# =============================================================================
# Helpers
# =============================================================================


def _get_version() -> str:
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("codeplane")
    except Exception:
        return "unknown"


def _derive_features(tool_names: list[str]) -> list[str]:
    features: set[str] = set()
    for name in tool_names:
        if name.startswith("git_"):
            features.add("git_ops")
        elif name == "refactor":
            features.add("refactoring")
        elif name == "session":
            features.add("session_management")
        elif name == "test":
            features.add("testing")
        elif name == "lint":
            features.add("linting")
        elif name in ("search", "map_repo"):
            features.add("indexing")
        elif name in ("read_files", "list_files", "write_files"):
            features.add("file_ops")
        elif name == "describe":
            features.add("introspection")
    return sorted(features)


# =============================================================================
# Tool Handler
# =============================================================================


@registry.register(
    "describe",
    "Introspection: describe tools, errors, capabilities, workflows, or operations",
    DescribeParams,
)
async def describe(ctx: AppContext, params: DescribeParams) -> dict[str, Any]:
    """Unified introspection tool.

    Actions:
    - tool: Get detailed documentation for a specific tool
    - error: Get documentation for an error code
    - capabilities: List server capabilities and available tools
    - workflows: Get common tool workflow patterns
    - operations: Query recent mutation operations for debugging
    """
    if params.action == "tool":
        if not params.name:
            return {"error": "tool action requires 'name'", "summary": "error: missing params"}
        doc = get_tool_documentation(params.name)
        if doc is None:
            spec = registry.get(params.name)
            if spec is None:
                return {
                    "found": False,
                    "error": f"Tool '{params.name}' not found",
                    "available_tools": [s.name for s in registry.get_all()],
                    "summary": f"tool '{params.name}' not found",
                }
            return {
                "found": True,
                "name": spec.name,
                "description": spec.description,
                "extended_docs": False,
                "summary": f"basic docs for {spec.name}",
            }
        return {
            "found": True,
            "extended_docs": True,
            **doc.to_dict(),
            "summary": f"full docs for {params.name}",
        }

    if params.action == "error":
        if not params.code:
            return {"error": "error action requires 'code'", "summary": "error: missing params"}
        err_doc = get_error_documentation(params.code)
        if err_doc is None:
            return {
                "found": False,
                "error": f"Error code '{params.code}' not documented",
                "available_codes": list(ERROR_CATALOG.keys()),
                "summary": f"error code '{params.code}' not found",
            }
        return {
            "found": True,
            "code": err_doc.code.value,
            "category": err_doc.category,
            "description": err_doc.description,
            "causes": err_doc.causes,
            "remediation": err_doc.remediation,
            "summary": f"{err_doc.code.value}: {err_doc.description}",
        }

    if params.action == "capabilities":
        all_tools = registry.get_all()
        tool_names = [spec.name for spec in all_tools]
        tool_list = [{"name": spec.name, "description": spec.description} for spec in all_tools]
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

    if params.action == "workflows":
        workflows = [
            {
                "name": "code_review",
                "description": "Review code changes before commit",
                "steps": ["git_status", "git_diff", "lint", "test"],
            },
            {
                "name": "refactor_symbol",
                "description": "Safely rename a symbol across codebase",
                "steps": [
                    "refactor (action=rename)",
                    "refactor (action=inspect)",
                    "refactor (action=apply)",
                ],
            },
            {
                "name": "explore_codebase",
                "description": "Understand repository structure",
                "steps": ["map_repo", "search", "read_files"],
            },
            {
                "name": "fix_and_commit",
                "description": "Edit files, lint, test, and commit",
                "steps": ["write_files", "lint", "test", "git_commit"],
            },
        ]
        return {
            "workflows": workflows,
            "summary": f"{len(workflows)} workflow patterns",
        }

    if params.action == "operations":
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

    return {"error": f"unknown action: {params.action}", "summary": "error: unknown action"}
