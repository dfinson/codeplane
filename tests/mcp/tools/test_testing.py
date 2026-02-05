"""Tests for MCP testing tools.

Verifies summary helpers and serialization.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.testing import (
    _build_logs_hint,
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


class TestBuildLogsHint:
    """Tests for _build_logs_hint helper."""

    def test_no_artifact_dir(self) -> None:
        """Should return None if no artifact_dir."""
        hint = _build_logs_hint(None, "running")
        assert hint is None

    def test_running_status_without_targets(self) -> None:
        """Should show generic hint for running tests without target info."""
        hint = _build_logs_hint(".codeplane/artifacts/tests/abc123", "running")
        assert hint is not None
        assert ".codeplane/artifacts/tests/abc123" in hint
        assert "read_files" in hint

    def test_running_status_with_targets(self) -> None:
        """Should show actual target file names for running tests."""
        hint = _build_logs_hint(
            ".codeplane/artifacts/tests/abc123",
            "running",
            target_selectors=["tests/test_foo.py", "tests/test_bar.py"],
        )
        assert hint is not None
        assert ".codeplane/artifacts/tests/abc123" in hint
        # Should contain actual file names, not <target_id> placeholder
        assert "test_tests_test_foo.py.stdout.txt" in hint
        assert "test_tests_test_bar.py.stdout.txt" in hint
        assert "<target_id>" not in hint
        assert "read_files" in hint

    def test_completed_status_without_targets(self) -> None:
        """Should show generic hint for completed tests without target info."""
        hint = _build_logs_hint(".codeplane/artifacts/tests/abc123", "completed")
        assert hint is not None
        assert ".codeplane/artifacts/tests/abc123" in hint
        assert "result.json" in hint
        assert "read_files" in hint

    def test_completed_status_with_targets(self) -> None:
        """Should show actual target file names for completed tests."""
        hint = _build_logs_hint(
            ".codeplane/artifacts/tests/abc123",
            "completed",
            target_selectors=["tests/test_foo.py"],
        )
        assert hint is not None
        assert "test_tests_test_foo.py.stdout.txt" in hint
        assert "<target_id>" not in hint
        assert "result.json" in hint
        assert "read_files" in hint

    def test_failed_status(self) -> None:
        """Should show hint for failed tests."""
        hint = _build_logs_hint(".codeplane/artifacts/tests/abc123", "failed")
        assert hint is not None
        assert "result.json" in hint

    def test_cancelled_status(self) -> None:
        """Should show hint for cancelled tests."""
        hint = _build_logs_hint(".codeplane/artifacts/tests/abc123", "cancelled")
        assert hint is not None
        assert "result.json" in hint

    def test_not_found_status(self) -> None:
        """Should return None for not_found status."""
        hint = _build_logs_hint(".codeplane/artifacts/tests/abc123", "not_found")
        assert hint is None

    def test_many_targets_shows_ellipsis(self) -> None:
        """Should show ellipsis for many targets."""
        hint = _build_logs_hint(
            ".codeplane/artifacts/tests/abc123",
            "completed",
            target_selectors=[
                "tests/test_one.py",
                "tests/test_two.py",
                "tests/test_three.py",
                "tests/test_four.py",
                "tests/test_five.py",
            ],
        )
        assert hint is not None
        # Should show first 3
        assert "test_tests_test_one.py.stdout.txt" in hint
        assert "test_tests_test_two.py.stdout.txt" in hint
        assert "test_tests_test_three.py.stdout.txt" in hint
        # Should indicate more
        assert "2 more targets" in hint
        # Should NOT show the last ones
        assert "test_tests_test_four.py" not in hint


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

    def test_poll_hint_included(self) -> None:
        """Should include poll_after_seconds."""
        status = TestRunStatus(
            run_id="abc123",
            status="running",
            progress=TestProgress(
                targets=TargetProgress(total=10, completed=0),
            ),
        )
        result = TestResult(action="status", run_status=status)
        serialized = _serialize_test_result(result)

        assert "poll_after_seconds" in serialized["run_status"]

    def test_logs_hint_for_status(self) -> None:
        """Should include logs_hint for status checks."""
        status = TestRunStatus(
            run_id="abc123",
            status="running",
            artifact_dir=".codeplane/artifacts/tests/abc123",
        )
        result = TestResult(action="status", run_status=status)
        serialized = _serialize_test_result(result, is_action=False)

        assert "logs_hint" in serialized["run_status"]
        assert ".codeplane/artifacts/tests/abc123" in serialized["run_status"]["logs_hint"]

    def test_no_logs_hint_for_action(self) -> None:
        """Should NOT include logs_hint for run_test_targets action."""
        status = TestRunStatus(
            run_id="abc123",
            status="running",
            artifact_dir=".codeplane/artifacts/tests/abc123",
        )
        result = TestResult(action="run", run_status=status)
        serialized = _serialize_test_result(result, is_action=True)

        # logs_hint should not be in run_status for actions
        assert "logs_hint" not in serialized["run_status"]
