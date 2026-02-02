"""Index MCP tools - search, map_repo handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

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
    scope_paths: list[str] | None = None
    scope_languages: list[str] | None = None
    scope_kinds: list[str] | None = None
    limit: int = Field(default=20, le=100)
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
    depth: int = Field(default=3, le=10)


class GetDefParams(BaseParams):
    """Parameters for get_def (search definitions mode)."""

    name: str
    context_id: int | None = None


class GetReferencesParams(BaseParams):
    """Parameters for get_references."""

    symbol: str
    limit: int = Field(default=100, le=500)


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
            return {"results": [], "query_time_ms": 0}
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
            "query_time_ms": 0,
        }

    if params.mode == "references":
        # First find the definition, then get references
        def_fact = await ctx.coordinator.get_def(params.query, context_id=None)
        if def_fact is None:
            return {"results": [], "query_time_ms": 0}

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
        return {"results": ref_results, "query_time_ms": 0}

    # Lexical or symbol search
    search_results = await ctx.coordinator.search(
        params.query,
        mode_map[params.mode],
        limit=params.limit,
    )

    return {
        "results": [
            {
                "path": r.path,
                "line": r.line,
                "column": r.column,
                "snippet": r.snippet,
                "score": r.score,
                "match_type": "fuzzy",
            }
            for r in search_results
        ],
        "query_time_ms": 0,
    }


@registry.register("map_repo", "Get repository mental model", MapRepoParams)
async def map_repo(ctx: AppContext, params: MapRepoParams) -> dict[str, Any]:
    """Build repository mental model from indexed data."""
    result = await ctx.coordinator.map_repo(
        include=params.include,
        depth=params.depth,
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
