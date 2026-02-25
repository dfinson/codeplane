"""Unit tests for verify.py helpers.

Tests the pure helper functions:
- _summarize_verify
- _summarize_run
- _target_matches_affected_files
- _normalize_selector
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codeplane.mcp.tools.checkpoint import (
    _normalize_selector,
    _summarize_run,
    _summarize_verify,
    _target_matches_affected_files,
)

# =========================================================================
# _summarize_verify
# =========================================================================


class TestSummarizeVerify:
    """Tests for _summarize_verify."""

    def test_all_clean(self) -> None:
        result = _summarize_verify("clean", 0, 5, 0, "completed")
        assert "lint: clean" in result
        assert "tests: 5 passed" in result

    def test_lint_issues_tests_failed(self) -> None:
        result = _summarize_verify("issues", 3, 4, 2, "completed")
        assert "lint: 3 issues" in result
        assert "2 FAILED" in result

    def test_skipped(self) -> None:
        result = _summarize_verify("skipped", 0, 0, 0, "skipped")
        assert "lint: skipped" in result
        assert "tests: skipped" in result

    def test_no_tests(self) -> None:
        result = _summarize_verify("clean", 0, 0, 0, "completed")
        assert "lint: clean" in result
        assert "tests: completed" in result


# =========================================================================
# _summarize_run
# =========================================================================


class TestSummarizeRun:
    """Tests for _summarize_run."""

    def test_no_status(self) -> None:
        result = MagicMock()
        result.run_status = None
        assert _summarize_run(result) == "no run status"

    def test_completed_all_passed(self) -> None:
        result = MagicMock()
        result.run_status.status = "completed"
        result.run_status.duration_seconds = 2.5
        result.run_status.progress.cases.passed = 10
        result.run_status.progress.cases.failed = 0
        assert "10 passed" in _summarize_run(result)
        assert "2.5s" in _summarize_run(result)

    def test_completed_with_failures(self) -> None:
        result = MagicMock()
        result.run_status.status = "completed"
        result.run_status.duration_seconds = 3.0
        result.run_status.progress.cases.passed = 8
        result.run_status.progress.cases.failed = 2
        summary = _summarize_run(result)
        assert "8 passed" in summary
        assert "2 failed" in summary


# =========================================================================
# _normalize_selector
# =========================================================================


class TestNormalizeSelector:
    """Tests for _normalize_selector."""

    def test_current_dir(self) -> None:
        assert _normalize_selector(".") == ""

    def test_go_wildcard(self) -> None:
        assert _normalize_selector("./...") == ""

    def test_relative_path(self) -> None:
        assert _normalize_selector("./pkg/foo") == "pkg/foo"

    def test_plain_path(self) -> None:
        assert _normalize_selector("tests/unit") == "tests/unit"


# =========================================================================
# _target_matches_affected_files
# =========================================================================


class TestTargetMatchesAffectedFiles:
    """Tests for _target_matches_affected_files."""

    def test_exact_file_match(self) -> None:
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "./tests/test_foo.py"
        assert _target_matches_affected_files(target, {"tests/test_foo.py"}, Path("/repo"))

    def test_no_match(self) -> None:
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "./tests/test_bar.py"
        assert not _target_matches_affected_files(target, {"tests/test_foo.py"}, Path("/repo"))

    def test_package_scope(self) -> None:
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "./tests"
        assert _target_matches_affected_files(target, {"tests/test_foo.py"}, Path("/repo"))

    def test_root_scope_matches_all(self) -> None:
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "."
        assert _target_matches_affected_files(target, {"src/foo.py"}, Path("/repo"))

    def test_empty_affected_paths(self) -> None:
        target = MagicMock()
        target.workspace_root = "/repo"
        target.selector = "."
        assert not _target_matches_affected_files(target, set(), Path("/repo"))
