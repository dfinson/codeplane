"""Refactor MCP tools - refactor.* handlers."""

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
    """Parameters for refactor.rename."""

    symbol: str  # Symbol name or path:line:col locator
    new_name: str
    include_comments: bool = True
    contexts: list[str] | None = None


class RefactorMoveParams(BaseParams):
    """Parameters for refactor.move."""

    from_path: str
    to_path: str
    include_comments: bool = True


class RefactorDeleteParams(BaseParams):
    """Parameters for refactor.delete."""

    target: str  # Symbol or path
    include_comments: bool = True


class RefactorApplyParams(BaseParams):
    """Parameters for refactor.apply."""

    refactor_id: str


class RefactorCancelParams(BaseParams):
    """Parameters for refactor.cancel."""

    refactor_id: str


class RefactorInspectParams(BaseParams):
    """Parameters for refactor.inspect."""

    refactor_id: str
    path: str
    context_lines: int = 2


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_refactor(status: str, files_affected: int, preview: Any) -> str:
    """Generate summary for refactor operations."""
    if status == "cancelled":
        return "refactoring cancelled"
    if status == "applied":
        return f"applied to {files_affected} files"
    if status == "pending" and preview:
        high = preview.high_certainty_count or 0
        med = preview.medium_certainty_count or 0
        low = preview.low_certainty_count or 0
        total = high + med + low
        parts = [f"preview: {total} changes in {files_affected} files"]
        if low:
            parts.append(f"({low} need review)")
        return " ".join(parts)
    return status


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("refactor.rename", "Rename a symbol across the codebase", RefactorRenameParams)
async def refactor_rename(ctx: AppContext, params: RefactorRenameParams) -> dict[str, Any]:
    """Rename symbol with certainty-scored candidates."""
    result = await ctx.refactor_ops.rename(
        params.symbol,
        params.new_name,
        _include_comments=params.include_comments,
        _contexts=params.contexts,
    )

    return _serialize_refactor_result(result)


@registry.register("refactor.move", "Move a file/module, updating imports", RefactorMoveParams)
async def refactor_move(ctx: AppContext, params: RefactorMoveParams) -> dict[str, Any]:
    """Move file/module and update all import references."""
    result = await ctx.refactor_ops.move(
        params.from_path,
        params.to_path,
        include_comments=params.include_comments,
    )
    return _serialize_refactor_result(result)


@registry.register(
    "refactor.delete",
    "Find all references to a symbol/file for manual cleanup",
    RefactorDeleteParams,
)
async def refactor_delete(ctx: AppContext, params: RefactorDeleteParams) -> dict[str, Any]:
    """Find references that need cleanup when deleting.

    Unlike rename/move, this does NOT auto-remove references.
    It surfaces them for manual cleanup since deletion semantics vary.
    """
    result = await ctx.refactor_ops.delete(
        params.target,
        include_comments=params.include_comments,
    )
    return _serialize_refactor_result(result)


@registry.register("refactor.apply", "Apply a previewed refactoring", RefactorApplyParams)
async def refactor_apply(ctx: AppContext, params: RefactorApplyParams) -> dict[str, Any]:
    """Apply pending refactor."""
    result = await ctx.refactor_ops.apply(params.refactor_id, ctx.mutation_ops)
    return _serialize_refactor_result(result)


@registry.register("refactor.cancel", "Cancel a pending refactoring", RefactorCancelParams)
async def refactor_cancel(ctx: AppContext, params: RefactorCancelParams) -> dict[str, Any]:
    """Cancel pending refactor."""
    result = await ctx.refactor_ops.cancel(params.refactor_id)
    return _serialize_refactor_result(result)


@registry.register(
    "refactor.inspect",
    "Inspect low-certainty matches in a file with context",
    RefactorInspectParams,
)
async def refactor_inspect(ctx: AppContext, params: RefactorInspectParams) -> dict[str, Any]:
    """Inspect low-certainty matches before applying.

    Returns snippets with surrounding context for verification.
    Use this to check if lexical matches are true references or false positives.
    """
    result = await ctx.refactor_ops.inspect(
        params.refactor_id,
        params.path,
        context_lines=params.context_lines,
    )
    return {
        "path": result.path,
        "matches": result.matches,
        "summary": f"{len(result.matches)} matches in {result.path}",
    }


def _serialize_refactor_result(result: RefactorResult) -> dict[str, Any]:
    """Convert RefactorResult to dict."""
    files_affected = result.preview.files_affected if result.preview else 0
    output: dict[str, Any] = {
        "refactor_id": result.refactor_id,
        "status": result.status,
        "summary": _summarize_refactor(result.status, files_affected, result.preview),
    }

    if result.preview:
        preview_dict: dict[str, Any] = {
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
        # Add verification fields if present
        if result.preview.verification_required:
            preview_dict["verification_required"] = True
            preview_dict["low_certainty_files"] = result.preview.low_certainty_files
            preview_dict["verification_guidance"] = result.preview.verification_guidance
        output["preview"] = preview_dict

    if result.divergence:
        output["divergence"] = {
            "conflicting_hunks": result.divergence.conflicting_hunks,
            "resolution_options": result.divergence.resolution_options,
        }

    return output
