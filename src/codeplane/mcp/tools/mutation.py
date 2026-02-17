"""Mutation MCP tools - write_files handler."""

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import BaseModel, ConfigDict, Field

from codeplane.mcp.errors import (
    ContentNotFoundError,
    MCPError,
    MCPErrorCode,
    MultipleMatchesError,
)
from codeplane.mcp.ledger import get_ledger

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models (nested types only)
# =============================================================================


class EditParam(BaseModel):
    """A single file edit.

    For updates, use old_content/new_content for safe content-addressed replacement,
    or start_line/end_line/expected_file_sha256 for span-based replacement.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    action: Literal["create", "update", "delete"]

    # Full content replacement (create, or update without exact matching)
    content: str | None = Field(
        None, description="Full file content for create or full replacement"
    )

    # Exact mode (update only) - content-addressed replacement
    old_content: str | None = Field(None, description="Exact content to find and replace")
    new_content: str | None = Field(None, description="Content to replace old_content with")
    expected_occurrences: int = Field(1, ge=1, description="Expected number of old_content matches")

    # Span mode (update only) - line-range replacement with hash verification
    start_line: int | None = Field(None, gt=0, description="Start line for span edit (1-indexed)")
    end_line: int | None = Field(None, gt=0, description="End line for span edit (1-indexed)")
    expected_file_sha256: str | None = Field(
        None, description="SHA256 of whole file from read_source response"
    )


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_write(delta_files: list[Any], dry_run: bool) -> str:
    """Generate summary for write_files."""
    from codeplane.core.formatting import compress_path, format_path_list

    prefix = "(dry-run) " if dry_run else ""
    if not delta_files:
        return f"{prefix}no changes"

    # Count by action
    actions: dict[str, int] = {}
    for f in delta_files:
        action = f.action if hasattr(f, "action") else f.get("action", "updated")
        actions[action] = actions.get(action, 0) + 1

    # Single file: show compressed path and action
    if len(delta_files) == 1:
        f = delta_files[0]
        path = compress_path(f.path if hasattr(f, "path") else f.get("path", ""), 35)
        action = f.action if hasattr(f, "action") else f.get("action", "updated")
        return f"{prefix}{action} {path}"

    # Multiple files: show counts by action with compressed paths
    paths = [f.path if hasattr(f, "path") else f.get("path", "") for f in delta_files]
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


def _display_write(files: list[Any], dry_run: bool) -> str:
    """Human-friendly message for write_files action."""
    if not files:
        return "No changes applied." if not dry_run else "Dry run: no changes would be applied."

    actions = {"created": 0, "updated": 0, "deleted": 0}
    for f in files:
        action = f.action if hasattr(f, "action") else f.get("action", "updated")
        actions[action] = actions.get(action, 0) + 1

    parts = []
    if actions.get("created"):
        parts.append(f"{actions['created']} created")
    if actions.get("updated"):
        parts.append(f"{actions['updated']} updated")
    if actions.get("deleted"):
        parts.append(f"{actions['deleted']} deleted")

    prefix = "Dry run: would have " if dry_run else ""
    suffix = " files." if dry_run else " files."
    return f"{prefix}{', '.join(parts)}{suffix}"


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register mutation tools with FastMCP server."""

    @mcp.tool
    async def write_files(
        ctx: Context,
        edits: list[EditParam] = Field(..., description="List of file edits to apply atomically"),
        dry_run: bool = Field(False, description="Preview changes without applying"),
    ) -> dict[str, Any]:
        """Create, update, or delete files atomically.

        For updates, provide old_content and new_content. The tool will:
        1. Find old_content in the file (must match exactly)
        2. Verify it appears expected_occurrences times (default 1)
        3. Replace with new_content

        Returns structured error if:
        - CONTENT_NOT_FOUND: old_content doesn't exist in file
        - MULTIPLE_MATCHES: old_content found more times than expected
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        import hashlib

        from codeplane.files.ops import validate_path_in_repo
        from codeplane.mcp.errors import FileHashMismatchError, SpanOverlapError
        from codeplane.mutation.ops import Edit

        ledger = get_ledger()

        # Separate span edits from regular edits
        span_edits = [
            e
            for e in edits
            if e.action == "update" and e.start_line is not None and e.end_line is not None
        ]
        regular_edits = [e for e in edits if e not in span_edits]

        # Process span edits first
        span_results: list[dict[str, Any]] = []
        if span_edits:
            # Group by path and check for overlaps
            by_path: dict[str, list[EditParam]] = {}
            for e in span_edits:
                by_path.setdefault(e.path, []).append(e)

            for path, path_edits in by_path.items():
                # Sort by start_line
                sorted_edits = sorted(path_edits, key=lambda x: (x.start_line or 0))
                # Check overlaps
                for i in range(len(sorted_edits) - 1):
                    if (sorted_edits[i].end_line or 0) >= (sorted_edits[i + 1].start_line or 0):
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

            # Apply span edits (descending line order per file)
            for path, path_edits in by_path.items():
                try:
                    full_path = validate_path_in_repo(app_ctx.repo_root, path)
                except Exception as exc:
                    raise MCPError(
                        code=MCPErrorCode.FILE_NOT_FOUND,
                        message=f"File not found: {path}",
                        remediation="Check the file path.",
                    ) from exc

                content = full_path.read_text(encoding="utf-8", errors="replace")
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
                for e in sorted(path_edits, key=lambda x: -(x.start_line or 0)):
                    start = (e.start_line or 1) - 1
                    end = e.end_line or len(lines)
                    new_lines = (e.new_content or "").splitlines(keepends=True)
                    if new_lines and not new_lines[-1].endswith("\n"):
                        new_lines[-1] += "\n"
                    lines[start:end] = new_lines

                new_content = "".join(lines)
                if not dry_run:
                    full_path.write_text(new_content, encoding="utf-8")

                new_sha = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
                old_lines = content.count("\n")
                new_lines_count = new_content.count("\n")
                span_results.append(
                    {
                        "path": path,
                        "action": "updated",
                        "old_hash": actual_sha[:8],
                        "new_hash": new_sha[:8],
                        "insertions": max(0, new_lines_count - old_lines),
                        "deletions": max(0, old_lines - new_lines_count),
                    }
                )

                # Trigger reindex
                if not dry_run:
                    from pathlib import Path

                    if app_ctx.mutation_ops._on_mutation:
                        app_ctx.mutation_ops._on_mutation([Path(path)])

        # Process regular edits via existing path
        edit_list = []
        for e in regular_edits:
            edit_list.append(
                Edit(
                    path=e.path,
                    action=e.action,
                    content=e.content,
                    old_content=e.old_content,
                    new_content=e.new_content,
                    expected_occurrences=e.expected_occurrences,
                )
            )

        try:
            if edit_list:
                result = app_ctx.mutation_ops.write_files(edit_list, dry_run=dry_run)

                # Log successful operation
                for file_delta in result.delta.files:
                    ledger.log_operation(
                        tool="write_files",
                        success=True,
                        path=file_delta.path,
                        action=file_delta.action,
                        before_hash=file_delta.old_hash,
                        after_hash=file_delta.new_hash,
                        insertions=file_delta.insertions,
                        deletions=file_delta.deletions,
                        session_id=session.session_id,
                    )

                all_files = list(result.delta.files)
            else:
                result = None
                all_files = []

            # Merge span results
            if span_edits:
                for sr in span_results:
                    ledger.log_operation(
                        tool="write_files",
                        success=True,
                        path=str(sr["path"]),
                        action=str(sr["action"]),
                        before_hash=str(sr["old_hash"]),
                        after_hash=str(sr["new_hash"]),
                        insertions=int(sr["insertions"]),
                        deletions=int(sr["deletions"]),
                        session_id=session.session_id,
                    )

            total_insertions = sum(f.insertions for f in all_files) + sum(
                int(sr["insertions"]) for sr in span_results
            )
            total_deletions = sum(f.deletions for f in all_files) + sum(
                int(sr["deletions"]) for sr in span_results
            )

            file_dicts = [
                {
                    "path": f.path,
                    "action": f.action,
                    "old_hash": f.old_hash,
                    "new_hash": f.new_hash,
                    "insertions": f.insertions,
                    "deletions": f.deletions,
                }
                for f in all_files
            ] + [
                {
                    "path": sr["path"],
                    "action": sr["action"],
                    "old_hash": sr["old_hash"],
                    "new_hash": sr["new_hash"],
                    "insertions": sr["insertions"],
                    "deletions": sr["deletions"],
                }
                for sr in span_results
            ]

            response = {
                "applied": not dry_run,
                "dry_run": dry_run,
                "delta": {
                    "mutation_id": result.delta.mutation_id if result else "span_only",
                    "files_changed": len(file_dicts),
                    "insertions": total_insertions,
                    "deletions": total_deletions,
                    "stats_are_estimates": True,
                    "files": file_dicts,
                },
                "summary": _summarize_write(file_dicts, dry_run),
                "display_to_user": _display_write(file_dicts, dry_run),
            }

            # Add dry run info if present
            if result and result.dry_run_info:
                response["dry_run_info"] = {
                    "content_hash": result.dry_run_info.content_hash,
                }

            return response

        except Exception as e:
            # Convert mutation errors to MCPError
            from codeplane.mutation.ops import ContentNotFoundError as OpsContentNotFound
            from codeplane.mutation.ops import MultipleMatchesError as OpsMultipleMatches

            # Log failed operation
            error_code = None
            error_path = None

            if isinstance(e, OpsContentNotFound):
                error_code = MCPErrorCode.CONTENT_NOT_FOUND.value
                error_path = e.path
                ledger.log_operation(
                    tool="write_files",
                    success=False,
                    error_code=error_code,
                    error_message=str(e),
                    path=error_path,
                    session_id=session.session_id,
                )
                raise ContentNotFoundError(e.path, e.snippet) from e

            elif isinstance(e, OpsMultipleMatches):
                error_code = MCPErrorCode.MULTIPLE_MATCHES.value
                error_path = e.path
                ledger.log_operation(
                    tool="write_files",
                    success=False,
                    error_code=error_code,
                    error_message=str(e),
                    path=error_path,
                    session_id=session.session_id,
                )
                raise MultipleMatchesError(e.path, e.count, e.lines) from e

            elif isinstance(e, FileNotFoundError):
                error_code = MCPErrorCode.FILE_NOT_FOUND.value
                ledger.log_operation(
                    tool="write_files",
                    success=False,
                    error_code=error_code,
                    error_message=str(e),
                    session_id=session.session_id,
                )
                raise MCPError(
                    code=MCPErrorCode.FILE_NOT_FOUND,
                    message=str(e),
                    remediation="Check the file path. Use index.map to see available files.",
                ) from e

            elif isinstance(e, FileExistsError):
                error_code = MCPErrorCode.FILE_EXISTS.value
                ledger.log_operation(
                    tool="write_files",
                    success=False,
                    error_code=error_code,
                    error_message=str(e),
                    session_id=session.session_id,
                )
                raise MCPError(
                    code=MCPErrorCode.FILE_EXISTS,
                    message=str(e),
                    remediation="Use action='update' instead of 'create' for existing files.",
                ) from e

            # Re-raise unknown errors
            ledger.log_operation(
                tool="files.edit",
                success=False,
                error_code=MCPErrorCode.INTERNAL_ERROR.value,
                error_message=str(e),
                session_id=session.session_id,
            )
            raise

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
