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

    For updates, use old_content/new_content for safe content-addressed replacement.
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


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_write(files_changed: int, insertions: int, deletions: int, dry_run: bool) -> str:
    """Generate summary for write_files."""
    prefix = "(dry-run) " if dry_run else ""
    if files_changed == 0:
        return f"{prefix}no changes"
    return f"{prefix}{files_changed} files (+{insertions}/-{deletions})"


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

        from codeplane.mutation.ops import Edit

        ledger = get_ledger()

        # Convert params to ops types
        edit_list = []
        for e in edits:
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
            result = app_ctx.mutation_ops.atomic_edit_files(edit_list, dry_run=dry_run)

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

            response = {
                "applied": result.applied,
                "dry_run": result.dry_run,
                "delta": {
                    "mutation_id": result.delta.mutation_id,
                    "files_changed": result.delta.files_changed,
                    "insertions": result.delta.insertions,
                    "deletions": result.delta.deletions,
                    "stats_are_estimates": True,
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
                "summary": _summarize_write(
                    result.delta.files_changed,
                    result.delta.insertions,
                    result.delta.deletions,
                    result.dry_run,
                ),
                "display_to_user": _display_write(result.delta.files, result.dry_run),
            }

            # Add dry run info if present
            if result.dry_run_info:
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
