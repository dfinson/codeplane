"""FastMCP server creation and wiring.

Uses native FastMCP @mcp.tool decorators for tool registration.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


def create_mcp_server(context: "AppContext") -> "FastMCP":
    """Create FastMCP server with all tools wired to context.

    Args:
        context: AppContext with all ops instances

    Returns:
        Configured FastMCP server ready to run
    """
    import fastmcp
    from fastmcp import FastMCP

    from codeplane.mcp.tools import (
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
    fastmcp.settings.json_response = True

    mcp = FastMCP(
        "codeplane",
        instructions="CodePlane repository control plane for AI coding agents.",
    )

    # Register all tools using native FastMCP decorators
    files.register_tools(mcp, context)
    git.register_tools(mcp, context)
    index.register_tools(mcp, context)
    mutation.register_tools(mcp, context)
    refactor.register_tools(mcp, context)
    testing.register_tools(mcp, context)
    introspection.register_tools(mcp, context)

    tool_count = len(mcp._tool_manager._tools)
    log.info("mcp_server_created", tool_count=tool_count)

    return mcp


def run_server(repo_root: Path, db_path: Path, tantivy_path: Path) -> None:
    """Create and run the MCP server."""
    from codeplane.config.models import LoggingConfig, LogOutputConfig
    from codeplane.core.logging import configure_logging
    from codeplane.mcp.context import AppContext

    # Configure logging to both stderr and a file for debugging
    # Console: INFO level, no tracebacks
    # File: DEBUG level with full tracebacks
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
