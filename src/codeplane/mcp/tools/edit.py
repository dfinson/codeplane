"""Edit MCP tool — refactor_edit handler.

Find-and-replace file editing with sha256 locking.  This replaces
the old span-based ``write_source`` with a mechanism that does not
require agents to remember exact line numbers.

Resolution logic:
1. Exact match (1 occurrence) → replace in place
2. Multiple exact matches + span hint → disambiguate within range
3. Zero exact matches → fuzzy whitespace-normalized search within span or whole file
4. Multiple matches + no span → reject with match locations
"""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field

from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.ledger import get_ledger
from codeplane.mcp.session import _MAX_EDIT_BATCHES, EditTicket

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


# =============================================================================
# Parameter Models
# =============================================================================


class FindReplaceEdit(BaseModel):
    """A single find-and-replace edit.

    Primary: old_content + new_content (find-and-replace).
    Optional: start_line / end_line as disambiguation hints.
    For file creation: set old_content=None, new_content=<full body>.
    For file deletion: set old_content=None, new_content=None, delete=True.
    """

    model_config = ConfigDict(extra="forbid")

    edit_ticket: str | None = Field(
        None,
        description=(
            "Edit ticket from refactor_plan output. Required for updates. "
            "Not needed for file creates (old_content=None, new_content=body)."
        ),
    )
    path: str | None = Field(
        None,
        description=(
            "File path relative to repo root. For updates, this is derived "
            "from the edit_ticket — only provide for creates/deletes."
        ),
    )
    expected_file_sha256: str | None = Field(
        None,
        description=(
            "SHA256 of the file. For updates, this is derived from the "
            "edit_ticket — only provide for creates."
        ),
    )
    old_content: str | None = Field(
        None,
        description=(
            "The exact text to find and replace. If None and new_content "
            "is set, creates a new file. If None and delete=True, deletes the file."
        ),
    )
    new_content: str | None = Field(
        None,
        description=("The replacement text. For creates, this is the full file body."),
    )
    start_line: int | None = Field(
        None,
        gt=0,
        description=(
            "Optional start line hint (1-indexed). Used only to disambiguate "
            "when old_content matches multiple locations."
        ),
    )
    end_line: int | None = Field(
        None,
        gt=0,
        description=(
            "Optional end line hint (1-indexed, inclusive). Used only to "
            "disambiguate when old_content matches multiple locations."
        ),
    )
    delete: bool = Field(
        False,
        description="Set to True to delete this file. old_content and new_content must be None.",
    )


# =============================================================================
# Resolution Logic
# =============================================================================


def _find_all_occurrences(content: str, needle: str) -> list[int]:
    """Find all byte-offset positions where needle occurs in content."""
    positions: list[int] = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _offset_to_line(content: str, offset: int) -> int:
    """Convert a byte offset to a 1-based line number."""
    return content[:offset].count("\n") + 1


def _fuzzy_find(content: str, needle: str) -> list[int]:
    """Whitespace-normalized fuzzy search.

    Normalizes both content and needle by collapsing runs of whitespace,
    then maps matches back to original positions.
    """
    import re

    def normalize(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip())

    norm_needle = normalize(needle)
    if not norm_needle:
        return []

    norm_content = normalize(content)
    positions: list[int] = []
    start = 0
    while True:
        idx = norm_content.find(norm_needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _resolve_edit(
    content: str,
    old_content: str,
    new_content: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve a find-and-replace edit against file content.

    Returns:
        (new_file_content, metadata_dict)

    Raises:
        MCPError on ambiguous or failed matches.
    """
    # ── Step 1: exact match search ──
    positions = _find_all_occurrences(content, old_content)

    if len(positions) == 1:
        # Unambiguous — replace
        pos = positions[0]
        result = content[:pos] + new_content + content[pos + len(old_content) :]
        line = _offset_to_line(content, pos)
        return result, {"match_line": line, "match_kind": "exact"}

    if len(positions) > 1:
        # Multiple matches — try span disambiguation
        if start_line is not None:
            # Find the match whose line falls within the span hint
            for pos in positions:
                line = _offset_to_line(content, pos)
                span_end = end_line or (start_line + old_content.count("\n") + 5)
                if start_line <= line <= span_end:
                    result = content[:pos] + new_content + content[pos + len(old_content) :]
                    return result, {
                        "match_line": line,
                        "match_kind": "exact_span_disambiguated",
                    }

        # Still ambiguous — reject with locations
        match_lines = [_offset_to_line(content, p) for p in positions]
        raise MCPError(
            code=MCPErrorCode.AMBIGUOUS_MATCH,
            message=(
                f"old_content matches {len(positions)} locations "
                f"(lines {match_lines}). Add start_line/end_line "
                "to disambiguate."
            ),
            remediation=(
                "Provide start_line and end_line to narrow which "
                "occurrence to replace, or use a longer old_content "
                "snippet that is unique."
            ),
        )

    # ── Step 2: zero exact matches → fuzzy search ──
    fuzzy_positions = _fuzzy_find(content, old_content)
    if len(fuzzy_positions) == 1:
        # Fuzzy found one match — need to find the actual span
        # Re-search line by line with whitespace normalization
        import re

        def norm(s: str) -> str:
            return re.sub(r"\s+", " ", s.strip())

        needle_lines = old_content.splitlines()
        content_lines = content.splitlines(keepends=True)
        needle_norm = [norm(ln) for ln in needle_lines]

        for i in range(len(content_lines) - len(needle_lines) + 1):
            candidate = [norm(ln) for ln in content_lines[i : i + len(needle_lines)]]
            if candidate == needle_norm:
                # Found fuzzy match at line i
                original_text = "".join(content_lines[i : i + len(needle_lines)])
                result = content.replace(original_text, new_content, 1)
                return result, {
                    "match_line": i + 1,
                    "match_kind": "fuzzy_whitespace",
                }

    if len(fuzzy_positions) > 1:
        raise MCPError(
            code=MCPErrorCode.AMBIGUOUS_MATCH,
            message=(
                f"old_content has {len(fuzzy_positions)} fuzzy matches. "
                "Use a more specific snippet or add start_line/end_line."
            ),
            remediation=(
                "Provide start_line/end_line to narrow to one occurrence, "
                "or use a longer old_content snippet that is unique."
            ),
        )

    # ── No match at all ──
    # Show nearby content for context
    preview_lines = content.splitlines()[:20]
    preview = "\n".join(preview_lines)
    raise MCPError(
        code=MCPErrorCode.CONTENT_MISMATCH,
        message=(
            "old_content not found in file (exact or fuzzy). "
            "The file may have changed since you last read it."
        ),
        remediation=(
            "Re-read the file with recon_resolve to get current content "
            f"and sha256. File starts with:\n{preview}"
        ),
    )


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_edit(results: list[dict[str, Any]]) -> str:
    """Generate summary for refactor_edit."""
    from codeplane.core.formatting import compress_path

    if not results:
        return "no changes"
    if len(results) == 1:
        r = results[0]
        path = compress_path(r.get("path", ""), 35)
        return f"{r.get('action', 'updated')} {path}"

    actions: dict[str, int] = {}
    for r in results:
        a = r.get("action", "updated")
        actions[a] = actions.get(a, 0) + 1

    parts = []
    for action in ("created", "updated", "deleted"):
        if actions.get(action):
            parts.append(f"{actions[action]} {action}")
    return ", ".join(parts)


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register the refactor_edit tool."""

    @mcp.tool(
        annotations={
            "title": "Edit: find-and-replace with sha256 locking",
        },
    )
    async def refactor_edit(
        ctx: Context,
        edits: list[FindReplaceEdit] = Field(
            ...,
            description=(
                "List of find-and-replace edits. Each edit specifies "
                "old_content to find and new_content to replace it with. "
                "edit_ticket (from refactor_plan) is required for updates. "
                "Batch ALL edits into a single call."
            ),
        ),
        plan_id: str | None = Field(
            None,
            description=(
                "Plan ID from refactor_plan. Required for file updates. "
                "Not needed for create-only or delete-only calls."
            ),
        ),
    ) -> dict[str, Any]:
        """Edit files using find-and-replace with sha256 locking.

        Primary mechanism: provide old_content (text to find) and
        new_content (replacement text).  The server finds old_content
        in the file and replaces it.

        If old_content appears multiple times, provide start_line /
        end_line as hints to disambiguate.  These are optional hints,
        not primary addressing.

        Also supports file creation (old_content=None, new_content=body)
        and file deletion (delete=True).
        """
        from codeplane.files.ops import validate_path_in_repo
        from codeplane.mutation.ops import Edit

        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        ledger = get_ledger()
        repo_root = app_ctx.coordinator.repo_root

        # ── Read-only gate ──
        if getattr(session, "read_only", None) is True:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="Session is read-only — mutations are blocked.",
                remediation=(
                    "This session was started with recon(read_only=True). "
                    'Call recon(read_only=False, task="...") to start a '
                    "read-write session before editing files."
                ),
            )

        # ── Batch-limit gate ──
        if session.edits_since_checkpoint >= _MAX_EDIT_BATCHES:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"Edit batch limit reached ({_MAX_EDIT_BATCHES} batches "
                    "since last checkpoint). Checkpoint is required."
                ),
                remediation=(
                    'Call checkpoint(changed_files=[...], commit_message="...") '
                    "to lint, test, and commit before editing more files."
                ),
            )

        results: list[dict[str, Any]] = []
        creates: list[FindReplaceEdit] = []
        deletes: list[FindReplaceEdit] = []
        updates: list[FindReplaceEdit] = []

        for edit in edits:
            if edit.delete:
                deletes.append(edit)
            elif edit.old_content is None:
                creates.append(edit)
            else:
                updates.append(edit)

        # ── Plan gate (updates require active plan) ──
        if updates:
            if session.active_plan is None:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=(
                        "No active refactor plan. Call refactor_plan first "
                        "to declare your edit targets and get edit tickets."
                    ),
                    remediation=(
                        "Call refactor_plan(edit_targets=[...], "
                        'description="...") to declare your edit set '
                        "before calling refactor_edit."
                    ),
                )
            if plan_id != session.active_plan.plan_id:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=(
                        f"plan_id mismatch. Expected: "
                        f"'{session.active_plan.plan_id}', "
                        f"got: '{plan_id}'."
                    ),
                    remediation=("Use the plan_id returned by your refactor_plan call."),
                )
            if session.active_plan.edit_calls_made >= session.active_plan.expected_edit_calls:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=(
                        "Edit call budget exhausted "
                        f"({session.active_plan.edit_calls_made}/"
                        f"{session.active_plan.expected_edit_calls} "
                        "calls used). Checkpoint to start a new plan."
                    ),
                    remediation=(
                        "Call checkpoint(changed_files=[...], "
                        'commit_message="...") to complete this plan, '
                        "then create a new one if needed."
                    ),
                )

        # ── Process find-and-replace updates (ticket-gated) ──
        continuation_tickets: list[dict[str, str]] = []
        for edit in updates:
            # ── Resolve edit ticket ──
            if not edit.edit_ticket:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message="edit_ticket is required for updates.",
                    remediation=(
                        "Call refactor_plan first to declare your edit set. "
                        "Each planned file comes with an edit_ticket."
                    ),
                )

            ticket = session.edit_tickets.get(edit.edit_ticket)
            if ticket is None:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=f"Unknown edit_ticket '{edit.edit_ticket}'.",
                    remediation=(
                        "Use an edit_ticket from refactor_plan output or "
                        "a continuation ticket from a previous refactor_edit."
                    ),
                )
            if ticket.used:
                raise MCPError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=f"Edit ticket '{edit.edit_ticket}' already used.",
                    remediation=(
                        "Each ticket is single-use. Use the continuation "
                        "ticket from the previous edit response, or call "
                        "refactor_plan again to get fresh tickets."
                    ),
                )

            edit_path = ticket.path
            expected_sha = ticket.sha256

            try:
                full_path = validate_path_in_repo(repo_root, edit_path)
            except Exception as exc:
                raise MCPError(
                    code=MCPErrorCode.FILE_NOT_FOUND,
                    message=f"File not found: {edit_path}",
                    remediation="Check the path. Call recon to discover files.",
                ) from exc

            content = full_path.read_text(encoding="utf-8", errors="replace")
            actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # ── SHA256 verification (ticket embeds expected hash) ──
            if expected_sha != actual_sha:
                from codeplane.mcp.errors import FileHashMismatchError

                raise FileHashMismatchError(
                    path=edit_path,
                    expected=expected_sha,
                    actual=actual_sha,
                )

            # Mark ticket as consumed
            ticket.used = True

            # ── Resolve the edit ──
            new_content, match_meta = _resolve_edit(
                content,
                edit.old_content or "",
                edit.new_content or "",
                start_line=edit.start_line,
                end_line=edit.end_line,
            )

            # Write the file
            full_path.write_text(new_content, encoding="utf-8")
            new_sha = hashlib.sha256(new_content.encode("utf-8")).hexdigest()

            old_line_count = content.count("\n")
            new_line_count = new_content.count("\n")

            # ── Issue continuation ticket ──
            new_ticket_id = f"{ticket.candidate_id}:{new_sha[:8]}"
            session.edit_tickets[new_ticket_id] = EditTicket(
                ticket_id=new_ticket_id,
                path=edit_path,
                sha256=new_sha,
                candidate_id=ticket.candidate_id,
                issued_by="continuation",
            )
            continuation_tickets.append(
                {
                    "path": edit_path,
                    "edit_ticket": new_ticket_id,
                }
            )

            result_entry = {
                "path": edit_path,
                "action": "updated",
                "old_hash": actual_sha[:8],
                "new_hash": new_sha[:8],
                "file_sha256": new_sha,
                "edit_ticket": new_ticket_id,
                "insertions": max(0, new_line_count - old_line_count),
                "deletions": max(0, old_line_count - new_line_count),
                **match_meta,
            }
            results.append(result_entry)

            ledger.log_operation(
                tool="refactor_edit",
                success=True,
                path=edit_path,
                action="updated",
                before_hash=actual_sha[:8],
                after_hash=new_sha[:8],
            )

            # Trigger reindex
            app_ctx.mutation_ops.notify_mutation([Path(edit_path)])

        # ── Process creates and deletes via mutation_ops ──
        if creates or deletes:
            edit_list = []
            for e in creates:
                if not e.path:
                    raise MCPError(
                        code=MCPErrorCode.INVALID_PARAMS,
                        message="path is required for file creation.",
                        remediation="Provide path for new file creates.",
                    )
                edit_list.append(
                    Edit(
                        path=e.path,
                        action="create",
                        content=e.new_content,
                    )
                )
            for e in deletes:
                if not e.path:
                    raise MCPError(
                        code=MCPErrorCode.INVALID_PARAMS,
                        message="path is required for file deletion.",
                        remediation="Provide path for file to delete.",
                    )
                edit_list.append(
                    Edit(
                        path=e.path,
                        action="delete",
                    )
                )

            try:
                result = app_ctx.mutation_ops.write_source(edit_list, dry_run=False)
                for file_delta in result.delta.files:
                    entry = {
                        "path": file_delta.path,
                        "action": file_delta.action,
                        "old_hash": file_delta.old_hash,
                        "new_hash": file_delta.new_hash,
                        "insertions": file_delta.insertions,
                        "deletions": file_delta.deletions,
                    }
                    results.append(entry)
                    ledger.log_operation(
                        tool="refactor_edit",
                        success=True,
                        path=file_delta.path,
                        action=file_delta.action,
                        before_hash=file_delta.old_hash,
                        after_hash=file_delta.new_hash,
                    )
            except FileNotFoundError as exc:
                raise MCPError(
                    code=MCPErrorCode.FILE_NOT_FOUND,
                    message=str(exc),
                    remediation="Check that the path exists relative to repo root.",
                ) from exc
            except FileExistsError as exc:
                raise MCPError(
                    code=MCPErrorCode.FILE_EXISTS,
                    message=str(exc),
                    remediation="File already exists. Use old_content/new_content to update.",
                ) from exc

        # ── Increment plan edit call counter ──
        if session.active_plan is not None:
            session.active_plan.edit_calls_made += 1

        # Track edited files in session + increment batch counter
        try:
            if "edited_files" not in session.counters:
                session.counters["edited_files"] = set()  # type: ignore[assignment]
            edited: set[str] = session.counters["edited_files"]  # type: ignore[assignment]
            for r in results:
                edited.add(r["path"])
            session.edits_since_checkpoint += 1
        except Exception:  # noqa: BLE001
            pass

        total_insertions = sum(r.get("insertions", 0) for r in results)
        total_deletions = sum(r.get("deletions", 0) for r in results)

        batches_left = _MAX_EDIT_BATCHES - session.edits_since_checkpoint
        if batches_left <= 0:
            batch_note = (
                "CHECKPOINT REQUIRED: You have used all edit batches. "
                "Call checkpoint before editing more files."
            )
        else:
            batch_note = f"{batches_left} edit batch(es) remaining before checkpoint is required."

        agentic_hint = (
            f"Edited {len(results)} file(s). {batch_note}\n"
            'NEXT: call checkpoint(changed_files=[...], commit_message="...") '
            "to lint, test, and commit. Ask the user whether they want "
            "push=True or push=False."
        )

        response: dict[str, Any] = {
            "applied": True,
            "delta": {
                "files_changed": len(results),
                "insertions": total_insertions,
                "deletions": total_deletions,
                "files": results,
            },
            "summary": _summarize_edit(results),
            "agentic_hint": agentic_hint,
        }

        if continuation_tickets:
            response["continuation_tickets"] = continuation_tickets

        return response
