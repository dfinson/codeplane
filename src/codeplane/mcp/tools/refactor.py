"""Refactor MCP tools - refactor_* handlers."""

from typing import TYPE_CHECKING, Any

from fastmcp import Context
from pydantic import Field

from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.session import _MAX_EDIT_BATCHES

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext
    from codeplane.refactor.ops import RefactorResult


_MIN_JUSTIFICATION_CHARS = 50


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


def _display_refactor(status: str, files_affected: int, preview: Any, refactor_id: str) -> str:
    """Human-friendly message for refactor operations."""
    if status == "cancelled":
        return "Refactoring cancelled."
    if status == "applied":
        return f"Refactoring applied: {files_affected} files modified."
    if status == "pending" and preview:
        high = preview.high_certainty_count or 0
        low = preview.low_certainty_count or 0
        total = high + (preview.medium_certainty_count or 0) + low
        if low > 0:
            return f"Preview ready: {total} changes in {files_affected} files ({low} require review). Refactor ID: {refactor_id}"
        return (
            f"Preview ready: {total} changes in {files_affected} files. Refactor ID: {refactor_id}"
        )
    return f"Refactoring {status}."


def _serialize_refactor_result(result: "RefactorResult") -> dict[str, Any]:
    """Convert RefactorResult to dict."""
    # Get files_affected from preview or applied delta
    if result.preview:
        files_affected = result.preview.files_affected
    elif result.applied:
        files_affected = result.applied.files_changed
    else:
        files_affected = 0

    output: dict[str, Any] = {
        "refactor_id": result.refactor_id,
        "status": result.status,
        "summary": _summarize_refactor(result.status, files_affected, result.preview),
        "display_to_user": _display_refactor(
            result.status, files_affected, result.preview, result.refactor_id
        ),
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
            # Convert low_certainty_files to low_certainty_matches with span info
            low_matches = []
            for fe in result.preview.edits:
                for h in fe.hunks:
                    if h.certainty == "low":
                        # Compute end_line from old content line count
                        old_lines = h.old.count("\n") + 1 if h.old else 1
                        low_matches.append(
                            {
                                "path": fe.path,
                                "span": {"start_line": h.line, "end_line": h.line + old_lines - 1},
                                "certainty": h.certainty,
                                "match_text": h.old[:80] if h.old else "",
                            }
                        )
            preview_dict["verification_guidance"] = result.preview.verification_guidance
            if low_matches:
                preview_dict["low_certainty_matches"] = low_matches
        output["preview"] = preview_dict

    if result.divergence:
        output["divergence"] = {
            "conflicting_hunks": result.divergence.conflicting_hunks,
            "resolution_options": result.divergence.resolution_options,
        }
    # Include warning if present (e.g., path:line:col format detected)
    if result.warning:
        output["warning"] = result.warning

    from codeplane.mcp.delivery import wrap_response

    return wrap_response(output, resource_kind="refactor_preview")


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register refactor tools with FastMCP server."""

    def _require_recon_and_justification(session: Any, justification: str | None) -> None:
        """Gate: recon must have been called + justification required."""
        if not session.candidate_maps:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="Recon required before refactoring.",
                remediation=(
                    'Call recon(task="...") first to discover files, then use refactor tools.'
                ),
            )
        if not justification or len(justification.strip()) < _MIN_JUSTIFICATION_CHARS:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"justification must be at least {_MIN_JUSTIFICATION_CHARS} "
                    f"characters (got {len(justification.strip()) if justification else 0})."
                ),
                remediation=("Explain what you are renaming/moving/analyzing and why."),
            )

    @mcp.tool(
        annotations={
            "title": "Rename: cross-file symbol rename",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_rename(
        ctx: Context,
        symbol: str = Field(
            ...,
            description="Symbol name to rename (e.g., 'MyClass', 'my_function'). Do NOT use path:line:col format.",
        ),
        new_name: str = Field(..., description="New name for the symbol"),
        justification: str = Field(
            ...,
            description=("Explain what you are renaming and why (50+ chars)."),
        ),
        include_comments: bool = Field(True, description="Include comment references"),
        contexts: list[str] | None = Field(None, description="Limit to specific contexts"),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Rename a symbol across the codebase."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        _require_recon_and_justification(session, justification)

        result = await app_ctx.refactor_ops.rename(
            symbol,
            new_name,
            _include_comments=include_comments,
            _contexts=contexts,
        )
        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Move: relocate file with import updates",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_move(
        ctx: Context,
        from_path: str = Field(..., description="Source file path"),
        to_path: str = Field(..., description="Destination file path"),
        justification: str = Field(
            ...,
            description=("Explain what you are moving and why (50+ chars)."),
        ),
        include_comments: bool = Field(True, description="Include comment references"),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Move a file/module, updating imports."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        _require_recon_and_justification(session, justification)

        result = await app_ctx.refactor_ops.move(
            from_path,
            to_path,
            include_comments=include_comments,
        )
        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Impact: reference analysis before removal",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_impact(
        ctx: Context,
        target: str = Field(..., description="Symbol or path to analyze for impact"),
        justification: str = Field(
            ...,
            description=("Explain what you are analyzing and why (50+ chars)."),
        ),
        include_comments: bool = Field(True, description="Include comment references"),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Find all references to a symbol/file for impact analysis before removal."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        _require_recon_and_justification(session, justification)

        result = await app_ctx.refactor_ops.impact(
            target,
            include_comments=include_comments,
        )
        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Commit: apply or inspect refactoring preview",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def refactor_commit(
        ctx: Context,
        refactor_id: str = Field(..., description="ID of the refactoring to apply or inspect"),
        inspect_path: str | None = Field(
            None,
            description=(
                "If provided, inspect low-certainty matches in this file "
                "instead of applying. Returns match details with context."
            ),
        ),
        context_lines: int = Field(
            2,
            description="Lines of context around matches (only used with inspect_path).",
        ),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Apply a previewed refactoring, or inspect low-certainty matches.

        Without inspect_path: applies the refactoring (like the old refactor_apply).
        With inspect_path: inspects matches in that file (like the old refactor_inspect).
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        if inspect_path is not None:
            # Inspect mode
            inspect_result = await app_ctx.refactor_ops.inspect(
                refactor_id,
                inspect_path,
                context_lines=context_lines,
            )
            from codeplane.core.formatting import compress_path

            return {
                "path": inspect_result.path,
                "matches": inspect_result.matches,
                "summary": (
                    f"{len(inspect_result.matches)} matches in "
                    f"{compress_path(inspect_result.path, 35)}"
                ),
            }

        # Apply mode — counts as an edit batch
        if session.edits_since_checkpoint >= _MAX_EDIT_BATCHES:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"Edit batch limit reached ({_MAX_EDIT_BATCHES} batches "
                    "since last checkpoint). Checkpoint is required."
                ),
                remediation=(
                    'Call checkpoint(changed_files=[...], commit_message="...") '
                    "to lint, test, and commit before applying refactorings."
                ),
            )

        result = await app_ctx.refactor_ops.apply(refactor_id, app_ctx.mutation_ops)
        session.edits_since_checkpoint += 1

        # Reset scope budget duplicate tracking after mutation
        if scope_id:
            from codeplane.mcp.tools.files import _scope_manager

            _scope_manager.record_mutation(scope_id)

        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Cancel: discard refactoring preview",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_cancel(
        ctx: Context,
        refactor_id: str = Field(..., description="ID of the refactoring to cancel"),
        gate_token: str | None = Field(
            None,
            description="Gate confirmation token from a previous gate block.",
        ),
        gate_reason: str | None = Field(
            None,
            description="Justification for passing the gate (min chars per gate spec).",
        ),
    ) -> dict[str, Any]:
        """Cancel a pending refactoring."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.refactor_ops.cancel(refactor_id)
        return _serialize_refactor_result(result)
