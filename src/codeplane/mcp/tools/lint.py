"""Lint MCP tools - lint.check handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry as mcp_registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class LintCheckParams(BaseParams):
    """Parameters for lint.check."""

    paths: list[str] | None = None  # Paths to check (default: entire repo)
    tools: list[str] | None = None  # Specific tool IDs (default: auto-detect)
    categories: list[str] | None = None  # Filter by category
    dry_run: bool = False  # Preview changes without modifying files


class LintToolsParams(BaseParams):
    """Parameters for lint.tools."""

    language: str | None = None  # Filter by language
    category: str | None = None  # Filter by category


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_lint(status: str, total_diagnostics: int, files_modified: int, dry_run: bool) -> str:
    """Generate summary for lint.check."""
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
# Tool Handlers
# =============================================================================


@mcp_registry.register(
    "lint_check",
    "Run linters, formatters, and type checkers. Applies fixes by default.",
    LintCheckParams,
)
async def lint_check(ctx: AppContext, params: LintCheckParams) -> dict[str, Any]:
    """Run lint/format/type-check tools.

    By default, applies fixes. Use dry_run=True to preview changes.

    Available categories: type_check, lint, format, security

    Tools are auto-detected based on config files in your repository.
    Use lint.tools to see available tools.
    """
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

    # Include agentic hint if present (for edge cases like no tools detected)
    if result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint

    return output


@mcp_registry.register(
    "lint_tools",
    "List available lint tools and their detection status",
    LintToolsParams,
)
async def lint_tools(ctx: AppContext, params: LintToolsParams) -> dict[str, Any]:
    """List available lint tools.

    Shows which tools are detected (have config files) in the repository.
    """
    import shutil

    from codeplane.lint import registry
    from codeplane.lint.models import ToolCategory

    all_tools = registry.all()
    detected = registry.detect(ctx.lint_ops._repo_root)
    detected_ids = {t.tool_id for t in detected}

    # Filter by language if specified
    if params.language:
        all_tools = [t for t in all_tools if params.language in t.languages]

    # Filter by category if specified
    if params.category:
        try:
            cat = ToolCategory(params.category)
            all_tools = [t for t in all_tools if t.category == cat]
        except ValueError:
            pass

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
        "detected_count": len(detected),
        "total_count": len(all_tools),
        "summary": f"{len(detected)} of {len(all_tools)} tools detected",
    }
