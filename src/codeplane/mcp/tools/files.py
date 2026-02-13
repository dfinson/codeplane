"""Files MCP tools - read_files, list_files handlers."""

import contextlib
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.config.constants import FILES_LIST_MAX
from codeplane.mcp.budget import BudgetAccumulator, make_budget_pagination, measure_bytes

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class FileTarget(BaseModel):
    """File read target with optional line range.

    Each target specifies a file path and optional line range.
    The path is always required, eliminating the old failure mode where
    targets without paths silently failed to match any file.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    start_line: int | None = Field(None, gt=0, description="Start line (1-indexed, inclusive)")
    end_line: int | None = Field(None, gt=0, description="End line (1-indexed, inclusive)")

    @model_validator(mode="after")
    def validate_range(self) -> "FileTarget":
        if self.start_line is not None and self.end_line is not None:
            if self.end_line < self.start_line:
                raise ValueError(
                    f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                )
        elif (self.start_line is None) != (self.end_line is None):
            raise ValueError("start_line and end_line must both be set or both omitted")
        return self


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_read(files: list[dict[str, Any]], not_found: int = 0) -> str:
    """Generate summary for files.read."""
    from codeplane.core.formatting import compress_path, format_path_list, pluralize

    if not files and not_found:
        return f"{not_found} file(s) not found"

    total_lines = sum(f.get("line_count", 0) for f in files)
    paths = [f["path"] for f in files]

    if len(paths) == 1:
        compressed = compress_path(paths[0], 35)
        rng = files[0].get("range")
        if rng:
            return f"1 file ({compressed}:{rng[0]}-{rng[1]}), {total_lines} lines"
        return f"1 file ({compressed}), {total_lines} lines"

    # Multiple files: compress all paths
    compressed_paths = [compress_path(p, 20) for p in paths]
    path_list = format_path_list(compressed_paths, max_total=40, compress=False)
    suffix = f", {not_found} not found" if not_found else ""
    return f"{pluralize(len(files), 'file')} ({path_list}), {total_lines} lines{suffix}"


def _summarize_list(path: str, total: int, truncated: bool) -> str:
    """Generate summary for files.list."""
    loc = path or "repo root"
    trunc = " (truncated)" if truncated else ""
    return f"{total} entries in {loc}{trunc}"


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register file tools with FastMCP server."""

    @mcp.tool
    async def read_files(
        ctx: Context,
        targets: list[FileTarget] = Field(
            ...,
            description=(
                "File targets to read. Each specifies a path and optional "
                "start_line/end_line to read a subset of the file."
            ),
        ),
        include_metadata: bool = Field(False, description="Include file stats (size, mtime)"),
        cursor: str | None = Field(None, description="Pagination cursor"),
    ) -> dict[str, Any]:
        """Read file contents with optional line ranges."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Apply cursor: skip targets already returned
        start_idx = 0
        if cursor:
            with contextlib.suppress(ValueError):
                parsed = int(cursor)
                if parsed >= 0:
                    start_idx = parsed

        page_targets = targets[start_idx:]

        # Derive paths and target map from FileTarget objects.
        # Reject duplicate paths - only one FileTarget per path is supported.
        paths: list[str] = []
        target_map: dict[str, tuple[int, int]] = {}
        seen_paths: set[str] = set()
        for t in page_targets:
            if t.path in seen_paths:
                return {
                    "error": f"Duplicate path '{t.path}' in targets is not supported; "
                    "provide only one FileTarget per file path.",
                }
            seen_paths.add(t.path)
            paths.append(t.path)
            if t.start_line is not None and t.end_line is not None:
                target_map[t.path] = (t.start_line, t.end_line)

        result = app_ctx.file_ops.read_files(
            paths,
            targets=target_map if target_map else None,
            include_metadata=include_metadata,
        )

        # Build a map from path to file result for efficient lookup
        file_by_path: dict[str, Any] = {f.path: f for f in result.files}

        # Reserve overhead for fixed response fields
        base_response = {
            "files": [],
            "pagination": {"truncated": False, "next_cursor": "x" * 40, "total_estimate": 99999},
            "summary": "X" * 200,
            "not_found": ["X" * 100] * 10,  # Worst case: 10 paths of ~100 chars
            "not_found_count": 99999,
        }
        overhead = measure_bytes(base_response)
        acc = BudgetAccumulator()
        acc.reserve(overhead)
        processed_targets = 0
        missing_paths: list[str] = []
        missing_count = 0  # Track total missing, not just capped list

        # Process targets in order, tracking both found and missing
        for t in page_targets:
            if t.path in file_by_path:
                f = file_by_path[t.path]
                item = {
                    "path": f.path,
                    "content": f.content,
                    "language": f.language,
                    "line_count": f.line_count,
                    "range": f.range,
                    "metadata": f.metadata,
                }
                if not acc.try_add(item):
                    # Budget exhausted, stop processing
                    break
            else:
                # Target not found - track it but don't count against budget
                missing_count += 1
                # Cap missing_paths list to prevent unbounded response size
                if len(missing_paths) < 100:
                    missing_paths.append(t.path)
            processed_targets += 1

        # Check if there are more targets to process
        has_more_targets = processed_targets < len(page_targets)
        next_offset = start_idx + processed_targets
        response: dict[str, Any] = {
            "files": acc.items,
            "pagination": make_budget_pagination(
                has_more=has_more_targets,
                next_cursor=str(next_offset) if has_more_targets else None,
                total_estimate=len(targets) if has_more_targets else None,
            ),
            "summary": _summarize_read(acc.items, missing_count),
        }
        if missing_paths:
            response["not_found"] = missing_paths
            if missing_count > len(missing_paths):
                # Indicate there are more missing than listed
                response["not_found_count"] = missing_count
        return response

    @mcp.tool
    async def list_files(
        ctx: Context,
        path: str | None = Field(
            None, description="Directory path relative to repo root (default: repo root)"
        ),
        pattern: str | None = Field(
            None, description="Glob pattern to filter (e.g., '*.py', '**/*.ts')"
        ),
        recursive: bool = Field(False, description="Recurse into subdirectories"),
        include_hidden: bool = Field(False, description="Include dotfiles and dotdirs"),
        include_metadata: bool = Field(False, description="Include size and mtime for files"),
        file_type: Literal["all", "file", "directory"] = Field(
            "all", description="Filter by entry type"
        ),
        limit: int = Field(200, ge=1, le=FILES_LIST_MAX, description="Maximum entries to return"),
    ) -> dict[str, Any]:
        """List files and directories with optional filtering."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = app_ctx.file_ops.list_files(
            path=path,
            pattern=pattern,
            recursive=recursive,
            include_hidden=include_hidden,
            include_metadata=include_metadata,
            file_type=file_type,
            limit=limit,
        )

        return {
            "path": result.path,
            "entries": [
                {
                    "name": e.name,
                    "path": e.path,
                    "type": e.type,
                    **(
                        {"size": e.size, "modified_at": e.modified_at}
                        if include_metadata and e.type == "file"
                        else {}
                    ),
                }
                for e in result.entries
            ],
            "total": result.total,
            "truncated": result.truncated,
            "summary": _summarize_list(result.path, result.total, result.truncated),
        }

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
