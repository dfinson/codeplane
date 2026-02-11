"""Files MCP tools - read_files, list_files handlers."""

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.config.constants import FILES_LIST_MAX

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


# Backward compatibility alias
RangeParam = FileTarget


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
        paths: list[str] = Field(..., description="File paths relative to repo root"),
        targets: list[FileTarget] | None = Field(
            None,
            description=(
                "Optional file targets with line ranges. Each target specifies a file path "
                "and optional start_line/end_line to read a subset of the file."
            ),
        ),
        include_metadata: bool = Field(False, description="Include file stats (size, mtime)"),
    ) -> dict[str, Any]:
        """Read file contents with optional line ranges."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        # Build target map keyed by path.  FileTarget guarantees path is always
        # set, so we never get the old "" key-mismatch bug.
        target_map: dict[str, tuple[int, int]] = {}
        if targets:
            for t in targets:
                # Ensure the target's file is in paths so it gets read.
                if t.path not in paths:
                    paths.append(t.path)
                if t.start_line is not None and t.end_line is not None:
                    target_map[t.path] = (t.start_line, t.end_line)

        result = app_ctx.file_ops.read_files(
            paths,
            targets=target_map,
            include_metadata=include_metadata,
        )

        files = [
            {
                "path": f.path,
                "content": f.content,
                "language": f.language,
                "line_count": f.line_count,
                "range": f.range,
                "metadata": f.metadata,
            }
            for f in result.files
        ]

        not_found = len(paths) - len(files)
        return {
            "files": files,
            "summary": _summarize_read(files, not_found),
        }

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
