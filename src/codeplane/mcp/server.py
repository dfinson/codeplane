"""FastMCP server creation and wiring.

Uses native FastMCP @mcp.tool decorators for tool registration.
Includes logging middleware for tool call instrumentation.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

# Suppress Rich tracebacks BEFORE importing fastmcp
# FastMCP configures RichHandler at import time with rich_tracebacks=True
from rich.logging import RichHandler as _RichHandler

_original_rich_emit = _RichHandler.emit


def _patched_rich_emit(self: _RichHandler, record: Any) -> None:
    """Patched RichHandler.emit that suppresses traceback rendering."""
    # If this handler has rich_tracebacks enabled and there's exc_info,
    # clear the exc_info so no traceback is rendered
    if getattr(self, "rich_tracebacks", False) and record.exc_info:
        record.exc_info = None
        record.exc_text = None
    _original_rich_emit(self, record)


_RichHandler.emit = _patched_rich_emit  # type: ignore[method-assign]

import structlog  # noqa: E402  # Must import after patching RichHandler

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _noop_docket_lifespan(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
    """No-op lifespan that replaces FastMCP's Docket task queue.

    FastMCP's Docket uses an in-memory backend that polls continuously,
    burning ~15% CPU at idle. Since we don't use Docket's task scheduling,
    we replace the lifespan with a no-op to eliminate this CPU drain.
    """
    yield


def _patch_fastmcp_docket() -> None:
    """Disable FastMCP's Docket task queue to eliminate idle CPU usage.

    The Docket in-memory backend polls at ~5Hz even with no tasks,
    causing unnecessary CPU usage. Since CodePlane doesn't use Docket,
    we monkey-patch the lifespan to be a no-op.
    """
    from fastmcp import FastMCP

    # Only patch once
    if not hasattr(FastMCP, "_docket_patched"):
        FastMCP._docket_lifespan = staticmethod(_noop_docket_lifespan)  # type: ignore[method-assign]
        FastMCP._docket_patched = True  # type: ignore[attr-defined]
        log.debug("fastmcp_docket_disabled")


def create_mcp_server(context: "AppContext") -> "FastMCP":
    """Create FastMCP server with all tools wired to context.

    Args:
        context: AppContext with all ops instances

    Returns:
        Configured FastMCP server ready to run
    """
    import fastmcp
    from fastmcp import FastMCP

    from codeplane.mcp.middleware import ToolMiddleware
    from codeplane.mcp.tools import (
        diff,
        files,
        git,
        index,
        introspection,
        lint,
        mutation,
        refactor,
        testing,
    )

    log.info("mcp_server_creating", repo_root=str(context.repo_root))

    # Disable Docket task queue to eliminate ~15% idle CPU usage
    _patch_fastmcp_docket()

    # Configure FastMCP global settings
    fastmcp.settings.json_response = True
    # Disable FastMCP's rich tracebacks - we handle errors in middleware
    fastmcp.settings.enable_rich_tracebacks = False

    mcp = FastMCP(
        "codeplane",
        instructions="CodePlane repository control plane for AI coding agents.",
    )

    # Add middleware for structured error handling and UX
    mcp.add_middleware(ToolMiddleware(session_manager=context.session_manager))

    # Register all tools using native FastMCP decorators
    diff.register_tools(mcp, context)
    files.register_tools(mcp, context)
    git.register_tools(mcp, context)
    index.register_tools(mcp, context)
    lint.register_tools(mcp, context)
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

    # Generate session ID and log file path
    # Format: .codeplane/logs/YYYY-MM-DD/HHMMSS-<6-digit-hash>.log
    now = datetime.now()
    session_hash = uuid4().hex[:6]
    log_dir = repo_root / ".codeplane" / "logs" / now.strftime("%Y-%m-%d")
    log_file = log_dir / f"{now.strftime('%H%M%S')}-{session_hash}.log"
    session_id = f"{now.strftime('%H%M%S')}-{session_hash}"

    # Configure logging to both stderr and a file for debugging
    # Console: INFO level, no tracebacks
    # File: DEBUG level with full tracebacks
    configure_logging(
        config=LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(destination="stderr", format="console", level="INFO"),
                LogOutputConfig(destination=str(log_file), format="json", level="DEBUG"),
            ],
        ),
    )

    log.info(
        "mcp_server_starting",
        repo_root=str(repo_root),
        db_path=str(db_path),
        tantivy_path=str(tantivy_path),
        log_file=str(log_file),
        session_id=session_id,
    )

    context = AppContext.create(repo_root, db_path, tantivy_path)
    mcp = create_mcp_server(context)

    log.info("mcp_server_running")
    mcp.run()
