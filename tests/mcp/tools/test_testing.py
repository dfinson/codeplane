"""Tests for MCP testing tools.

Verifies summary helpers and serialization.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.testing import (
    _display_discover,
    _display_run_start,
    _display_run_status,
    _serialize_test_result,
    _summarize_discover,
    _summarize_run,
)
from codeplane.testing.models import (
    TargetProgress,
    TestCaseProgress,
    TestProgress,
    TestResult,
    TestRunStatus,
)


class TestSummarizeDiscover:
    """Tests for _summarize_discover helper."""

    def test_no_targets(self) -> None:
        """Should handle no targets."""
        summary = _summarize_discover(0)
        assert "no test targets" in summary

    def test_with_count_only(self) -> None:
        """Should show count without targets."""
        summary = _summarize_discover(42)
        assert "42" in summary

    def test_with_targets_by_language(self) -> None:
        """Should group by language."""
        targets = [
            MagicMock(language="python"),
            MagicMock(language="python"),
            MagicMock(language="javascript"),
        ]
        summary = _summarize_discover(3, targets)
        assert "3 targets" in summary
        assert "python" in summary
        assert "javascript" in summary


class TestDisplayDiscover:
    """Tests for _display_discover helper."""

    def test_no_targets(self) -> None:
        """Should handle no targets."""
        display = _display_discover(0, [])
        assert "No test targets found" in display

    def test_with_targets(self) -> None:
        """Should group by language."""
        targets = [
            MagicMock(language="python"),
            MagicMock(language="python"),
            MagicMock(language="javascript"),
        ]
        display = _display_discover(3, targets)
        assert "Found 3 test targets" in display
        assert "python" in display
        assert "javascript" in display


class TestSummarizeRun:
    """Tests for _summarize_run helper."""

    def test_no_run_status(self) -> None:
        """Should handle missing status."""
        result = TestResult(action="run", run_status=None)
        summary = _summarize_run(result)
        assert "no run status" in summary

    def test_completed_with_failures(self) -> None:
        """Should show failure indicator."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=5.0,
            progress=TestProgress(
                targets=TargetProgress(total=10, completed=10),
                cases=TestCaseProgress(total=100, passed=95, failed=5),
            ),
        )
        result = TestResult(action="status", run_status=status)
        summary = _summarize_run(result)
        assert "95 passed" in summary
        assert "5 failed" in summary

    def test_completed_all_passed(self) -> None:
        """Should show check mark for success."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=3.0,
            progress=TestProgress(
                targets=TargetProgress(total=5, completed=5),
                cases=TestCaseProgress(total=100, passed=100),
            ),
        )
        result = TestResult(action="status", run_status=status)
        summary = _summarize_run(result)
        assert "100 passed" in summary
        # Note: checkmark is only in display_to_user, not in summary

    def test_running_status(self) -> None:
        """Should show running status."""
        status = TestRunStatus(
            run_id="abc123",
            status="running",
            progress=TestProgress(
                cases=TestCaseProgress(total=50, passed=25),
            ),
        )
        result = TestResult(action="status", run_status=status)
        summary = _summarize_run(result)
        assert "running" in summary
        assert "25" in summary


class TestDisplayRunStart:
    """Tests for _display_run_start helper."""

    def test_no_status(self) -> None:
        """Should handle missing status."""
        result = TestResult(action="run", run_status=None)
        display = _display_run_start(result)
        assert "initiated" in display.lower()

    def test_with_status(self) -> None:
        """Should show target count and run ID."""
        status = TestRunStatus(
            run_id="abc123",
            status="running",
            progress=TestProgress(
                targets=TargetProgress(total=5),
            ),
        )
        result = TestResult(action="run", run_status=status)
        display = _display_run_start(result)
        assert "5 targets" in display
        assert "abc123" in display


class TestDisplayRunStatus:
    """Tests for _display_run_status helper."""

    def test_no_status(self) -> None:
        """Should return None for missing status."""
        result = TestResult(action="status", run_status=None)
        display = _display_run_status(result)
        assert display is None

    def test_running_returns_none(self) -> None:
        """Should return None for running status (no noise)."""
        status = TestRunStatus(run_id="abc123", status="running")
        result = TestResult(action="status", run_status=status)
        display = _display_run_status(result)
        assert display is None

    def test_completed_with_failures(self) -> None:
        """Should show FAILED for failing runs."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=5.5,
            progress=TestProgress(
                targets=TargetProgress(total=5, completed=5),
                cases=TestCaseProgress(total=100, passed=90, failed=10),
            ),
        )
        result = TestResult(action="status", run_status=status)
        display = _display_run_status(result)
        assert display is not None
        assert "90 passed" in display
        assert "10 FAILED" in display
        assert "5.5s" in display

    def test_completed_all_passed(self) -> None:
        """Should show success for all passing."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=3.0,
            progress=TestProgress(
                targets=TargetProgress(total=5, completed=5),
                cases=TestCaseProgress(total=100, passed=100),
            ),
        )
        result = TestResult(action="status", run_status=status)
        display = _display_run_status(result)
        assert display is not None
        assert "100 passed" in display
        assert "FAILED" not in display

    def test_cancelled(self) -> None:
        """Should show cancelled message."""
        status = TestRunStatus(run_id="abc123", status="cancelled")
        result = TestResult(action="status", run_status=status)
        display = _display_run_status(result)
        assert display is not None
        assert "cancelled" in display.lower()


class TestSerializeTestResult:
    """Tests for _serialize_test_result helper."""

    def test_basic_result(self) -> None:
        """Should serialize basic result."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=5.0,
        )
        result = TestResult(action="status", run_status=status)
        serialized = _serialize_test_result(result)

        assert serialized["action"] == "status"
        assert "run_status" in serialized
        assert serialized["run_status"]["run_id"] == "abc123"
        assert serialized["run_status"]["status"] == "completed"

    def test_with_progress(self) -> None:
        """Should serialize progress."""
        status = TestRunStatus(
            run_id="abc123",
            status="running",
            progress=TestProgress(
                targets=TargetProgress(total=10, completed=5),
                cases=TestCaseProgress(total=50, passed=25),
            ),
        )
        result = TestResult(action="run", run_status=status)
        serialized = _serialize_test_result(result, is_action=True)

        assert "progress" in serialized["run_status"]
        progress = serialized["run_status"]["progress"]
        assert progress["targets"]["total"] == 10
        assert progress["cases"]["passed"] == 25

    def test_with_agentic_hint(self) -> None:
        """Should include agentic hint."""
        result = TestResult(
            action="discover",
            agentic_hint="No test framework detected.",
        )
        serialized = _serialize_test_result(result)
        assert serialized["agentic_hint"] == "No test framework detected."

    def test_no_poll_fields_in_output(self) -> None:
        """Tests always block — no poll_after_seconds in output."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=5.0,
        )
        result = TestResult(action="run", run_status=status)
        serialized = _serialize_test_result(result, is_action=True)

        assert "poll_after_seconds" not in serialized["run_status"]
        # No sleep/poll hints in agentic_hint
        if "agentic_hint" in serialized:
            assert "Sleep for" not in serialized["agentic_hint"]
            assert "get_test_run_status" not in serialized["agentic_hint"]

    def test_agentic_hint_forwarded_from_result(self) -> None:
        """agentic_hint from TestResult is forwarded directly."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            duration_seconds=5.0,
        )
        result = TestResult(
            action="run",
            run_status=status,
            agentic_hint="No test framework detected.",
        )
        serialized = _serialize_test_result(result, is_action=True)

        assert serialized["agentic_hint"] == "No test framework detected."
