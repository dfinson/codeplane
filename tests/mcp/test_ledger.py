"""Tests for MCP operation ledger.

Covers:
- OperationRecord dataclass
- DryRunRecord dataclass and TTL validation
- OperationLedger operations
- Dry run creation and validation
- Record cleanup and limits
"""

from __future__ import annotations

import time

from codeplane.mcp.ledger import (
    DryRunRecord,
    OperationLedger,
    OperationRecord,
    get_ledger,
)


class TestOperationRecord:
    """Tests for OperationRecord dataclass."""

    def test_create_success_record(self) -> None:
        """Create a successful operation record."""
        record = OperationRecord(
            op_id="abc123",
            timestamp=1234567890.0,
            tool="write_files",
            success=True,
            path="test.py",
            action="update",
            before_hash="aaa111",
            after_hash="bbb222",
            diff_summary="+5 -3 lines",
        )
        assert record.success is True
        assert record.error_code is None
        assert record.path == "test.py"

    def test_create_failure_record(self) -> None:
        """Create a failed operation record."""
        record = OperationRecord(
            op_id="def456",
            timestamp=1234567890.0,
            tool="write_files",
            success=False,
            error_code="FILE_NOT_FOUND",
            error_message="File does not exist",
        )
        assert record.success is False
        assert record.error_code == "FILE_NOT_FOUND"
        assert record.error_message == "File does not exist"

    def test_to_dict(self) -> None:
        """to_dict returns complete dictionary."""
        record = OperationRecord(
            op_id="xyz789",
            timestamp=1000.0,
            tool="test_tool",
            success=True,
            path="file.py",
            action="create",
            session_id="sess_001",
            dry_run_id="dry_001",
        )
        d = record.to_dict()

        assert d["op_id"] == "xyz789"
        assert d["timestamp"] == 1000.0
        assert d["tool"] == "test_tool"
        assert d["success"] is True
        assert d["path"] == "file.py"
        assert d["action"] == "create"
        assert d["session_id"] == "sess_001"
        assert d["dry_run_id"] == "dry_001"

    def test_to_dict_includes_all_fields(self) -> None:
        """to_dict includes all fields even if None."""
        record = OperationRecord(
            op_id="min",
            timestamp=0.0,
            tool="t",
            success=False,
        )
        d = record.to_dict()

        expected_keys = {
            "op_id",
            "timestamp",
            "tool",
            "success",
            "error_code",
            "error_message",
            "path",
            "action",
            "before_hash",
            "after_hash",
            "diff_summary",
            "session_id",
            "dry_run_id",
        }
        assert set(d.keys()) == expected_keys


class TestDryRunRecord:
    """Tests for DryRunRecord dataclass."""

    def test_is_valid_within_ttl(self) -> None:
        """is_valid returns True within TTL."""
        now = time.time()
        record = DryRunRecord(
            dry_run_id="dr001",
            created_at=now,
            valid_until=now + 60.0,
            path="test.py",
            content_hash="abc123",
        )
        assert record.is_valid() is True

    def test_is_valid_expired(self) -> None:
        """is_valid returns False after TTL."""
        now = time.time()
        record = DryRunRecord(
            dry_run_id="dr002",
            created_at=now - 120.0,
            valid_until=now - 60.0,  # Expired 60 seconds ago
            path="test.py",
            content_hash="def456",
        )
        assert record.is_valid() is False

    def test_stores_line_range(self) -> None:
        """DryRunRecord stores line range."""
        record = DryRunRecord(
            dry_run_id="dr003",
            created_at=0.0,
            valid_until=100.0,
            path="test.py",
            content_hash="xyz789",
            start_line=10,
            end_line=20,
        )
        assert record.start_line == 10
        assert record.end_line == 20


class TestOperationLedgerLogOperation:
    """Tests for OperationLedger.log_operation."""

    def test_log_success_operation(self) -> None:
        """Log a successful operation."""
        ledger = OperationLedger()
        record = ledger.log_operation(
            "write_files",
            success=True,
            path="test.py",
            action="update",
        )

        assert record.tool == "write_files"
        assert record.success is True
        assert record.path == "test.py"
        assert len(record.op_id) == 12

    def test_log_failure_operation(self) -> None:
        """Log a failed operation."""
        ledger = OperationLedger()
        record = ledger.log_operation(
            "write_files",
            success=False,
            error_code="CONTENT_NOT_FOUND",
            error_message="Content not found in file",
        )

        assert record.success is False
        assert record.error_code == "CONTENT_NOT_FOUND"

    def test_log_with_diff_summary(self) -> None:
        """Log operation with insertions/deletions creates diff summary."""
        ledger = OperationLedger()
        record = ledger.log_operation(
            "write_files",
            success=True,
            insertions=10,
            deletions=5,
        )

        assert record.diff_summary == "+10 -5 lines"

    def test_log_with_hashes(self) -> None:
        """Log operation with before/after hashes."""
        ledger = OperationLedger()
        record = ledger.log_operation(
            "write_files",
            success=True,
            before_hash="before123",
            after_hash="after456",
        )

        assert record.before_hash == "before123"
        assert record.after_hash == "after456"

    def test_log_with_session_and_dry_run(self) -> None:
        """Log operation with session and dry run linkage."""
        ledger = OperationLedger()
        record = ledger.log_operation(
            "write_files",
            success=True,
            session_id="sess_001",
            dry_run_id="dry_001",
        )

        assert record.session_id == "sess_001"
        assert record.dry_run_id == "dry_001"

    def test_log_stores_record(self) -> None:
        """Logged records are stored in ledger."""
        ledger = OperationLedger()
        ledger.log_operation("tool1", success=True)
        ledger.log_operation("tool2", success=False)

        records = ledger.list_operations()
        assert len(records) == 2

    def test_log_generates_timestamp(self) -> None:
        """Log operation generates current timestamp."""
        ledger = OperationLedger()
        before = time.time()
        record = ledger.log_operation("test", success=True)
        after = time.time()

        assert before <= record.timestamp <= after

    def test_log_trims_old_records(self) -> None:
        """Log operation trims records beyond MAX_RECORDS."""
        ledger = OperationLedger()
        ledger.MAX_RECORDS = 5  # Reduce for testing

        for i in range(10):
            ledger.log_operation(f"tool_{i}", success=True)

        assert len(ledger._records) == 5
        # Should keep most recent
        assert ledger._records[0].tool == "tool_5"
        assert ledger._records[4].tool == "tool_9"


class TestOperationLedgerDryRun:
    """Tests for OperationLedger dry run operations."""

    def test_create_dry_run(self) -> None:
        """Create a dry run record."""
        ledger = OperationLedger()
        record = ledger.create_dry_run("test.py", "content here")

        assert len(record.dry_run_id) == 12
        assert record.path == "test.py"
        assert len(record.content_hash) == 16

    def test_create_dry_run_with_line_range(self) -> None:
        """Create dry run with line range."""
        ledger = OperationLedger()
        record = ledger.create_dry_run(
            "test.py",
            "content",
            start_line=10,
            end_line=20,
        )

        assert record.start_line == 10
        assert record.end_line == 20

    def test_create_dry_run_sets_ttl(self) -> None:
        """Create dry run sets valid_until based on TTL."""
        ledger = OperationLedger()
        before = time.time()
        record = ledger.create_dry_run("test.py", "x")
        after = time.time()

        # TTL is 60 seconds by default
        assert record.valid_until >= before + ledger.DRY_RUN_TTL
        assert record.valid_until <= after + ledger.DRY_RUN_TTL

    def test_get_dry_run_valid(self) -> None:
        """Get a valid dry run."""
        ledger = OperationLedger()
        created = ledger.create_dry_run("test.py", "content")

        retrieved = ledger.get_dry_run(created.dry_run_id)
        assert retrieved is not None
        assert retrieved.dry_run_id == created.dry_run_id

    def test_get_dry_run_not_found(self) -> None:
        """Get returns None for non-existent dry run."""
        ledger = OperationLedger()
        result = ledger.get_dry_run("nonexistent")
        assert result is None

    def test_get_dry_run_expired(self) -> None:
        """Get returns None for expired dry run."""
        ledger = OperationLedger()
        record = ledger.create_dry_run("test.py", "content")

        # Manually expire it
        ledger._dry_runs[record.dry_run_id].valid_until = time.time() - 1

        result = ledger.get_dry_run(record.dry_run_id)
        assert result is None

    def test_invalidate_dry_run(self) -> None:
        """Invalidate removes dry run."""
        ledger = OperationLedger()
        record = ledger.create_dry_run("test.py", "content")

        ledger.invalidate_dry_run(record.dry_run_id)

        assert ledger.get_dry_run(record.dry_run_id) is None

    def test_invalidate_nonexistent_is_safe(self) -> None:
        """Invalidating non-existent dry run doesn't raise."""
        ledger = OperationLedger()
        ledger.invalidate_dry_run("does_not_exist")  # Should not raise

    def test_create_dry_run_cleans_expired(self) -> None:
        """Creating dry run cleans up expired ones."""
        ledger = OperationLedger()

        # Create and expire a dry run
        old = ledger.create_dry_run("old.py", "old content")
        ledger._dry_runs[old.dry_run_id].valid_until = time.time() - 1

        # Creating new one should cleanup expired
        ledger.create_dry_run("new.py", "new content")

        assert old.dry_run_id not in ledger._dry_runs

    def test_content_hash_is_deterministic(self) -> None:
        """Same content produces same hash."""
        ledger = OperationLedger()
        r1 = ledger.create_dry_run("a.py", "same content")
        r2 = ledger.create_dry_run("b.py", "same content")

        assert r1.content_hash == r2.content_hash


class TestOperationLedgerListOperations:
    """Tests for OperationLedger.list_operations."""

    def test_list_empty(self) -> None:
        """List returns empty for new ledger."""
        ledger = OperationLedger()
        assert ledger.list_operations() == []

    def test_list_returns_most_recent_first(self) -> None:
        """List returns operations in reverse chronological order."""
        ledger = OperationLedger()
        ledger.log_operation("first", success=True)
        ledger.log_operation("second", success=True)
        ledger.log_operation("third", success=True)

        records = ledger.list_operations()
        assert records[0].tool == "third"
        assert records[1].tool == "second"
        assert records[2].tool == "first"

    def test_list_filter_by_path(self) -> None:
        """List filters by path."""
        ledger = OperationLedger()
        ledger.log_operation("t1", success=True, path="a.py")
        ledger.log_operation("t2", success=True, path="b.py")
        ledger.log_operation("t3", success=True, path="a.py")

        records = ledger.list_operations(path="a.py")
        assert len(records) == 2
        assert all(r.path == "a.py" for r in records)

    def test_list_filter_by_session_id(self) -> None:
        """List filters by session_id."""
        ledger = OperationLedger()
        ledger.log_operation("t1", success=True, session_id="s1")
        ledger.log_operation("t2", success=True, session_id="s2")
        ledger.log_operation("t3", success=True, session_id="s1")

        records = ledger.list_operations(session_id="s1")
        assert len(records) == 2
        assert all(r.session_id == "s1" for r in records)

    def test_list_filter_success_only(self) -> None:
        """List filters by success status."""
        ledger = OperationLedger()
        ledger.log_operation("t1", success=True)
        ledger.log_operation("t2", success=False)
        ledger.log_operation("t3", success=True)

        records = ledger.list_operations(success_only=True)
        assert len(records) == 2
        assert all(r.success for r in records)

    def test_list_respects_limit(self) -> None:
        """List respects limit parameter."""
        ledger = OperationLedger()
        for i in range(10):
            ledger.log_operation(f"t{i}", success=True)

        records = ledger.list_operations(limit=3)
        assert len(records) == 3

    def test_list_default_limit(self) -> None:
        """List has default limit of 50."""
        ledger = OperationLedger()
        for i in range(100):
            ledger.log_operation(f"t{i}", success=True)

        records = ledger.list_operations()
        assert len(records) == 50

    def test_list_combines_filters(self) -> None:
        """List can combine multiple filters."""
        ledger = OperationLedger()
        ledger.log_operation("t1", success=True, path="a.py", session_id="s1")
        ledger.log_operation("t2", success=False, path="a.py", session_id="s1")
        ledger.log_operation("t3", success=True, path="b.py", session_id="s1")
        ledger.log_operation("t4", success=True, path="a.py", session_id="s2")

        records = ledger.list_operations(
            path="a.py",
            session_id="s1",
            success_only=True,
        )
        assert len(records) == 1
        assert records[0].tool == "t1"


class TestGetLedger:
    """Tests for get_ledger global function."""

    def test_get_ledger_returns_instance(self) -> None:
        """get_ledger returns OperationLedger instance."""
        ledger = get_ledger()
        assert isinstance(ledger, OperationLedger)

    def test_get_ledger_returns_same_instance(self) -> None:
        """get_ledger returns same instance on multiple calls."""
        l1 = get_ledger()
        l2 = get_ledger()
        assert l1 is l2

    def test_get_ledger_creates_if_none(self) -> None:
        """get_ledger creates instance if None."""
        import codeplane.mcp.ledger as ledger_module

        original = ledger_module._ledger
        try:
            ledger_module._ledger = None
            ledger = get_ledger()
            assert ledger is not None
            assert ledger_module._ledger is ledger
        finally:
            ledger_module._ledger = original


class TestDryRunRecordValidity:
    """Edge cases for DryRunRecord validity."""

    def test_exactly_at_expiry(self) -> None:
        """Record at exact expiry time is invalid."""
        now = time.time()
        record = DryRunRecord(
            dry_run_id="test",
            created_at=now - 60,
            valid_until=now,  # Exact boundary
            path="test.py",
            content_hash="abc",
        )
        # At exactly valid_until, should be invalid (< not <=)
        assert record.is_valid() is False

    def test_just_before_expiry(self) -> None:
        """Record just before expiry is valid."""
        future = time.time() + 0.001
        record = DryRunRecord(
            dry_run_id="test",
            created_at=0,
            valid_until=future,
            path="test.py",
            content_hash="abc",
        )
        assert record.is_valid() is True
