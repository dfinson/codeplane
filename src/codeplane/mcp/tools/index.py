"""Index MCP tools - search, map_repo handlers."""

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from codeplane.config.constants import MAP_DEPTH_MAX, MAP_LIMIT_MAX, SEARCH_MAX_LIMIT

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_search(
    count: int, mode: str, query: str, fallback: bool = False, file_count: int = 0
) -> str:
    """Generate summary for search."""
    from codeplane.core.formatting import truncate_query

    suffix = " (literal fallback)" if fallback else ""
    q = truncate_query(query, 20)
    if count == 0:
        return f'no {mode} results for "{q}"{suffix}'
    files_str = f" across {file_count} files" if file_count > 0 else ""
    return f'{count} {mode} results for "{q}"{files_str}{suffix}'


def _summarize_map(file_count: int, sections: list[str], truncated: bool) -> str:
    """Generate summary for map_repo."""
    parts = [f"{file_count} files"]
    if sections:
        parts.append(f"sections: {', '.join(sections)}")
    if truncated:
        parts.append("(truncated)")
    return ", ".join(parts)


def _serialize_tree(nodes: list[Any]) -> list[dict[str, Any]]:
    """Recursively serialize directory tree nodes."""
    result: list[dict[str, Any]] = []
    for node in nodes:
        item: dict[str, Any] = {
            "name": node.name,
            "path": node.path,
            "is_dir": node.is_dir,
        }
        if node.is_dir:
            item["file_count"] = node.file_count
            item["children"] = _serialize_tree(node.children)
        else:
            item["line_count"] = node.line_count
        result.append(item)
    return result


async def _get_file_path(app_ctx: "AppContext", file_id: int) -> str:
    """Look up file path from file_id."""
    from codeplane.index.models import File

    with app_ctx.coordinator.db.session() as session:
        file = session.get(File, file_id)
        return file.path if file else "unknown"


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register index tools with FastMCP server."""

    @mcp.tool
    async def search(
        ctx: Context,
        query: str = Field(..., description="Search query"),
        mode: Literal["lexical", "symbol", "references", "definitions"] = Field(
            "lexical", description="Search mode"
        ),
        filter_paths: list[str] | None = Field(None, description="Filter by paths"),
        filter_languages: list[str] | None = Field(None, description="Filter by languages"),
        filter_kinds: list[str] | None = Field(None, description="Filter by symbol kinds"),
        limit: int = Field(default=20, le=SEARCH_MAX_LIMIT, description="Maximum results"),
        context_lines: int = Field(
            default=1, ge=0, le=5, description="Lines of context before/after each match"
        ),
        cursor: str | None = Field(None, description="Pagination cursor"),
        include_snippets: bool = Field(True, description="Include code snippets"),
    ) -> dict[str, Any]:
        """Search code, symbols, or references."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from codeplane.index.ops import SearchMode

        # Map mode to SearchMode
        mode_map = {
            "lexical": SearchMode.TEXT,
            "symbol": SearchMode.SYMBOL,
            "definitions": SearchMode.SYMBOL,
            "references": SearchMode.TEXT,  # Handled specially below
        }

        if mode == "definitions":
            # Use get_def for definition search
            def_fact = await app_ctx.coordinator.get_def(query, context_id=None)
            if def_fact is None:
                return {
                    "results": [],
                    "pagination": {},
                    "query_time_ms": 0,
                    "summary": _summarize_search(0, "definitions", query),
                }
            return {
                "results": [
                    {
                        "path": await _get_file_path(app_ctx, def_fact.file_id),
                        "line": def_fact.start_line,
                        "column": def_fact.start_col,
                        "snippet": def_fact.display_name or def_fact.name,
                        "symbol": {
                            "name": def_fact.name,
                            "kind": def_fact.kind,
                            "qualified_name": def_fact.qualified_name,
                        },
                        "score": 1.0,
                        "match_type": "exact",
                    }
                ],
                "pagination": {},
                "query_time_ms": 0,
                "summary": _summarize_search(1, "definitions", query),
            }

        if mode == "references":
            # First find the definition, then get references
            def_fact = await app_ctx.coordinator.get_def(query, context_id=None)
            if def_fact is None:
                return {
                    "results": [],
                    "pagination": {},
                    "query_time_ms": 0,
                    "summary": _summarize_search(0, "references", query),
                }

            refs = await app_ctx.coordinator.get_references(def_fact, _context_id=0, limit=limit)
            ref_results: list[dict[str, Any]] = []
            ref_files: set[str] = set()
            for ref in refs:
                path = await _get_file_path(app_ctx, ref.file_id)
                ref_files.add(path)
                ref_results.append(
                    {
                        "path": path,
                        "line": ref.start_line,
                        "column": ref.start_col,
                        "snippet": ref.token_text,
                        "score": 1.0 if ref.certainty == "CERTAIN" else 0.5,
                        "match_type": "exact" if ref.certainty == "CERTAIN" else "fuzzy",
                    }
                )
            return {
                "results": ref_results,
                "pagination": {},
                "query_time_ms": 0,
                "summary": _summarize_search(
                    len(ref_results), "references", query, file_count=len(ref_files)
                ),
            }

        # Lexical or symbol search
        search_response = await app_ctx.coordinator.search(
            query,
            mode_map[mode],
            limit=limit,
            context_lines=context_lines,
            filter_languages=filter_languages,
        )

        # Count unique files
        unique_files = {r.path for r in search_response.results}

        result: dict[str, Any] = {
            "results": [
                {
                    "path": r.path,
                    "line": r.line,
                    "column": r.column,
                    "snippet": r.snippet,
                    "score": r.score,
                    "match_type": "fuzzy",
                }
                for r in search_response.results
            ],
            "pagination": {},
            "query_time_ms": 0,
            "summary": _summarize_search(
                len(search_response.results),
                mode,
                query,
                fallback=search_response.fallback_reason is not None,
                file_count=len(unique_files),
            ),
        }

        # Include fallback reason if query syntax was invalid
        if search_response.fallback_reason:
            result["fallback_reason"] = search_response.fallback_reason

        return result

    @mcp.tool
    async def map_repo(
        ctx: Context,
        include: (
            list[
                Literal[
                    "structure",
                    "languages",
                    "entry_points",
                    "dependencies",
                    "test_layout",
                    "public_api",
                ]
            ]
            | None
        ) = Field(None, description="Sections to include"),
        depth: int = Field(default=3, le=MAP_DEPTH_MAX, description="Tree depth"),
        cursor: str | None = Field(None, description="Pagination cursor"),
        limit: int = Field(default=100, le=MAP_LIMIT_MAX, description="Maximum entries"),
        include_globs: list[str] | None = Field(
            None, description="Glob patterns to include (e.g., ['src/**', 'lib/**'])"
        ),
        exclude_globs: list[str] | None = Field(
            None,
            description="Glob patterns to exclude (e.g., ['**/output/**', '**/mlruns/**'])",
        ),
        respect_gitignore: bool = Field(
            True, description="Honor .gitignore patterns (default: true)"
        ),
    ) -> dict[str, Any]:
        """Get repository mental model."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.coordinator.map_repo(
            include=include,
            depth=depth,
            limit=limit,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            respect_gitignore=respect_gitignore,
        )

        # Convert dataclasses to dicts
        output: dict[str, Any] = {}

        if result.structure:
            output["structure"] = {
                "root": result.structure.root,
                "tree": _serialize_tree(result.structure.tree),
                "file_count": result.structure.file_count,
                "contexts": result.structure.contexts,
            }

        if result.languages:
            output["languages"] = [
                {
                    "language": lang.language,
                    "file_count": lang.file_count,
                    "percentage": lang.percentage,
                }
                for lang in result.languages
            ]

        if result.entry_points:
            output["entry_points"] = [
                {
                    "path": ep.path,
                    "kind": ep.kind,
                    "name": ep.name,
                    "qualified_name": ep.qualified_name,
                }
                for ep in result.entry_points
            ]

        if result.dependencies:
            output["dependencies"] = {
                "external_modules": result.dependencies.external_modules,
                "import_count": result.dependencies.import_count,
            }

        if result.test_layout:
            output["test_layout"] = {
                "test_files": result.test_layout.test_files,
                "test_count": result.test_layout.test_count,
            }

        if result.public_api:
            output["public_api"] = [
                {
                    "name": sym.name,
                    "def_uid": sym.def_uid,
                    "certainty": sym.certainty,
                    "evidence": sym.evidence,
                }
                for sym in result.public_api
            ]

        # Add pagination info
        output["pagination"] = {
            "truncated": result.truncated,
        }
        if result.next_cursor:
            output["pagination"]["next_cursor"] = result.next_cursor
        if result.total_estimate:
            output["pagination"]["total_estimate"] = result.total_estimate

        # Build sections list for summary
        sections: list[str] = []
        if result.structure:
            sections.append("structure")
        if result.languages:
            sections.append("languages")
        if result.entry_points:
            sections.append("entry_points")
        if result.dependencies:
            sections.append("dependencies")
        if result.test_layout:
            sections.append("test_layout")
        if result.public_api:
            sections.append("public_api")

        file_count = result.structure.file_count if result.structure else 0
        output["summary"] = _summarize_map(file_count, sections, result.truncated)

        return output

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
