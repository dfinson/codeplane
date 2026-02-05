"""Lint MCP tools - linting, formatting, and type checking.

Split into action-based tools:
- lint_check: Run linters/formatters/type checkers
- lint_tools: List available lint tools
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_lint(status: str, total_diagnostics: int, files_modified: int, dry_run: bool) -> str:
    """Generate summary for lint_check."""
    prefix = "(dry-run) " if dry_run else ""
    if status == "clean":
        return f"{prefix}✓ clean"
    if files_modified > 0 and total_diagnostics > 0:
        return f"{prefix}⚠ {files_modified} fixed, {total_diagnostics} remain"
    if files_modified > 0:
        return f"{prefix}✓ {files_modified} fixed"
    if total_diagnostics > 0:
        return f"{prefix}✗ {total_diagnostics} issues"
    return f"{prefix}{status}"


def _display_lint_check(
    status: str, total_diagnostics: int, files_modified: int, dry_run: bool
) -> str | None:
    """Human-friendly message for lint check."""
    if status == "clean":
        return "All checks passed - no issues found."
    prefix = "(dry-run) " if dry_run else ""
    if files_modified > 0:
        return (
            f"{prefix}{files_modified} files auto-fixed, {total_diagnostics} remaining diagnostics."
        )
    if total_diagnostics > 0:
        return f"{prefix}{total_diagnostics} issues found."
    return None


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register lint tools with FastMCP server."""

    @mcp.tool
    async def lint_check(
        ctx: Context,
        paths: list[str] | None = Field(None, description="Paths to lint (default: entire repo)"),
        tools: list[str] | None = Field(None, description="Specific tool IDs to run"),
        categories: list[str] | None = Field(
            None, description="Categories: linter, formatter, typechecker"
        ),
        dry_run: bool = Field(False, description="Report issues without applying fixes"),
    ) -> dict[str, Any]:
        """Run linters, formatters, and type checkers. Applies auto-fixes by default."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.lint_ops.check(
            paths=paths,
            tools=tools,
            categories=categories,
            dry_run=dry_run,
        )

        output: dict[str, Any] = {
            "action": result.action,
            "dry_run": result.dry_run,
            "status": result.status,
            "total_diagnostics": result.total_diagnostics,
            "total_files_modified": result.total_files_modified,
            "duration_seconds": round(result.duration_seconds, 2),
            "tools_run": [
                {
                    "tool_id": t.tool_id,
                    "status": t.status,
                    "files_checked": t.files_checked,
                    "files_modified": t.files_modified,
                    "duration_seconds": round(t.duration_seconds, 2),
                    "diagnostics": [
                        {
                            "path": d.path,
                            "line": d.line,
                            "column": d.column,
                            "end_line": d.end_line,
                            "end_column": d.end_column,
                            "severity": d.severity.value,
                            "code": d.code,
                            "message": d.message,
                            "source": d.source,
                            "fix_applied": d.fix_applied,
                        }
                        for d in t.diagnostics
                    ],
                    "error_detail": t.error_detail,
                }
                for t in result.tools_run
            ],
            "summary": _summarize_lint(
                result.status, result.total_diagnostics, result.total_files_modified, result.dry_run
            ),
        }

        display = _display_lint_check(
            result.status, result.total_diagnostics, result.total_files_modified, result.dry_run
        )
        if display:
            output["display_to_user"] = display

        if result.agentic_hint:
            output["agentic_hint"] = result.agentic_hint

        return output

    @mcp.tool
    async def lint_tools(
        ctx: Context,
        language: str | None = Field(
            None, description="Filter by language (e.g., python, javascript)"
        ),
        category: str | None = Field(
            None, description="Filter by category: linter, formatter, typechecker"
        ),
    ) -> dict[str, Any]:
        """List available lint tools and their detection status in this repo."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from codeplane.lint import registry
        from codeplane.lint.models import ToolCategory

        all_tools = registry.all()

        # Try to get detected tools from index first, fall back to runtime detection
        detected_ids: set[str] = set()
        detected_configs: dict[str, str | None] = {}  # tool_id -> config_file
        try:
            indexed_tools = await app_ctx.coordinator.get_lint_tools()
            if indexed_tools:
                detected_ids = {t.tool_id for t in indexed_tools}
                detected_configs = {t.tool_id: t.config_file for t in indexed_tools}
            else:
                # Index empty, fall back to runtime detection
                detected_pairs = registry.detect(app_ctx.lint_ops._repo_root)
                detected_ids = {t.tool_id for t, cfg in detected_pairs}
                detected_configs = {
                    t.tool_id: str(cfg) if cfg else None for t, cfg in detected_pairs
                }
        except (RuntimeError, AttributeError):
            # Coordinator not initialized, fall back to runtime detection
            detected_pairs = registry.detect(app_ctx.lint_ops._repo_root)
            detected_ids = {t.tool_id for t, cfg in detected_pairs}
            detected_configs = {t.tool_id: str(cfg) if cfg else None for t, cfg in detected_pairs}

        # Filter by language if specified
        if language:
            matching = [t for t in all_tools if language in t.languages]
            if not matching:
                return {
                    "tools": [],
                    "detected_count": 0,
                    "total_count": 0,
                    "summary": f"No tools available for language '{language}'",
                    "agentic_hint": f"Language '{language}' is not supported. "
                    f"Supported languages include: python, javascript, typescript, go, rust, ruby, php, java, kotlin",
                }
            all_tools = matching

        # Filter by category if specified
        if category:
            valid_categories = {e.value for e in ToolCategory}
            if category not in valid_categories:
                return {
                    "tools": [],
                    "detected_count": 0,
                    "total_count": 0,
                    "summary": f"Invalid category '{category}'",
                    "agentic_hint": f"Valid categories: {', '.join(sorted(valid_categories))}",
                }
            cat = ToolCategory(category)
            all_tools = [t for t in all_tools if t.category == cat]

        filtered_detected = [t for t in all_tools if t.tool_id in detected_ids]

        # Build summary with detected tool names and their config contexts
        if filtered_detected:
            tool_names = [t.name for t in filtered_detected[:3]]
            names_str = ", ".join(tool_names)
            if len(filtered_detected) > 3:
                names_str += f", +{len(filtered_detected) - 3} more"
            # Get unique config files for context
            configs: set[str] = {
                c for t in filtered_detected if (c := detected_configs.get(t.tool_id)) is not None
            }
            if configs:
                ctx_str = ", ".join(sorted(configs)[:2])
                if len(configs) > 2:
                    ctx_str += f", +{len(configs) - 2}"
                summary = f"{len(filtered_detected)} detected: {names_str} (via {ctx_str})"
            else:
                summary = f"{len(filtered_detected)} detected: {names_str}"
        else:
            summary = f"0 of {len(all_tools)} tools detected"

        return {
            "tools": [
                {
                    "tool_id": t.tool_id,
                    "name": t.name,
                    "languages": sorted(t.languages),
                    "category": t.category.value,
                    "executable": t.executable,
                    "detected": t.tool_id in detected_ids,
                    "config_file": detected_configs.get(t.tool_id),
                    "executable_available": shutil.which(t.executable) is not None,
                }
                for t in sorted(all_tools, key=lambda x: (x.category.value, x.tool_id))
            ],
            "detected_count": len(filtered_detected),
            "total_count": len(all_tools),
            "summary": summary,
        }
