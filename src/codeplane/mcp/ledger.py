"""Operation ledger for mutation audit trail.

Append-only log of all mutation operations for debugging and recovery.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class OperationRecord:
    """Immutable record of a mutation operation."""

    op_id: str
    timestamp: float
    tool: str

    # Outcome
    success: bool
    error_code: str | None = None
    error_message: str | None = None

    # For successful mutations
    path: str | None = None
    action: str | None = None  # create, update, delete
    before_hash: str | None = None
    after_hash: str | None = None
    diff_summary: str | None = None  # "+5 -3 lines"

    # Linkage
    session_id: str | None = None
    dry_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "op_id": self.op_id,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "success": self.success,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "path": self.path,
            "action": self.action,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "diff_summary": self.diff_summary,
            "session_id": self.session_id,
            "dry_run_id": self.dry_run_id,
        }


@dataclass
class DryRunRecord:
    """Record of a dry run for validation."""

    dry_run_id: str
    created_at: float
    valid_until: float  # TTL
    path: str
    content_hash: str  # Hash of content being replaced
    start_line: int | None = None
    end_line: int | None = None

    def is_valid(self) -> bool:
        """Check if dry run is still valid."""
        return time.time() < self.valid_until


class OperationLedger:
    """In-memory ledger for mutation operations.

    Provides audit trail and dry-run validation.
    Could be persisted to SQLite in future.
    """

    MAX_RECORDS = 1000  # Keep last N records
    DRY_RUN_TTL = 60.0  # Seconds

    def __init__(self) -> None:
        self._records: list[OperationRecord] = []
        self._dry_runs: dict[str, DryRunRecord] = {}

    def log_operation(
        self,
        tool: str,
        *,
        success: bool,
        error_code: str | None = None,
        error_message: str | None = None,
        path: str | None = None,
        action: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        insertions: int = 0,
        deletions: int = 0,
        session_id: str | None = None,
        dry_run_id: str | None = None,
    ) -> OperationRecord:
        """Log a mutation operation."""
        diff_summary = None
        if insertions or deletions:
            diff_summary = f"+{insertions} -{deletions} lines"

        record = OperationRecord(
            op_id=str(uuid.uuid4())[:12],
            timestamp=time.time(),
            tool=tool,
            success=success,
            error_code=error_code,
            error_message=error_message,
            path=path,
            action=action,
            before_hash=before_hash,
            after_hash=after_hash,
            diff_summary=diff_summary,
            session_id=session_id,
            dry_run_id=dry_run_id,
        )

        self._records.append(record)

        # Trim old records
        if len(self._records) > self.MAX_RECORDS:
            self._records = self._records[-self.MAX_RECORDS :]

        log.debug(
            "operation_logged",
            op_id=record.op_id,
            tool=tool,
            success=success,
            path=path,
        )

        return record

    def create_dry_run(
        self,
        path: str,
        content: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> DryRunRecord:
        """Create a dry run record for validation."""
        now = time.time()
        dry_run_id = str(uuid.uuid4())[:12]
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        record = DryRunRecord(
            dry_run_id=dry_run_id,
            created_at=now,
            valid_until=now + self.DRY_RUN_TTL,
            path=path,
            content_hash=content_hash,
            start_line=start_line,
            end_line=end_line,
        )

        self._dry_runs[dry_run_id] = record

        # Cleanup expired dry runs
        self._cleanup_expired_dry_runs()

        log.debug("dry_run_created", dry_run_id=dry_run_id, path=path)

        return record

    def get_dry_run(self, dry_run_id: str) -> DryRunRecord | None:
        """Get a dry run record by ID."""
        record = self._dry_runs.get(dry_run_id)
        if record and record.is_valid():
            return record
        return None

    def invalidate_dry_run(self, dry_run_id: str) -> None:
        """Invalidate a dry run after use."""
        self._dry_runs.pop(dry_run_id, None)

    def list_operations(
        self,
        *,
        path: str | None = None,
        session_id: str | None = None,
        success_only: bool = False,
        limit: int = 50,
    ) -> list[OperationRecord]:
        """List recent operations with optional filters."""
        results = self._records.copy()

        if path:
            results = [r for r in results if r.path == path]

        if session_id:
            results = [r for r in results if r.session_id == session_id]

        if success_only:
            results = [r for r in results if r.success]

        # Most recent first
        results = list(reversed(results))

        return results[:limit]

    def _cleanup_expired_dry_runs(self) -> None:
        """Remove expired dry run records."""
        now = time.time()
        expired = [k for k, v in self._dry_runs.items() if now >= v.valid_until]
        for k in expired:
            del self._dry_runs[k]


# Global ledger instance
_ledger: OperationLedger | None = None


def get_ledger() -> OperationLedger:
    """Get or create the global ledger instance."""
    global _ledger
    if _ledger is None:
        _ledger = OperationLedger()
    return _ledger
