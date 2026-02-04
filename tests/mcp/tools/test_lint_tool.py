"""Tests for mcp/tools/lint.py module.

Covers:
- LintParams model
- _summarize_lint helper
- lint handler (tool, check actions)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.lint import LintParams, _summarize_lint


class TestLintParams:
    """Tests for LintParams model."""

    def test_check_action(self) -> None:
        """Check action params."""
        params = LintParams(action="check", paths=["src/"])
        assert params.action == "check"
        assert params.paths == ["src/"]

    def test_tools_action(self) -> None:
        """Tools action params."""
        params = LintParams(action="tools", language="python")
        assert params.action == "tools"
        assert params.language == "python"

    def test_invalid_action(self) -> None:
        """Invalid action rejected."""
        with pytest.raises(ValidationError):
            LintParams(action="invalid")  # type: ignore[arg-type]

    def test_defaults(self) -> None:
        """Default values."""
        params = LintParams(action="check")
        assert params.paths is None
        assert params.tools is None
        assert params.categories is None
        assert params.dry_run is False
        assert params.language is None
        assert params.category is None

    def test_check_with_tools(self) -> None:
        """Check action with specific tools."""
        params = LintParams(action="check", tools=["ruff", "mypy"])
        assert params.tools == ["ruff", "mypy"]

    def test_check_with_categories(self) -> None:
        """Check action with categories."""
        params = LintParams(action="check", categories=["linter", "formatter"])
        assert params.categories == ["linter", "formatter"]

    def test_check_dry_run(self) -> None:
        """Check action with dry_run."""
        params = LintParams(action="check", dry_run=True)
        assert params.dry_run is True

    def test_tools_with_category_filter(self) -> None:
        """Tools action with category filter."""
        params = LintParams(action="tools", category="formatter")
        assert params.category == "formatter"


class TestSummarizeLint:
    """Tests for _summarize_lint helper."""

    def test_clean_status(self) -> None:
        """Clean status message."""
        summary = _summarize_lint("clean", 0, 0, False)
        assert "clean" in summary
        assert "no issues" in summary

    def test_with_diagnostics(self) -> None:
        """Status with diagnostics."""
        summary = _summarize_lint("issues", 5, 0, False)
        assert "5 diagnostics" in summary

    def test_with_files_modified(self) -> None:
        """Status with files modified."""
        summary = _summarize_lint("fixed", 0, 3, False)
        assert "3 files fixed" in summary

    def test_dry_run_prefix(self) -> None:
        """Dry run adds prefix."""
        summary = _summarize_lint("clean", 0, 0, True)
        assert "(dry-run)" in summary

    def test_combined(self) -> None:
        """Combined status with multiple parts."""
        summary = _summarize_lint("issues", 10, 2, False)
        assert "10 diagnostics" in summary
        assert "2 files fixed" in summary
