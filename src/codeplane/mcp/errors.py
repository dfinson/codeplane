"""Structured error system for MCP tools.

Provides typed exceptions with error codes and remediation hints.
Enables agents to understand failures and self-correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastmcp.exceptions import ToolError


class MCPErrorCode(StrEnum):
    """Machine-readable error codes for MCP tool failures."""

    # Validation errors - agent should fix input
    ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
    ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    INVALID_PARAMS = "INVALID_PARAMS"
    INVALID_RANGE = "INVALID_RANGE"
    INVALID_MODE = "INVALID_MODE"

    # State errors - agent should re-read file
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    FILE_MODIFIED = "FILE_MODIFIED"
    HASH_MISMATCH = "HASH_MISMATCH"
    DRY_RUN_EXPIRED = "DRY_RUN_EXPIRED"
    DRY_RUN_REQUIRED = "DRY_RUN_REQUIRED"

    # File errors
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_EXISTS = "FILE_EXISTS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ENCODING_ERROR = "ENCODING_ERROR"

    # Git errors
    REF_NOT_FOUND = "REF_NOT_FOUND"
    MERGE_CONFLICT = "MERGE_CONFLICT"
    DIRTY_WORKING_TREE = "DIRTY_WORKING_TREE"
    HOOK_FAILED = "HOOK_FAILED"

    # System errors
    IO_ERROR = "IO_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Delivery envelope errors
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SCOPE_EXPIRED = "SCOPE_EXPIRED"
    SPAN_OVERLAP = "SPAN_OVERLAP"
    FILE_HASH_MISMATCH = "FILE_HASH_MISMATCH"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    DUPLICATE_FULL_READ = "DUPLICATE_FULL_READ"
    RESOURCE_EVICTED = "RESOURCE_EVICTED"


@dataclass
class ErrorResponse:
    """Structured error response for MCP tools."""

    code: MCPErrorCode
    message: str
    remediation: str
    path: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "code": self.code.value,
            "message": self.message,
            "remediation": self.remediation,
            "path": self.path,
            "context": self.context,
        }


class MCPError(ToolError):
    """Base exception for MCP tool errors with structured response.

    Extends FastMCP's ToolError so that FastMCP's tool_manager passes it
    through (``except FastMCPError: raise``) instead of wrapping it in a
    generic ToolError.  Our ToolMiddleware then catches it as MCPError
    and returns the structured error code.
    """

    """Base exception for MCP tool errors with structured response."""

    def __init__(
        self,
        code: MCPErrorCode,
        message: str,
        remediation: str,
        path: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation
        self.path = path
        self.context = context

    def to_response(self) -> ErrorResponse:
        """Convert to ErrorResponse."""
        return ErrorResponse(
            code=self.code,
            message=self.message,
            remediation=self.remediation,
            path=self.path,
            context=self.context,
        )


# =============================================================================
# Specific Error Classes
# =============================================================================


class InvalidRangeError(MCPError):
    """Raised when line range is invalid."""

    def __init__(self, path: str, start: int, end: int, line_count: int) -> None:
        super().__init__(
            code=MCPErrorCode.INVALID_RANGE,
            message=f"Invalid range [{start}, {end}] for file with {line_count} lines",
            remediation="Ensure start <= end and both are within file bounds (1 to line_count).",
            path=path,
            start=start,
            end=end,
            line_count=line_count,
        )


class HashMismatchError(MCPError):
    """Raised when content hash doesn't match expected."""

    def __init__(self, path: str, expected: str, actual: str) -> None:
        super().__init__(
            code=MCPErrorCode.HASH_MISMATCH,
            message=f"File {path} was modified since dry run",
            remediation="Re-read the file and re-run with dry_run=True to get new hash.",
            path=path,
            expected_hash=expected,
            actual_hash=actual,
        )


class DryRunRequiredError(MCPError):
    """Raised when line_range mode is used without prior dry_run."""

    def __init__(self, path: str) -> None:
        super().__init__(
            code=MCPErrorCode.DRY_RUN_REQUIRED,
            message=f"Line-range edit on {path} requires prior dry_run",
            remediation="Call write_source with dry_run=True first to get content_hash, then call again with the hash.",
            path=path,
        )


class DryRunExpiredError(MCPError):
    """Raised when dry_run_id is too old."""

    def __init__(self, dry_run_id: str, age_seconds: float) -> None:
        super().__init__(
            code=MCPErrorCode.DRY_RUN_EXPIRED,
            message=f"Dry run {dry_run_id} expired ({age_seconds:.1f}s old, max 60s)",
            remediation="Re-run with dry_run=True to get a fresh validation token.",
            dry_run_id=dry_run_id,
            age_seconds=age_seconds,
        )


class BudgetExceededError(MCPError):
    """Raised when a scope budget is exceeded."""

    def __init__(self, scope_id: str, counter: str, hint: str) -> None:
        super().__init__(
            code=MCPErrorCode.BUDGET_EXCEEDED,
            message=f"Scope budget exceeded for '{counter}' in scope '{scope_id}'",
            remediation=hint,
            scope_id=scope_id,
            counter=counter,
        )


class SpanOverlapError(MCPError):
    """Raised when span-based edits have overlapping ranges."""

    def __init__(self, path: str, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(
            code=MCPErrorCode.SPAN_OVERLAP,
            message=f"Overlapping span edits in {path}",
            remediation="Ensure all span edits are non-overlapping. Combine overlapping spans into a single edit.",
            path=path,
            conflicts=conflicts,
        )


class FileHashMismatchError(MCPError):
    """Raised when file SHA256 doesn't match expected for span edits."""

    def __init__(self, path: str, expected: str, actual: str) -> None:
        super().__init__(
            code=MCPErrorCode.FILE_HASH_MISMATCH,
            message=f"File {path} was modified since last read (hash mismatch)",
            remediation="Re-read the file with read_source to get the current file_sha256, then retry.",
            path=path,
            expected_file_sha256=expected,
            current_file_sha256=actual,
        )


class ConfirmationRequiredError(MCPError):
    """Raised when a two-phase confirmation is required."""

    _RESERVED_KEYS = frozenset({"code", "message", "remediation", "path", "confirmation_token"})

    def __init__(self, reason: str, token: str, details: dict[str, Any] | None = None) -> None:
        safe_details = (
            {k: v for k, v in details.items() if k not in self._RESERVED_KEYS} if details else {}
        )
        super().__init__(
            code=MCPErrorCode.CONFIRMATION_REQUIRED,
            message=reason,
            remediation="Retry with confirmation_token and confirm_reason parameters.",
            confirmation_token=token,
            **safe_details,
        )


class HookFailedError(MCPError):
    """Raised when a git hook fails."""

    def __init__(
        self,
        hook_type: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        modified_files: list[str] | None = None,
    ) -> None:
        super().__init__(
            code=MCPErrorCode.HOOK_FAILED,
            message=f"{hook_type} hook failed with exit code {exit_code}",
            remediation="Fix the issues reported below. Auto-fixes may have been applied - check git status.",
            hook_type=hook_type,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            modified_files=modified_files or [],
        )


# =============================================================================
# Error Catalog for Introspection
# =============================================================================


@dataclass
class ErrorDocumentation:
    """Documentation for an error code."""

    code: MCPErrorCode
    category: str  # validation, state, file, git, system
    description: str
    causes: list[str]
    remediation: list[str]


ERROR_CATALOG: dict[str, ErrorDocumentation] = {
    MCPErrorCode.HASH_MISMATCH.value: ErrorDocumentation(
        code=MCPErrorCode.HASH_MISMATCH,
        category="state",
        description="The file was modified between dry_run and apply.",
        causes=[
            "Another process modified the file",
            "Auto-formatter ran on save",
            "Too much time elapsed between dry_run and apply",
        ],
        remediation=[
            "Re-read the file to see current state",
            "Re-run with dry_run=True to get fresh content_hash",
            "Apply changes more quickly after dry_run",
        ],
    ),
    MCPErrorCode.CONTENT_MISMATCH.value: ErrorDocumentation(
        code=MCPErrorCode.CONTENT_MISMATCH,
        category="state",
        description="expected_content does not match actual file content at the given span.",
        causes=[
            "File was modified after read_source but before write_source",
            "Agent line numbers drifted beyond fuzzy-match window (±5 lines)",
            "expected_content was fabricated instead of copied from read_source",
        ],
        remediation=[
            "Re-read the target span with read_source to get current content",
            "Use the returned file_sha256 and actual content for the next write",
            "Ensure expected_content is copied verbatim from read_source output",
        ],
    ),
    MCPErrorCode.DRY_RUN_REQUIRED.value: ErrorDocumentation(
        code=MCPErrorCode.DRY_RUN_REQUIRED,
        category="validation",
        description="Line-range edits require a prior dry_run for safety.",
        causes=[
            "Attempted line_range edit without dry_run",
            "Missing content_hash parameter",
        ],
        remediation=[
            "First call write_source with dry_run=True to preview and get content_hash",
            "Then call again with the content_hash to apply",
            "Or use 'exact' mode which doesn't require dry_run",
        ],
    ),
    MCPErrorCode.INVALID_RANGE.value: ErrorDocumentation(
        code=MCPErrorCode.INVALID_RANGE,
        category="validation",
        description="The specified line range is invalid.",
        causes=[
            "start > end",
            "Line numbers exceed file length",
            "Line numbers are 0 or negative",
        ],
        remediation=[
            "Line numbers are 1-indexed",
            "Use read_files to check file length first",
            "Ensure start <= end",
        ],
    ),
    MCPErrorCode.FILE_NOT_FOUND.value: ErrorDocumentation(
        code=MCPErrorCode.FILE_NOT_FOUND,
        category="file",
        description="The specified file does not exist.",
        causes=[
            "Typo in file path",
            "File was deleted or moved",
            "Path is absolute instead of relative to repo root",
        ],
        remediation=[
            "Use map_repo to see available files",
            "Ensure path is relative to repository root",
            "Check for typos in directory names",
        ],
    ),
    MCPErrorCode.FILE_EXISTS.value: ErrorDocumentation(
        code=MCPErrorCode.FILE_EXISTS,
        category="file",
        description="Cannot create file that already exists.",
        causes=[
            "File already exists at target path",
            "Trying to create instead of update",
        ],
        remediation=[
            "Use action='update' instead of 'create' for existing files",
            "Choose a different path for new file",
        ],
    ),
    MCPErrorCode.HOOK_FAILED.value: ErrorDocumentation(
        code=MCPErrorCode.HOOK_FAILED,
        category="git",
        description="A git hook (pre-commit, commit-msg, etc.) failed.",
        causes=[
            "Lint errors detected by pre-commit hooks",
            "Formatting issues that couldn't be auto-fixed",
            "Type errors or other code quality violations",
            "Hook scripts exited with non-zero status",
        ],
        remediation=[
            "Read stdout/stderr in the error response for specific issues",
            "Check modified_files - hooks may have auto-fixed some issues",
            "Fix remaining errors and retry the commit",
            "If auto-fixes were applied, stage them and retry",
        ],
    ),
    MCPErrorCode.BUDGET_EXCEEDED.value: ErrorDocumentation(
        code=MCPErrorCode.BUDGET_EXCEEDED,
        category="system",
        description="A scope budget counter was exceeded.",
        causes=[
            "Too many reads, searches, or full file accesses in one scope",
        ],
        remediation=[
            "Use more targeted queries to reduce resource usage",
            "Use read_source with spans instead of read_file_full",
            "Start a new scope if the current one is exhausted",
        ],
    ),
    MCPErrorCode.SPAN_OVERLAP.value: ErrorDocumentation(
        code=MCPErrorCode.SPAN_OVERLAP,
        category="validation",
        description="Span-based edits have overlapping line ranges in the same file.",
        causes=[
            "Two or more edits in the same file have overlapping start_line/end_line ranges",
        ],
        remediation=[
            "Combine overlapping spans into a single edit",
            "Ensure all edits to the same file have non-overlapping line ranges",
        ],
    ),
    MCPErrorCode.FILE_HASH_MISMATCH.value: ErrorDocumentation(
        code=MCPErrorCode.FILE_HASH_MISMATCH,
        category="state",
        description="File was modified since last read (SHA256 mismatch).",
        causes=[
            "Another process modified the file",
            "A previous write_source call changed the file",
            "Auto-formatter or pre-commit hook modified the file",
        ],
        remediation=[
            "Re-read the file with read_source to get current file_sha256",
            "Retry the span edit with the updated hash",
        ],
    ),
    MCPErrorCode.CONFIRMATION_REQUIRED.value: ErrorDocumentation(
        code=MCPErrorCode.CONFIRMATION_REQUIRED,
        category="validation",
        description="Operation requires two-phase confirmation.",
        causes=[
            "Reading more than 500 lines in a single span",
            "Reading a large file with read_file_full",
            "Exceeding per-call target limits",
        ],
        remediation=[
            "Retry with confirmation_token and confirm_reason from the error response",
            "Or reduce the request scope to avoid confirmation",
        ],
    ),
    MCPErrorCode.RESOURCE_EVICTED.value: ErrorDocumentation(
        code=MCPErrorCode.RESOURCE_EVICTED,
        category="system",
        description="Resource was evicted from cache before TTL expired.",
        causes=[
            "Cache capacity exceeded, oldest entries evicted",
            "Resource TTL expired",
        ],
        remediation=[
            "Re-request the original tool call to regenerate the resource",
            "Consider fetching resources promptly after receiving URIs",
        ],
    ),
}


def get_error_documentation(code: str) -> ErrorDocumentation | None:
    """Get documentation for an error code."""
    return ERROR_CATALOG.get(code)
