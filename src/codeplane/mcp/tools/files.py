"""Files MCP tools - read_files, list_files handlers."""

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
        None, description="File path this range applies to (optional if single file)"
    )
    start: int = Field(..., gt=0, description="Start line (1-indexed, inclusive)")
    end: int = Field(..., gt=0, description="End line (1-indexed, inclusive)")

    @model_validator(mode="after")
    def validate_range(self) -> RangeParam:
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) must be >= start ({self.start})")
        return self


class ReadFilesParams(BaseParams):
    """Parameters for read_files."""

    paths: list[str] = Field(..., description="File paths relative to repo root")
    ranges: list[RangeParam] | None = Field(
        None,
        description="Optional line ranges. Use 'start' and 'end' (1-indexed, inclusive).",
    )
    include_metadata: bool = Field(False, description="Include file stats (size, mtime)")


class ListFilesParams(BaseParams):
    """Parameters for list_files."""

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
# Tool Handlers
# =============================================================================


@registry.register("read_files", "Read file contents with optional line ranges", ReadFilesParams)
async def read_files(ctx: AppContext, params: ReadFilesParams) -> dict[str, Any]:
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

    return {
        "files": [
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
    }


@registry.register(
    "list_files", "List files in a directory with optional filtering", ListFilesParams
)
async def list_files(ctx: AppContext, params: ListFilesParams) -> dict[str, Any]:
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
    }
