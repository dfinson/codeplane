"""Index MCP tools - search, map_repo handlers."""

import contextlib
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from codeplane.config.constants import (
    MAP_DEPTH_MAX,
    MAP_LIMIT_MAX,
    SEARCH_CONTEXT_LINES_MAX,
    SEARCH_MAX_LIMIT,
    SEARCH_SCOPE_FALLBACK_LINES_DEFAULT,
)
from codeplane.mcp.budget import BudgetAccumulator, make_budget_pagination, measure_bytes

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

    # Context preset line counts
    CONTEXT_PRESETS = {
        "none": 0,
        "minimal": 1,
        "standard": 5,
        "rich": 20,
    }

    @mcp.tool
    async def search(
        ctx: Context,
        query: str = Field(..., description="Search query"),
        mode: Literal["lexical", "symbol", "references", "definitions"] = Field(
            "lexical", description="Search mode"
        ),
        context: Literal["none", "minimal", "standard", "rich", "function", "class"] = Field(
            "standard",
            description=(
                "Context mode: 'none' (path+line only), 'minimal' (1 line), "
                "'standard' (5 lines), 'rich' (20 lines), "
                "'function' (enclosing function body), 'class' (enclosing class body)"
            ),
        ),
        context_lines: int | None = Field(
            None,
            ge=0,
            le=SEARCH_CONTEXT_LINES_MAX,
            description=(
                "Override context lines for line-based modes; "
                "fallback lines for structural modes (function/class)"
            ),
        ),
        filter_paths: list[str] | None = Field(None, description="Filter by paths"),
        filter_languages: list[str] | None = Field(None, description="Filter by languages"),
        filter_kinds: list[str] | None = Field(None, description="Filter by symbol kinds"),
        limit: int = Field(default=20, le=SEARCH_MAX_LIMIT, description="Maximum results"),
        cursor: str | None = Field(None, description="Pagination cursor"),
    ) -> dict[str, Any]:
        """Search code, symbols, or references.

        Context modes control how much content is returned around each match:
        - 'none': Path and line only, no content (for counting/listing)
        - 'minimal': 1 line of context (for quick location)
        - 'standard': 5 lines of context (default, for understanding)
        - 'rich': 20 lines of context (edit-ready, line-based)
        - 'function': Enclosing function body (edit-ready, structural)
        - 'class': Enclosing class body (broad context, structural)

        Structural modes ('function', 'class') use the indexed scope facts to return
        complete function or class bodies. If scope resolution fails (e.g., unsupported
        language), they fall back to context_lines (default 25).
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from codeplane.index._internal.indexing import resolve_scope_region_for_path
        from codeplane.index.ops import SearchMode

        # Determine effective context lines
        is_structural = context in ("function", "class")
        if context_lines is not None:
            effective_lines = context_lines
        elif is_structural:
            effective_lines = SEARCH_SCOPE_FALLBACK_LINES_DEFAULT
        else:
            effective_lines = CONTEXT_PRESETS.get(context, 5)

        # Map mode to SearchMode
        mode_map = {
            "lexical": SearchMode.TEXT,
            "symbol": SearchMode.SYMBOL,
            "definitions": SearchMode.SYMBOL,
            "references": SearchMode.TEXT,
        }

        if mode == "definitions":
            # Use get_def for definition search
            def_fact = await app_ctx.coordinator.get_def(query, context_id=None)
            if def_fact is None:
                return {
                    "results": [],
                    "pagination": make_budget_pagination(has_more=False),
                    "query_time_ms": 0,
                    "summary": _summarize_search(0, "definitions", query),
                }

            file_path = await _get_file_path(app_ctx, def_fact.file_id)

            # Build result with context handling
            result_item: dict[str, Any] = {
                "path": file_path,
                "line": def_fact.start_line,
                "column": def_fact.start_col,
                "score": 1.0,
                "match_type": "exact",
                "symbol": {
                    "name": def_fact.name,
                    "kind": def_fact.kind,
                    "qualified_name": def_fact.qualified_name,
                },
            }

            # Add content based on context mode
            if context != "none":
                if is_structural:
                    with app_ctx.coordinator.db.session() as session:
                        scope_region, content = resolve_scope_region_for_path(
                            session,
                            app_ctx.coordinator.repo_root,
                            file_path,
                            def_fact.start_line,
                            preference="function" if context == "function" else "class",
                            fallback_lines=effective_lines,
                        )
                    result_item["content"] = content
                    result_item["content_range"] = {
                        "start": scope_region.start_line,
                        "end": scope_region.end_line,
                    }
                    result_item["context_resolved"] = scope_region.kind
                else:
                    result_item["snippet"] = def_fact.display_name or def_fact.name
                    result_item["context_resolved"] = "lines"

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
            refs = await app_ctx.coordinator.get_references(
                def_fact, _context_id=0, limit=limit + 1, offset=start_idx
            )

            # Check if there are more refs beyond this page
            has_more_refs = len(refs) > limit
            if has_more_refs:
                refs = refs[:limit]

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

            for ref in refs:
                path = await _get_file_path(app_ctx, ref.file_id)

                result_item = {
                    "path": path,
                    "line": ref.start_line,
                    "column": ref.start_col,
                    "score": 1.0 if ref.certainty == "CERTAIN" else 0.5,
                    "match_type": "exact" if ref.certainty == "CERTAIN" else "fuzzy",
                }

                # Add content based on context mode
                if context != "none":
                    if is_structural:
                        with app_ctx.coordinator.db.session() as session:
                            scope_region, content = resolve_scope_region_for_path(
                                session,
                                app_ctx.coordinator.repo_root,
                                path,
                                ref.start_line,
                                preference="function" if context == "function" else "class",
                                fallback_lines=effective_lines,
                            )
                        result_item["content"] = content
                        result_item["content_range"] = {
                            "start": scope_region.start_line,
                            "end": scope_region.end_line,
                        }
                        result_item["context_resolved"] = scope_region.kind
                    else:
                        result_item["snippet"] = ref.token_text
                        result_item["context_resolved"] = "lines"

                if not acc.try_add(result_item):
                    break
                ref_files.add(path)

            # Determine if there are more results
            # has_more_refs = True if we fetched limit+1 rows
            # budget_more = True if budget was exhausted before all refs in page
            budget_more = not acc.has_room and len(refs) > acc.count
            has_more = has_more_refs or budget_more
            next_offset = start_idx + acc.count
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

        # Symbol search — uses dedicated search_symbols with SQLite + Tantivy fallback
        if mode == "symbol":
            search_response = await app_ctx.coordinator.search_symbols(
                query,
                filter_kinds=filter_kinds,
                filter_paths=filter_paths,
                limit=limit + 1,
                offset=start_idx,
            )
        else:
            # Lexical search - fetch limit+1 to detect has_more
            search_response = await app_ctx.coordinator.search(
                query,
                mode_map[mode],
                limit=limit + 1,
                offset=start_idx,
                context_lines=effective_lines if not is_structural else 1,
                filter_languages=filter_languages,
                filter_paths=filter_paths,
            )

        # Check if there are more results beyond this page
        all_results = search_response.results
        has_more_results = len(all_results) > limit
        if has_more_results:
            all_results = all_results[:limit]

        # Build results with context handling, bounded by budget
        # Reserve overhead for fixed response fields
        base_response = {
            "results": [],
            "pagination": {"truncated": False, "next_cursor": "x" * 40, "total_estimate": 99999},
            "query_time_ms": 99999,
            "summary": "X" * 200,
            "agentic_hint": "X" * 200,
        }
        overhead = measure_bytes(base_response)
        acc = BudgetAccumulator()
        acc.reserve(overhead)
        unique_files: set[str] = set()
        for r in all_results:
            result_item = {
                "path": r.path,
                "line": r.line,
                "column": r.column,
                "score": r.score,
                "match_type": "fuzzy",
            }

            # Add content based on context mode
            if context != "none":
                if is_structural:
                    with app_ctx.coordinator.db.session() as session:
                        scope_region, content = resolve_scope_region_for_path(
                            session,
                            app_ctx.coordinator.repo_root,
                            r.path,
                            r.line,
                            preference="function" if context == "function" else "class",
                            fallback_lines=effective_lines,
                        )
                    result_item["content"] = content
                    result_item["content_range"] = {
                        "start": scope_region.start_line,
                        "end": scope_region.end_line,
                    }
                    result_item["context_resolved"] = scope_region.kind
                else:
                    result_item["snippet"] = r.snippet
                    result_item["context_resolved"] = "lines"

            if not acc.try_add(result_item):
                break
            unique_files.add(r.path)

        # Determine if there are more results
        # has_more_results = True if coordinator returned limit+1 rows
        # budget_more = True if budget was exhausted before all results in page
        budget_more = not acc.has_room and len(all_results) > acc.count
        has_more = has_more_results or budget_more
        next_offset = start_idx + acc.count

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

        # Add pagination and summary before measuring budget so the
        # measurement reflects the full serialized response.
        truncated = result.truncated
        output["pagination"] = make_budget_pagination(
            has_more=truncated,
            next_cursor=result.next_cursor,
            total_estimate=result.total_estimate,
        )
        output["summary"] = _summarize_map(file_count, sections, truncated)
        return output

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
