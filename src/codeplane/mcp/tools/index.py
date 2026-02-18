"""Index MCP tools - search, map_repo handlers."""

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from codeplane.config.constants import (
    MAP_DEPTH_MAX,
    MAP_LIMIT_MAX,
    SEARCH_MAX_LIMIT,
)

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


def _serialize_tree(nodes: list[Any], *, include_line_counts: bool = True) -> list[dict[str, Any]]:
    """Recursively serialize directory tree nodes.

    Note: 'name' field is omitted - agents can derive it from path.split('/')[-1].
    This saves ~8% response size with minimal agentic impact.

    Args:
        nodes: The tree nodes to serialize.
        include_line_counts: If False, omit line_count from file entries (standard mode).
    """
    result: list[dict[str, Any]] = []
    for node in nodes:
        item: dict[str, Any] = {
            "path": node.path,
            "is_dir": node.is_dir,
        }
        if node.is_dir:
            item["file_count"] = node.file_count
            item["children"] = _serialize_tree(
                node.children, include_line_counts=include_line_counts
            )
        elif include_line_counts:
            item["line_count"] = node.line_count
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Tiered Section Serializers (F+E Pattern)
#
# Each section has 3 tiers:
#   - Tier 1 (summary): Just counts - always fits
#   - Tier 2 (sample): Top N items - usually fits in 7.5KB
#   - Tier 3 (full): Everything
#
# map_repo always uses Tier 3 (full) now.
# ---------------------------------------------------------------------------

# Tier constants
_TIER_FULL = "full"
_TIER_SAMPLE = "sample"
_TIER_SUMMARY = "summary"

# Sample sizes for Tier 2
_STRUCTURE_TOP_DIRS = 10
_DEPS_TOP_MODULES = 25
_TESTS_TOP_FILES = 15
_ENTRY_POINTS_SAMPLE = 10
_PUBLIC_API_SAMPLE = 10


def _serialize_structure_tiered(
    structure: Any, tier: str, include_line_counts: bool = True
) -> dict[str, Any]:
    """Serialize structure section at specified tier."""
    if tier == _TIER_SUMMARY:
        # Tier 1: Just counts and top-level dir names
        top_dirs = [n.path for n in structure.tree[:5] if n.is_dir]
        return {
            "tier": _TIER_SUMMARY,
            "root": structure.root,
            "file_count": structure.file_count,
            "top_dirs": top_dirs,
        }

    if tier == _TIER_SAMPLE:
        # Tier 2: Top N directories with their immediate file counts (depth 1)
        top_entries = []
        for node in structure.tree[:_STRUCTURE_TOP_DIRS]:
            entry: dict[str, Any] = {"path": node.path, "is_dir": node.is_dir}
            if node.is_dir:
                entry["file_count"] = node.file_count
            elif include_line_counts:
                entry["line_count"] = node.line_count
            top_entries.append(entry)
        return {
            "tier": _TIER_SAMPLE,
            "root": structure.root,
            "file_count": structure.file_count,
            "entries": top_entries,
            "entries_shown": len(top_entries),
            "entries_total": len(structure.tree),
        }

    # Tier 3: Full nested tree
    tree = _serialize_tree(structure.tree, include_line_counts=include_line_counts)
    result: dict[str, Any] = {
        "tier": _TIER_FULL,
        "root": structure.root,
        "file_count": structure.file_count,
        "tree": tree,
    }
    if structure.contexts:
        result["contexts"] = structure.contexts
    return result


def _serialize_dependencies_tiered(deps: Any, tier: str) -> dict[str, Any]:
    """Serialize dependencies section at specified tier."""
    all_modules = deps.external_modules

    if tier == _TIER_SUMMARY:
        return {
            "tier": _TIER_SUMMARY,
            "external_count": len(all_modules),
            "import_count": deps.import_count,
        }

    if tier == _TIER_SAMPLE:
        top = all_modules[:_DEPS_TOP_MODULES]
        return {
            "tier": _TIER_SAMPLE,
            "top_modules": top,
            "modules_shown": len(top),
            "external_count": len(all_modules),
            "import_count": deps.import_count,
        }

    # Tier 3: Full list
    return {
        "tier": _TIER_FULL,
        "external_modules": all_modules,
        "import_count": deps.import_count,
    }


def _serialize_test_layout_tiered(test_layout: Any, tier: str) -> dict[str, Any]:
    """Serialize test_layout section at specified tier."""
    all_files = test_layout.test_files

    if tier == _TIER_SUMMARY:
        # Extract unique test directories
        test_dirs = sorted({f.rsplit("/", 1)[0] for f in all_files if "/" in f})
        return {
            "tier": _TIER_SUMMARY,
            "test_count": test_layout.test_count,
            "file_count": len(all_files),
            "test_dirs": test_dirs[:10],
        }

    if tier == _TIER_SAMPLE:
        top = all_files[:_TESTS_TOP_FILES]
        return {
            "tier": _TIER_SAMPLE,
            "test_files": top,
            "files_shown": len(top),
            "file_count": len(all_files),
            "test_count": test_layout.test_count,
        }

    # Tier 3: Full list
    return {
        "tier": _TIER_FULL,
        "test_files": all_files,
        "test_count": test_layout.test_count,
    }


def _serialize_entry_points_tiered(entry_points: list[Any], tier: str) -> dict[str, Any]:
    """Serialize entry_points section at specified tier."""
    if tier == _TIER_SUMMARY:
        return {
            "tier": _TIER_SUMMARY,
            "count": len(entry_points),
        }

    def _ep_to_dict(ep: Any) -> dict[str, Any]:
        return {
            "path": ep.path,
            "kind": ep.kind,
            "name": ep.name,
            "qualified_name": ep.qualified_name,
        }

    if tier == _TIER_SAMPLE:
        top = [_ep_to_dict(ep) for ep in entry_points[:_ENTRY_POINTS_SAMPLE]]
        return {
            "tier": _TIER_SAMPLE,
            "items": top,
            "items_shown": len(top),
            "count": len(entry_points),
        }

    # Tier 3: Full list
    return {
        "tier": _TIER_FULL,
        "items": [_ep_to_dict(ep) for ep in entry_points],
    }


def _serialize_public_api_tiered(public_api: list[Any], tier: str) -> dict[str, Any]:
    """Serialize public_api section at specified tier."""
    if tier == _TIER_SUMMARY:
        return {
            "tier": _TIER_SUMMARY,
            "count": len(public_api),
        }

    def _sym_to_dict(sym: Any) -> dict[str, Any]:
        return {
            "name": sym.name,
            "def_uid": sym.def_uid,
            "certainty": sym.certainty,
            "evidence": sym.evidence,
        }

    if tier == _TIER_SAMPLE:
        top = [_sym_to_dict(s) for s in public_api[:_PUBLIC_API_SAMPLE]]
        return {
            "tier": _TIER_SAMPLE,
            "items": top,
            "items_shown": len(top),
            "count": len(public_api),
        }

    # Tier 3: Full list
    return {
        "tier": _TIER_FULL,
        "items": [_sym_to_dict(s) for s in public_api],
    }


def _serialize_languages(languages: list[Any]) -> list[dict[str, Any]]:
    """Serialize languages (always fits, no tiers needed)."""
    return [
        {
            "language": lang.language,
            "file_count": lang.file_count,
            "percentage": lang.percentage,
        }
        for lang in languages
    ]


def _build_overview(result: Any) -> dict[str, Any]:
    """Build the always-fits overview block with counts."""
    overview: dict[str, Any] = {}

    if result.structure:
        overview["file_count"] = result.structure.file_count

    if result.languages:
        overview["languages"] = [
            {"name": lang.language, "count": lang.file_count, "pct": lang.percentage}
            for lang in result.languages
        ]

    if result.dependencies:
        overview["dependency_count"] = len(result.dependencies.external_modules)
        overview["import_count"] = result.dependencies.import_count

    if result.test_layout:
        overview["test_file_count"] = result.test_layout.test_count
        overview["test_count"] = result.test_layout.test_count

    if result.entry_points:
        overview["entry_point_count"] = len(result.entry_points)

    if result.public_api:
        overview["public_api_count"] = len(result.public_api)

    return overview


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
        enrichment: Literal["none", "minimal", "standard", "function", "class"] = Field(
            "none",
            description=(
                "Enrichment level for metadata (NEVER returns source text): "
                "'none' (span+kind+symbol_id only), 'minimal' (+enclosing scope name, signature), "
                "'standard' (+docstring flag, param names, return type), "
                "'function' (+enclosing function span), 'class' (+full class span)"
            ),
        ),
        filter_paths: list[str] | None = Field(None, description="Filter by paths"),
        filter_languages: list[str] | None = Field(None, description="Filter by languages"),
        filter_kinds: list[str] | None = Field(None, description="Filter by symbol kinds"),
        limit: int = Field(default=20, le=SEARCH_MAX_LIMIT, description="Maximum results"),
        files_only: bool = Field(
            False,
            description="Return one result per file (like rg -l). Includes match_count per file.",
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Search code, symbols, or references. Returns spans + metadata, NEVER source text.

        Use read_source to retrieve actual content for spans returned by search.

        Enrichment controls metadata richness (not source text):
        - 'none': span + kind + symbol_id only (default)
        - 'minimal': + enclosing scope name, signature metadata
        - 'standard': + docstring presence flag, parameter names, return type hint
        - 'function': + enclosing function span, enclosing class span
        - 'class': + full class span (all member spans)
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from codeplane.index.ops import SearchMode

        is_structural = enrichment in ("function", "class")
        effective_lines = 1  # We only need line info for span resolution

        # Map mode to SearchMode
        mode_map = {
            "lexical": SearchMode.TEXT,
            "symbol": SearchMode.SYMBOL,
            "definitions": SearchMode.SYMBOL,
            "references": SearchMode.TEXT,
        }

        if mode == "definitions":
            def_fact = await app_ctx.coordinator.get_def(query, context_id=None)
            if def_fact is None:
                return {
                    "results": [],
                    "query_time_ms": 0,
                    "summary": _summarize_search(0, "definitions", query),
                }

            file_path = await _get_file_path(app_ctx, def_fact.file_id)

            result_item: dict[str, Any] = {
                "hit_id": f"def:{file_path}:{def_fact.start_line}",
                "path": file_path,
                "span": {
                    "start_line": def_fact.start_line,
                    "start_col": def_fact.start_col,
                    "end_line": def_fact.end_line or def_fact.start_line,
                    "end_col": def_fact.end_col or 0,
                },
                "kind": "def",
                "symbol_id": def_fact.qualified_name or def_fact.name,
                "preview_line": def_fact.display_name or def_fact.name,
            }

            # Enrichment metadata (no source text)
            if enrichment != "none":
                result_item["symbol"] = {
                    "name": def_fact.name,
                    "kind": def_fact.kind,
                    "qualified_name": def_fact.qualified_name,
                }
            if enrichment in ("standard", "function", "class"):
                result_item["has_docstring"] = False  # Not tracked in index
                if def_fact.signature_hash:
                    result_item["signature_hash"] = def_fact.signature_hash
            if is_structural:
                from typing import Literal as _Lit

                from codeplane.index._internal.indexing import resolve_scope_region_for_path

                scope_pref: _Lit["function", "class", "block"] = (
                    "function" if enrichment == "function" else "class"
                )
                with app_ctx.coordinator.db.session() as session:
                    scope_region, _ = resolve_scope_region_for_path(
                        session,
                        app_ctx.coordinator.repo_root,
                        file_path,
                        def_fact.start_line,
                        preference=scope_pref,
                        fallback_lines=25,
                    )
                result_item["enclosing_span"] = {
                    "start_line": scope_region.start_line,
                    "end_line": scope_region.end_line,
                    "kind": scope_region.kind,
                }

            return {
                "results": [result_item],
                "query_time_ms": 0,
                "summary": _summarize_search(1, "definitions", query),
            }

        if mode == "references":
            # First find the definition, then get references
            def_fact = await app_ctx.coordinator.get_def(query, context_id=None)
            if def_fact is None:
                return {
                    "results": [],
                    "query_time_ms": 0,
                    "summary": _summarize_search(0, "references", query),
                }

            # Fetch references
            ref_fetch_limit = limit * 10 if files_only else limit
            refs = await app_ctx.coordinator.get_references(
                def_fact, _context_id=0, limit=ref_fetch_limit
            )

            # Resolve paths for all refs upfront for dedup
            ref_path_pairs: list[tuple[str, Any]] = []
            for ref in refs:
                path = await _get_file_path(app_ctx, ref.file_id)
                ref_path_pairs.append((path, ref))

            # files_only dedup for references
            if files_only:
                ref_file_groups: dict[str, tuple[str, Any, int]] = {}
                for path, ref in ref_path_pairs:
                    if path not in ref_file_groups:
                        ref_file_groups[path] = (path, ref, 1)
                    else:
                        p, r, c = ref_file_groups[path]
                        ref_file_groups[path] = (p, r, c + 1)
                ref_match_counts: dict[str, int] = {}
                deduped_pairs: list[tuple[str, Any]] = []
                for path, (p, ref, count) in ref_file_groups.items():
                    deduped_pairs.append((p, ref))
                    ref_match_counts[path] = count
                if len(deduped_pairs) > limit:
                    deduped_pairs = deduped_pairs[:limit]
                    kept = {p for p, _ in deduped_pairs}
                    ref_match_counts = {p: c for p, c in ref_match_counts.items() if p in kept}
                ref_path_pairs = deduped_pairs
            else:
                ref_match_counts = {}

            items: list[dict[str, Any]] = []
            ref_files: set[str] = set()
            for path, ref in ref_path_pairs:
                result_item = {
                    "hit_id": f"ref:{path}:{ref.start_line}:{ref.start_col}",
                    "path": path,
                    "span": {
                        "start_line": ref.start_line,
                        "start_col": ref.start_col,
                        "end_line": ref.end_line or ref.start_line,
                        "end_col": ref.end_col or 0,
                    },
                    "kind": "ref",
                    "symbol_id": ref.target_name if hasattr(ref, "target_name") else None,
                    "preview_line": ref.token_text[:120] if ref.token_text else None,
                }
                if ref_match_counts and path in ref_match_counts:
                    result_item["match_count"] = ref_match_counts[path]

                # Enrichment metadata (no source text)
                if enrichment != "none" and enrichment in ("function", "class"):
                    from codeplane.index._internal.indexing import resolve_scope_region_for_path

                    with app_ctx.coordinator.db.session() as session:
                        scope_region, _ = resolve_scope_region_for_path(
                            session,
                            app_ctx.coordinator.repo_root,
                            path,
                            ref.start_line,
                            preference="function" if enrichment == "function" else "class",
                            fallback_lines=25,
                        )
                    result_item["enclosing_span"] = {
                        "start_line": scope_region.start_line,
                        "end_line": scope_region.end_line,
                        "kind": scope_region.kind,
                    }

                items.append(result_item)
                ref_files.add(path)

            return {
                "results": items,
                "query_time_ms": 0,
                "summary": _summarize_search(
                    len(items), "references", query, file_count=len(ref_files)
                ),
            }
        # When files_only, a single file can produce many line-level results,
        # so we fetch a larger batch to ensure enough unique files after dedup.
        fetch_limit = limit * 10 if files_only else limit

        # Symbol search — uses dedicated search_symbols with SQLite + Tantivy fallback
        if mode == "symbol":
            search_response = await app_ctx.coordinator.search_symbols(
                query,
                filter_kinds=filter_kinds,
                filter_paths=filter_paths,
                limit=fetch_limit,
            )
        else:
            search_response = await app_ctx.coordinator.search(
                query,
                mode_map[mode],
                limit=fetch_limit,
                context_lines=effective_lines if not is_structural else 1,
                filter_languages=filter_languages,
                filter_paths=filter_paths,
            )

        all_results = search_response.results

        # files_only: deduplicate to one result per file with match_count
        if files_only and mode in ("lexical", "symbol", "references"):
            file_groups: dict[str, tuple[Any, int]] = {}
            for r in all_results:
                if r.path not in file_groups:
                    file_groups[r.path] = (r, 1)
                else:
                    first, count = file_groups[r.path]
                    file_groups[r.path] = (first, count + 1)

            all_results_deduped = []
            match_counts: dict[str, int] = {}
            for path, (r, count) in file_groups.items():
                all_results_deduped.append(r)
                match_counts[path] = count

            # Cap to requested limit
            if len(all_results_deduped) > limit:
                all_results_deduped = all_results_deduped[:limit]
                kept_paths = {r.path for r in all_results_deduped}
                match_counts = {p: c for p, c in match_counts.items() if p in kept_paths}
            all_results = all_results_deduped
        else:
            match_counts = {}
            if len(all_results) > limit:
                all_results = all_results[:limit]

        # Build results as spans + metadata (no source text)
        search_items: list[dict[str, Any]] = []
        unique_files: set[str] = set()
        for r in all_results:
            result_item = {
                "hit_id": f"{mode}:{r.path}:{r.line}:{r.column}",
                "path": r.path,
                "span": {
                    "start_line": r.line,
                    "start_col": r.column,
                    "end_line": r.line,
                    "end_col": 0,
                },
                "kind": "def" if mode == "symbol" else "lexical",
                "symbol_id": getattr(r, "qualified_name", None) or getattr(r, "name", None),
                "preview_line": (r.snippet or "")[:120] if r.snippet else None,
            }
            if match_counts and r.path in match_counts:
                result_item["match_count"] = match_counts[r.path]

            # Enrichment metadata (no source text)
            if enrichment != "none" and hasattr(r, "name") and r.name:
                result_item["symbol"] = {
                    "name": r.name,
                    "kind": getattr(r, "kind", None),
                    "qualified_name": getattr(r, "qualified_name", None),
                }
            if is_structural:
                from codeplane.index._internal.indexing import resolve_scope_region_for_path

                with app_ctx.coordinator.db.session() as session:
                    scope_region, _ = resolve_scope_region_for_path(
                        session,
                        app_ctx.coordinator.repo_root,
                        r.path,
                        r.line,
                        preference="function" if enrichment == "function" else "class",
                        fallback_lines=25,
                    )
                result_item["enclosing_span"] = {
                    "start_line": scope_region.start_line,
                    "end_line": scope_region.end_line,
                    "kind": scope_region.kind,
                }

            search_items.append(result_item)
            unique_files.add(r.path)

        result: dict[str, Any] = {
            "results": search_items,
            "query_time_ms": 0,
            "summary": _summarize_search(
                len(search_items),
                mode,
                query,
                fallback=search_response.fallback_reason is not None,
                file_count=len(unique_files),
            ),
        }

        # Include fallback reason if query syntax was invalid
        if search_response.fallback_reason:
            result["fallback_reason"] = search_response.fallback_reason

        from codeplane.mcp.delivery import wrap_existing_response

        # Track scope usage
        scope_usage = None
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            budget = _scope_manager.get_or_create(scope_id)
            budget.increment_search(len(items))
            exceeded = budget.check_budget("search_calls")
            exceeded_counter = "search_calls"
            if not exceeded:
                exceeded = budget.check_budget("search_hits")
                exceeded_counter = "search_hits"
            if exceeded:
                from codeplane.mcp.errors import BudgetExceededError

                raise BudgetExceededError(scope_id, exceeded_counter, exceeded)
            scope_usage = budget.to_usage_dict()

        return wrap_existing_response(
            result,
            resource_kind="search_hits",
            scope_id=scope_id,
            scope_usage=scope_usage,
        )

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
        verbosity: Literal["full", "standard", "minimal"] = Field(
            "full",
            description=(
                "Output detail level: full=tree with line counts, "
                "standard=tree without line counts, minimal=counts only (no tree)"
            ),
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Get repository mental model."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)
        include_line_counts = verbosity == "full"

        # Fetch data from coordinator
        result = await app_ctx.coordinator.map_repo(
            include=include,
            depth=depth,
            limit=limit,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            respect_gitignore=respect_gitignore,
        )

        # Build overview
        overview = _build_overview(result)

        # Serialize all sections at full detail
        sections_output: dict[str, Any] = {}
        if result.languages:
            sections_output["languages"] = _serialize_languages(result.languages)
        if result.structure:
            sections_output["structure"] = _serialize_structure_tiered(
                result.structure, _TIER_FULL, include_line_counts
            )
        if result.dependencies:
            sections_output["dependencies"] = _serialize_dependencies_tiered(
                result.dependencies, _TIER_FULL
            )
        if result.test_layout:
            sections_output["test_layout"] = _serialize_test_layout_tiered(
                result.test_layout, _TIER_FULL
            )
        if result.entry_points:
            sections_output["entry_points"] = _serialize_entry_points_tiered(
                result.entry_points, _TIER_FULL
            )
        if result.public_api:
            sections_output["public_api"] = _serialize_public_api_tiered(
                result.public_api, _TIER_FULL
            )

        # Build output
        output: dict[str, Any] = {
            "overview": overview,
            **sections_output,
        }

        # Summary
        file_count = result.structure.file_count if result.structure else 0
        section_names = list(sections_output.keys())
        output["summary"] = _summarize_map(file_count, section_names, False)
        output["preset_used"] = "synopsis" if include is None else "custom"

        from codeplane.mcp.delivery import wrap_existing_response

        return wrap_existing_response(
            output,
            resource_kind="repo_map",
            scope_id=scope_id,
        )

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
