"""Files MCP tools - read_files handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from codeplane.mcp.registry import registry

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class ReadFilesParams(BaseModel):
    """Parameters for read_files."""

    paths: list[str]
    ranges: list[dict[str, int]] | None = (
        None  # [{"path": str, "start_line": int, "end_line": int}]
    )
    include_metadata: bool = False


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("read_files", "Read file contents with optional line ranges", ReadFilesParams)
async def read_files(ctx: AppContext, params: ReadFilesParams) -> dict[str, Any]:
    """Read file contents."""
    result = ctx.file_ops.read_files(
        params.paths,
        ranges=params.ranges,
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
