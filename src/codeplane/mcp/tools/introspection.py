"""Introspection MCP tools - meta_describe_* handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from codeplane.mcp.docs import (
    get_tool_documentation,
)
from codeplane.mcp.errors import ERROR_CATALOG, get_error_documentation
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class DescribeToolParams(BaseParams):
    """Parameters for meta_describe_tool."""

    name: str = Field(..., description="Name of the tool to describe")


class DescribeErrorParams(BaseParams):
    """Parameters for meta_describe_error."""

    code: str = Field(..., description="Error code to describe (e.g., 'CONTENT_NOT_FOUND')")


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register(
    "meta_describe_tool",
    "Get detailed documentation for a specific tool",
    DescribeToolParams,
)
async def meta_describe_tool(_ctx: AppContext, params: DescribeToolParams) -> dict[str, Any]:
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


@registry.register(
    "meta_describe_error",
    "Get detailed documentation for an error code",
    DescribeErrorParams,
)
async def meta_describe_error(_ctx: AppContext, params: DescribeErrorParams) -> dict[str, Any]:
    """Return documentation for an error code including remediation steps."""
    doc = get_error_documentation(params.code)

    if doc is None:
        return {
            "found": False,
            "error": f"Error code '{params.code}' not documented",
            "available_codes": list(ERROR_CATALOG.keys()),
            "summary": f"error code '{params.code}' not found",
        }

    return {
        "found": True,
        "code": doc.code.value,
        "category": doc.category,
        "description": doc.description,
        "causes": doc.causes,
        "remediation": doc.remediation,
        "summary": f"{doc.code.value}: {doc.description}",
    }
