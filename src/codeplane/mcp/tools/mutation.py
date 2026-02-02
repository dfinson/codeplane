"""Mutation MCP tools - mutate handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from codeplane.mcp.errors import (
    ContentNotFoundError,
    ErrorCode,
    MCPError,
    MultipleMatchesError,
)
from codeplane.mcp.ledger import get_ledger
from codeplane.mcp.registry import registry
from codeplane.mcp.tools.base import BaseParams

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext


# =============================================================================
# Parameter Models
# =============================================================================


class EditParam(BaseModel):
    """A single file edit.

    For updates, use old_content/new_content for safe content-addressed replacement.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    action: Literal["create", "update", "delete"]

    # Full content replacement (create, or update without exact matching)
    content: str | None = Field(None, description="Full file content for create or full replacement")

    # Exact mode (update only) - content-addressed replacement
    old_content: str | None = Field(None, description="Exact content to find and replace")
    new_content: str | None = Field(None, description="Content to replace old_content with")
    expected_occurrences: int = Field(1, ge=1, description="Expected number of old_content matches")


class MutateParams(BaseParams):
    """Parameters for mutate."""

    edits: list[EditParam] = Field(..., description="List of file edits to apply atomically")
    dry_run: bool = Field(False, description="Preview changes without applying")


# =============================================================================
# Tool Handlers
# =============================================================================


@registry.register("mutate", "Atomic file edits with structured delta response", MutateParams)
async def mutate(ctx: AppContext, params: MutateParams) -> dict[str, Any]:
    """Apply atomic file edits.

    For updates, provide old_content and new_content. The tool will:
    1. Find old_content in the file (must match exactly)
    2. Verify it appears expected_occurrences times (default 1)
    3. Replace with new_content

    Returns structured error if:
    - CONTENT_NOT_FOUND: old_content doesn't exist in file
    - MULTIPLE_MATCHES: old_content found more times than expected
    """
    from codeplane.mutation.ops import Edit

    ledger = get_ledger()

    # Convert params to ops types
    edits = []
    for e in params.edits:
        edits.append(
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
        result = ctx.mutation_ops.mutate(edits, dry_run=params.dry_run)

        # Log successful operation
        for file_delta in result.delta.files:
            ledger.log_operation(
                tool="mutate",
                success=True,
                path=file_delta.path,
                action=file_delta.action,
                before_hash=file_delta.old_hash,
                after_hash=file_delta.new_hash,
                insertions=file_delta.insertions,
                deletions=file_delta.deletions,
                session_id=params.session_id,
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
            error_code = ErrorCode.CONTENT_NOT_FOUND.value
            error_path = e.path
            ledger.log_operation(
                tool="mutate",
                success=False,
                error_code=error_code,
                error_message=str(e),
                path=error_path,
                session_id=params.session_id,
            )
            raise ContentNotFoundError(e.path, e.snippet) from e

        elif isinstance(e, OpsMultipleMatches):
            error_code = ErrorCode.MULTIPLE_MATCHES.value
            error_path = e.path
            ledger.log_operation(
                tool="mutate",
                success=False,
                error_code=error_code,
                error_message=str(e),
                path=error_path,
                session_id=params.session_id,
            )
            raise MultipleMatchesError(e.path, e.count, e.lines) from e

        elif isinstance(e, FileNotFoundError):
            error_code = ErrorCode.FILE_NOT_FOUND.value
            ledger.log_operation(
                tool="mutate",
                success=False,
                error_code=error_code,
                error_message=str(e),
                session_id=params.session_id,
            )
            raise MCPError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=str(e),
                remediation="Check the file path. Use map_repo to see available files.",
            ) from e

        elif isinstance(e, FileExistsError):
            error_code = ErrorCode.FILE_EXISTS.value
            ledger.log_operation(
                tool="mutate",
                success=False,
                error_code=error_code,
                error_message=str(e),
                session_id=params.session_id,
            )
            raise MCPError(
                code=ErrorCode.FILE_EXISTS,
                message=str(e),
                remediation="Use action='update' instead of 'create' for existing files.",
            ) from e

        # Re-raise unknown errors
        ledger.log_operation(
            tool="mutate",
            success=False,
            error_code=ErrorCode.INTERNAL_ERROR.value,
            error_message=str(e),
            session_id=params.session_id,
        )
        raise

