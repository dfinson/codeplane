"""Mutation MCP tools - write_source handler."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeplane.mcp.errors import (
    MCPError,
    MCPErrorCode,
)
from codeplane.mcp.ledger import get_ledger

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class EditParam(BaseModel):
    """A single file edit.

    For create: provide content (full file body).
    For update: provide start_line, end_line, expected_file_sha256, and new_content
                (span-based replacement only).
    For delete: no extra fields needed.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    action: Literal["create", "update", "delete"]

    # create: full file content
    content: str | None = Field(None, description="Full file content (required for create)")

    # update: span-based replacement (all three required for updates)
    start_line: int | None = Field(None, gt=0, description="Start line (1-indexed, inclusive)")
    end_line: int | None = Field(None, gt=0, description="End line (1-indexed, inclusive)")
    expected_file_sha256: str | None = Field(
        None, description="SHA256 of whole file from read_source (required for update)"
    )
    new_content: str | None = Field(
        None, description="Replacement content for the span (required for update)"
    )
    expected_content: str | None = Field(
        None,
        description=(
            "Expected content at the span location (required for update). "
            "The server fuzzy-matches nearby lines if your line numbers are "
            "slightly off, auto-correcting within a few lines."
        ),
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "EditParam":
        if self.action == "create":
            if self.content is None:
                msg = "content is required for action='create'"
                raise ValueError(msg)
        elif self.action == "update":
            missing = []
            if self.start_line is None:
                missing.append("start_line")
            if self.end_line is None:
                missing.append("end_line")
            if self.expected_file_sha256 is None:
                missing.append("expected_file_sha256")
            if self.new_content is None:
                missing.append("new_content")
            if self.expected_content is None:
                missing.append("expected_content")
            if missing:
                msg = f"update requires: {', '.join(missing)}"
                raise ValueError(msg)
            if (self.end_line or 0) < (self.start_line or 0):
                msg = f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                raise ValueError(msg)
        return self


# =============================================================================
# Fuzzy Span Matching
# =============================================================================

_FUZZY_SEARCH_WINDOW = 5  # Max lines to search in each direction


def _fuzzy_match_span(
    lines: list[str],
    start: int,
    end: int,
    expected_content: str,
) -> tuple[int, int, bool]:
    """Try to find expected_content near the given span, auto-correcting line numbers.

    Args:
        lines: All file lines (with line endings).
        start: 0-indexed start line.
        end: 0-indexed exclusive end line (like slice notation).
        expected_content: The content the agent expects at [start:end].

    Returns:
        (corrected_start, corrected_end, was_corrected) tuple.
        If no match found nearby, returns original values with was_corrected=False.
    """
    expected_lines = expected_content.splitlines(keepends=True)
    # Normalize: ensure trailing newline for comparison
    if expected_lines and not expected_lines[-1].endswith("\n"):
        expected_lines[-1] += "\n"
    search_len = len(expected_lines)
    span_width = end - start

    # First check: does expected_content match at the given position and width?
    actual_at_span = lines[start:end]
    if _lines_match(actual_at_span, expected_lines):
        return start, end, False  # Already correct

    # Width-correction: same position but use expected_content's line count.
    # Catches off-by-one in end_line (agent miscounted span width).
    if search_len != span_width and start >= 0 and start + search_len <= len(lines):
        candidate = lines[start : start + search_len]
        if _lines_match(candidate, expected_lines):
            return start, start + search_len, True

    # Search nearby positions (both offset and width corrected)
    for offset in range(1, _FUZZY_SEARCH_WINDOW + 1):
        for direction in (-1, 1):
            candidate_start = start + (offset * direction)
            candidate_end = candidate_start + search_len
            if candidate_start < 0 or candidate_end > len(lines):
                continue
            candidate = lines[candidate_start:candidate_end]
            if _lines_match(candidate, expected_lines):
                return candidate_start, candidate_end, True

    # No match found — return original (caller verifies content)
    return start, end, False


def _lines_match(actual: list[str], expected: list[str]) -> bool:
    """Compare lines with whitespace-normalized matching."""
    if len(actual) != len(expected):
        return False
    return all(a.rstrip() == e.rstrip() for a, e in zip(actual, expected, strict=True))


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_write(delta_files: list[dict[str, Any]], dry_run: bool) -> str:
    """Generate summary for write_source."""
    from codeplane.core.formatting import compress_path, format_path_list

    prefix = "(dry-run) " if dry_run else ""
    if not delta_files:
        return f"{prefix}no changes"

    actions: dict[str, int] = {}
    for f in delta_files:
        action = f.get("action", "updated")
        actions[action] = actions.get(action, 0) + 1

    if len(delta_files) == 1:
        path = compress_path(delta_files[0].get("path", ""), 35)
        action = delta_files[0].get("action", "updated")
        return f"{prefix}{action} {path}"

    paths = [f.get("path", "") for f in delta_files]
    compressed_paths = [compress_path(p, 20) for p in paths]
    path_list = format_path_list(compressed_paths, max_total=35, compress=False)

    parts: list[str] = []
    if actions.get("created"):
        parts.append(f"{actions['created']} created")
    if actions.get("updated"):
        parts.append(f"{actions['updated']} updated")
    if actions.get("deleted"):
        parts.append(f"{actions['deleted']} deleted")

    return f"{prefix}{', '.join(parts)} ({path_list})"


def _display_write(files: list[dict[str, Any]], dry_run: bool) -> str:
    """Human-friendly message for write_source action."""
    if not files:
        return "No changes applied." if not dry_run else "Dry run: no changes would be applied."

    actions = {"created": 0, "updated": 0, "deleted": 0}
    for f in files:
        actions[f.get("action", "updated")] = actions.get(f.get("action", "updated"), 0) + 1

    parts = []
    if actions.get("created"):
        parts.append(f"{actions['created']} created")
    if actions.get("updated"):
        parts.append(f"{actions['updated']} updated")
    if actions.get("deleted"):
        parts.append(f"{actions['deleted']} deleted")

    prefix = "Dry run: would have " if dry_run else ""
    return f"{prefix}{', '.join(parts)} files."


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register mutation tools with FastMCP server."""

    @mcp.tool
    async def write_source(
        ctx: Context,
        edits: list[EditParam] = Field(..., description="List of file edits to apply atomically"),
        dry_run: bool = Field(False, description="Preview changes without applying"),
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
        """Create, update, or delete files atomically.

        For cross-file renames, use refactor_rename instead.

        For updates: span-based only. Provide start_line, end_line,
        expected_file_sha256 (from read_source), and new_content.
        The file hash is verified before applying to prevent stale edits.

        For creates: provide content (full file body).
        For deletes: only path is needed.
        """
        from codeplane.files.ops import validate_path_in_repo
        from codeplane.mcp.errors import (
            FileHashMismatchError,
            InvalidRangeError,
            SpanOverlapError,
        )
        from codeplane.mutation.ops import Edit

        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        ledger = get_ledger()

        # Separate by action type
        updates = [e for e in edits if e.action == "update"]
        creates = [e for e in edits if e.action == "create"]
        deletes = [e for e in edits if e.action == "delete"]

        file_results: list[dict[str, Any]] = []

        # --- Process span-based updates ---
        if updates:
            # Group by path
            by_path: dict[str, list[EditParam]] = {}
            for e in updates:
                by_path.setdefault(e.path, []).append(e)

            for path, path_edits in by_path.items():
                # Sort by start_line and check for overlaps
                sorted_edits = sorted(path_edits, key=lambda x: x.start_line or 0)
                for i in range(len(sorted_edits) - 1):
                    # Use inf for None end_line to match apply logic (None = EOF)
                    raw_end = sorted_edits[i].end_line
                    end_i: float = raw_end if raw_end is not None else float("inf")
                    start_j = sorted_edits[i + 1].start_line or 0
                    if end_i >= start_j:
                        raise SpanOverlapError(
                            path=path,
                            conflicts=[
                                {
                                    "span_a": {
                                        "start_line": sorted_edits[i].start_line,
                                        "end_line": sorted_edits[i].end_line,
                                    },
                                    "span_b": {
                                        "start_line": sorted_edits[i + 1].start_line,
                                        "end_line": sorted_edits[i + 1].end_line,
                                    },
                                }
                            ],
                        )

                # Validate path and read file
                try:
                    full_path = validate_path_in_repo(app_ctx.repo_root, path)
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    raise MCPError(
                        code=MCPErrorCode.FILE_NOT_FOUND,
                        message=f"File not found: {path}",
                        remediation="Check the file path. Use list_files to see available files.",
                    ) from exc

                actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

                # Verify hash for all edits to this file
                for e in path_edits:
                    if e.expected_file_sha256 and e.expected_file_sha256 != actual_sha:
                        raise FileHashMismatchError(
                            path=path,
                            expected=e.expected_file_sha256,
                            actual=actual_sha,
                        )

                lines = content.splitlines(keepends=True)

                # Apply in descending order to preserve line numbers
                corrections: list[dict[str, Any]] = []
                for e in sorted(path_edits, key=lambda x: -(x.start_line or 0)):
                    start = (e.start_line or 1) - 1  # 1-indexed to 0-indexed
                    end = e.end_line or len(lines)  # 1-indexed inclusive

                    # Fuzzy match: auto-correct line numbers if expected_content provided
                    if e.expected_content is not None:
                        new_start, new_end, was_corrected = _fuzzy_match_span(
                            lines, start, end, e.expected_content
                        )
                        if was_corrected:
                            corrections.append(
                                {
                                    "original": {
                                        "start_line": e.start_line,
                                        "end_line": e.end_line,
                                    },
                                    "corrected": {"start_line": new_start + 1, "end_line": new_end},
                                }
                            )
                            start = new_start
                            end = new_end
                        else:
                            # Verify content actually matches — fail loudly vs silent corruption
                            exp_lines = e.expected_content.splitlines(keepends=True)
                            if exp_lines and not exp_lines[-1].endswith("\n"):
                                exp_lines[-1] += "\n"
                            if not _lines_match(lines[start:end], exp_lines):
                                exp_count = len(exp_lines)
                                actual_count = end - start
                                raise MCPError(
                                    code=MCPErrorCode.CONTENT_MISMATCH,
                                    message=(
                                        f"expected_content ({exp_count} lines) does not match "
                                        f"actual content ({actual_count} lines) at "
                                        f"{path}:{e.start_line}-{e.end_line}. "
                                        f"Fuzzy search (±{_FUZZY_SEARCH_WINDOW} lines) also found no match."
                                    ),
                                    remediation=(
                                        "Re-read the target span with read_source to get "
                                        "current content and correct line numbers, then retry."
                                    ),
                                )
                    if start >= len(lines):
                        raise InvalidRangeError(
                            path=path,
                            start=e.start_line or 1,
                            end=e.end_line or len(lines),
                            line_count=len(lines),
                        )
                    new_lines = (e.new_content or "").splitlines(keepends=True)
                    if new_lines and not new_lines[-1].endswith("\n"):
                        new_lines[-1] += "\n"
                    lines[start:end] = new_lines
                new_content = "".join(lines)
                if not dry_run:
                    full_path.write_text(new_content, encoding="utf-8")

                new_sha = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
                old_line_count = content.count("\n")
                new_line_count = new_content.count("\n")
                insertions = max(0, new_line_count - old_line_count)
                deletions = max(0, old_line_count - new_line_count)

                result_entry = {
                    "path": path,
                    "action": "updated",
                    "old_hash": actual_sha[:8],
                    "new_hash": new_sha[:8],
                    "file_sha256": new_sha,
                    "insertions": insertions,
                    "deletions": deletions,
                }
                if corrections:
                    result_entry["line_corrections"] = corrections

                file_results.append(result_entry)

                ledger.log_operation(
                    tool="write_source",
                    success=True,
                    path=path,
                    action="updated",
                    before_hash=actual_sha[:8],
                    after_hash=new_sha[:8],
                    insertions=insertions,
                    deletions=deletions,
                )
                # Trigger reindex
                if not dry_run:
                    app_ctx.mutation_ops.notify_mutation([Path(path)])

        # --- Process creates and deletes via mutation_ops ---
        if creates or deletes:
            edit_list = []
            for e in creates:
                edit_list.append(
                    Edit(
                        path=e.path,
                        action="create",
                        content=e.content,
                    )
                )
            for e in deletes:
                edit_list.append(
                    Edit(
                        path=e.path,
                        action="delete",
                    )
                )

            try:
                result = app_ctx.mutation_ops.write_source(edit_list, dry_run=dry_run)
                for file_delta in result.delta.files:
                    entry = {
                        "path": file_delta.path,
                        "action": file_delta.action,
                        "old_hash": file_delta.old_hash,
                        "new_hash": file_delta.new_hash,
                        "insertions": file_delta.insertions,
                        "deletions": file_delta.deletions,
                    }
                    file_results.append(entry)
                    ledger.log_operation(
                        tool="write_source",
                        success=True,
                        path=file_delta.path,
                        action=file_delta.action,
                        before_hash=file_delta.old_hash,
                        after_hash=file_delta.new_hash,
                        insertions=file_delta.insertions,
                        deletions=file_delta.deletions,
                        session_id=session.session_id,
                    )
            except FileNotFoundError as exc:
                ledger.log_operation(
                    tool="write_source",
                    success=False,
                    error_code=MCPErrorCode.FILE_NOT_FOUND.value,
                    error_message=str(exc),
                    session_id=session.session_id,
                )
                raise MCPError(
                    code=MCPErrorCode.FILE_NOT_FOUND,
                    message=str(exc),
                    remediation="Check the file path. Use list_files to see available files.",
                ) from exc
            except FileExistsError as exc:
                ledger.log_operation(
                    tool="write_source",
                    success=False,
                    error_code=MCPErrorCode.FILE_EXISTS.value,
                    error_message=str(exc),
                    session_id=session.session_id,
                )
                raise MCPError(
                    code=MCPErrorCode.FILE_EXISTS,
                    message=str(exc),
                    remediation="Use action='update' instead of 'create' for existing files.",
                ) from exc

        # --- Wire scope budget mutation reset ---
        if scope_id and not dry_run:
            from codeplane.mcp.tools.files import _scope_manager

            _scope_manager.record_mutation(scope_id)

        total_insertions = sum(f.get("insertions", 0) for f in file_results)
        total_deletions = sum(f.get("deletions", 0) for f in file_results)

        response: dict[str, Any] = {
            "applied": not dry_run,
            "dry_run": dry_run,
            "delta": {
                "files_changed": len(file_results),
                "insertions": total_insertions,
                "deletions": total_deletions,
                "files": file_results,
            },
            "summary": _summarize_write(file_results, dry_run),
            "display_to_user": _display_write(file_results, dry_run),
        }

        return response
