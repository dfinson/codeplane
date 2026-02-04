"""Tests for MCP testing tools.

Verifies parameter models and summary helpers.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.testing import (
    CancelTestRunParams,
    DiscoverTestTargetsParams,
    GetTestRunStatusParams,
    RunTestTargetsParams,
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


class TestDiscoverTestTargetsParams:
    """Tests for DiscoverTestTargetsParams model."""

    def test_minimal_params(self) -> None:
        """Should work with no params."""
        params = DiscoverTestTargetsParams()
        assert params.paths is None

    def test_with_paths(self) -> None:
        """Should accept paths."""
        params = DiscoverTestTargetsParams(paths=["tests/", "src/tests/"])
        assert params.paths == ["tests/", "src/tests/"]


class TestRunTestTargetsParams:
    """Tests for RunTestTargetsParams model."""

    def test_minimal_params(self) -> None:
        """Should work with no params (run all)."""
        params = RunTestTargetsParams()
        assert params.targets is None
        assert params.fail_fast is False
        assert params.coverage is False

    def test_all_params(self) -> None:
        """Should accept all params."""
        params = RunTestTargetsParams(
            targets=["tests/test_foo.py"],
            pattern="*_test",
            tags=["unit", "fast"],
            failed_only=True,
            parallelism=4,
            timeout_sec=120,
            fail_fast=True,
            coverage=True,
        )
        assert params.targets == ["tests/test_foo.py"]
        assert params.pattern == "*_test"
        assert params.tags == ["unit", "fast"]
        assert params.failed_only is True
        assert params.parallelism == 4
        assert params.timeout_sec == 120
        assert params.fail_fast is True
        assert params.coverage is True


class TestGetTestRunStatusParams:
    """Tests for GetTestRunStatusParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = GetTestRunStatusParams(run_id="abc123")
        assert params.run_id == "abc123"


class TestCancelTestRunParams:
    """Tests for CancelTestRunParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = CancelTestRunParams(run_id="abc123")
        assert params.run_id == "abc123"


class TestSummarizeDiscover:
    """Tests for _summarize_discover helper."""

    def test_no_targets(self) -> None:
        """Should handle no targets."""
        summary = _summarize_discover(0)
        assert "no test targets" in summary

    def test_with_targets(self) -> None:
        """Should show count."""
        summary = _summarize_discover(42)
        assert "42" in summary
        assert "discovered" in summary


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

    def test_with_progress(self) -> None:
        """Should show pass/fail counts."""
        status = TestRunStatus(
            run_id="abc123",
            status="completed",
            progress=TestProgress(
                targets=TargetProgress(total=10, completed=10),
                cases=TestCaseProgress(total=100, passed=95, failed=3, skipped=2),
            ),
        )
        result = TestResult(action="status", run_status=status)
        summary = _summarize_run(result)
        assert "completed" in summary
        assert "95/100 passed" in summary
        assert "3 failed" in summary
        assert "2 skipped" in summary


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
