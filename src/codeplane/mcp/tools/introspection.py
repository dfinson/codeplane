"""Describe MCP tool - unified introspection handler.

Stripped to two actions for v2:
- tool: Get detailed documentation for a specific tool
- error: Get documentation for an error code
"""

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from pydantic import Field

from codeplane.mcp.docs import get_tool_documentation
from codeplane.mcp.errors import ERROR_CATALOG, get_error_documentation

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register introspection tools with FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Describe: tool params and error codes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def describe(
        ctx: Context,
        action: Literal["tool", "error"] = Field(
            ..., description="Introspection action: 'tool' or 'error'"
        ),
        name: str | None = Field(
            None, description="Tool name to describe (required for action='tool')"
        ),
        code: str | None = Field(
            None, description="Error code to describe (required for action='error')"
        ),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Introspection: describe tool parameters or error codes.

        Actions:
        - tool: Get detailed documentation for a specific tool
        - error: Get documentation for an error code
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        if action == "tool":
            if not name:
                return {"error": "tool action requires 'name'", "summary": "error: missing params"}
            doc = get_tool_documentation(name)
            if doc is None:
                # Get available tools from MCP tool manager
                from codeplane.mcp._compat import get_tools_sync

                tools_dict = get_tools_sync(mcp)
                available_tools = list(tools_dict.keys())
                if name not in available_tools:
                    return {
                        "found": False,
                        "error": f"Tool '{name}' not found",
                        "available_tools": available_tools,
                        "summary": f"tool '{name}' not found",
                    }
                # Basic info from tool manager
                tool_spec = tools_dict.get(name)
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

        return {"error": f"unknown action: {action}", "summary": "error: unknown action"}
