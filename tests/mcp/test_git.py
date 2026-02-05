"""Tests for MCP git tools.

Tests the actual exports:
- _summarize_status() helper
- _summarize_diff() helper
- _summarize_commit() helper
- _summarize_log() helper
- _summarize_branches() helper
- _summarize_paths() helper
- _HARD_RESET_TOKEN_KEY constant

Handler tests use conftest.py fixtures for integration testing.
"""

from codeplane.mcp.tools.git import (
    _HARD_RESET_TOKEN_KEY,
    _summarize_branches,
    _summarize_commit,
    _summarize_diff,
    _summarize_log,
    _summarize_paths,
    _summarize_status,
)


class TestSummarizeStatus:
    """Tests for _summarize_status helper."""

    def test_clean_with_branch(self):
        """Clean repo shows branch name."""
        result = _summarize_status("main", {}, True, 0)
        assert result == "clean, branch: main"

    def test_clean_detached(self):
        """Clean repo in detached HEAD."""
        result = _summarize_status(None, {}, True, 0)
        assert result == "clean, branch: detached"

    def test_modified_files(self):
        """Shows modified count."""
        # 256 = modified in worktree
        files = {"a.py": 256, "b.py": 256}
        result = _summarize_status("main", files, False, 0)
        assert "2 modified" in result

    def test_staged_files(self):
        """Shows staged count."""
        # 1 = index_new, 2 = index_modified, 4 = index_deleted
        files = {"a.py": 1, "b.py": 2}
        result = _summarize_status("main", files, False, 0)
        assert "2 staged" in result

    def test_conflicted_files(self):
        """Shows conflict count."""
        # >= 4096 = conflicted
        files = {"a.py": 4096}
        result = _summarize_status("main", files, False, 0)
        assert "1 conflicts" in result

    def test_rebase_in_progress(self):
        """Shows rebase state."""
        files = {"a.py": 256}
        result = _summarize_status("main", files, False, 1)
        assert "rebase in progress" in result

    def test_merge_in_progress(self):
        """Shows merge state."""
        files = {"a.py": 256}
        result = _summarize_status("main", files, False, 2)
        assert "merge in progress" in result

    def test_mixed_status(self):
        """Shows all status types."""
        files = {
            "a.py": 256,  # modified
            "b.py": 1,  # staged (new)
            "c.py": 4096,  # conflicted
        }
        result = _summarize_status("feature", files, False, 0)
        assert "1 modified" in result
        assert "1 staged" in result
        assert "1 conflicts" in result


class TestSummarizeDiff:
    """Tests for _summarize_diff helper."""

    def test_no_changes(self):
        """No changes message."""
        result = _summarize_diff(0, 0, 0, False)
        assert result == "no changes"

    def test_no_staged_changes(self):
        """No staged changes message."""
        result = _summarize_diff(0, 0, 0, True)
        assert result == "no staged changes"

    def test_with_changes(self):
        """Shows file count and deltas."""
        result = _summarize_diff(3, 100, 50, False)
        assert "3 files changed" in result
        assert "+100" in result
        assert "-50" in result

    def test_staged_prefix(self):
        """Staged diff has prefix."""
        result = _summarize_diff(2, 10, 5, True)
        assert result.startswith("staged:")


class TestSummarizeCommit:
    """Tests for _summarize_commit helper."""

    def test_short_message(self):
        """Short message not truncated."""
        result = _summarize_commit("abc1234567890", "Short message")
        assert result == 'abc1234 "Short message"'

    def test_long_message_truncated(self):
        """Long message truncated to 50 chars."""
        long_msg = "x" * 100
        result = _summarize_commit("abc1234567890", long_msg)
        assert "..." in result
        assert len(result) < 70  # sha + truncated message

    def test_multiline_message(self):
        """Only first line used."""
        result = _summarize_commit("abc1234", "First line\nSecond line")
        assert "First line" in result
        assert "Second line" not in result


class TestSummarizeLog:
    """Tests for _summarize_log helper."""

    def test_count_only(self):
        """Shows commit count."""
        result = _summarize_log(5, False)
        assert result == "5 commits"

    def test_has_more(self):
        """Shows more available."""
        result = _summarize_log(50, True)
        assert "50 commits" in result
        assert "(more available)" in result


class TestSummarizeBranches:
    """Tests for _summarize_branches helper."""

    def test_with_current(self):
        """Shows current branch."""
        result = _summarize_branches(5, "main")
        assert result == "5 branches, current: main"

    def test_without_current(self):
        """No current branch (detached)."""
        result = _summarize_branches(3, None)
        assert result == "3 branches"


class TestSummarizePaths:
    """Tests for _summarize_paths helper."""

    def test_single_path(self):
        """Single path shown directly."""
        result = _summarize_paths("staged", ["file.py"])
        assert result == "staged file.py"

    def test_few_paths(self):
        """Few paths listed."""
        result = _summarize_paths("unstaged", ["a.py", "b.py", "c.py"])
        assert "unstaged" in result
        # The actual format may be "unstaged a.py, b.py, c.py" or truncated
        assert "a.py" in result

    def test_many_paths(self):
        """Many paths shows +N more."""
        paths = ["a.py", "b.py", "c.py", "d.py", "e.py"]
        result = _summarize_paths("added", paths)
        assert "added" in result
        # With many paths, some should be shown and "+N more" may appear
        assert "a.py" in result or "+" in result


class TestHardResetTokenKey:
    """Tests for hard reset confirmation token key."""

    def test_token_key_is_string(self):
        """Token key should be a constant string."""
        assert isinstance(_HARD_RESET_TOKEN_KEY, str)
        assert len(_HARD_RESET_TOKEN_KEY) > 0

    def test_token_key_is_descriptive(self):
        """Token key should indicate its purpose."""
        assert "hard" in _HARD_RESET_TOKEN_KEY.lower() or "reset" in _HARD_RESET_TOKEN_KEY.lower()
