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


# Periodic tool-preference reminders (ambient, not corrective).
# Weighted rotation: A appears twice, B once per cycle.
_REJOINDER_INTERVAL = 5
_REJOINDERS = (
    "REJOINDER: recon + recon_resolve replace grep/rg/find/cat/head/tail/sed/wc.",
    "REJOINDER: checkpoint replaces direct test runner and linter invocation.",
)
_REJOINDER_ROTATION = (0, 1, 0)

# How often to include the full suggested_workflow dict in pattern hints.
# First detection always includes it; subsequent detections include it every Nth.
_WORKFLOW_HINT_INTERVAL = 3


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

    def __init__(self, session_manager: Any = None) -> None:
        """Initialize with optional session manager for gate/pattern tracking."""
        super().__init__()
        self._session_manager = session_manager

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
        session = None
        if context.fastmcp_context:
            full_session_id = context.fastmcp_context.session_id or "unknown"
            session_id = full_session_id[:8]  # Truncate for display

            # Resolve client profile from session and set for envelope builders
            self._resolve_and_set_profile(context.fastmcp_context)

            # Get session for exclusive lock enforcement
            if self._session_manager:
                session = self._session_manager.get_or_create(full_session_id)

        # Extract key params for logging (avoid logging huge content)
        log_params = self._extract_log_params(tool_name, arguments)

        start_time = time.perf_counter()
        log.info("tool_start", tool=tool_name, session_id=session_id, **log_params)

        console = get_console()

        # --- Exclusive tool enforcement ---
        # All tools acquire the session's exclusive lock. For exclusive tools
        # (checkpoint, semantic_diff) this blocks any concurrent
        # tool call on the same session until the exclusive tool completes.
        # For regular tools, the lock is acquired and released quickly.

        short = self._strip_tool_prefix(tool_name)

        if session:
            async with session.exclusive(short):
                return await self._run_tool(
                    context,
                    call_next,
                    tool_name,
                    arguments,
                    session_id,
                    start_time,
                    console,
                )
        else:
            return await self._run_tool(
                context,
                call_next,
                tool_name,
                arguments,
                session_id,
                start_time,
                console,
            )

    async def _run_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequest],
        call_next: CallNext[mt.CallToolRequest, Any],
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        start_time: float,
        console: Any,
    ) -> Any:
        """Execute a tool call with structured error handling and UX."""

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

            # Tick gate expiry, record call, and evaluate bypass patterns
            bypass_match = self._post_call_bookkeeping(context, tool_name, arguments, result)

            # --- Hint injection (pattern hints + periodic rejoinders) ---
            # Both can coexist in the same response.  We accumulate changes
            # into result_dict and repack into a new ToolResult at the end.
            result_dict: dict[str, Any] | None = None
            needs_repack = False

            if bypass_match:
                result_dict = self._extract_result_dict(result)
                if result_dict is not None:
                    from codeplane.mcp.gate import build_pattern_hint

                    hint_fields = build_pattern_hint(bypass_match)
                    existing_hint = result_dict.get("agentic_hint")
                    if existing_hint:
                        hint_fields["agentic_hint"] = (
                            existing_hint + "\n\n" + hint_fields["agentic_hint"]
                        )

                    # Throttle suggested_workflow: include on first detection
                    # and every _WORKFLOW_HINT_INTERVAL-th detection after.
                    if context.fastmcp_context and self._session_manager:
                        session = self._session_manager.get_or_create(
                            context.fastmcp_context.session_id,
                        )
                        n = session.counters.get("pattern_detections", 0) + 1
                        session.counters["pattern_detections"] = n
                        if n > 1 and n % _WORKFLOW_HINT_INTERVAL != 1:
                            hint_fields.pop("suggested_workflow", None)

                    result_dict.update(hint_fields)
                    needs_repack = True

            rejoinder = self._maybe_get_rejoinder(context)
            if rejoinder:
                if result_dict is None:
                    result_dict = self._extract_result_dict(result)
                if result_dict is not None:
                    existing = result_dict.get("agentic_hint", "")
                    if existing:
                        result_dict["agentic_hint"] = existing + "\n\n" + rejoinder
                    else:
                        result_dict["agentic_hint"] = rejoinder
                    needs_repack = True

            if needs_repack and result_dict is not None:
                return ToolResult(structured_content=result_dict)

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
                        "Correct parameter schema is in tool_schema above. "
                        "Fix the parameters and retry."
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
                        "Internal error — verify your parameters are correct and retry. "
                        "If the error persists, it may be a server-side issue."
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
            if server is None or not hasattr(server, "local_provider"):
                return None
            from codeplane.mcp._compat import get_tools_sync

            tool_spec = get_tools_sync(server).get(tool_name)
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

    def _post_call_bookkeeping(
        self,
        context: MiddlewareContext[mt.CallToolRequest],
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> Any:
        """Gate tick + pattern recording after a successful tool call.

        Returns the detected PatternMatch if a warn-severity bypass
        pattern was found, or None.
        """
        if not context.fastmcp_context or not self._session_manager:
            return

        session = self._session_manager.get_or_create(context.fastmcp_context.session_id)

        # 1. Tick gate expiry (decrement pending gate counters)
        session.gate_manager.tick()

        # 2. Record call in pattern detector
        #    Strip MCP prefix (e.g. "codeplane-copy3_search" -> "search")
        short_name = self._strip_tool_prefix(tool_name)

        # Extract file paths from arguments or result for pattern tracking
        files: list[str] = []
        if "paths" in arguments:
            files = arguments["paths"] if isinstance(arguments["paths"], list) else []
        elif "targets" in arguments and isinstance(arguments["targets"], list):
            files = [t.get("path", "") for t in arguments["targets"] if isinstance(t, dict)]

        # Extract hit count from search results.
        # Result may be CallToolResult (content items) or a dict.
        result_dict = self._extract_result_dict(result)
        hit_count = 0
        if result_dict:
            results_list = result_dict.get("results", [])
            if isinstance(results_list, list):
                hit_count = len(results_list)
        # 3. Evaluate patterns BEFORE recording (recording may clear the window)
        #    Return match so caller can replace the MCP result with a hint-injected one.
        pattern_match = session.pattern_detector.evaluate(current_tool=short_name)

        # 4. Record call in pattern detector (may clear window for action categories).
        #    Checkpoint only clears when lint actually auto-fixed files (conditional mutation).
        lint_mutated = False
        if short_name == "checkpoint" and result_dict is not None:
            lint_section = result_dict.get("lint")
            if isinstance(lint_section, dict):
                lint_mutated = lint_section.get("total_files_modified", 0) > 0
        session.pattern_detector.record(
            tool_name=short_name,
            files=files,
            hit_count=hit_count,
            clears_window=lint_mutated,
        )

        # Reset consecutive recon counter when refactor_edit is called
        if short_name == "refactor_edit":
            session.counters["recon_consecutive"] = 0

        if pattern_match and pattern_match.severity == "warn":
            return pattern_match
        return None

    def _maybe_get_rejoinder(
        self,
        context: MiddlewareContext[mt.CallToolRequest],
    ) -> str | None:
        """Return a periodic tool-preference rejoinder, or ``None``.

        Fires every ``_REJOINDER_INTERVAL`` successful tool calls using
        a weighted rotation (A twice, B once per 3-slot cycle).
        """
        if not context.fastmcp_context or not self._session_manager:
            return None

        session = self._session_manager.get_or_create(
            context.fastmcp_context.session_id,
        )
        count = session.counters.get("rejoinder_calls", 0) + 1
        session.counters["rejoinder_calls"] = count

        if count % _REJOINDER_INTERVAL != 0:
            return None

        slot = (count // _REJOINDER_INTERVAL - 1) % len(_REJOINDER_ROTATION)
        return _REJOINDERS[_REJOINDER_ROTATION[slot]]

    @staticmethod
    def _strip_tool_prefix(tool_name: str) -> str:
        """Strip MCP server prefix from tool name.

        e.g. 'codeplane-copy3_search' -> 'search'
        The prefix is everything up to the last underscore-separated
        known tool name.
        """
        from codeplane.mcp.gate import TOOL_CATEGORIES

        # Try matching from the longest suffix
        for known in TOOL_CATEGORIES:
            if tool_name.endswith(known):
                return known
        return tool_name

    def _resolve_and_set_profile(self, fastmcp_ctx: Any) -> None:
        """Resolve client profile from session and set on context var.

        Extracts clientInfo from the MCP session's initialization params
        and resolves the appropriate delivery profile.
        """
        from codeplane.mcp.delivery import resolve_profile, set_current_profile

        try:
            session = fastmcp_ctx.session
            client_params = getattr(session, "client_params", None)
            if client_params is None:
                return

            client_info = getattr(client_params, "clientInfo", None)
            client_info_dict = None
            if client_info is not None:
                client_info_dict = {
                    "name": getattr(client_info, "name", ""),
                    "version": getattr(client_info, "version", ""),
                }

            capabilities = getattr(client_params, "capabilities", None)
            caps_dict = None
            if capabilities is not None:
                caps_dict = (
                    capabilities.model_dump(exclude_none=True)
                    if hasattr(capabilities, "model_dump")
                    else {}
                )

            profile = resolve_profile(client_info=client_info_dict, capabilities=caps_dict)
            set_current_profile(profile)
        except Exception:
            # Profile resolution is best-effort; don't break tool calls
            log.debug("profile_resolution_failed", exc_info=True)

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

    @staticmethod
    def _extract_result_dict(result: Any) -> dict[str, Any] | None:
        """Extract the JSON dict from an MCP result.

        Handles:
        - MCP CallToolResult with text content (JSON strings)
        - ToolResult with structured_content dict
        - Plain dict

        Returns None if extraction fails.
        """
        # MCP CallToolResult — parse JSON from first text content item
        if hasattr(result, "content") and result.content:
            for content_item in result.content:
                if hasattr(content_item, "text"):
                    try:
                        data = json.loads(content_item.text)
                        if isinstance(data, dict):
                            return data
                    except (json.JSONDecodeError, AttributeError):
                        pass
        # ToolResult with structured_content
        elif hasattr(result, "structured_content") and isinstance(result.structured_content, dict):
            return dict(result.structured_content)  # copy to avoid mutation
        # Plain dict
        elif isinstance(result, dict):
            return dict(result)
        return None

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
        if tool_name == "recon" and "files" in result:
            summary["files_returned"] = len(result.get("files", []))
        elif tool_name == "refactor_edit" and "edits" in result:
            edits = result.get("edits", [])
            summary["edits_applied"] = len(
                [e for e in edits if isinstance(e, dict) and e.get("status") == "ok"]
            )
        elif tool_name == "checkpoint" and "tests" in result:
            tests = result.get("tests", {})
            if isinstance(tests, dict):
                summary["passed"] = tests.get("passed", 0)
                summary["failed"] = tests.get("failed", 0)

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
        if tool_name == "recon":
            files = data.get("files", [])
            return f"{len(files)} files returned"

        if tool_name == "recon_resolve":
            files = data.get("files", [])
            return f"{len(files)} files resolved"

        if tool_name == "refactor_edit":
            edits = data.get("edits", [])
            ok = len([e for e in edits if isinstance(e, dict) and e.get("status") == "ok"])
            return f"{ok}/{len(edits)} edits applied"

        if tool_name == "checkpoint":
            if "summary" in data:
                return str(data["summary"])
            tests = data.get("tests", {})
            if isinstance(tests, dict):
                passed = tests.get("passed", 0)
                failed = tests.get("failed", 0)
                return f"{passed} passed, {failed} failed"
            return "checkpoint complete"

        if tool_name == "semantic_diff":
            changes = data.get("changes", [])
            return f"{len(changes)} structural changes"

        # Default: return empty string (no summary shown)
        return ""
