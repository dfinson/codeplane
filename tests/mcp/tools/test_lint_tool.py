"""Tests for mcp/tools/lint.py module.

Covers:
- _summarize_lint helper
- _display_lint_check helper
"""

from __future__ import annotations

from codeplane.mcp.tools.lint import _display_lint_check, _summarize_lint


class TestSummarizeLint:
    """Tests for _summarize_lint helper."""

    def test_clean(self) -> None:
        """Clean status."""
        result = _summarize_lint(
            status="clean", total_diagnostics=0, files_modified=0, dry_run=False
        )
        assert result == "clean"

    def test_clean_dry_run(self) -> None:
        """Clean status with dry run."""
        result = _summarize_lint(
            status="clean", total_diagnostics=0, files_modified=0, dry_run=True
        )
        assert result == "(dry-run) clean"

    def test_with_diagnostics(self) -> None:
        """Has diagnostics only."""
        result = _summarize_lint(
            status="dirty", total_diagnostics=5, files_modified=0, dry_run=False
        )
        assert "5 issues" in result

    def test_with_fixes(self) -> None:
        """Files were fixed."""
        result = _summarize_lint(
            status="fixed", total_diagnostics=0, files_modified=3, dry_run=False
        )
        assert "3 fixed" in result

    def test_with_fixes_and_remaining(self) -> None:
        """Some fixed, some remain."""
        result = _summarize_lint(
            status="partial", total_diagnostics=2, files_modified=3, dry_run=False
        )
        assert "3 fixed" in result
        assert "2 remain" in result

    def test_dry_run_with_issues(self) -> None:
        """Dry run shows prefix."""
        result = _summarize_lint(
            status="dirty", total_diagnostics=5, files_modified=0, dry_run=True
        )
        assert "(dry-run)" in result
        assert "5 issues" in result


class TestDisplayLintCheck:
    """Tests for _display_lint_check helper."""

    def test_clean(self) -> None:
        """Clean status message."""
        result = _display_lint_check(
            status="clean", total_diagnostics=0, files_modified=0, dry_run=False
        )
        assert result is not None
        assert "passed" in result.lower() or "no issues" in result.lower()

    def test_with_issues(self) -> None:
        """Has issues message."""
        result = _display_lint_check(
            status="dirty", total_diagnostics=5, files_modified=0, dry_run=False
        )
        assert result is not None
        assert "5" in result
        assert "issues" in result.lower() or "found" in result.lower()

    def test_with_fixes(self) -> None:
        """Fixed files message."""
        result = _display_lint_check(
            status="fixed", total_diagnostics=2, files_modified=3, dry_run=False
        )
        assert result is not None
        assert "3" in result
        assert "fixed" in result.lower() or "auto" in result.lower()

    def test_dry_run_prefix(self) -> None:
        """Dry run message includes prefix."""
        result = _display_lint_check(
            status="dirty", total_diagnostics=5, files_modified=0, dry_run=True
        )
        assert result is not None
        assert "dry" in result.lower()
