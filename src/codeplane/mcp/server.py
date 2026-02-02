"""FastMCP server creation and wiring."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.utilities.json_schema import dereference_refs
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


class ToolResponse(BaseModel):
    """Standardized tool response envelope per Spec §23.3."""

    # Using snake_case for wire format compatibility with Spec
    result: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)

    # Robustness fields (implied by "structured error response")
    success: bool
    error: str | None = None


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
        stateless_http=True,
        json_response=True,  # Use JSON responses instead of SSE for broader client compatibility
    )

    # Wire all registered tools
    for spec in registry.get_all():
        _wire_tool(mcp, spec, context)

    return mcp


def _wire_tool(mcp: FastMCP, spec: Any, context: AppContext) -> None:
    """Wire a single tool spec to FastMCP.

    Creates a handler function with the params model's fields as direct
    parameters, ensuring FastMCP generates a flat schema compatible with
    all MCP clients including Claude.
    """
    from fastmcp.tools.tool import FunctionTool

    params_model = spec.params_model
    spec_handler = spec.handler

    # Get the JSON schema from the params model and fully dereference it
    # dereference_refs inlines all $refs and removes $defs for full compatibility
    raw_schema = params_model.model_json_schema()
    flat_schema = dereference_refs(raw_schema)

    # Create handler that accepts **kwargs and reconstructs the params model
    async def handler(**kwargs: Any) -> dict[str, Any]:
        # Reconstruct the params model from kwargs
        params = params_model(**kwargs)

        session_id = getattr(params, "session_id", None)
        session = context.session_manager.get_or_create(session_id)

        try:
            result_data: dict[str, Any] = await spec_handler(context, params)
            return ToolResponse(
                success=True,
                result=result_data,
                meta={
                    "session_id": session.session_id,
                    "timestamp": int(time.time() * 1000),
                },
            ).model_dump()
        except Exception as e:
            return ToolResponse(
                success=False,
                result=None,
                error=str(e),
                meta={
                    "session_id": session.session_id,
                },
            ).model_dump()

    # Create a FunctionTool with the flattened schema
    tool = FunctionTool(
        name=spec.name,
        description=spec.description,
        parameters=flat_schema,
        fn=handler,
    )

    mcp.add_tool(tool)


def run_server(repo_root: Path, db_path: Path, tantivy_path: Path) -> None:
    """Create and run the MCP server."""
    from codeplane.mcp.context import AppContext

    context = AppContext.create(repo_root, db_path, tantivy_path)
    mcp = create_mcp_server(context)
    mcp.run()
