"""Tests for git error types."""

from __future__ import annotations

from codeplane.git.errors import (
    AuthenticationError,
    ConflictError,
    DetachedHeadError,
    DirtyWorkingTreeError,
    NothingToCommitError,
    RebaseConflictError,
    RebaseInProgressError,
    StashNotFoundError,
    SubmoduleNotFoundError,
    SubmoduleNotInitializedError,
    UnmergedBranchError,
    WorktreeExistsError,
    WorktreeLockedError,
    WorktreeNotFoundError,
)


class TestGitErrorMessages:
    """Tests for git error message formatting."""

    def test_unmerged_branch_error(self) -> None:
        """UnmergedBranchError includes branch name."""
        err = UnmergedBranchError("feature")
        assert "feature" in str(err)
        assert "force=True" in str(err)
        assert err.name == "feature"

    def test_nothing_to_commit_error(self) -> None:
        """NothingToCommitError has descriptive message."""
        err = NothingToCommitError()
        assert "Nothing to commit" in str(err)

    def test_conflict_error(self) -> None:
        """ConflictError includes operation and paths."""
        err = ConflictError("merge", ["file1.txt", "file2.txt"])
        assert "merge" in str(err)
        assert "file1.txt" in str(err)
        assert err.operation == "merge"
        assert err.paths == ["file1.txt", "file2.txt"]

    def test_dirty_working_tree_error(self) -> None:
        """DirtyWorkingTreeError includes operation."""
        err = DirtyWorkingTreeError("checkout")
        assert "checkout" in str(err)
        assert err.operation == "checkout"

    def test_detached_head_error(self) -> None:
        """DetachedHeadError includes operation."""
        err = DetachedHeadError("push")
        assert "push" in str(err)
        assert "detached" in str(err)
        assert err.operation == "push"

    def test_authentication_error_with_operation(self) -> None:
        """AuthenticationError includes remote and operation."""
        err = AuthenticationError("origin", "push")
        assert "origin" in str(err)
        assert "push" in str(err)
        assert err.remote == "origin"
        assert err.operation == "push"

    def test_authentication_error_without_operation(self) -> None:
        """AuthenticationError works without operation."""
        err = AuthenticationError("origin")
        assert "origin" in str(err)
        assert err.operation is None

    def test_stash_not_found_error(self) -> None:
        """StashNotFoundError includes index."""
        err = StashNotFoundError(2)
        assert "stash@{2}" in str(err)
        assert err.index == 2

    def test_worktree_not_found_error(self) -> None:
        """WorktreeNotFoundError includes name."""
        err = WorktreeNotFoundError("feature-wt")
        assert "feature-wt" in str(err)
        assert err.name == "feature-wt"

    def test_worktree_exists_error(self) -> None:
        """WorktreeExistsError includes name."""
        err = WorktreeExistsError("feature-wt")
        assert "feature-wt" in str(err)
        assert err.name == "feature-wt"

    def test_worktree_locked_error_with_reason(self) -> None:
        """WorktreeLockedError includes name and reason."""
        err = WorktreeLockedError("feature-wt", "in use")
        assert "feature-wt" in str(err)
        assert "in use" in str(err)
        assert err.name == "feature-wt"
        assert err.reason == "in use"

    def test_worktree_locked_error_without_reason(self) -> None:
        """WorktreeLockedError works without reason."""
        err = WorktreeLockedError("feature-wt")
        assert "feature-wt" in str(err)
        assert err.reason is None

    def test_submodule_not_found_error(self) -> None:
        """SubmoduleNotFoundError includes path."""
        err = SubmoduleNotFoundError("libs/mylib")
        assert "libs/mylib" in str(err)
        assert err.path == "libs/mylib"

    def test_submodule_not_initialized_error(self) -> None:
        """SubmoduleNotInitializedError includes path."""
        err = SubmoduleNotInitializedError("libs/mylib")
        assert "libs/mylib" in str(err)
        assert "not initialized" in str(err)
        assert err.path == "libs/mylib"

    def test_rebase_in_progress_error(self) -> None:
        """RebaseInProgressError has helpful message."""
        err = RebaseInProgressError()
        assert "already in progress" in str(err)
        assert "rebase_continue" in str(err)

    def test_rebase_conflict_error(self) -> None:
        """RebaseConflictError includes paths."""
        err = RebaseConflictError(["file1.txt", "file2.txt"])
        assert "file1.txt" in str(err)
        assert err.paths == ["file1.txt", "file2.txt"]
