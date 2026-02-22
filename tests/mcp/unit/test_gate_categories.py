"""Unit tests for tool categorization after git/lint/test consolidation.

Covers:
- categorize_tool() returns correct categories for all remaining tools
- ACTION_CATEGORIES frozenset correctness
- TOOL_CATEGORIES dict completeness
- Window clear behavior (commit clears, verify does not)
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
            ("refactor_impact", "refactor"),
            ("refactor_apply", "refactor"),
            ("refactor_cancel", "meta"),
            ("refactor_inspect", "meta"),
            ("semantic_diff", "diff"),
            ("map_repo", "meta"),
            ("list_files", "meta"),
            ("describe", "meta"),
            ("reset_budget", "meta"),
            ("verify", "test"),
            ("commit", "git"),
        ],
    )
    def test_known_tool_category(self, tool_name: str, expected_category: str) -> None:
        """Each known tool maps to its expected category."""
        assert categorize_tool(tool_name) == expected_category

    def test_unknown_tool_returns_meta(self) -> None:
        """Unknown tool names default to 'meta'."""
        assert categorize_tool("completely_unknown_tool") == "meta"
        assert categorize_tool("") == "meta"

    def test_deleted_tools_are_not_in_categories(self) -> None:
        """Tools removed in consolidation are NOT in TOOL_CATEGORIES."""
        deleted = [
            "git_status", "git_diff", "git_log", "git_branch",
            "git_remote", "git_inspect", "git_history", "git_submodule",
            "git_worktree", "git_commit", "git_stage_and_commit",
            "git_stage", "git_push", "git_pull", "git_checkout",
            "git_merge", "git_reset", "git_stash", "git_rebase",
            "lint_check", "lint_tools", "run_test_targets",
            "discover_test_targets", "inspect_affected_tests",
        ]
        for name in deleted:
            assert name not in TOOL_CATEGORIES, f"{name} should be removed"


# =========================================================================
# ACTION_CATEGORIES
# =========================================================================


class TestActionCategories:
    """Tests for the ACTION_CATEGORIES frozenset."""

    def test_expected_members(self) -> None:
        """ACTION_CATEGORIES contains exactly the expected members."""
        assert frozenset({"write", "refactor", "git"}) == ACTION_CATEGORIES

    @pytest.mark.parametrize(
        "excluded",
        ["lint", "test", "diff", "git_read", "search", "read", "read_full", "meta"],
    )
    def test_non_mutation_categories_excluded(self, excluded: str) -> None:
        assert excluded not in ACTION_CATEGORIES

    def test_no_action_category_values_missing_from_frozenset(self) -> None:
        """Every category that should clear the window is in ACTION_CATEGORIES."""
        action_cats_in_dict = {
            v for v in TOOL_CATEGORIES.values() if v in ("write", "refactor", "git")
        }
        for cat in action_cats_in_dict:
            assert cat in ACTION_CATEGORIES


# =========================================================================
# Window clear behavior
# =========================================================================


class TestWindowClearBehavior:
    """Integration: which tools clear the pattern window and which don't."""

    def test_commit_clears_window(self) -> None:
        """commit (mutating git) clears the pattern window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("search")
        det.record("commit")
        assert det.window_length == 0

    def test_verify_no_clear(self) -> None:
        """verify (test category) does NOT clear the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("verify")
        assert det.window_length == 3

    def test_semantic_diff_no_clear(self) -> None:
        """semantic_diff (category 'diff') does NOT clear the window."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("semantic_diff")
        assert det.window_length == 3

    def test_clears_window_override(self) -> None:
        """clears_window=True forces clear regardless of category."""
        det = CallPatternDetector()
        det.record("search")
        det.record("search")
        det.record("verify", clears_window=True)
        assert det.window_length == 0


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
                CallRecord(category="test_scoped", tool_name="verify"),
                CallRecord(category="read", tool_name="read_source"),
            ]
        )
        assert has_recent_scoped_test(window) is True

    def test_empty_window(self) -> None:
        """Returns False for an empty window."""
        assert has_recent_scoped_test(deque()) is False
