"""Describe MCP tool - unified introspection handler."""

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from codeplane.mcp.docs import get_tool_documentation
from codeplane.mcp.errors import ERROR_CATALOG, get_error_documentation

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Helpers
# =============================================================================


def _get_version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("codeplane")
    except PackageNotFoundError:
        # Package not installed in editable mode or not found
        return "unknown"


def _derive_features(tool_names: list[str]) -> list[str]:
    features: set[str] = set()
    for name in tool_names:
        if name.startswith("git_"):
            features.add("git_ops")
        elif name.startswith("refactor_"):
            features.add("refactoring")
        elif name == "test":
            features.add("testing")
        elif name == "lint":
            features.add("linting")
        elif name in ("search", "map_repo"):
            features.add("indexing")
        elif name in ("read_source", "list_files", "write_files"):
            features.add("file_ops")
        elif name == "describe":
            features.add("introspection")
    return sorted(features)


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register introspection tools with FastMCP server."""

    @mcp.tool
    async def describe(
        ctx: Context,
        action: Literal["tool", "error", "capabilities", "workflows", "operations"] = Field(
            ..., description="Introspection action"
        ),
        name: str | None = Field(None, description="Tool name to describe"),
        code: str | None = Field(None, description="Error code to describe"),
        path: str | None = Field(None, description="Filter operations by path"),
        success_only: bool = Field(False, description="Show only successful operations"),
        limit: int = Field(50, description="Maximum operations to return"),
    ) -> dict[str, Any]:
        """Introspection: describe tools, errors, capabilities, workflows, or operations.

        Actions:
        - tool: Get detailed documentation for a specific tool
        - error: Get documentation for an error code
        - capabilities: List server capabilities and available tools
        - workflows: Get common tool workflow patterns
        - operations: Query recent mutation operations for debugging
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "tool":
            if not name:
                return {"error": "tool action requires 'name'", "summary": "error: missing params"}
            doc = get_tool_documentation(name)
            if doc is None:
                # Get available tools from MCP tool manager
                available_tools = list(mcp._tool_manager._tools.keys())
                if name not in available_tools:
                    return {
                        "found": False,
                        "error": f"Tool '{name}' not found",
                        "available_tools": available_tools,
                        "summary": f"tool '{name}' not found",
                    }
                # Basic info from tool manager
                tool_spec = mcp._tool_manager._tools.get(name)
                desc = tool_spec.description if tool_spec else "No description"
                return {
                    "found": True,
                    "name": name,
                    "description": desc,
                    "extended_docs": False,
                    "summary": f"{name}: {desc}",
                }
            return {
                "found": True,
                "extended_docs": True,
                **doc.to_dict(),
                "summary": f"{name}: {doc.description}",
            }

        if action == "error":
            if not code:
                return {"error": "error action requires 'code'", "summary": "error: missing params"}
            err_doc = get_error_documentation(code)
            if err_doc is None:
                return {
                    "found": False,
                    "error": f"Error code '{code}' not documented",
                    "available_codes": list(ERROR_CATALOG.keys()),
                    "summary": f"error code '{code}' not found",
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

        if action == "capabilities":
            all_tools = mcp._tool_manager._tools
            tool_names = list(all_tools.keys())
            tool_list = [
                {"name": name, "description": spec.description} for name, spec in all_tools.items()
            ]
            index_status: dict[str, Any] = {}
            try:
                epoch = app_ctx.coordinator.get_current_epoch()
                index_status["current_epoch"] = epoch
                index_status["initialized"] = app_ctx.coordinator._initialized
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

        if action == "workflows":
            workflows = [
                {
                    "name": "code_review",
                    "description": "Review code changes before commit",
                    "steps": ["git_status", "git_diff", "lint", "test"],
                },
                {
                    "name": "refactor_symbol",
                    "description": "Safely rename a symbol across codebase",
                    "steps": ["refactor_rename", "refactor_inspect", "refactor_apply"],
                    "details": {
                        "certainty_levels": {
                            "high": "Definition proven by structural index",
                            "medium": "Comment/docstring references",
                            "low": "Lexical text matches (cross-file refs, imports)",
                        },
                        "unique_identifiers": "For unique names (MyClassName), low-certainty matches are usually safe to apply directly",
                        "common_words": "For common words (data, result), use refactor_inspect first to check for false positives",
                        "response_fields": [
                            "verification_required",
                            "low_certainty_files",
                            "verification_guidance",
                        ],
                    },
                },
                {
                    "name": "explore_codebase",
                    "description": "Understand repository structure",
                    "steps": ["map_repo", "search", "read_source"],
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

        if action == "operations":
            from codeplane.mcp.ledger import get_ledger

            ledger = get_ledger()
            ops = ledger.list_operations(
                path=path,
                success_only=success_only,
                limit=min(limit, 200),
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

        return {"error": f"unknown action: {action}", "summary": "error: unknown action"}

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
