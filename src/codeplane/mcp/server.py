"""FastMCP server creation and wiring."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


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
    import fastmcp
    from fastmcp import FastMCP

    from codeplane.mcp.registry import registry

    # Import tools to trigger registration
    from codeplane.mcp.tools import (  # noqa: F401
        files,
        git,
        index,
        introspection,
        mutation,
        refactor,
        testing,
    )

    log.info("mcp_server_creating", repo_root=str(context.repo_root))

    # Configure FastMCP global settings for HTTP transport
    fastmcp.settings.stateless_http = True
    fastmcp.settings.json_response = True

    mcp = FastMCP(
        "codeplane",
        instructions="CodePlane repository control plane for AI coding agents.",
    )

    # Wire all registered tools
    tool_count = 0
    for spec in registry.get_all():
        _wire_tool(mcp, spec, context)
        tool_count += 1

    log.info("mcp_server_created", tool_count=tool_count)

    return mcp


def _wire_tool(mcp: FastMCP, spec: Any, context: AppContext) -> None:
    """Wire a single tool spec to FastMCP.

    Creates a handler function with the params model's fields as direct
    parameters, ensuring FastMCP generates a flat schema compatible with
    all MCP clients including Claude.
    """
    from fastmcp.tools.tool import FunctionTool
    from pydantic import ValidationError

    from codeplane.mcp.errors import MCPError

    params_model = spec.params_model
    spec_handler = spec.handler

    # Get the JSON schema from the params model and fully dereference it
    # dereference_refs inlines all $refs and removes $defs for full compatibility
    raw_schema = params_model.model_json_schema()
    flat_schema = dereference_refs(raw_schema)

    # Create handler that accepts **kwargs and reconstructs the params model
    async def handler(**kwargs: Any) -> dict[str, Any]:
        tool_name = spec.name  # Capture for logging
        log.info("tool_call", tool=tool_name)

        # Reconstruct the params model from kwargs
        try:
            params = params_model(**kwargs)
        except ValidationError as e:
            # Return structured validation error
            log.warning("mcp_tool_validation_error", tool=tool_name, errors=e.error_count())
            return ToolResponse(
                success=False,
                result=None,
                error=f"Validation error: {e.errors()[0]['msg'] if e.errors() else str(e)}",
                meta={
                    "error_type": "validation",
                    "validation_errors": [
                        {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
                        for err in e.errors()[:5]  # Limit to first 5 errors
                    ],
                },
            ).model_dump()

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

        except MCPError as e:
            # Structured error response
            log.warning(
                "mcp_tool_error",
                tool=tool_name,
                error_code=e.code.value,
                path=e.path,
            )
            return ToolResponse(
                success=False,
                result=None,
                error=e.message,
                meta={
                    "session_id": session.session_id,
                    "error": e.to_response().to_dict(),
                },
            ).model_dump()

        except Exception as e:
            log.error("mcp_tool_call_error", tool=tool_name, error=str(e), exc_info=True)
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
    from codeplane.config.models import LoggingConfig, LogOutputConfig
    from codeplane.core.logging import configure_logging
    from codeplane.mcp.context import AppContext

    # Configure logging to both stderr and a file for debugging
    log_file = repo_root / ".codeplane" / "mcp-server.log"
    configure_logging(
        config=LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(destination="stderr", format="console", level="INFO"),
                LogOutputConfig(destination=str(log_file), format="json", level="DEBUG"),
            ],
        )
    )

    log.info(
        "mcp_server_starting",
        repo_root=str(repo_root),
        db_path=str(db_path),
        tantivy_path=str(tantivy_path),
        log_file=str(log_file),
    )

    context = AppContext.create(repo_root, db_path, tantivy_path)
    mcp = create_mcp_server(context)

    log.info("mcp_server_running")
    mcp.run()
