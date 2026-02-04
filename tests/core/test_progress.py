"""Tests for core/progress.py module.

Covers:
- _is_tty() function
- status() function
- progress() generator
- task() context manager
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from codeplane.core.progress import (
    _PROGRESS_THRESHOLD,
    _STYLES,
    _is_tty,
    progress,
    status,
    task,
)


class TestIsTty:
    """Tests for _is_tty function."""

    def test_returns_bool(self) -> None:
        """Returns a boolean."""
        result = _is_tty()
        assert isinstance(result, bool)

    def test_false_for_stringio(self) -> None:
        """Returns False for non-TTY stderr."""
        original = sys.stderr
        try:
            sys.stderr = StringIO()
            assert _is_tty() is False
        finally:
            sys.stderr = original


class TestStyles:
    """Tests for _STYLES constant."""

    def test_has_expected_styles(self) -> None:
        """Contains expected style keys."""
        expected = {"success", "error", "info", "none"}
        assert set(_STYLES.keys()) == expected

    def test_success_style(self) -> None:
        """Success style has checkmark."""
        assert "✓" in _STYLES["success"]

    def test_error_style(self) -> None:
        """Error style has X mark."""
        assert "✗" in _STYLES["error"]


class TestStatus:
    """Tests for status function."""

    def test_prints_message(self) -> None:
        """Prints a message to console."""
        # This test verifies the function doesn't raise
        # We mock the console to avoid actual output
        with patch("codeplane.core.progress._console") as mock_console:
            status("Test message")
            mock_console.print.assert_called_once()

    def test_success_style(self) -> None:
        """Applies success style."""
        with patch("codeplane.core.progress._console") as mock_console:
            status("Done", style="success")
            call_args = mock_console.print.call_args[0][0]
            assert "✓" in call_args

    def test_error_style(self) -> None:
        """Applies error style."""
        with patch("codeplane.core.progress._console") as mock_console:
            status("Failed", style="error")
            call_args = mock_console.print.call_args[0][0]
            assert "✗" in call_args

    def test_with_indent(self) -> None:
        """Applies indentation."""
        with patch("codeplane.core.progress._console") as mock_console:
            status("Indented", indent=4)
            call_args = mock_console.print.call_args[0][0]
            assert call_args.startswith("    ")


class TestProgress:
    """Tests for progress generator."""

    def test_yields_all_items(self) -> None:
        """Yields all items from iterable."""
        items = [1, 2, 3, 4, 5]
        result = list(progress(items))
        assert result == items

    def test_small_list_no_bar(self) -> None:
        """No progress bar for small lists."""
        items = list(range(10))  # Less than threshold
        # Should complete without error
        result = list(progress(items))
        assert len(result) == 10

    def test_with_description(self) -> None:
        """Works with description."""
        items = [1, 2, 3]
        result = list(progress(items, desc="Processing"))
        assert result == [1, 2, 3]

    def test_with_total(self) -> None:
        """Works with explicit total."""
        items = iter([1, 2, 3])  # No len()
        result = list(progress(items, total=3))
        assert result == [1, 2, 3]

    def test_with_unit(self) -> None:
        """Works with custom unit."""
        items = [1, 2]
        result = list(progress(items, unit="items"))
        assert result == [1, 2]

    def test_force_shows_bar(self) -> None:
        """force=True shows bar even for small lists."""
        # This mainly tests that force parameter is accepted
        items = [1, 2, 3]
        result = list(progress(items, force=True))
        assert result == [1, 2, 3]

    def test_threshold_constant(self) -> None:
        """Progress threshold is 100."""
        assert _PROGRESS_THRESHOLD == 100


class TestTask:
    """Tests for task context manager."""

    def test_completes_successfully(self) -> None:
        """Task completes and prints success."""
        with patch("codeplane.core.progress.status") as mock_status:
            with task("Test task"):
                pass  # Do nothing

            # Should have been called with success style at end
            calls = mock_status.call_args_list
            assert len(calls) >= 2  # Start and end

    def test_prints_error_on_failure(self) -> None:
        """Task prints error on exception."""
        with patch("codeplane.core.progress.status") as mock_status:
            with pytest.raises(ValueError), task("Failing task"):
                raise ValueError("test error")

            # Last call should have error style
            last_call = mock_status.call_args_list[-1]
            assert last_call[1].get("style") == "error"

    def test_reports_elapsed_time(self) -> None:
        """Task reports elapsed time."""
        import time

        with patch("codeplane.core.progress.status") as mock_status:
            with task("Timed task"):
                time.sleep(0.1)  # Brief delay

            # Success message should include time
            success_call = mock_status.call_args_list[-1]
            message = success_call[0][0]
            assert "s)" in message  # e.g., "(0.1s)"

    def test_re_raises_exception(self) -> None:
        """Task re-raises the original exception."""
        with pytest.raises(RuntimeError, match="original"), task("Error task"):
            raise RuntimeError("original")
