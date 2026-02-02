"""Refactor MCP tools - refactor_* handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext
    from codeplane.refactor.ops import RefactorResult


# =============================================================================
# Parameter Models
# =============================================================================


class RefactorRenameParams(BaseParams):
    """Parameters for refactor_rename."""

    symbol: str  # Symbol name or path:line:col locator
    new_name: str
    include_comments: bool = True
    contexts: list[str] | None = None


class RefactorMoveParams(BaseParams):
    """Parameters for refactor_move."""

    from_path: str
    to_path: str
    include_comments: bool = True


class RefactorDeleteParams(BaseParams):
    """Parameters for refactor_delete."""

    target: str  # Symbol or path
    include_comments: bool = True


class RefactorApplyParams(BaseParams):
    """Parameters for refactor_apply."""

    refactor_id: str


class RefactorCancelParams(BaseParams):
    """Parameters for refactor_cancel."""

    refactor_id: str


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("refactor_rename", "Rename a symbol across the codebase", RefactorRenameParams)
async def refactor_rename(ctx: AppContext, params: RefactorRenameParams) -> dict[str, Any]:
    """Rename symbol with certainty-scored candidates."""
    result = await ctx.refactor_ops.rename(
        params.symbol,
        params.new_name,
        _include_comments=params.include_comments,
        _contexts=params.contexts,
    )

    return _serialize_refactor_result(result)


# TODO: Implement RefactorOps.move() before enabling
# @registry.register("refactor_move", "Move a file/module, updating imports", RefactorMoveParams)
# async def refactor_move(ctx: AppContext, params: RefactorMoveParams) -> dict[str, Any]:
#     """Move file/module."""
#     result = await ctx.refactor_ops.move(
#         params.from_path,
#         params.to_path,
#         include_comments=params.include_comments,
#     )
#
#     return _serialize_refactor_result(result)


# TODO: Implement RefactorOps.delete() before enabling
# @registry.register(
#     "refactor_delete", "Delete symbol/file, cleaning up references", RefactorDeleteParams
# )
# async def refactor_delete(ctx: AppContext, params: RefactorDeleteParams) -> dict[str, Any]:
#     """Delete with reference cleanup."""
#     result = await ctx.refactor_ops.delete(
#         params.target,
#         include_comments=params.include_comments,
#     )
#     result = await ctx.refactor_ops.apply(params.refactor_id, ctx.mutation_ops)
#     return _serialize_refactor_result(result)


@registry.register("refactor_apply", "Apply a previewed refactoring", RefactorApplyParams)
async def refactor_apply(ctx: AppContext, params: RefactorApplyParams) -> dict[str, Any]:
    """Apply pending refactor."""
    result = await ctx.refactor_ops.apply(params.refactor_id, ctx.mutation_ops)
    return _serialize_refactor_result(result)


@registry.register("refactor_cancel", "Cancel a pending refactoring", RefactorCancelParams)
async def refactor_cancel(ctx: AppContext, params: RefactorCancelParams) -> dict[str, Any]:
    """Cancel pending refactor."""
    result = await ctx.refactor_ops.cancel(params.refactor_id)
    return _serialize_refactor_result(result)


def _serialize_refactor_result(result: RefactorResult) -> dict[str, Any]:
    """Convert RefactorResult to dict."""
    output: dict[str, Any] = {
        "refactor_id": result.refactor_id,
        "status": result.status,
    }

    if result.preview:
        output["preview"] = {
            "files_affected": result.preview.files_affected,
            "high_certainty_count": result.preview.high_certainty_count,
            "medium_certainty_count": result.preview.medium_certainty_count,
            "low_certainty_count": result.preview.low_certainty_count,
            "edits": [
                {
                    "path": fe.path,
                    "hunks": [
                        {
                            "old": h.old,
                            "new": h.new,
                            "line": h.line,
                            "certainty": h.certainty,
                        }
                        for h in fe.hunks
                    ],
                }
                for fe in result.preview.edits
            ],
        }

    if result.divergence:
        output["divergence"] = {
            "conflicting_hunks": result.divergence.conflicting_hunks,
            "resolution_options": result.divergence.resolution_options,
        }

    return output
