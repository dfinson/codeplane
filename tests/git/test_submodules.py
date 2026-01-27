"""Tests for submodule operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.git import GitOps, SubmoduleNotFoundError


class TestSubmodulesList:
    """Tests for submodules() method."""

    def test_given_repo_without_submodules_when_list_then_empty(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Repo without submodules should return empty list."""
        _, ops = git_repo_with_commit

        submodules = ops.submodules()

        assert submodules == []


class TestSubmoduleInit:
    """Tests for submodule_init() method."""

    def test_given_no_submodules_when_init_with_path_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Init nonexistent submodule should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(SubmoduleNotFoundError):
            ops.submodule_init(["nonexistent"])


class TestSubmoduleStatus:
    """Tests for submodule_status() method."""

    def test_given_no_submodules_when_status_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Status for nonexistent submodule should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(SubmoduleNotFoundError):
            ops.submodule_status("nonexistent")


# Note: Full submodule tests require creating a separate repository
# to use as a submodule source, which adds complexity.
# These tests verify basic error handling.
# Integration tests with actual submodules would be in tests/integration/
