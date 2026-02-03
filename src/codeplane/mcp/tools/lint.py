"""Lint MCP tool - unified lint handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from codeplane.mcp.registry import registry as mcp_registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Model
# =============================================================================


class LintParams(BaseParams):
    """Parameters for lint tool."""

    action: Literal["check", "tools"]

    # check params
    paths: list[str] | None = None
    tools: list[str] | None = None
    categories: list[str] | None = None
    dry_run: bool = False

    # tools params (filter by language or category)
    language: str | None = None
    category: str | None = None


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_lint(status: str, total_diagnostics: int, files_modified: int, dry_run: bool) -> str:
    prefix = "(dry-run) " if dry_run else ""
    if status == "clean":
        return f"{prefix}clean, no issues"
    parts = [status]
    if total_diagnostics:
        parts.append(f"{total_diagnostics} diagnostics")
    if files_modified:
        parts.append(f"{files_modified} files fixed")
    return f"{prefix}{', '.join(parts)}"


# =============================================================================
# Tool Handler
# =============================================================================


@mcp_registry.register(
    "lint",
    "Lint operations: run checks/fixes or list available tools",
    LintParams,
)
async def lint(ctx: AppContext, params: LintParams) -> dict[str, Any]:
    """Unified lint tool.

    Actions:
    - check: Run linters, formatters, and type checkers (applies fixes by default)
    - tools: List available lint tools and their detection status
    """
    if params.action == "check":
        result = await ctx.lint_ops.check(
            paths=params.paths,
            tools=params.tools,
            categories=params.categories,
            dry_run=params.dry_run,
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

        if result.agentic_hint:
            output["agentic_hint"] = result.agentic_hint

        return output

    if params.action == "tools":
        import shutil

        from codeplane.lint import registry
        from codeplane.lint.models import ToolCategory

        all_tools = registry.all()

        # Try to get detected tools from index first, fall back to runtime detection
        detected_ids: set[str] = set()
        try:
            indexed_tools = await ctx.coordinator.get_lint_tools()
            if indexed_tools:
                detected_ids = {t.tool_id for t in indexed_tools}
            else:
                # Index empty, fall back to runtime detection
                detected_pairs = registry.detect(ctx.lint_ops._repo_root)
                detected_ids = {t.tool_id for t, _ in detected_pairs}
        except (RuntimeError, AttributeError):
            # Coordinator not initialized, fall back to runtime detection
            detected_pairs = registry.detect(ctx.lint_ops._repo_root)
            detected_ids = {t.tool_id for t, _ in detected_pairs}

        # Filter by language if specified
        if params.language:
            matching = [t for t in all_tools if params.language in t.languages]
            if not matching:
                return {
                    "tools": [],
                    "detected_count": 0,
                    "total_count": 0,
                    "summary": f"No tools available for language '{params.language}'",
                    "agentic_hint": f"Language '{params.language}' is not supported. "
                    f"Supported languages include: python, javascript, typescript, go, rust, ruby, php, java, kotlin",
                }
            all_tools = matching

        # Filter by category if specified
        if params.category:
            valid_categories = {e.value for e in ToolCategory}
            if params.category not in valid_categories:
                return {
                    "tools": [],
                    "detected_count": 0,
                    "total_count": 0,
                    "summary": f"Invalid category '{params.category}'",
                    "agentic_hint": f"Valid categories: {', '.join(sorted(valid_categories))}",
                }
            cat = ToolCategory(params.category)
            all_tools = [t for t in all_tools if t.category == cat]

        filtered_detected = [t for t in all_tools if t.tool_id in detected_ids]

        return {
            "tools": [
                {
                    "tool_id": t.tool_id,
                    "name": t.name,
                    "languages": sorted(t.languages),
                    "category": t.category.value,
                    "executable": t.executable,
                    "detected": t.tool_id in detected_ids,
                    "executable_available": shutil.which(t.executable) is not None,
                }
                for t in sorted(all_tools, key=lambda x: (x.category.value, x.tool_id))
            ],
            "detected_count": len(filtered_detected),
            "total_count": len(all_tools),
            "summary": f"{len(filtered_detected)} of {len(all_tools)} tools detected",
        }

    return {"error": f"unknown action: {params.action}", "summary": "error: unknown action"}
