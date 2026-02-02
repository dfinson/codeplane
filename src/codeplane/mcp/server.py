"""FastMCP server creation and wiring."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


class ToolResponse(BaseModel):
    """Standardized tool response envelope per Spec §23.3."""

    success: bool
    data: Any
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_mcp_server(context: AppContext) -> FastMCP:
    """Create FastMCP server with all tools wired to context.

    Args:
        context: AppContext with all ops instances

    Returns:
        Configured FastMCP server ready to run
    """
    from fastmcp import FastMCP

    from codeplane.mcp.registry import registry

    # Import tools to trigger registration
    from codeplane.mcp.tools import (  # noqa: F401
        files,
        git,
        index,
        mutation,
        refactor,
        testing,
    )

    mcp = FastMCP(
        "codeplane",
        instructions="CodePlane repository control plane for AI coding agents.",
    )

    # Wire all registered tools
    for spec in registry.get_all():
        _wire_tool(mcp, spec, context)

    return mcp


def _wire_tool(mcp: FastMCP, spec: Any, context: AppContext) -> None:
    """Wire a single tool spec to FastMCP."""
    # Capture spec in closure by binding to default argument
    params_model = spec.params_model
    spec_handler = spec.handler

    # Create handler with explicit type annotation set after definition
    async def handler(params: Any) -> ToolResponse:
        try:
            result: dict[str, Any] = await spec_handler(context, params)
            return ToolResponse(success=True, data=result)
        except Exception as e:
            # We catch all exceptions to ensure we return a structured error response
            # instead of crashing the MCP connection or returning an RPC error.
            return ToolResponse(success=False, data=None, error=str(e))

    # Set the type hint explicitly so FastMCP can introspect it
    handler.__annotations__["params"] = params_model

    # Register with FastMCP - it will extract schema from the pydantic model
    mcp.tool(name=spec.name, description=spec.description)(handler)


def run_server(repo_root: Path, db_path: Path, tantivy_path: Path) -> None:
    """Create and run the MCP server."""
    from codeplane.mcp.context import AppContext

    context = AppContext.create(repo_root, db_path, tantivy_path)
    mcp = create_mcp_server(context)
    mcp.run()
