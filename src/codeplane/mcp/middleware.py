"""MCP middleware for tool call handling.

Provides:
- Structured error handling (catches exceptions, returns structured responses)
- Console UX (spinner during execution, summary output after)
- Logging with timing and result summaries
- No tracebacks printed to console (exception log pointers instead)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from codeplane.mcp.errors import MCPError

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext
    from mcp import types as mt

log = structlog.get_logger(__name__)

# Source tag for agent output - escaped for Rich markup
_AGENT_TAG = "\\[agent] "


def _timestamp() -> str:
    """Return current time as HH:MM:SS for log prefix."""
    return time.strftime("%H:%M:%S")


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

            # Print session log to console with timestamp
            summary_text = self._format_tool_summary(tool_name, result)
            if summary_text:
                ts = f"[dim]\\[{_timestamp()}][/dim] "
                console.print(
                    f"{ts}{_AGENT_TAG}Session {session_id}: {tool_name} -> {summary_text}",
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
            return ToolResult(
                structured_content={
                    "error": {
                        "code": "CANCELLED",
                        "message": f"Tool '{tool_name}' cancelled: server shutting down",
                    },
                    "summary": "error: cancelled",
                }
            )

        except ValidationError as e:
            # User input error - return structured response with schema help
            duration_ms = (time.perf_counter() - start_time) * 1000
            errors = e.errors()
            error_details = [
                {
                    "field": ".".join(str(p) for p in err.get("loc", [])),
                    "message": err.get("msg", ""),
                    "type": err.get("type", ""),
                }
                for err in errors
            ]
            log.warning(
                "tool_validation_error",
                tool=tool_name,
                errors=error_details,
                duration_ms=round(duration_ms, 1),
            )

            # Build schema info from FastMCP tool manager
            tool_schema = self._get_tool_schema(context, tool_name)

            return ToolResult(
                structured_content={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": f"Invalid parameters for '{tool_name}'",
                        "details": error_details,
                    },
                    "tool_schema": tool_schema,
                    "agentic_hint": (
                        f"Use 'describe' with action='tool' and name='{tool_name}' for full documentation. "
                        "For other tools you're unsure about, call describe to see correct usage."
                    ),
                    "summary": f"error: validation failed for {tool_name}",
                }
            )

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
            return ToolResult(
                structured_content={
                    "error": error_response.to_dict(),
                    "summary": f"error: {e.code.value}",
                }
            )

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

            # Print concise error with log file pointer (no stacktrace)
            self._print_error_with_log_pointer(console, tool_name, type(e).__name__, str(e))

            return ToolResult(
                structured_content={
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": f"Error calling tool '{tool_name}': {e}",
                        "error_type": type(e).__name__,
                    },
                    "agentic_hint": (
                        "This is an internal error. Check the log file for details. "
                        f"Use 'describe' with action='tool' and name='{tool_name}' to verify correct usage."
                    ),
                    "summary": f"error: internal error in {tool_name}",
                }
            )

    def _print_error_with_log_pointer(
        self,
        console: Any,
        tool_name: str,
        error_type: str,
        error_msg: str,
    ) -> None:
        """Print a concise error message with a pointer to the log file.

        Format: [agent] <tool> failed: <error_type> - <brief msg>. See <log_file> for details.
        """
        from codeplane.core.logging import get_log_file_path

        # Truncate error message to keep console output compact
        brief_msg = error_msg[:60] + "..." if len(error_msg) > 60 else error_msg

        log_file = get_log_file_path()
        ts = f"[dim]\\[{_timestamp()}][/dim] "
        if log_file:
            console.print(
                f"{ts}{_AGENT_TAG}{tool_name} failed: {error_type} - {brief_msg}. "
                f"See {log_file} for details.",
                style="red",
                highlight=False,
            )
        else:
            # No log file configured, just print the error
            console.print(
                f"{ts}{_AGENT_TAG}{tool_name} failed: {error_type} - {brief_msg}",
                style="red",
                highlight=False,
            )

    def _get_tool_schema(
        self,
        context: MiddlewareContext[Any],
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Extract schema info for a tool from FastMCP tool manager.

        Returns a dict with name, description, and parameters schema,
        or None if tool not found.
        """
        try:
            # Access FastMCP server through the middleware context
            # Navigate: fastmcp_context -> _mcp_context -> session -> app (FastMCP server)
            if not context.fastmcp_context:
                return None
            mcp_ctx = getattr(context.fastmcp_context, "_mcp_context", None)
            if mcp_ctx is None:
                return None
            server = getattr(getattr(mcp_ctx, "session", None), "app", None)
            if server is None or not hasattr(server, "_tool_manager"):
                return None
            tool_spec = server._tool_manager._tools.get(tool_name)
            if not tool_spec:
                return None

            # Build a concise schema representation
            schema: dict[str, Any] = {
                "name": tool_name,
                "description": tool_spec.description or "",
            }

            # Extract parameters from the JSON schema
            if tool_spec.parameters:
                params = tool_spec.parameters
                properties = params.get("properties", {})
                required = set(params.get("required", []))

                param_list: list[dict[str, Any]] = []
                for pname, pschema in properties.items():
                    if pname == "ctx":  # Skip FastMCP context param
                        continue
                    param_info: dict[str, Any] = {
                        "name": pname,
                        "type": pschema.get("type", pschema.get("anyOf", "unknown")),
                        "required": pname in required,
                    }
                    if "description" in pschema:
                        param_info["description"] = pschema["description"]
                    if "default" in pschema:
                        param_info["default"] = pschema["default"]
                    if "enum" in pschema:
                        param_info["allowed_values"] = pschema["enum"]
                    if "minimum" in pschema:
                        param_info["minimum"] = pschema["minimum"]
                    if "maximum" in pschema:
                        param_info["maximum"] = pschema["maximum"]
                    param_list.append(param_info)

                schema["parameters"] = param_list

            return schema
        except Exception:
            # Don't let schema extraction failure break error handling
            return None

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

        # Handle ToolResult with structured_content
        if hasattr(result, "structured_content") and result.structured_content:
            return self._extract_from_dict(tool_name, result.structured_content)

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
        elif hasattr(result, "structured_content") and result.structured_content:
            data = result.structured_content
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

        if tool_name in ("read_source", "read_file_full"):
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
