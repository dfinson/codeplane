"""Tests for git/_internal/preconditions.py module.

Covers:
- require_not_unborn()
- require_current_branch()
- require_not_current_branch()
- require_branch_exists()
- check_nothing_to_commit()
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codeplane.git._internal.preconditions import (
    check_nothing_to_commit,
    require_branch_exists,
    require_current_branch,
    require_not_current_branch,
    require_not_unborn,
)
from codeplane.git.errors import (
    BranchNotFoundError,
    DetachedHeadError,
    GitError,
    NothingToCommitError,
)


class TestRequireNotUnborn:
    """Tests for require_not_unborn function."""

    def test_passes_when_not_unborn(self) -> None:
        """Does not raise when repo has commits."""
        access = MagicMock()
        access.is_unborn = False

        # Should not raise
        require_not_unborn(access, "test operation")

    def test_raises_when_unborn(self) -> None:
        """Raises GitError when repo is unborn."""
        access = MagicMock()
        access.is_unborn = True

        with pytest.raises(GitError, match="no commits yet"):
            require_not_unborn(access, "merge")

    def test_error_message_includes_operation(self) -> None:
        """Error message includes the operation name."""
        access = MagicMock()
        access.is_unborn = True

        with pytest.raises(GitError, match="test op"):
            require_not_unborn(access, "test op")


class TestRequireCurrentBranch:
    """Tests for require_current_branch function."""

    def test_returns_branch_name(self) -> None:
        """Returns current branch name when on a branch."""
        access = MagicMock()
        access.current_branch_name.return_value = "main"

        result = require_current_branch(access, "push")
        assert result == "main"

    def test_raises_on_detached_head(self) -> None:
        """Raises DetachedHeadError when HEAD is detached."""
        access = MagicMock()
        access.current_branch_name.return_value = None

        with pytest.raises(DetachedHeadError):
            require_current_branch(access, "push")

    def test_detached_error_includes_operation(self) -> None:
        """DetachedHeadError includes the operation name."""
        access = MagicMock()
        access.current_branch_name.return_value = None

        with pytest.raises(DetachedHeadError) as exc_info:
            require_current_branch(access, "push")

        assert "push" in str(exc_info.value)


class TestRequireNotCurrentBranch:
    """Tests for require_not_current_branch function."""

    def test_passes_for_different_branch(self) -> None:
        """Does not raise when branch is different from current."""
        access = MagicMock()
        access.current_branch_name.return_value = "main"

        # Should not raise
        require_not_current_branch(access, "feature")

    def test_raises_for_current_branch(self) -> None:
        """Raises GitError when trying to operate on current branch."""
        access = MagicMock()
        access.current_branch_name.return_value = "main"

        with pytest.raises(GitError, match="Cannot delete current branch"):
            require_not_current_branch(access, "main")

    def test_error_message_includes_branch(self) -> None:
        """Error message includes the branch name."""
        access = MagicMock()
        access.current_branch_name.return_value = "feature-x"

        with pytest.raises(GitError, match="feature-x"):
            require_not_current_branch(access, "feature-x")


class TestRequireBranchExists:
    """Tests for require_branch_exists function."""

    def test_passes_when_branch_exists(self) -> None:
        """Does not raise when branch exists."""
        access = MagicMock()
        access.has_local_branch.return_value = True

        # Should not raise
        require_branch_exists(access, "main")

    def test_raises_when_branch_not_found(self) -> None:
        """Raises BranchNotFoundError when branch doesn't exist."""
        access = MagicMock()
        access.has_local_branch.return_value = False

        with pytest.raises(BranchNotFoundError):
            require_branch_exists(access, "nonexistent")

    def test_calls_has_local_branch_with_name(self) -> None:
        """Calls has_local_branch with the provided name."""
        access = MagicMock()
        access.has_local_branch.return_value = True

        require_branch_exists(access, "my-branch")
        access.has_local_branch.assert_called_once_with("my-branch")


class TestCheckNothingToCommit:
    """Tests for check_nothing_to_commit function."""

    def test_allow_empty_skips_check(self) -> None:
        """Does not raise when allow_empty is True."""
        access = MagicMock()
        access.is_unborn = True
        access.index = []  # Empty index

        # Should not raise even with empty index
        check_nothing_to_commit(access, allow_empty=True)

    def test_unborn_with_empty_index_raises(self) -> None:
        """Raises NothingToCommitError when unborn and index is empty."""
        access = MagicMock()
        access.is_unborn = True
        access.index = []

        with pytest.raises(NothingToCommitError):
            check_nothing_to_commit(access, allow_empty=False)

    def test_unborn_with_staged_files_passes(self) -> None:
        """Does not raise when unborn but index has files."""
        access = MagicMock()
        access.is_unborn = True
        access.index = ["file1.py", "file2.py"]  # Non-empty index

        # Should not raise
        check_nothing_to_commit(access, allow_empty=False)

    def test_not_unborn_no_changes_raises(self) -> None:
        """Raises NothingToCommitError when diff shows no changes."""
        access = MagicMock()
        access.is_unborn = False

        tree = MagicMock()
        access.must_head_tree.return_value = tree

        diff = MagicMock()
        diff.stats.files_changed = 0
        access.index.diff_to_tree.return_value = diff

        with pytest.raises(NothingToCommitError):
            check_nothing_to_commit(access, allow_empty=False)

    def test_not_unborn_with_changes_passes(self) -> None:
        """Does not raise when diff shows changes."""
        access = MagicMock()
        access.is_unborn = False

        tree = MagicMock()
        access.must_head_tree.return_value = tree

        diff = MagicMock()
        diff.stats.files_changed = 3  # Has changes
        access.index.diff_to_tree.return_value = diff

        # Should not raise
        check_nothing_to_commit(access, allow_empty=False)
