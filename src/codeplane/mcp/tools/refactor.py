"""Refactor MCP tools - refactor_* handlers."""

import uuid
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from pydantic import Field

from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.session import (
    _MAX_EDIT_BATCHES,
    _MAX_PLAN_TARGETS,
    EditTicket,
    RefactorPlan,
)

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
    if status in ("pending", "previewed") and preview:
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
    if status in ("pending", "previewed") and preview:
        high = preview.high_certainty_count or 0
        low = preview.low_certainty_count or 0
        total = high + (preview.medium_certainty_count or 0) + low
        if low > 0:
            return f"Preview ready: {total} changes in {files_affected} files ({low} require review). Refactor ID: {refactor_id}"
        return (
            f"Preview ready: {total} changes in {files_affected} files. Refactor ID: {refactor_id}"
        )
    return f"Refactoring {status}."


def _build_refactor_agentic_hint(result: "RefactorResult", files_affected: int) -> str:
    """Build next-step instruction for the agent after a refactor operation."""
    rid = result.refactor_id

    if result.status == "cancelled":
        return "Refactoring cancelled. No further action needed."

    if result.status == "applied":
        changed = []
        if result.applied:
            changed = [fd.path for fd in result.applied.files]
        files_str = ", ".join(changed[:5]) if changed else f"{files_affected} file(s)"
        if len(changed) > 5:
            files_str += f" (+{len(changed) - 5} more)"
        return (
            f"Refactoring applied to {files_str}.\n"
            f"NEXT: call checkpoint(changed_files=[{', '.join(repr(p) for p in changed[:5])}], "
            'commit_message="...") to lint, test, and commit.\n'
            "Ask the user whether they want push=True or push=False."
        )

    if result.status in ("pending", "previewed") and result.preview:
        low = result.preview.low_certainty_count or 0
        parts = [f"Preview ready: {files_affected} file(s). Refactor ID: {rid}\n"]
        if low > 0:
            low_files = sorted(
                {fe.path for fe in result.preview.edits for h in fe.hunks if h.certainty == "low"}
            )
            parts.append(
                f"{low} low-certainty match(es) require review.\n"
                f'INSPECT: refactor_commit(refactor_id="{rid}", '
                f'inspect_path="{low_files[0] if low_files else "<path>"}")\n'
            )
        parts.append(
            f'APPLY: refactor_commit(refactor_id="{rid}")\n'
            f'CANCEL: refactor_cancel(refactor_id="{rid}")'
        )
        return "".join(parts)

    return f"Refactoring status: {result.status}."


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

    # ── Agentic hint — next-step instruction ──
    output["agentic_hint"] = _build_refactor_agentic_hint(result, files_affected)

    from codeplane.mcp.delivery import wrap_response

    return wrap_response(output, resource_kind="refactor_preview")


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register refactor tools with FastMCP server."""

    def _require_recon_and_justification(session: Any, justification: str | None) -> None:
        """Gate: recon must have been called + justification required + not read-only."""
        if getattr(session, "read_only", None) is True:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="Session is read-only — refactor tools are blocked.",
                remediation=(
                    "This session was started with recon(read_only=True). "
                    'Call recon(read_only=False, task="...") to start a '
                    "read-write session before refactoring."
                ),
            )
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
            "title": "Plan: declare edit targets and call budget",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def refactor_plan(
        ctx: Context,
        edit_targets: list[str] = Field(
            ...,
            description=(
                "candidate_id values of files you will edit. "
                "Must have been resolved via recon_resolve first."
            ),
        ),
        description: str = Field(
            ...,
            description=("Describe the edit plan: what changes you will make and why (50+ chars)."),
        ),
        expected_edit_calls: int = Field(
            1,
            ge=1,
            description=(
                "Number of refactor_edit calls you need. Default 1. "
                "Batch ALL edits into a single call unless impossible."
            ),
        ),
        batch_justification: str | None = Field(
            None,
            description=(
                "REQUIRED if expected_edit_calls > 1. Explain why edits "
                "cannot be batched into a single refactor_edit call "
                "(100+ chars)."
            ),
        ),
        recon_id: str | None = Field(
            None,
            description=(
                "recon_id from a prior recon call. If omitted, uses the most recent recon session."
            ),
        ),
    ) -> dict[str, Any]:
        """Declare your edit set and call budget before editing.

        Call this AFTER recon_resolve to commit to your edit targets.
        This mints edit tickets required by refactor_edit.

        DEFAULT: expected_edit_calls=1. You MUST batch all edits into
        a single refactor_edit call.  If you need multiple calls, you
        MUST provide batch_justification (100+ chars) explaining why
        batching into one call is impossible.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        # ── Read-only gate ──
        if getattr(session, "read_only", None) is True:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="Session is read-only — refactor_plan is blocked.",
                remediation=(
                    'Call recon(read_only=False, task="...") to start a read-write session.'
                ),
            )

        # ── Existing plan gate ──
        if session.active_plan is not None:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"Active plan already exists (plan_id="
                    f"{session.active_plan.plan_id}). "
                    "Checkpoint or complete the current plan first."
                ),
                remediation=(
                    "Call checkpoint(changed_files=[...]) to complete the "
                    "current plan, then create a new plan."
                ),
            )

        # ── Validate description ──
        if not description or len(description.strip()) < _MIN_JUSTIFICATION_CHARS:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"description must be at least {_MIN_JUSTIFICATION_CHARS} "
                    f"characters (got {len(description.strip()) if description else 0})."
                ),
                remediation="Describe what changes you plan to make and why.",
            )

        # ── Validate target count ──
        if not edit_targets:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="edit_targets must not be empty.",
                remediation="List the candidate_id values of files you will edit.",
            )
        if len(edit_targets) > _MAX_PLAN_TARGETS:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"Too many edit targets ({len(edit_targets)}). Max is {_MAX_PLAN_TARGETS}."
                ),
                remediation=f"Limit to {_MAX_PLAN_TARGETS} files per plan.",
            )

        # ── Validate batching justification ──
        if expected_edit_calls > 1 and (
            not batch_justification or len(batch_justification.strip()) < 100
        ):
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    "batch_justification (100+ chars) is required when "
                    f"expected_edit_calls > 1 (got {expected_edit_calls})."
                ),
                remediation=(
                    "Explain WHY you cannot batch all edits into a single "
                    "refactor_edit call. Default is 1 — maximize batching."
                ),
            )

        # ── Resolve candidate_ids → paths ──
        if not session.candidate_maps:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="No recon data found. Call recon first.",
                remediation=(
                    'Call recon(task="...") first, then recon_resolve, then refactor_plan.'
                ),
            )

        # Pick the recon to use
        if recon_id:
            if recon_id not in session.candidate_maps:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=f"Unknown recon_id '{recon_id}'.",
                    remediation="Use a recon_id from a prior recon call.",
                )
            selected_recon_id = recon_id
        else:
            selected_recon_id = list(session.candidate_maps.keys())[-1]

        # Merge all candidate maps for lookup
        id_to_path: dict[str, str] = {}
        for cmap in session.candidate_maps.values():
            id_to_path.update(cmap)

        # Get resolved files (path → sha256)
        resolved_files: dict[str, str] = session.counters.get(  # type: ignore[assignment]
            "resolved_files",
            {},
        )

        # Map targets and mint tickets
        target_paths: dict[str, str] = {}
        minted_tickets: dict[str, EditTicket] = {}
        ticket_list: list[dict[str, str]] = []

        for cid in edit_targets:
            path = id_to_path.get(cid)
            if path is None:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=(f"Unknown candidate_id '{cid}'. Not found in any recon output."),
                    remediation=(
                        "Use candidate_id values from recon scaffold_files or lite_files."
                    ),
                )
            sha256 = resolved_files.get(path)
            if sha256 is None:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=(f"File '{path}' (candidate {cid}) has not been resolved."),
                    remediation=(
                        "Call recon_resolve with this candidate_id first "
                        "to fetch content and sha256, then include it in "
                        "the plan."
                    ),
                )
            target_paths[cid] = path
            ticket_id = f"{cid}:{sha256[:8]}"
            ticket = EditTicket(
                ticket_id=ticket_id,
                path=path,
                sha256=sha256,
                candidate_id=cid,
                issued_by="plan",
            )
            minted_tickets[ticket_id] = ticket
            ticket_list.append(
                {
                    "candidate_id": cid,
                    "path": path,
                    "edit_ticket": ticket_id,
                },
            )

        # ── Create plan ──
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        plan = RefactorPlan(
            plan_id=plan_id,
            recon_id=selected_recon_id,
            description=description.strip(),
            expected_edit_calls=expected_edit_calls,
            batch_justification=(batch_justification.strip() if batch_justification else None),
            edit_targets=target_paths,
            edit_tickets=minted_tickets,
        )

        # Store plan and tickets on session
        session.active_plan = plan
        session.edit_tickets.update(minted_tickets)

        # ── Build response ──
        if expected_edit_calls == 1:
            budget_note = "Budget: 1 refactor_edit call. Batch ALL your edits into a single call."
        else:
            budget_note = (
                f"Budget: {expected_edit_calls} refactor_edit call(s). "
                "Batch edits into as few calls as possible."
            )

        # ── Build plan-scoped context reminder ──
        context_lines: list[str] = []
        try:
            if session.resolved_paths:
                plan_paths = set(target_paths.values())
                resolved_but_not_planned = {
                    p for p in session.resolved_paths if p not in plan_paths
                }
                if resolved_but_not_planned:
                    extra = ", ".join(sorted(resolved_but_not_planned))
                    context_lines.append(
                        f"Also resolved (not in edit set): {extra}. "
                        "Use content from prior resolve — do NOT re-read."
                    )
        except Exception:  # noqa: BLE001
            pass
        context_str = "\n".join(context_lines)

        agentic_hint = (
            f"Plan created: {plan_id}\n"
            f"{len(ticket_list)} edit ticket(s) minted for "
            f"{len(target_paths)} file(s).\n"
            f"{budget_note}\n\n"
        )
        if context_str:
            agentic_hint += context_str + "\n\n"
        agentic_hint += (
            f'NEXT: refactor_edit(plan_id="{plan_id}", edits=[...]) — '
            "include ALL your find-and-replace edits in one call.\n"
            "After editing → checkpoint(changed_files=[...], "
            'commit_message="...")'
        )

        from codeplane.mcp.delivery import wrap_response

        return wrap_response(
            {
                "plan_id": plan_id,
                "edit_targets": ticket_list,
                "expected_edit_calls": expected_edit_calls,
                "agentic_hint": agentic_hint,
            },
            resource_kind="refactor_plan",
            session_id=ctx.session_id,
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
        session.mutation_ctx.pending_refactors[result.refactor_id] = "rename"
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
        session.mutation_ctx.pending_refactors[result.refactor_id] = "move"
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
        session.mutation_ctx.pending_refactors[result.refactor_id] = "impact"
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
        if session.mutation_ctx.mutations_since_checkpoint >= _MAX_EDIT_BATCHES:
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
        session.mutation_ctx.mutations_since_checkpoint += 1
        session.mutation_ctx.pending_refactors.pop(refactor_id, None)

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
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.refactor_ops.cancel(refactor_id)
        session.mutation_ctx.pending_refactors.pop(refactor_id, None)
        return _serialize_refactor_result(result)
