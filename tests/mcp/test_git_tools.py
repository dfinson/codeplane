"""Tests for MCP git tool summary helpers.

Tests the summary helper functions in mcp/tools/git.py:
- _summarize_status: Git status summary
- _summarize_diff: Git diff summary
- _summarize_commit: Git commit summary
- _summarize_log: Git log summary
- _summarize_branches: Git branches summary
- _summarize_paths: Git paths summary
"""

from codeplane.mcp.tools.git import (
    _summarize_branches,
    _summarize_commit,
    _summarize_diff,
    _summarize_log,
    _summarize_paths,
    _summarize_status,
)


class TestSummarizeStatus:
    """Tests for _summarize_status helper."""

    def test_clean_status(self) -> None:
        """Clean status shows branch."""
        result = _summarize_status(
            branch="main",
            files={},
            is_clean=True,
            state=0,
        )
        assert "clean" in result
        assert "main" in result

    def test_modified_files(self) -> None:
        """Modified files are reported."""
        result = _summarize_status(
            branch="feature",
            files={"a.py": 256, "b.py": 256, "c.py": 512},  # Modified flags
            is_clean=False,
            state=0,
        )
        assert "modified" in result.lower()
        assert "feature" in result

    def test_staged_files(self) -> None:
        """Staged files are reported."""
        result = _summarize_status(
            branch="main",
            files={"a.py": 1, "b.py": 2},  # Staged flags
            is_clean=False,
            state=0,
        )
        assert "staged" in result.lower()

    def test_conflicted_files(self) -> None:
        """Conflicted files are reported."""
        result = _summarize_status(
            branch="main",
            files={"conflict.py": 4096},  # Conflict flag
            is_clean=False,
            state=0,
        )
        assert "conflict" in result.lower()

    def test_detached_head(self) -> None:
        """Detached HEAD shown when no branch."""
        result = _summarize_status(
            branch=None,
            files={},
            is_clean=True,
            state=0,
        )
        assert "detached" in result

    def test_rebase_in_progress(self) -> None:
        """Rebase state is shown."""
        result = _summarize_status(
            branch="main",
            files={},
            is_clean=False,
            state=1,  # Rebase state
        )
        assert "rebase" in result.lower()

    def test_merge_in_progress(self) -> None:
        """Merge state is shown."""
        result = _summarize_status(
            branch="main",
            files={},
            is_clean=False,
            state=2,  # Merge state
        )
        assert "merge" in result.lower()


class TestSummarizeDiff:
    """Tests for _summarize_diff helper."""

    def test_no_changes(self) -> None:
        """No changes shows appropriate message."""
        result = _summarize_diff(
            page_files=0,
            page_additions=0,
            page_deletions=0,
            staged=False,
        )
        assert "no change" in result.lower()

    def test_no_staged_changes(self) -> None:
        """No staged changes with staged flag."""
        result = _summarize_diff(
            page_files=0,
            page_additions=0,
            page_deletions=0,
            staged=True,
        )
        assert "no staged" in result.lower()

    def test_single_file_diff(self) -> None:
        """Single file with changes."""
        result = _summarize_diff(
            page_files=1,
            page_additions=10,
            page_deletions=5,
            staged=False,
        )
        assert "1" in result
        assert "+10" in result
        assert "-5" in result

    def test_multiple_files(self) -> None:
        """Multiple files changed."""
        result = _summarize_diff(
            page_files=5,
            page_additions=50,
            page_deletions=30,
            staged=False,
        )
        assert "5" in result
        assert "+50" in result
        assert "-30" in result

    def test_staged_prefix(self) -> None:
        """Staged diff has prefix."""
        result = _summarize_diff(
            page_files=2,
            page_additions=20,
            page_deletions=10,
            staged=True,
        )
        assert "staged" in result.lower()

    def test_paginated_summary(self) -> None:
        """Paginated summary shows page/total."""
        result = _summarize_diff(
            page_files=3,
            page_additions=30,
            page_deletions=10,
            staged=False,
            total_files=10,
        )
        # Should show "3/10 files" format
        assert "3" in result
        assert "10" in result
        assert "+30" in result
        assert "-10" in result


class TestSummarizeCommit:
    """Tests for _summarize_commit helper."""

    def test_short_sha(self) -> None:
        """Shows short SHA."""
        result = _summarize_commit(
            sha="abc123def456789",
            message="Fix bug in parser",
        )
        assert "abc123d" in result

    def test_message_included(self) -> None:
        """Message is included."""
        result = _summarize_commit(
            sha="abc123def456789",
            message="Add new feature",
        )
        assert "Add new feature" in result or "add new" in result.lower()

    def test_long_message_truncated(self) -> None:
        """Long messages are truncated."""
        long_message = "This is a very long commit message that should be truncated " * 3
        result = _summarize_commit(
            sha="abc123def456789",
            message=long_message,
        )
        # Result should be shorter than full message
        assert len(result) < len(long_message)

    def test_multiline_message_first_line(self) -> None:
        """Only first line of multiline message."""
        result = _summarize_commit(
            sha="abc123def456789",
            message="First line\nSecond line\nThird line",
        )
        assert "First line" in result
        assert "Second line" not in result


class TestSummarizeLog:
    """Tests for _summarize_log helper."""

    def test_single_commit(self) -> None:
        """Single commit log."""
        result = _summarize_log(count=1, has_more=False)
        assert "1" in result
        assert "commit" in result.lower()

    def test_multiple_commits(self) -> None:
        """Multiple commits log."""
        result = _summarize_log(count=10, has_more=False)
        assert "10" in result
        assert "commit" in result.lower()

    def test_has_more_indicator(self) -> None:
        """Shows more available when has_more=True."""
        result = _summarize_log(count=50, has_more=True)
        assert "50" in result
        assert "more" in result.lower() or "available" in result.lower()

    def test_no_more_indicator(self) -> None:
        """No indicator when has_more=False."""
        result = _summarize_log(count=5, has_more=False)
        assert "more" not in result.lower() or "5 commits" in result

    def test_zero_commits(self) -> None:
        """Zero commits."""
        result = _summarize_log(count=0, has_more=False)
        assert "0" in result


class TestSummarizeBranches:
    """Tests for _summarize_branches helper."""

    def test_with_current_branch(self) -> None:
        """Shows current branch."""
        result = _summarize_branches(count=5, current="main")
        assert "5" in result
        assert "main" in result
        assert "branch" in result.lower()

    def test_without_current_branch(self) -> None:
        """Works without current (detached HEAD)."""
        result = _summarize_branches(count=3, current=None)
        assert "3" in result
        assert "branch" in result.lower()

    def test_single_branch(self) -> None:
        """Single branch."""
        result = _summarize_branches(count=1, current="main")
        assert "1" in result


class TestSummarizePaths:
    """Tests for _summarize_paths helper."""

    def test_empty_paths(self) -> None:
        """Empty paths list."""
        result = _summarize_paths(action="stage", paths=[])
        assert "nothing" in result.lower()
        assert "stage" in result.lower()

    def test_single_path(self) -> None:
        """Single path."""
        result = _summarize_paths(action="staged", paths=["src/main.py"])
        assert "staged" in result.lower()
        assert "main.py" in result or "src" in result

    def test_multiple_paths(self) -> None:
        """Multiple paths."""
        result = _summarize_paths(
            action="unstaged",
            paths=["a.py", "b.py", "c.py"],
        )
        assert "unstaged" in result.lower()

    def test_action_prefix(self) -> None:
        """Different actions."""
        result = _summarize_paths(action="discarded", paths=["file.py"])
        assert "discarded" in result.lower()
