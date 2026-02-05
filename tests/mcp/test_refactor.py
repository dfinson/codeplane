"""Tests for MCP refactor tools.

Tests the actual exports:
- _summarize_refactor() helper
- _display_refactor() helper

Handler tests use conftest.py fixtures for integration testing.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.refactor import (
    _display_refactor,
    _summarize_refactor,
)


class TestSummarizeRefactor:
    """Tests for _summarize_refactor helper."""

    def test_cancelled(self):
        """Cancelled status."""
        result = _summarize_refactor("cancelled", 0, None)
        assert result == "refactoring cancelled"

    def test_applied(self):
        """Applied status shows file count."""
        result = _summarize_refactor("applied", 5, None)
        assert result == "applied to 5 files"

    def test_pending_with_preview(self):
        """Pending with preview shows change counts."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 3
        preview.low_certainty_count = 2

        result = _summarize_refactor("pending", 3, preview)
        assert "preview" in result
        assert "15 changes" in result


class TestDisplayRefactor:
    """Tests for _display_refactor helper."""

    def test_cancelled(self):
        """Cancelled message."""
        result = _display_refactor("cancelled", 0, None, "ref_123")
        assert result == "Refactoring cancelled."

    def test_applied(self):
        """Applied message."""
        result = _display_refactor("applied", 3, None, "ref_123")
        assert "Refactoring applied" in result
        assert "3 files modified" in result
