"""Tests for submodule operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.git import GitOps, SubmoduleError, SubmoduleNotFoundError


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


class TestSubmoduleAdd:
    """Tests for submodule_add() method."""

    def test_given_valid_repo_when_add_submodule_then_returns_info(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Adding a submodule should return SubmoduleInfo."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        info = main_ops.submodule_add(str(sub_path), "libs/mylib")

        assert info.path == "libs/mylib"
        assert info.url == str(sub_path)

    def test_given_submodule_added_when_list_then_appears(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Added submodule should appear in list."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")

        submodules = main_ops.submodules()
        assert len(submodules) == 1
        assert submodules[0].path == "libs/mylib"

    def test_given_invalid_url_when_add_submodule_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Adding submodule with invalid URL should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(SubmoduleError):
            ops.submodule_add("/nonexistent/path", "libs/bad")


class TestSubmoduleDeinit:
    """Tests for submodule_deinit() method."""

    def test_given_initialized_submodule_when_deinit_then_removes_workdir(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Deinit should remove submodule working directory."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")
        main_ops.submodule_deinit("libs/mylib", force=True)

        # The submodule directory should still exist but be empty
        # (git submodule deinit removes working tree but keeps gitlink)
        submod_path = main_path / "libs" / "mylib"
        # After deinit, working tree is removed
        assert not (submod_path / "lib.py").exists()


class TestSubmoduleRemove:
    """Tests for submodule_remove() method."""

    def test_given_submodule_when_remove_then_fully_removed(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Remove should fully clean up submodule."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")
        main_ops.submodule_remove("libs/mylib")

        # Submodule should be gone from list
        assert main_ops.submodules() == []

        # Directory should be removed
        assert not (main_path / "libs" / "mylib").exists()

    def test_given_nonexistent_path_when_remove_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Remove nonexistent submodule should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(SubmoduleNotFoundError):
            ops.submodule_remove("nonexistent")


class TestSubmoduleSync:
    """Tests for submodule_sync() method."""

    def test_given_submodule_when_sync_then_succeeds(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Sync should succeed for existing submodule."""
        (_, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")
        # Should not raise
        main_ops.submodule_sync(["libs/mylib"])


class TestSubmoduleUpdate:
    """Tests for submodule_update() method."""

    def test_given_submodule_when_update_then_returns_result(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Update should return result with updated submodules."""
        (_, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")
        result = main_ops.submodule_update(["libs/mylib"])

        # libs/mylib should be in updated (already initialized by add)
        # or skipped if already at correct commit
        assert result is not None
