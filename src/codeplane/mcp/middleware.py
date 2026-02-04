"""MCP middleware for tool call handling.

Provides:
- Structured error handling (catches exceptions, returns structured responses)
- Console UX (spinner during execution, summary output after)
- Logging with timing and result summaries
- No tracebacks printed to console
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import ValidationError

from codeplane.mcp.errors import MCPError

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext
    from mcp import types as mt

log = structlog.get_logger(__name__)


class ToolMiddleware(Middleware):
    """Middleware that handles tool calls with structured errors and UX.

    Replicates the functionality of the old _wire_tool wrapper:
    - Catches exceptions and returns structured error responses
    - Shows spinner during execution
    - Prints summary after completion
    - Two-phase logging (tool_start + tool_completed)
    - No tracebacks printed to console
    """

    async def on_call_tool(  # type: ignore[override]
        self,
        context: MiddlewareContext[mt.CallToolRequest],
        call_next: CallNext[mt.CallToolRequest, Any],
    ) -> Any:
        """Handle tool calls with structured error handling and UX."""
        from codeplane.core.progress import get_console

        # context.message is CallToolRequestParams with name and arguments directly
        params = context.message
        tool_name = getattr(params, "name", "unknown")
        arguments = getattr(params, "arguments", {}) or {}

        # Get MCP session ID from the FastMCP context (agent's session)
        session_id = "unknown"
        if context.fastmcp_context:
            full_session_id = context.fastmcp_context.session_id or "unknown"
            session_id = full_session_id[:8]  # Truncate for display

        # Extract key params for logging (avoid logging huge content)
        log_params = self._extract_log_params(tool_name, arguments)

        start_time = time.perf_counter()
        log.info("tool_start", tool=tool_name, session_id=session_id, **log_params)

        console = get_console()

        try:
            result = await call_next(context)

            duration_ms = (time.perf_counter() - start_time) * 1000

            # Extract summary from result for logging
            summary_dict = self._extract_result_summary(tool_name, result)
            log.info(
                "tool_completed",
                tool=tool_name,
                session_id=session_id,
                duration_ms=round(duration_ms, 1),
                **summary_dict,
            )

            # Print session log to console: "Agent Session <id>: tool -> summary"
            summary_text = self._format_tool_summary(tool_name, result)
            if summary_text:
                console.print(
                    f"Agent Session {session_id}: {tool_name} -> {summary_text}",
                    style="green",
                    highlight=False,
                )

            return result

        except asyncio.CancelledError:
            # Server shutdown during tool execution - return graceful error
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(
                "tool_cancelled",
                tool=tool_name,
                session_id=session_id,
                duration_ms=round(duration_ms, 1),
            )
            raise ToolError(f"Tool '{tool_name}' cancelled: server shutting down") from None

        except ValidationError as e:
            # User input error - no traceback needed
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_msg = e.errors()[0]["msg"] if e.errors() else str(e)
            log.warning(
                "tool_validation_error",
                tool=tool_name,
                error=error_msg,
                duration_ms=round(duration_ms, 1),
            )
            raise ToolError(f"Validation error: {error_msg}") from e

        except MCPError as e:
            # Expected error - return structured response, not exception
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.warning(
                "tool_error",
                tool=tool_name,
                error_code=e.code.value,
                error=e.message,
                path=e.path,
                duration_ms=round(duration_ms, 1),
            )
            # Return structured error response instead of raising
            error_response = e.to_response()
            return {
                "error": error_response.to_dict(),
                "summary": f"error: {e.code.value}",
            }

        except Exception as e:
            # Internal error - log error, no traceback to console
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.error(
                "tool_internal_error",
                tool=tool_name,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 1),
            )
            # Full traceback at DEBUG level (goes to file only per logging config)
            log.debug("tool_internal_error_traceback", tool=tool_name, exc_info=True)
            raise ToolError(f"Error calling tool '{tool_name}': {e}") from e

    def _extract_log_params(self, _tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Extract relevant parameters for logging.

        Returns a dict of key params to include in tool_start log.
        Omits internal params and limits long values.
        """
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

    def _extract_result_summary(self, tool_name: str, result: Any) -> dict[str, Any]:
        """Extract summary metrics from tool result for logging.

        Returns a dict with key metrics like counts, totals, etc.
        """
        summary: dict[str, Any] = {}

        # Handle MCP CallToolResult which wraps content
        if hasattr(result, "content") and result.content:
            # Try to parse the first text content as JSON
            try:
                import json

                for content_item in result.content:
                    if hasattr(content_item, "text"):
                        data = json.loads(content_item.text)
                        return self._extract_from_dict(tool_name, data)
            except (json.JSONDecodeError, AttributeError):
                pass

        # Direct dict result
        if isinstance(result, dict):
            return self._extract_from_dict(tool_name, result)

        return summary

    def _extract_from_dict(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        """Extract summary from a dict result."""
        summary: dict[str, Any] = {}

        # Use explicit summary field if provided
        if "summary" in result and result["summary"]:
            summary["summary"] = str(result["summary"])[:100]
            return summary

        # Use display_to_user field
        if "display_to_user" in result and result["display_to_user"]:
            summary["summary"] = str(result["display_to_user"])[:100]
            return summary

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
                    cases = progress.get("cases", {})
                    if isinstance(cases, dict):
                        summary["passed"] = cases.get("passed", 0)
                        summary["failed"] = cases.get("failed", 0)

        return summary

    def _format_tool_summary(self, tool_name: str, result: Any) -> str:
        """Format a human-readable summary for console output.

        Returns a brief summary string suitable for display after tool completion.
        Uses the MCP result's summary field as the primary source when available.
        """
        # Try to extract data from CallToolResult
        data: dict[str, Any] = {}
        if hasattr(result, "content") and result.content:
            try:
                for content_item in result.content:
                    if hasattr(content_item, "text"):
                        data = json.loads(content_item.text)
                        break
            except (json.JSONDecodeError, AttributeError):
                pass
        elif isinstance(result, dict):
            data = result

        # Use explicit summary field if provided (MCP standard)
        if "summary" in data and data["summary"]:
            return str(data["summary"])

        # Use display_to_user field (CodePlane convention)
        if "display_to_user" in data and data["display_to_user"]:
            return str(data["display_to_user"])

        # Tool-specific formatting based on result structure
        if tool_name == "search":
            results = data.get("results", [])
            return f"{len(results)} results"

        if tool_name == "write_files":
            delta = data.get("delta", {})
            files_changed = delta.get("files_changed", 0)
            return f"{files_changed} files updated"

        if tool_name == "read_files":
            files = data.get("files", [])
            return f"{len(files)} files read"

        if tool_name == "list_files":
            entries = data.get("entries", [])
            return f"{len(entries)} entries"

        if tool_name in ("git_status", "git_diff", "git_commit", "git_branch"):
            # Git tools often return text-based summaries
            if "summary" in data:
                return str(data["summary"])
            return f"{tool_name} complete"

        if tool_name in ("run_test_targets", "get_test_run_status"):
            run_status = data.get("run_status", {})
            if isinstance(run_status, dict):
                progress = run_status.get("progress", {})
                if isinstance(progress, dict):
                    passed = progress.get("passed", 0)
                    failed = progress.get("failed", 0)
                    return f"{passed} passed, {failed} failed"

        if tool_name == "map_repo":
            entry_points = data.get("entry_points", [])
            languages = data.get("languages", [])
            return f"{len(languages)} languages, {len(entry_points)} entry points"

        # Default: return empty string (no summary shown)
        return ""
