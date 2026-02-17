"""Index MCP tools - search, map_repo handlers."""

import contextlib
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from codeplane.config.constants import (
    MAP_DEPTH_MAX,
    MAP_LIMIT_MAX,
    SEARCH_MAX_LIMIT,
)
from codeplane.mcp.budget import (
    BudgetAccumulator,
    get_effective_budget,
    make_budget_pagination,
    measure_bytes,
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


def _flatten_tree(nodes: list[Any], include_line_counts: bool = True) -> list[dict[str, Any]]:
    """Flatten tree to individual path entries for inline_only pagination.

    Unlike _serialize_tree which creates nested structures, this produces
    a flat list of entries that can be paginated item-by-item within
    the 7.5KB inline budget.
    """
    result: list[dict[str, Any]] = []

    def _walk(nodes: list[Any]) -> None:
        for node in nodes:
            entry: dict[str, Any] = {
                "path": node.path,
                "is_dir": node.is_dir,
            }
            if node.is_dir:
                entry["file_count"] = node.file_count
            elif include_line_counts:
                entry["line_count"] = node.line_count
            result.append(entry)
            if node.is_dir and node.children:
                _walk(node.children)

    _walk(nodes)
    return result


# ---------------------------------------------------------------------------
# Tiered Section Serializers (F+E Pattern)
#
# Each section has 3 tiers:
#   - Tier 1 (summary): Just counts - always fits
#   - Tier 2 (sample): Top N items - usually fits in 7.5KB
#   - Tier 3 (full): Everything - may need pagination
#
# The map_repo handler tries Tier 3, falls back to 2, then 1.
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
            "tier": _TIER_SUMMARY,
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
        overview["test_file_count"] = len(result.test_layout.test_files)
        overview["test_count"] = result.test_layout.test_count

    if result.entry_points:
        overview["entry_point_count"] = len(result.entry_points)

    if result.public_api:
        overview["public_api_count"] = len(result.public_api)

    return overview


def _try_section_with_fallback(
    section_name: str,
    serializer: Any,
    data: Any,
    budget_remaining: int,
    include_line_counts: bool = True,
) -> tuple[dict[str, Any], str]:
    """Try to serialize a section, falling back to lower tiers if needed.

    Returns (serialized_section, tier_used).
    """
    tiers = [_TIER_FULL, _TIER_SAMPLE, _TIER_SUMMARY]

    for tier in tiers:
        if section_name == "structure":
            section = serializer(data, tier, include_line_counts)
        else:
            section = serializer(data, tier)

        size = measure_bytes(section)
        if size <= budget_remaining:
            return section, tier

    # Even summary doesn't fit - return summary anyway (guaranteed small)
    if section_name == "structure":
        return serializer(data, _TIER_SUMMARY, include_line_counts), _TIER_SUMMARY
    return serializer(data, _TIER_SUMMARY), _TIER_SUMMARY


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
        cursor: str | None = Field(None, description="Pagination cursor"),
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
                    "pagination": make_budget_pagination(has_more=False),
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
                "pagination": make_budget_pagination(has_more=False),
                "query_time_ms": 0,
                "summary": _summarize_search(1, "definitions", query),
            }

        if mode == "references":
            # First find the definition, then get references
            def_fact = await app_ctx.coordinator.get_def(query, context_id=None)
            if def_fact is None:
                return {
                    "results": [],
                    "pagination": make_budget_pagination(has_more=False),
                    "query_time_ms": 0,
                    "summary": _summarize_search(0, "references", query),
                }

            # Apply cursor: skip past previously returned results
            start_idx = 0
            if cursor:
                with contextlib.suppress(ValueError):
                    parsed = int(cursor)
                    if parsed >= 0:
                        start_idx = parsed

            # Fetch refs with offset for pagination (fetch limit+1 to detect has_more)
            ref_fetch_limit = limit * 10 if files_only else limit
            refs = await app_ctx.coordinator.get_references(
                def_fact, _context_id=0, limit=ref_fetch_limit + 1, offset=start_idx
            )

            # Check if there are more refs beyond this page
            has_more_refs = len(refs) > ref_fetch_limit
            if has_more_refs:
                refs = refs[:ref_fetch_limit]

            # Reserve overhead for fixed response fields
            base_response = {
                "results": [],
                "pagination": {
                    "truncated": False,
                    "next_cursor": "x" * 40,
                    "total_estimate": 99999,
                },
                "query_time_ms": 99999,
                "summary": "X" * 200,
            }
            overhead = measure_bytes(base_response)
            acc = BudgetAccumulator()
            acc.reserve(overhead)
            ref_files: set[str] = set()
            raw_refs_consumed = len(refs)

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
                    has_more_refs = True
                    deduped_pairs = deduped_pairs[:limit]
                    kept = {p for p, _ in deduped_pairs}
                    raw_refs_consumed = sum(c for p, c in ref_match_counts.items() if p in kept)
                    ref_match_counts = {p: c for p, c in ref_match_counts.items() if p in kept}
                ref_path_pairs = deduped_pairs
            else:
                ref_match_counts = {}

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

                if not acc.try_add(result_item):
                    break
                ref_files.add(path)

            # Determine if there are more results
            # has_more_refs = True if we fetched ref_fetch_limit+1 rows
            # budget_more = True if budget was exhausted before all refs in page
            budget_more = not acc.has_room and len(ref_path_pairs) > acc.count
            has_more = has_more_refs or budget_more
            # If budget was exhausted, only count raw refs for files actually emitted.
            if files_only and budget_more and ref_match_counts:
                raw_refs_consumed = sum(c for p, c in ref_match_counts.items() if p in ref_files)
            next_offset = start_idx + (raw_refs_consumed if files_only else acc.count)
            return {
                "results": acc.items,
                "pagination": make_budget_pagination(
                    has_more=has_more,
                    next_cursor=str(next_offset) if has_more else None,
                ),
                "query_time_ms": 0,
                "summary": _summarize_search(
                    acc.count, "references", query, file_count=len(ref_files)
                ),
            }

        # Parse cursor for pagination
        start_idx = 0
        if cursor:
            with contextlib.suppress(ValueError):
                parsed = int(cursor)
                if parsed >= 0:
                    start_idx = parsed

        # When files_only, a single file can produce many line-level results,
        # so we fetch a larger batch to ensure enough unique files after dedup.
        fetch_limit = limit * 10 if files_only else limit

        # Symbol search — uses dedicated search_symbols with SQLite + Tantivy fallback
        if mode == "symbol":
            search_response = await app_ctx.coordinator.search_symbols(
                query,
                filter_kinds=filter_kinds,
                filter_paths=filter_paths,
                limit=fetch_limit + 1,
                offset=start_idx,
            )
        else:
            search_response = await app_ctx.coordinator.search(
                query,
                mode_map[mode],
                limit=fetch_limit + 1,
                offset=start_idx,
                context_lines=effective_lines if not is_structural else 1,
                filter_languages=filter_languages,
                filter_paths=filter_paths,
            )

        # Check if there are more results beyond this page
        all_results = search_response.results
        has_more_results = len(all_results) > fetch_limit
        if has_more_results:
            all_results = all_results[:fetch_limit]

        # Track how many raw (pre-dedup) results were consumed for cursor math
        raw_results_consumed = len(all_results)

        # files_only: deduplicate to one result per file with match_count
        if files_only and mode in ("lexical", "symbol", "references"):
            file_groups: dict[str, tuple[Any, int]] = {}
            for r in all_results:
                if r.path not in file_groups:
                    file_groups[r.path] = (r, 1)
                else:
                    first, count = file_groups[r.path]
                    file_groups[r.path] = (first, count + 1)

            # If the last raw result shares a path with an earlier result AND
            # there are more results beyond this batch, the trailing file was
            # only partially consumed.  Drop it from this page so it appears
            # complete on the next page.
            if has_more_results and all_results:
                trailing_path = all_results[-1].path
                if (
                    trailing_path in file_groups
                    and file_groups[trailing_path][1] > 0
                    and len(file_groups) > 1
                ):
                    raw_results_consumed -= file_groups[trailing_path][1]
                    del file_groups[trailing_path]

            all_results_deduped = []
            match_counts: dict[str, int] = {}
            for path, (r, count) in file_groups.items():
                all_results_deduped.append(r)
                match_counts[path] = count

            # Cap to requested limit; track raw results consumed up to that point
            if len(all_results_deduped) > limit:
                has_more_results = True
                all_results_deduped = all_results_deduped[:limit]
                # Recount raw results consumed: sum match_counts for kept files only
                kept_paths = {r.path for r in all_results_deduped}
                raw_results_consumed = sum(
                    count for path, count in match_counts.items() if path in kept_paths
                )
                match_counts = {p: c for p, c in match_counts.items() if p in kept_paths}
            all_results = all_results_deduped
        else:
            match_counts = {}
            # For non-files_only, apply original limit
            if len(all_results) > limit:
                has_more_results = True
                all_results = all_results[:limit]

        # Build results as spans + metadata (no source text)
        # Reserve overhead for fixed response fields
        base_response = {
            "results": [],
            "pagination": {"truncated": False, "next_cursor": "x" * 40, "total_estimate": 99999},
            "query_time_ms": 99999,
            "summary": "X" * 200,
        }
        overhead = measure_bytes(base_response)
        acc = BudgetAccumulator()
        acc.reserve(overhead)
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

            if not acc.try_add(result_item):
                break
            unique_files.add(r.path)

        # Determine if there are more results
        # has_more_results = True if coordinator returned limit+1 rows
        # budget_more = True if budget was exhausted before all results in page
        budget_more = not acc.has_room and len(all_results) > acc.count
        has_more = has_more_results or budget_more
        # When files_only, cursor must skip all raw results consumed (pre-dedup),
        # not just the deduplicated count, to avoid re-fetching the same rows.
        # If budget was exhausted, only count raw results for files actually emitted.
        if files_only and budget_more and match_counts:
            raw_results_consumed = sum(
                count for path, count in match_counts.items() if path in unique_files
            )
        next_offset = start_idx + (raw_results_consumed if files_only else acc.count)

        result: dict[str, Any] = {
            "results": acc.items,
            "pagination": make_budget_pagination(
                has_more=has_more,
                next_cursor=str(next_offset) if has_more else None,
            ),
            "query_time_ms": 0,
            "summary": _summarize_search(
                acc.count,
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

        return wrap_existing_response(result, resource_kind="search_hits", scope_id=scope_id)

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
        verbosity: Literal["full", "standard", "minimal"] = Field(
            "full",
            description=(
                "Output detail level: full=tree with line counts, "
                "standard=tree without line counts, minimal=counts only (no tree)"
            ),
        ),
        inline_only: bool = Field(
            False,
            description="If true, use 7.5KB budget for guaranteed inline display in VS Code",
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Get repository mental model with tiered budget-based output.

        Uses progressive disclosure (F+E pattern):
        - Overview block with counts always returned (guaranteed to fit)
        - Each section tries full → sample → summary tiers based on budget
        - Downgraded sections include expand cursors for drill-down

        Cursor types:
        - None: First page with overview + tiered sections
        - "expand:<section>:<offset>": Drill into a specific section
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        effective_budget = get_effective_budget(inline_only)
        include_line_counts = verbosity == "full"

        # Handle expand cursor for drill-down requests
        if cursor and cursor.startswith("expand:"):
            # Parse section from cursor to ensure it's included
            cursor_parts = cursor.split(":")
            if len(cursor_parts) >= 2:
                expand_section = cursor_parts[1]
                # Map cursor section names to include options
                section_map = {
                    "structure": "structure",
                    "dependencies": "dependencies",
                    "test_layout": "test_layout",
                    "entry_points": "entry_points",
                    "public_api": "public_api",
                }
                if expand_section in section_map:
                    # Fetch only the target section
                    expand_include = [section_map[expand_section]]
                    result = await app_ctx.coordinator.map_repo(
                        include=expand_include,  # type: ignore[arg-type]
                        depth=depth,
                        limit=limit,
                        include_globs=include_globs,
                        exclude_globs=exclude_globs,
                        respect_gitignore=respect_gitignore,
                    )
                    return await _handle_expand_cursor(
                        cursor, result, effective_budget, include_line_counts
                    )
            # Invalid expand cursor
            return {"error": "Invalid expand cursor", "cursor": cursor}

        # Fetch data from coordinator for first page
        result = await app_ctx.coordinator.map_repo(
            include=include,
            depth=depth,
            limit=limit,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            respect_gitignore=respect_gitignore,
        )

        # --- First page: Overview + tiered sections ---

        # Compute overhead for fixed response fields
        overhead_template: dict[str, Any] = {
            "overview": {},
            "sections": {},
            "pagination": {"expandable": [], "cursors": {}},
            "summary": "X" * 100,
            "agentic_hint": "X" * 150,
        }
        overhead = measure_bytes(overhead_template) + 300  # safety margin

        # Track budget usage
        used_bytes = overhead
        sections_output: dict[str, Any] = {}
        downgraded: list[str] = []
        expand_cursors: dict[str, str] = {}

        # Build overview (always fits, ~500 bytes)
        overview = _build_overview(result)
        used_bytes += measure_bytes(overview)

        # Allocate remaining budget across sections
        remaining = effective_budget - used_bytes

        # --- Languages (small, no tiering needed) ---
        if result.languages:
            lang_section = _serialize_languages(result.languages)
            size = measure_bytes({"languages": lang_section})
            if size <= remaining:
                sections_output["languages"] = lang_section
                remaining -= size

        # --- Structure ---
        if result.structure:
            section, tier = _try_section_with_fallback(
                "structure",
                _serialize_structure_tiered,
                result.structure,
                remaining,
                include_line_counts,
            )
            size = measure_bytes({"structure": section})
            if size <= remaining:
                sections_output["structure"] = section
                remaining -= size
                if tier != _TIER_FULL:
                    downgraded.append("structure")
                    expand_cursors["structure"] = "expand:structure:0"
            else:
                # Even summary didn't fit - add minimal
                minimal = _serialize_structure_tiered(
                    result.structure, _TIER_SUMMARY, include_line_counts
                )
                sections_output["structure"] = minimal
                downgraded.append("structure")
                expand_cursors["structure"] = "expand:structure:0"

        # --- Dependencies ---
        if result.dependencies:
            section, tier = _try_section_with_fallback(
                "dependencies",
                _serialize_dependencies_tiered,
                result.dependencies,
                remaining,
            )
            size = measure_bytes({"dependencies": section})
            if size <= remaining:
                sections_output["dependencies"] = section
                remaining -= size
                if tier != _TIER_FULL:
                    downgraded.append("dependencies")
                    expand_cursors["dependencies"] = "expand:dependencies:0"
            else:
                minimal = _serialize_dependencies_tiered(result.dependencies, _TIER_SUMMARY)
                sections_output["dependencies"] = minimal
                downgraded.append("dependencies")
                expand_cursors["dependencies"] = "expand:dependencies:0"

        # --- Test Layout ---
        if result.test_layout:
            section, tier = _try_section_with_fallback(
                "test_layout",
                _serialize_test_layout_tiered,
                result.test_layout,
                remaining,
            )
            size = measure_bytes({"test_layout": section})
            if size <= remaining:
                sections_output["test_layout"] = section
                remaining -= size
                if tier != _TIER_FULL:
                    downgraded.append("test_layout")
                    expand_cursors["test_layout"] = "expand:test_layout:0"
            else:
                minimal = _serialize_test_layout_tiered(result.test_layout, _TIER_SUMMARY)
                sections_output["test_layout"] = minimal
                downgraded.append("test_layout")
                expand_cursors["test_layout"] = "expand:test_layout:0"

        # --- Entry Points ---
        if result.entry_points:
            section, tier = _try_section_with_fallback(
                "entry_points",
                _serialize_entry_points_tiered,
                result.entry_points,
                remaining,
            )
            size = measure_bytes({"entry_points": section})
            if size <= remaining:
                sections_output["entry_points"] = section
                remaining -= size
                if tier != _TIER_FULL:
                    downgraded.append("entry_points")
                    expand_cursors["entry_points"] = "expand:entry_points:0"
            else:
                minimal = _serialize_entry_points_tiered(result.entry_points, _TIER_SUMMARY)
                sections_output["entry_points"] = minimal
                downgraded.append("entry_points")
                expand_cursors["entry_points"] = "expand:entry_points:0"

        # --- Public API ---
        if result.public_api:
            section, tier = _try_section_with_fallback(
                "public_api",
                _serialize_public_api_tiered,
                result.public_api,
                remaining,
            )
            size = measure_bytes({"public_api": section})
            if size <= remaining:
                sections_output["public_api"] = section
                remaining -= size
                if tier != _TIER_FULL:
                    downgraded.append("public_api")
                    expand_cursors["public_api"] = "expand:public_api:0"
            else:
                minimal = _serialize_public_api_tiered(result.public_api, _TIER_SUMMARY)
                sections_output["public_api"] = minimal
                downgraded.append("public_api")
                expand_cursors["public_api"] = "expand:public_api:0"

        # --- Build output ---
        output: dict[str, Any] = {
            "overview": overview,
            **sections_output,
        }

        # Build pagination
        pagination: dict[str, Any] = {}
        if downgraded:
            pagination["expandable"] = downgraded
            pagination["cursors"] = expand_cursors
        output["pagination"] = pagination

        # Summary
        file_count = result.structure.file_count if result.structure else 0
        section_names = list(sections_output.keys())
        output["summary"] = _summarize_map(file_count, section_names, bool(downgraded))

        # Agentic hint
        if downgraded:
            output["agentic_hint"] = (
                f"Sections {', '.join(downgraded)} were summarized to fit budget. "
                "Use pagination.cursors to expand specific sections for full detail."
            )
        else:
            pass  # Delivery envelope handles overflow

        output["preset_used"] = "synopsis" if include is None else "custom"

        from codeplane.mcp.delivery import wrap_existing_response

        return wrap_existing_response(output, resource_kind="repo_map", scope_id=scope_id)

    async def _handle_expand_cursor(
        cursor: str,
        result: Any,
        budget: int,
        include_line_counts: bool,
    ) -> dict[str, Any]:
        """Handle expand cursor for drilling into a specific section."""
        # Parse: "expand:<section>:<offset>"
        parts = cursor.split(":")
        if len(parts) != 3:
            return {"error": "Invalid cursor format", "cursor": cursor}

        section = parts[1]
        try:
            offset = int(parts[2])
        except ValueError:
            return {"error": "Invalid cursor offset", "cursor": cursor}

        # Reserve overhead
        overhead = 500
        acc = BudgetAccumulator(budget=budget)
        acc.reserve(overhead)

        output: dict[str, Any] = {"section": section}
        next_cursor: str | None = None

        if section == "structure" and result.structure:
            # Paginate flat tree entries
            all_entries = _flatten_tree(result.structure.tree, include_line_counts)
            items: list[dict[str, Any]] = []
            consumed = 0

            for entry in all_entries[offset:]:
                if acc.try_add(entry):
                    items.append(entry)
                    consumed += 1
                else:
                    break

            output["entries"] = items
            output["entries_shown"] = len(items)
            output["entries_total"] = len(all_entries)

            next_offset = offset + consumed
            if next_offset < len(all_entries):
                next_cursor = f"expand:structure:{next_offset}"

        elif section == "dependencies" and result.dependencies:
            all_modules = result.dependencies.external_modules
            items = []
            consumed = 0

            for mod in all_modules[offset:]:
                item = {"module": mod}
                if acc.try_add(item):
                    items.append(mod)
                    consumed += 1
                else:
                    break

            output["external_modules"] = items
            output["modules_shown"] = len(items)
            output["modules_total"] = len(all_modules)

            next_offset = offset + consumed
            if next_offset < len(all_modules):
                next_cursor = f"expand:dependencies:{next_offset}"

        elif section == "test_layout" and result.test_layout:
            all_files = result.test_layout.test_files
            items = []
            consumed = 0

            for f in all_files[offset:]:
                item = {"path": f}
                if acc.try_add(item):
                    items.append(f)
                    consumed += 1
                else:
                    break

            output["test_files"] = items
            output["files_shown"] = len(items)
            output["files_total"] = len(all_files)

            next_offset = offset + consumed
            if next_offset < len(all_files):
                next_cursor = f"expand:test_layout:{next_offset}"

        elif section == "entry_points" and result.entry_points:
            all_eps = result.entry_points
            items = []
            consumed = 0

            for ep in all_eps[offset:]:
                item = {
                    "path": ep.path,
                    "kind": ep.kind,
                    "name": ep.name,
                    "qualified_name": ep.qualified_name,
                }
                if acc.try_add(item):
                    items.append(item)
                    consumed += 1
                else:
                    break

            output["items"] = items
            output["items_shown"] = len(items)
            output["items_total"] = len(all_eps)

            next_offset = offset + consumed
            if next_offset < len(all_eps):
                next_cursor = f"expand:entry_points:{next_offset}"

        elif section == "public_api" and result.public_api:
            all_syms = result.public_api
            items = []
            consumed = 0

            for sym in all_syms[offset:]:
                item = {
                    "name": sym.name,
                    "def_uid": sym.def_uid,
                    "certainty": sym.certainty,
                    "evidence": sym.evidence,
                }
                if acc.try_add(item):
                    items.append(item)
                    consumed += 1
                else:
                    break

            output["items"] = items
            output["items_shown"] = len(items)
            output["items_total"] = len(all_syms)

            next_offset = offset + consumed
            if next_offset < len(all_syms):
                next_cursor = f"expand:public_api:{next_offset}"

        else:
            return {"error": f"Unknown or empty section: {section}"}

        output["pagination"] = {
            "next_cursor": next_cursor,
            "complete": next_cursor is None,
        }

        return output

    def _parse_map_cursor(cursor: str | None) -> tuple[int, int, int]:
        """Parse map_repo pagination cursor into (tree_offset, ep_offset, api_offset)."""
        if cursor is None:
            return 0, 0, 0
        parts = cursor.split(":")
        if len(parts) == 3:
            try:
                return int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                pass
        return 0, 0, 0

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
