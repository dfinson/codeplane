"""Mutation MCP tools - mutate handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class RangeParam(BaseModel):
    start: int
    end: int


class PatchParam(BaseModel):
    """A line-level patch."""

    range: RangeParam
    replacement: str


class EditParam(BaseModel):
    """A single file edit."""

    path: str
    action: Literal["create", "update", "delete"]
    content: str | None = None
    patches: list[PatchParam] | None = None


class MutateParams(BaseParams):
    """Parameters for mutate."""

    edits: list[EditParam]
    dry_run: bool = False


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("mutate", "Atomic file edits with structured delta response", MutateParams)
async def mutate(ctx: AppContext, params: MutateParams) -> dict[str, Any]:
    """Apply atomic file edits."""
    from codeplane.mutation.ops import Edit, Patch

    # Convert params to ops types
    edits = []
    for e in params.edits:
        patches = None
        if e.patches:
            patches = [
                Patch(start=p.range.start, end=p.range.end, replacement=p.replacement)
                for p in e.patches
            ]
        edits.append(
            Edit(
                path=e.path,
                action=e.action,
                content=e.content,
                patches=patches,
            )
        )

    result = ctx.mutation_ops.mutate(edits, dry_run=params.dry_run)

    return {
        "applied": result.applied,
        "dry_run": result.dry_run,
        "delta": {
            "mutation_id": result.delta.mutation_id,
            "files_changed": result.delta.files_changed,
            "insertions": result.delta.insertions,
            "deletions": result.delta.deletions,
            "files": [
                {
                    "path": f.path,
                    "action": f.action,
                    "old_hash": f.old_hash,
                    "new_hash": f.new_hash,
                    "insertions": f.insertions,
                    "deletions": f.deletions,
                }
                for f in result.delta.files
            ],
        },
    }
