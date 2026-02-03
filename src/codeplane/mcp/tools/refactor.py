"""Refactor MCP tool - unified refactoring handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext
    from codeplane.refactor.ops import RefactorResult


# =============================================================================
# Parameter Model
# =============================================================================


class RefactorParams(BaseParams):
    """Parameters for refactor tool."""

    action: Literal["rename", "move", "delete", "apply", "cancel", "inspect"]

    # rename params
    symbol: str | None = None  # Symbol name or path:line:col locator
    new_name: str | None = None

    # move params
    from_path: str | None = None
    to_path: str | None = None

    # delete params
    target: str | None = None  # Symbol or path

    # apply/cancel/inspect params
    refactor_id: str | None = None

    # inspect params
    path: str | None = None
    context_lines: int = 2

    # shared options
    include_comments: bool = True
    contexts: list[str] | None = None


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
# Tool Handler
# =============================================================================


@registry.register(
    "refactor",
    "Refactoring operations: rename symbols, move files, find references for deletion",
    RefactorParams,
)
async def refactor(ctx: AppContext, params: RefactorParams) -> dict[str, Any]:
    """Unified refactoring tool.

    Actions:
    - rename: Rename a symbol across the codebase
    - move: Move a file/module, updating imports
    - delete: Find all references for manual cleanup
    - apply: Apply a previewed refactoring
    - cancel: Cancel a pending refactoring
    - inspect: Inspect low-certainty matches with context
    """
    if params.action == "rename":
        if not params.symbol or not params.new_name:
            return {
                "error": "rename requires 'symbol' and 'new_name'",
                "summary": "error: missing params",
            }
        result = await ctx.refactor_ops.rename(
            params.symbol,
            params.new_name,
            _include_comments=params.include_comments,
            _contexts=params.contexts,
        )
        return _serialize_refactor_result(result)

    if params.action == "move":
        if not params.from_path or not params.to_path:
            return {
                "error": "move requires 'from_path' and 'to_path'",
                "summary": "error: missing params",
            }
        result = await ctx.refactor_ops.move(
            params.from_path,
            params.to_path,
            include_comments=params.include_comments,
        )
        return _serialize_refactor_result(result)

    if params.action == "delete":
        if not params.target:
            return {"error": "delete requires 'target'", "summary": "error: missing params"}
        result = await ctx.refactor_ops.delete(
            params.target,
            include_comments=params.include_comments,
        )
        return _serialize_refactor_result(result)

    if params.action == "apply":
        if not params.refactor_id:
            return {"error": "apply requires 'refactor_id'", "summary": "error: missing params"}
        result = await ctx.refactor_ops.apply(params.refactor_id, ctx.mutation_ops)
        return _serialize_refactor_result(result)

    if params.action == "cancel":
        if not params.refactor_id:
            return {"error": "cancel requires 'refactor_id'", "summary": "error: missing params"}
        result = await ctx.refactor_ops.cancel(params.refactor_id)
        return _serialize_refactor_result(result)

    if params.action == "inspect":
        if not params.refactor_id or not params.path:
            return {
                "error": "inspect requires 'refactor_id' and 'path'",
                "summary": "error: missing params",
            }
        insp = await ctx.refactor_ops.inspect(
            params.refactor_id,
            params.path,
            context_lines=params.context_lines,
        )
        return {
            "path": insp.path,
            "matches": insp.matches,
            "summary": f"{len(insp.matches)} matches in {insp.path}",
        }

    return {"error": f"unknown action: {params.action}", "summary": "error: unknown action"}


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
