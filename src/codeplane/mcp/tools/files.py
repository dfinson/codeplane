"""Files MCP tools - files.read, files.list handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class RangeParam(BaseModel):
    """Line range specification for partial file reads."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(
        None,
        description="File path this range applies to. Required when reading multiple files with different ranges.",
    )
    start: int = Field(..., gt=0, description="Start line (1-indexed, inclusive)")
    end: int = Field(..., gt=0, description="End line (1-indexed, inclusive)")

    @model_validator(mode="after")
    def validate_range(self) -> RangeParam:
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) must be >= start ({self.start})")
        return self


class ReadFilesParams(BaseParams):
    """Parameters for files.read."""

    paths: list[str] = Field(..., description="File paths relative to repo root")
    ranges: list[RangeParam] | None = Field(
        None,
        description=(
            "Optional line ranges per file. Each range can specify a 'path' to apply "
            "different ranges to different files in one call. Example: "
            "[{path: 'a.py', start: 1, end: 50}, {path: 'b.py', start: 100, end: 150}]"
        ),
    )
    include_metadata: bool = Field(False, description="Include file stats (size, mtime)")


class ListFilesParams(BaseParams):
    """Parameters for files.list."""

    path: str | None = Field(
        None, description="Directory path relative to repo root (default: repo root)"
    )
    pattern: str | None = Field(
        None, description="Glob pattern to filter (e.g., '*.py', '**/*.ts')"
    )
    recursive: bool = Field(False, description="Recurse into subdirectories")
    include_hidden: bool = Field(False, description="Include dotfiles and dotdirs")
    include_metadata: bool = Field(False, description="Include size and mtime for files")
    file_type: Literal["all", "file", "directory"] = Field(
        "all", description="Filter by entry type"
    )
    limit: int = Field(200, ge=1, le=1000, description="Maximum entries to return")


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_read(files: list[dict[str, Any]], not_found: int = 0) -> str:
    """Generate summary for files.read."""
    if not files and not_found:
        return f"{not_found} file(s) not found"

    total_lines = sum(f.get("line_count", 0) for f in files)
    paths = [f["path"] for f in files]

    if len(paths) == 1:
        rng = files[0].get("range")
        if rng:
            return f"1 file ({paths[0]}:{rng[0]}-{rng[1]}), {total_lines} lines"
        return f"1 file ({paths[0]}), {total_lines} lines"

    if len(paths) <= 3:
        path_list = ", ".join(paths)
    else:
        path_list = f"{paths[0]}, {paths[1]}, +{len(paths) - 2} more"

    suffix = f", {not_found} not found" if not_found else ""
    return f"{len(files)} files ({path_list}), {total_lines} lines{suffix}"


def _summarize_list(path: str, total: int, truncated: bool) -> str:
    """Generate summary for files.list."""
    loc = path or "repo root"
    trunc = " (truncated)" if truncated else ""
    return f"{total} entries in {loc}{trunc}"


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("files.read", "Read file contents with optional line ranges", ReadFilesParams)
async def files_read(ctx: AppContext, params: ReadFilesParams) -> dict[str, Any]:
    """Read file contents."""
    # Convert RangeParam models to dict format expected by FileOps
    ranges_dict = None
    if params.ranges:
        ranges_dict = [
            {"path": r.path or "", "start": r.start, "end": r.end} for r in params.ranges
        ]

    result = ctx.file_ops.read_files(
        params.paths,
        ranges=ranges_dict,
        include_metadata=params.include_metadata,
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

    not_found = len(params.paths) - len(files)

    return {
        "files": files,
        "summary": _summarize_read(files, not_found),
    }


@registry.register(
    "files.list", "List files in a directory with optional filtering", ListFilesParams
)
async def files_list(ctx: AppContext, params: ListFilesParams) -> dict[str, Any]:
    """List files and directories."""
    result = ctx.file_ops.list_files(
        path=params.path,
        pattern=params.pattern,
        recursive=params.recursive,
        include_hidden=params.include_hidden,
        include_metadata=params.include_metadata,
        file_type=params.file_type,
        limit=params.limit,
    )

    return {
        "path": result.path,
        "entries": [
            {
                "name": e.name,
                "path": e.path,
                "type": e.type,
                **(
                    {
                        "size": e.size,
                        "modified_at": e.modified_at,
                    }
                    if params.include_metadata and e.type == "file"
                    else {}
                ),
            }
            for e in result.entries
        ],
        "total": result.total,
        "truncated": result.truncated,
        "summary": _summarize_list(result.path, result.total, result.truncated),
    }
