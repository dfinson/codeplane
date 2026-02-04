"""FastMCP server creation and wiring.

Includes improved logging:
- Two-phase tool logging: tool_start with params, tool_complete with summary (Issue #7)
- Categorized exception logging: console summary only, full traceback to file (Issue #9)
"""

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


def _extract_log_params(_tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Extract relevant parameters for logging based on tool type.

    Returns a dict of key params to include in tool_start log.
    Omits session_id and limits long values.

    Args:
        _tool_name: Reserved for future tool-specific extraction logic.
        kwargs: Tool keyword arguments to extract.
    """
    # Skip internal params
    skip_keys = {"session_id"}
    params: dict[str, Any] = {}

    for key, value in kwargs.items():
        if key in skip_keys:
            continue
        # Truncate long strings
        if isinstance(value, str) and len(value) > 50:
            params[key] = value[:50] + "..."
        # Truncate long lists
        elif isinstance(value, list) and len(value) > 3:
            params[key] = f"[{len(value)} items]"
        elif value is not None:
            params[key] = value

    return params


def _extract_result_summary(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Extract summary metrics from tool result for logging.

    Returns a dict with key metrics like counts, totals, etc.
    """
    summary: dict[str, Any] = {}

    # Common result patterns
    if "total" in result:
        summary["total"] = result["total"]
    if "count" in result:
        summary["count"] = result["count"]
    if "results" in result and isinstance(result["results"], list):
        summary["results"] = len(result["results"])
    if "files" in result and isinstance(result["files"], list):
        summary["files"] = len(result["files"])
    if "entries" in result and isinstance(result["entries"], list):
        summary["entries"] = len(result["entries"])
    if "query_time_ms" in result:
        summary["query_time_ms"] = result["query_time_ms"]
    if "passed" in result:
        summary["passed"] = result["passed"]
    if "failed" in result:
        summary["failed"] = result["failed"]

    # Tool-specific summaries
    if tool_name == "search" and "results" in result:
        summary["matches"] = len(result.get("results", []))
    elif tool_name == "write_files" and "delta" in result:
        delta = result["delta"]
        summary["files_changed"] = delta.get("files_changed", 0)
    elif tool_name in ("run_test_targets", "get_test_run_status") and "run_status" in result:
        run_status = result.get("run_status", {})
        if isinstance(run_status, dict):
            progress = run_status.get("progress", {})
            if isinstance(progress, dict):
                summary["passed"] = progress.get("passed", 0)
                summary["failed"] = progress.get("failed", 0)

    return summary


def _format_tool_summary(tool_name: str, result: dict[str, Any]) -> str:
    """Format a human-readable summary for console output.

    Returns a brief summary string suitable for display after tool completion.
    Uses the MCP result's summary field as the primary source when available.
    """
    # Use explicit summary field if provided (MCP standard)
    if "summary" in result and result["summary"]:
        return str(result["summary"])

    # Use display_to_user field (CodePlane convention)
    if "display_to_user" in result and result["display_to_user"]:
        return str(result["display_to_user"])

    # Tool-specific formatting based on result structure
    if tool_name == "search":
        results = result.get("results", [])
        return f"{len(results)} results"

    if tool_name == "write_files":
        delta = result.get("delta", {})
        files_changed = delta.get("files_changed", 0)
        return f"{files_changed} files updated"

    if tool_name == "read_files":
        files = result.get("files", [])
        return f"{len(files)} files read"

    if tool_name == "list_files":
        entries = result.get("entries", [])
        return f"{len(entries)} entries"

    if tool_name in ("git_status", "git_diff", "git_commit", "git_branch"):
        # Git tools often return text-based summaries
        if "summary" in result:
            return str(result["summary"])
        return f"{tool_name} complete"

    if tool_name in ("run_test_targets", "get_test_run_status"):
        run_status = result.get("run_status", {})
        if isinstance(run_status, dict):
            progress = run_status.get("progress", {})
            if isinstance(progress, dict):
                passed = progress.get("passed", 0)
                failed = progress.get("failed", 0)
                return f"{passed} passed, {failed} failed"

    if tool_name == "map_repo":
        entry_points = result.get("entry_points", [])
        languages = result.get("languages", [])
        return f"{len(languages)} languages, {len(entry_points)} entry points"

    # Default: return empty string (no summary shown)
    return ""


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
        from codeplane.core.progress import get_console, is_console_suppressed, status

        tool_name = spec.name  # Capture for logging
        start_time = time.perf_counter()
        console = get_console()

        # Log tool start with relevant params (Issue #7)
        log_params = _extract_log_params(tool_name, kwargs)
        log.info("tool_start", tool=tool_name, **log_params)

        # Reconstruct the params model from kwargs
        try:
            params = params_model(**kwargs)
        except ValidationError as e:
            # User input error - no traceback needed (Issue #9)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            log.warning(
                "tool_validation_error",
                tool=tool_name,
                error=e.errors()[0]["msg"] if e.errors() else str(e),
                elapsed_ms=elapsed_ms,
            )
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

        # Show spinner during tool execution (only if console not suppressed)
        show_ui = not is_console_suppressed()

        try:
            if show_ui:
                with console.status(f"[cyan]{tool_name}[/cyan]", spinner="dots"):
                    result_data: dict[str, Any] = await spec_handler(context, params)
            else:
                result_data = await spec_handler(context, params)

            # Log tool completion with summary (Issue #7)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            summary = _extract_result_summary(tool_name, result_data)
            log.info("tool_complete", tool=tool_name, elapsed_ms=elapsed_ms, **summary)

            # Print summary to console (Issue #UX)
            # Agent tool calls: no checkmark, prefixed with session
            if show_ui:
                summary_text = _format_tool_summary(tool_name, result_data)
                if summary_text:
                    short_session = session.session_id[:12]
                    status(
                        f"[dim]{short_session}[/dim] {tool_name} -> {summary_text}",
                        style="none",
                    )

            return ToolResponse(
                success=True,
                result=result_data,
                meta={
                    "session_id": session.session_id,
                    "timestamp": int(time.time() * 1000),
                },
            ).model_dump()

        except MCPError as e:
            # Expected error - log warning, no traceback (Issue #9)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            log.warning(
                "tool_error",
                tool=tool_name,
                error_code=e.code.value,
                error=e.message,
                path=e.path,
                elapsed_ms=elapsed_ms,
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
            # Internal error - log error to console, full traceback to file only (Issue #9)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            # Console-friendly summary (no traceback)
            log.error(
                "tool_internal_error",
                tool=tool_name,
                error=str(e),
                elapsed_ms=elapsed_ms,
            )
            # Full traceback at DEBUG level (goes to file only per logging config)
            log.debug("tool_internal_error_traceback", tool=tool_name, exc_info=True)
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
