"""Tests for MCP lint tool.

Tests the actual exports:
- _summarize_lint() helper
- _display_lint_check() helper

Handler tests use conftest.py fixtures for integration testing.
"""

from codeplane.mcp.tools.lint import (
    _display_lint_check,
    _summarize_lint,
)


class TestSummarizeLint:
    """Tests for _summarize_lint helper."""

    def test_clean(self):
        """Clean status."""
        result = _summarize_lint("clean", 0, 0, False)
        assert result == "✓ clean"

    def test_clean_dry_run(self):
        """Clean status with dry run."""
        result = _summarize_lint("clean", 0, 0, True)
        assert result.startswith("(dry-run)")
        assert "✓ clean" in result

    def test_with_diagnostics(self):
        """Shows diagnostic count."""
        result = _summarize_lint("issues_found", 5, 0, False)
        assert "5 issues" in result
        assert "✗" in result

    def test_with_fixes(self):
        """Shows fixed files count."""
        result = _summarize_lint("fixed", 0, 3, False)
        assert "3 fixed" in result
        assert "✓" in result


class TestDisplayLintCheck:
    """Tests for _display_lint_check helper."""

    def test_clean(self):
        """Clean result message."""
        result = _display_lint_check("clean", 0, 0, False)
        assert result == "All checks passed - no issues found."

    def test_with_fixes(self):
        """Shows fix count and remaining."""
        result = _display_lint_check("partial_fix", 2, 3, False)
        assert "3 files auto-fixed" in result

    def test_issues_only(self):
        """Issues without fixes."""
        result = _display_lint_check("issues_found", 5, 0, False)
        assert "5 issues found" in result
