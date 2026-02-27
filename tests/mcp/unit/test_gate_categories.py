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
            ("recon", "search"),
            ("recon_resolve", "read"),
            ("refactor_edit", "write"),
            ("refactor_rename", "refactor"),
            ("refactor_move", "refactor"),
            ("refactor_impact", "refactor"),
            ("refactor_commit", "refactor"),
            ("refactor_cancel", "meta"),
            ("semantic_diff", "diff"),
            ("describe", "meta"),
            ("checkpoint", "test"),
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
        """Tools removed in v2 consolidation are NOT in TOOL_CATEGORIES."""
        deleted = [
            "git_status",
            "git_diff",
            "git_log",
            "git_branch",
            "git_remote",
            "git_inspect",
            "git_history",
            "git_submodule",
            "git_worktree",
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
            "lint_check",
            "lint_tools",
            "run_test_targets",
            "discover_test_targets",
            "inspect_affected_tests",
            "commit",
            "verify",
            # v1 tools killed in v2
            "search",
            "read_source",
            "read_file_full",
            "write_source",
            "map_repo",
            "list_files",
            "reset_budget",
            "refactor_apply",
            "refactor_inspect",
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
        assert frozenset({"write", "refactor"}) == ACTION_CATEGORIES

    @pytest.mark.parametrize(
        "excluded",
        ["lint", "test", "diff", "git", "git_read", "search", "read", "read_full", "meta"],
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
        """checkpoint with clears_window clears the pattern window."""
        det = CallPatternDetector()
        det.record("recon")
        det.record("recon")
        det.record("recon")
        det.record("checkpoint", clears_window=True)
        assert det.window_length == 0

    def test_verify_no_clear(self) -> None:
        """checkpoint (test category) does NOT clear the window by default."""
        det = CallPatternDetector()
        det.record("recon")
        det.record("recon")
        det.record("checkpoint")
        assert det.window_length == 3

    def test_semantic_diff_no_clear(self) -> None:
        """semantic_diff (category 'diff') does NOT clear the window."""
        det = CallPatternDetector()
        det.record("recon")
        det.record("recon")
        det.record("semantic_diff")
        assert det.window_length == 3

    def test_clears_window_override(self) -> None:
        """clears_window=True forces clear regardless of category."""
        det = CallPatternDetector()
        det.record("recon")
        det.record("recon")
        det.record("checkpoint", clears_window=True)
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
                CallRecord(category="search", tool_name="recon"),
                CallRecord(category="read", tool_name="recon_resolve"),
            ]
        )
        assert has_recent_scoped_test(window) is False

    def test_has_scoped_test(self) -> None:
        """Returns True when a test_scoped record exists."""
        window: deque[CallRecord] = deque(
            [
                CallRecord(category="search", tool_name="recon"),
                CallRecord(category="test_scoped", tool_name="checkpoint"),
                CallRecord(category="read", tool_name="recon_resolve"),
            ]
        )
        assert has_recent_scoped_test(window) is True

    def test_empty_window(self) -> None:
        """Returns False for an empty window."""
        assert has_recent_scoped_test(deque()) is False
