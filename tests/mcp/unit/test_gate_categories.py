"""Unit tests for tool categorization and git read/write split.

Covers:
- categorize_tool() returns correct categories for all known tools
- git_read tools are NOT in ACTION_CATEGORIES
- git (mutating) tools ARE in ACTION_CATEGORIES
- diff is NOT in ACTION_CATEGORIES
- ACTION_CATEGORIES frozenset completeness
- TOOL_CATEGORIES dict completeness
- has_recent_scoped_test() helper
"""

from __future__ import annotations

from collections import deque

import pytest

from codeplane.mcp.gate import (
    ACTION_CATEGORIES,
    TOOL_CATEGORIES,
    CallPatternDetector,
    CallRecord,
    categorize_tool,
    has_recent_scoped_test,
)

# =========================================================================
# categorize_tool()
# =========================================================================


class TestCategorizeTool:
    """Tests for categorize_tool()."""

    @pytest.mark.parametrize(
        "tool_name,expected_category",
        [
            ("search", "search"),
            ("read_source", "read"),
            ("read_file_full", "read_full"),
            ("write_source", "write"),
            ("refactor_rename", "refactor"),
            ("refactor_move", "refactor"),
            ("refactor_delete", "refactor"),
            ("refactor_apply", "refactor"),
            ("refactor_cancel", "meta"),
            ("refactor_inspect", "meta"),
            ("lint_check", "lint"),
            ("lint_tools", "meta"),
            ("run_test_targets", "test"),
            ("discover_test_targets", "meta"),
            ("get_test_run_status", "meta"),
            ("cancel_test_run", "meta"),
            ("semantic_diff", "diff"),
            ("map_repo", "meta"),
            ("list_files", "meta"),
            ("describe", "meta"),
            ("reset_budget", "meta"),
            ("inspect_affected_tests", "meta"),
        ],
    )
    def test_known_tool_category(self, tool_name: str, expected_category: str) -> None:
        """Each known tool maps to its expected category."""
        assert categorize_tool(tool_name) == expected_category

    def test_unknown_tool_returns_meta(self) -> None:
        """Unknown tool names default to 'meta'."""
        assert categorize_tool("completely_unknown_tool") == "meta"
        assert categorize_tool("") == "meta"


# =========================================================================
# Git read/write split
# =========================================================================


class TestGitReadWriteSplit:
    """Tests for git_read vs git (mutating) categorization."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "git_status",
            "git_diff",
            "git_log",
            "git_branch",
            "git_remote",
            "git_inspect",
            "git_history",
            "git_submodule",
            "git_worktree",
        ],
    )
    def test_git_read_tools_categorize_as_git_read(self, tool_name: str) -> None:
        """Read-only git tools are categorized as 'git_read'."""
        assert categorize_tool(tool_name) == "git_read"

    @pytest.mark.parametrize(
        "tool_name",
        [
            "git_commit",
            "git_stage_and_commit",
            "git_stage",
            "git_push",
            "git_pull",
            "git_checkout",
            "git_merge",
            "git_reset",
            "git_stash",
            "git_rebase",
        ],
    )
    def test_git_write_tools_categorize_as_git(self, tool_name: str) -> None:
        """Mutating git tools are categorized as 'git'."""
        assert categorize_tool(tool_name) == "git"

    @pytest.mark.parametrize(
        "tool_name",
        [
            "git_status",
            "git_diff",
            "git_log",
            "git_branch",
            "git_remote",
            "git_inspect",
            "git_history",
            "git_submodule",
            "git_worktree",
        ],
    )
    def test_git_read_not_in_action_categories(self, tool_name: str) -> None:
        """git_read is NOT in ACTION_CATEGORIES."""
        cat = categorize_tool(tool_name)
        assert cat not in ACTION_CATEGORIES

    @pytest.mark.parametrize(
        "tool_name",
        [
            "git_commit",
            "git_stage_and_commit",
            "git_stage",
            "git_push",
            "git_pull",
            "git_checkout",
            "git_merge",
            "git_reset",
            "git_stash",
            "git_rebase",
        ],
    )
    def test_git_write_in_action_categories(self, tool_name: str) -> None:
        """Mutating git tools ('git' category) ARE in ACTION_CATEGORIES."""
        cat = categorize_tool(tool_name)
        assert cat in ACTION_CATEGORIES


# =========================================================================
# ACTION_CATEGORIES
# =========================================================================


class TestActionCategories:
    """Tests for the ACTION_CATEGORIES frozenset."""

    def test_expected_members(self) -> None:
        """ACTION_CATEGORIES contains exactly the expected members."""
        assert frozenset({"write", "refactor", "lint", "test", "git"}) == ACTION_CATEGORIES

    def test_diff_not_included(self) -> None:
        """'diff' is NOT in ACTION_CATEGORIES."""
        assert "diff" not in ACTION_CATEGORIES

    def test_git_read_not_included(self) -> None:
        """'git_read' is NOT in ACTION_CATEGORIES."""
        assert "git_read" not in ACTION_CATEGORIES

    def test_search_not_included(self) -> None:
        """'search' is NOT in ACTION_CATEGORIES."""
        assert "search" not in ACTION_CATEGORIES

    def test_read_not_included(self) -> None:
        """'read' and 'read_full' are NOT in ACTION_CATEGORIES."""
        assert "read" not in ACTION_CATEGORIES
        assert "read_full" not in ACTION_CATEGORIES

    def test_meta_not_included(self) -> None:
        """'meta' is NOT in ACTION_CATEGORIES."""
        assert "meta" not in ACTION_CATEGORIES


# =========================================================================
# TOOL_CATEGORIES completeness
# =========================================================================


class TestToolCategoriesCompleteness:
    """Verify the TOOL_CATEGORIES dict is consistent."""

    def test_all_git_read_tools_present(self) -> None:
        """All expected git read tools are in TOOL_CATEGORIES."""
        expected = [
            "git_status",
            "git_diff",
            "git_log",
            "git_branch",
            "git_remote",
            "git_inspect",
            "git_history",
            "git_submodule",
            "git_worktree",
        ]
        for tool in expected:
            assert tool in TOOL_CATEGORIES, f"{tool} missing from TOOL_CATEGORIES"
            assert TOOL_CATEGORIES[tool] == "git_read"

    def test_all_git_write_tools_present(self) -> None:
        """All expected git write tools are in TOOL_CATEGORIES."""
        expected = [
            "git_commit",
            "git_stage_and_commit",
            "git_stage",
            "git_push",
            "git_pull",
            "git_checkout",
            "git_merge",
            "git_reset",
            "git_stash",
            "git_rebase",
        ]
        for tool in expected:
            assert tool in TOOL_CATEGORIES, f"{tool} missing from TOOL_CATEGORIES"
            assert TOOL_CATEGORIES[tool] == "git"

    def test_no_action_category_values_missing_from_frozenset(self) -> None:
        """Every category that should clear the window is in ACTION_CATEGORIES."""
        action_cats_in_dict = {
            v for v in TOOL_CATEGORIES.values() if v in ("write", "refactor", "lint", "test", "git")
        }
        for cat in action_cats_in_dict:
            assert cat in ACTION_CATEGORIES


# =========================================================================
# Git read does not clear pattern window (integration)
# =========================================================================


class TestGitReadWindowBehavior:
    """Integration: git_read tools do NOT clear the pattern window."""

    def test_git_status_no_clear(self) -> None:
        """git_status does not clear the pattern window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("search")
        det.record("git_status")
        assert det.window_length == 4

    def test_git_log_no_clear(self) -> None:
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("git_log")
        assert det.window_length == 3

    def test_git_diff_no_clear(self) -> None:
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("git_diff")
        assert det.window_length == 3

    def test_git_commit_does_clear(self) -> None:
        """git_commit (mutating) DOES clear the pattern window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("search")
        det.record("git_commit")
        assert det.window_length == 0

    def test_git_stage_and_commit_does_clear(self) -> None:
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("git_stage_and_commit")
        assert det.window_length == 0

    def test_semantic_diff_no_clear(self) -> None:
        """semantic_diff (category 'diff') does NOT clear the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("semantic_diff")
        assert det.window_length == 3


# =========================================================================
# has_recent_scoped_test
# =========================================================================


class TestHasRecentScopedTest:
    """Tests for the has_recent_scoped_test() helper."""

    def test_no_scoped_test(self) -> None:
        """Returns False when no test_scoped records exist."""
        window: deque[CallRecord] = deque(
            [
                CallRecord(category="search", tool_name="search"),
                CallRecord(category="read", tool_name="read_source"),
            ]
        )
        assert has_recent_scoped_test(window) is False

    def test_has_scoped_test(self) -> None:
        """Returns True when a test_scoped record exists."""
        window: deque[CallRecord] = deque(
            [
                CallRecord(category="search", tool_name="search"),
                CallRecord(category="test_scoped", tool_name="run_test_targets"),
                CallRecord(category="read", tool_name="read_source"),
            ]
        )
        assert has_recent_scoped_test(window) is True

    def test_empty_window(self) -> None:
        """Returns False for an empty window."""
        assert has_recent_scoped_test(deque()) is False
