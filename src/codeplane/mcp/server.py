"""FastMCP server creation and wiring."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP  # type: ignore[import-not-found]

    from codeplane.mcp.context import AppContext


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

    # Create a wrapper that takes the pydantic model and calls the registered handler
    async def handler(params: spec.params_model) -> dict[str, Any]:
        result: dict[str, Any] = await spec.handler(context, params)
        return result

    # Register with FastMCP - it will extract schema from the pydantic model
    mcp.tool(name=spec.name, description=spec.description)(handler)


def run_server(repo_root: Path, db_path: Path, tantivy_path: Path) -> None:
    """Create and run the MCP server."""
    from codeplane.mcp.context import AppContext

    context = AppContext.create(repo_root, db_path, tantivy_path)
    mcp = create_mcp_server(context)
    mcp.run()
