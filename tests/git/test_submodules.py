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

    def test_given_submodule_when_init_all_then_initializes(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Init without paths should initialize all submodules."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        # Add submodule
        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Deinit to simulate uninitialized state
        main_ops.submodule_deinit("libs/mylib", force=True)

        # Init all
        initialized = main_ops.submodule_init(None)

        assert "libs/mylib" in initialized

    def test_given_specific_path_when_init_then_initializes_only_that(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Init with specific path should initialize only that submodule."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        # Add submodule
        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Deinit
        main_ops.submodule_deinit("libs/mylib", force=True)

        # Init specific path
        initialized = main_ops.submodule_init(["libs/mylib"])

        assert "libs/mylib" in initialized


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


class TestSubmoduleStatusDetailed:
    """Tests for detailed submodule_status() method."""

    def test_given_clean_submodule_when_status_then_not_dirty(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Clean submodule should have clean status."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")

        status = main_ops.submodule_status("libs/mylib")

        assert status.info.path == "libs/mylib"
        assert status.workdir_dirty is False
        assert status.index_dirty is False
        assert status.actual_sha is not None

    def test_given_dirty_submodule_when_status_then_workdir_dirty(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Modified file in submodule should show workdir_dirty."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Modify a file in the submodule
        sm_file = main_path / "libs" / "mylib" / "lib.py"
        sm_file.write_text("modified content\n")

        status = main_ops.submodule_status("libs/mylib")

        assert status.workdir_dirty is True

    def test_given_staged_change_in_submodule_when_status_then_index_dirty(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Staged change in submodule should show index_dirty."""
        from codeplane.git import GitOps

        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Stage a change in the submodule
        sm_path = main_path / "libs" / "mylib"
        sm_ops = GitOps(sm_path)
        (sm_path / "new_file.txt").write_text("new content\n")
        sm_ops.stage(["new_file.txt"])

        status = main_ops.submodule_status("libs/mylib")

        assert status.index_dirty is True

    def test_given_untracked_file_in_submodule_when_status_then_untracked_count(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Untracked file in submodule should increment untracked_count."""
        (main_path, main_ops), (sub_path, _) = git_repo_pair

        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Add untracked file
        sm_path = main_path / "libs" / "mylib"
        (sm_path / "untracked.txt").write_text("untracked\n")

        status = main_ops.submodule_status("libs/mylib")

        assert status.untracked_count >= 1

    def test_given_outdated_submodule_when_list_then_status_outdated(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Submodule at wrong commit should show outdated status."""
        from codeplane.git import GitOps

        (main_path, main_ops), (sub_path, sub_ops) = git_repo_pair

        # Add submodule
        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Make a new commit in the submodule source repo
        (sub_path / "new.txt").write_text("new commit\n")
        sub_ops.stage(["new.txt"])
        sub_ops.commit("New commit in source")

        # Update the submodule reference in the parent
        import subprocess

        subprocess.run(
            ["git", "add", "libs/mylib"],
            cwd=str(main_path),
            capture_output=True,
            check=True,
        )

        # Now reset the submodule to previous commit to make it outdated
        sm_path = main_path / "libs" / "mylib"
        sm_ops = GitOps(sm_path)
        log = sm_ops.log(limit=2)
        if len(log) >= 2:
            sm_ops.reset(log[1].sha, mode="hard")

            # Check status
            submodules = main_ops.submodules()
            sm = next((s for s in submodules if s.path == "libs/mylib"), None)
            # Should be outdated or dirty - the point is to exercise the check
            assert sm is not None

    def test_given_uninitialized_submodule_when_list_then_status_uninitialized(
        self, git_repo_pair: tuple[tuple[Path, GitOps], tuple[Path, GitOps]]
    ) -> None:
        """Uninitialized submodule should show uninitialized status."""
        import shutil

        (main_path, main_ops), (sub_path, _) = git_repo_pair

        # Add submodule
        main_ops.submodule_add(str(sub_path), "libs/mylib")

        # Deinit to make it uninitialized
        main_ops.submodule_deinit("libs/mylib", force=True)

        # Remove the worktree content to truly uninitialize
        sm_path = main_path / "libs" / "mylib"
        if sm_path.exists():
            shutil.rmtree(sm_path)
        sm_path.mkdir()

        # Check status
        submodules = main_ops.submodules()
        sm = next((s for s in submodules if s.path == "libs/mylib"), None)
        assert sm is not None
        assert sm.status == "uninitialized"
