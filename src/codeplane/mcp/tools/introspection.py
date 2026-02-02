"""Introspection MCP tools - describe_tool, describe_error, list_operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from codeplane.mcp.docs import (
    get_common_workflows,
    get_tool_documentation,
    get_tools_by_category,
)
from codeplane.mcp.errors import ERROR_CATALOG, get_error_documentation
from codeplane.mcp.ledger import get_ledger
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class DescribeToolParams(BaseParams):
    """Parameters for describe_tool."""

    name: str = Field(..., description="Name of the tool to describe")


class DescribeErrorParams(BaseParams):
    """Parameters for describe_error."""

    code: str = Field(..., description="Error code to describe (e.g., 'CONTENT_NOT_FOUND')")


class ListOperationsParams(BaseParams):
    """Parameters for list_operations."""

    path: str | None = Field(None, description="Filter by file path")
    success_only: bool = Field(False, description="Only show successful operations")
    limit: int = Field(50, ge=1, le=200, description="Maximum number of results")


class ListWorkflowsParams(BaseParams):
    """Parameters for list_workflows."""

    pass


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register(
    "describe_tool",
    "Get detailed documentation for a specific tool",
    DescribeToolParams,
)
async def describe_tool(_ctx: AppContext, params: DescribeToolParams) -> dict[str, Any]:
    """Return full documentation for a tool."""
    doc = get_tool_documentation(params.name)

    if doc is None:
        # Tool exists but no extended docs - return basic info from registry
        spec = registry.get(params.name)
        if spec is None:
            return {
                "found": False,
                "error": f"Tool '{params.name}' not found",
                "available_tools": [s.name for s in registry.get_all()],
            }
        return {
            "found": True,
            "name": spec.name,
            "description": spec.description,
            "extended_docs": False,
        }

    return {
        "found": True,
        "extended_docs": True,
        **doc.to_dict(),
    }


@registry.register(
    "describe_error",
    "Get detailed documentation for an error code",
    DescribeErrorParams,
)
async def describe_error(_ctx: AppContext, params: DescribeErrorParams) -> dict[str, Any]:
    """Return documentation for an error code including remediation steps."""
    doc = get_error_documentation(params.code)

    if doc is None:
        return {
            "found": False,
            "error": f"Error code '{params.code}' not documented",
            "available_codes": list(ERROR_CATALOG.keys()),
        }

    return {
        "found": True,
        "code": doc.code.value,
        "category": doc.category,
        "description": doc.description,
        "causes": doc.causes,
        "remediation": doc.remediation,
    }


@registry.register(
    "list_operations",
    "Query recent mutation operations for debugging",
    ListOperationsParams,
)
async def list_operations(_ctx: AppContext, params: ListOperationsParams) -> dict[str, Any]:
    """List recent mutation operations from the ledger."""
    ledger = get_ledger()

    operations = ledger.list_operations(
        path=params.path,
        session_id=params.session_id,
        success_only=params.success_only,
        limit=params.limit,
    )

    return {
        "count": len(operations),
        "operations": [op.to_dict() for op in operations],
    }


@registry.register(
    "list_workflows",
    "Get common tool workflow patterns",
    ListWorkflowsParams,
)
async def list_workflows(_ctx: AppContext, _params: ListWorkflowsParams) -> dict[str, Any]:
    """Return common workflow patterns for tool usage."""
    return {
        "workflows": get_common_workflows(),
        "tools_by_category": get_tools_by_category(),
    }
