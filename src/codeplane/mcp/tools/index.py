"""Index MCP tools - search, map_repo handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from codeplane.config.constants import MAP_DEPTH_MAX, MAP_LIMIT_MAX, SEARCH_MAX_LIMIT
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class SearchParams(BaseParams):
    """Parameters for search."""

    query: str
    mode: Literal["lexical", "symbol", "references", "definitions"] = "lexical"
    filter_paths: list[str] | None = None
    filter_languages: list[str] | None = None
    filter_kinds: list[str] | None = None
    limit: int = Field(default=20, le=SEARCH_MAX_LIMIT)
    cursor: str | None = None
    include_snippets: bool = True


class MapRepoParams(BaseParams):
    """Parameters for map_repo."""

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
    ) = None
    depth: int = Field(default=3, le=MAP_DEPTH_MAX)
    cursor: str | None = None
    limit: int = Field(default=100, le=MAP_LIMIT_MAX)
    # Filtering options
    include_globs: list[str] | None = Field(
        default=None, description="Glob patterns to include (e.g., ['src/**', 'lib/**'])"
    )
    exclude_globs: list[str] | None = Field(
        default=None,
        description="Glob patterns to exclude (e.g., ['**/output/**', '**/mlruns/**'])",
    )
    respect_gitignore: bool = Field(
        default=True, description="Honor .gitignore patterns (default: true)"
    )


class GetDefParams(BaseParams):
    """Parameters for index.search definitions mode."""

    symbol_name: str
    context_id: int | None = None


class GetReferencesParams(BaseParams):
    """Parameters for index.search references mode."""

    symbol: str
    limit: int = Field(default=100, le=500)


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_search(count: int, mode: str, query: str, fallback: bool = False) -> str:
    """Generate summary for search."""
    suffix = " (literal fallback)" if fallback else ""
    if count == 0:
        q = query[:30] + "..." if len(query) > 30 else query
        return f'no {mode} results for "{q}"{suffix}'
    q = query[:30] + "..." if len(query) > 30 else query
    return f'{count} {mode} results for "{q}"{suffix}'


def _summarize_map(file_count: int, sections: list[str], truncated: bool) -> str:
    """Generate summary for map_repo."""
    parts = [f"{file_count} files"]
    if sections:
        parts.append(f"sections: {', '.join(sections)}")
    if truncated:
        parts.append("(truncated)")
    return ", ".join(parts)


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("search", "Search code, symbols, or references", SearchParams)
async def search(ctx: AppContext, params: SearchParams) -> dict[str, Any]:
    """Unified search across lexical index, symbols, and references."""
    from codeplane.index.ops import SearchMode

    # Map mode to SearchMode
    mode_map = {
        "lexical": SearchMode.TEXT,
        "symbol": SearchMode.SYMBOL,
        "definitions": SearchMode.SYMBOL,
        "references": SearchMode.TEXT,  # Handled specially below
    }

    if params.mode == "definitions":
        # Use get_def for definition search
        def_fact = await ctx.coordinator.get_def(params.query, context_id=None)
        if def_fact is None:
            return {
                "results": [],
                "pagination": {},
                "query_time_ms": 0,
                "summary": _summarize_search(0, "definitions", params.query),
            }
        return {
            "results": [
                {
                    "path": await _get_file_path(ctx, def_fact.file_id),
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
            "summary": _summarize_search(1, "definitions", params.query),
        }

    if params.mode == "references":
        # First find the definition, then get references
        def_fact = await ctx.coordinator.get_def(params.query, context_id=None)
        if def_fact is None:
            return {
                "results": [],
                "pagination": {},
                "query_time_ms": 0,
                "summary": _summarize_search(0, "references", params.query),
            }

        refs = await ctx.coordinator.get_references(def_fact, _context_id=0, limit=params.limit)
        ref_results: list[dict[str, Any]] = []
        for ref in refs:
            path = await _get_file_path(ctx, ref.file_id)
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
            "summary": _summarize_search(len(ref_results), "references", params.query),
        }

    # Lexical or symbol search
    search_response = await ctx.coordinator.search(
        params.query,
        mode_map[params.mode],
        limit=params.limit,
    )

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
            params.mode,
            params.query,
            fallback=search_response.fallback_reason is not None,
        ),
    }

    # Include fallback reason if query syntax was invalid
    if search_response.fallback_reason:
        result["fallback_reason"] = search_response.fallback_reason

    return result


@registry.register("map_repo", "Get repository mental model", MapRepoParams)
async def map_repo(ctx: AppContext, params: MapRepoParams) -> dict[str, Any]:
    """Build repository mental model from indexed data."""
    result = await ctx.coordinator.map_repo(
        include=params.include,
        depth=params.depth,
        limit=params.limit,
        include_globs=params.include_globs,
        exclude_globs=params.exclude_globs,
        respect_gitignore=params.respect_gitignore,
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


async def _get_file_path(ctx: AppContext, file_id: int) -> str:
    """Look up file path from file_id."""
    from codeplane.index.models import File

    with ctx.coordinator.db.session() as session:
        file = session.get(File, file_id)
        return file.path if file else "unknown"
