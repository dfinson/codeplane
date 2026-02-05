"""Tests for MCP testing tools.

Tests the actual exports:
- _summarize_discover() helper
- _display_discover() helper
- _summarize_run() helper

Handler tests use conftest.py fixtures for integration testing.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.testing import (
    _display_discover,
    _summarize_discover,
    _summarize_run,
)


class TestSummarizeDiscover:
    """Tests for _summarize_discover helper."""

    def test_no_targets(self) -> None:
        """No targets found."""
        result = _summarize_discover(0)
        assert result == "no test targets found"

    def test_with_targets(self) -> None:
        """Shows target count."""
        result = _summarize_discover(5)
        assert result == "5 test targets"


class TestDisplayDiscover:
    """Tests for _display_discover helper."""

    def test_no_targets(self) -> None:
        """No targets message."""
        result = _display_discover(0, [])
        assert result == "No test targets found in this repository."

    def test_single_language(self) -> None:
        """Shows language breakdown."""
        targets = [MagicMock(language="python"), MagicMock(language="python")]
        result = _display_discover(2, targets)
        assert "Found 2 test targets" in result
        assert "2 python" in result


class TestSummarizeRun:
    """Tests for _summarize_run helper."""

    def test_no_status(self) -> None:
        """No run status."""
        result = MagicMock()
        result.run_status = None
        summary = _summarize_run(result)
        assert summary == "no run status"

    def test_with_progress(self) -> None:
        """Shows pass/fail counts."""
        result = MagicMock()
        status = MagicMock()
        status.status = "completed"
        status.duration_seconds = 5.0
        progress = MagicMock()
        cases = MagicMock()
        cases.total = 10
        cases.passed = 8
        cases.failed = 2
        cases.skipped = 0
        progress.cases = cases
        status.progress = progress
        result.run_status = status

        summary = _summarize_run(result)
        assert "8" in summary
        assert "2" in summary
        assert "failed" in summary
