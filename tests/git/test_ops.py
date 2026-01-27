"""Tests for GitOps class."""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from codeplane.git import (
    BranchExistsError,
    BranchNotFoundError,
    GitOps,
    NotARepositoryError,
    NothingToCommitError,
    StashNotFoundError,
)


class TestGitOpsInit:
    def test_init_valid_repo(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        assert ops.path == Path(temp_repo.workdir)

    def test_init_not_a_repo(self, tmp_path: Path) -> None:
        with pytest.raises(NotARepositoryError):
            GitOps(tmp_path)

    def test_repo_property_exposes_pygit2(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        assert isinstance(ops.repo, pygit2.Repository)


class TestStatus:
    def test_clean_repo(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        status = ops.status()
        assert len(status) == 0

    def test_uncommitted_changes(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        status = ops.status()
        # Should have staged, modified, and untracked
        assert len(status) >= 2


class TestHead:
    def test_head_normal(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        head = ops.head()
        assert isinstance(head, pygit2.Reference)

    def test_head_commit(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        commit = ops.head_commit()
        assert isinstance(commit, pygit2.Commit)


class TestDiff:
    def test_diff_working_tree(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        diff = ops.diff()
        assert isinstance(diff, pygit2.Diff)
        assert diff.stats.files_changed >= 1

    def test_diff_staged(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        diff = ops.diff(staged=True)
        assert isinstance(diff, pygit2.Diff)
        assert diff.stats.files_changed == 1


class TestLog:
    def test_log_basic(self, repo_with_history: pygit2.Repository) -> None:
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=10)
        assert len(log) == 6  # 5 + initial
        assert all(isinstance(c, pygit2.Commit) for c in log)

    def test_log_limit(self, repo_with_history: pygit2.Repository) -> None:
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=2)
        assert len(log) == 2


class TestBranches:
    def test_list_branches(self, repo_with_branches: pygit2.Repository) -> None:
        ops = GitOps(repo_with_branches.workdir)
        branches = ops.branches(include_remote=False)
        names = {b.branch_name for b in branches}
        assert "main" in names
        assert "feature" in names

    def test_create_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        branch = ops.create_branch("new-feature")
        assert isinstance(branch, pygit2.Branch)
        assert branch.branch_name == "new-feature"

    def test_create_existing_branch(self, repo_with_branches: pygit2.Repository) -> None:
        ops = GitOps(repo_with_branches.workdir)
        with pytest.raises(BranchExistsError):
            ops.create_branch("feature")

    def test_checkout_branch(self, repo_with_branches: pygit2.Repository) -> None:
        ops = GitOps(repo_with_branches.workdir)
        ops.checkout("feature")
        assert ops.current_branch() == "feature"

    def test_checkout_create(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        ops.checkout("new-branch", create=True)
        assert ops.current_branch() == "new-branch"

    def test_delete_branch(self, repo_with_branches: pygit2.Repository) -> None:
        ops = GitOps(repo_with_branches.workdir)
        ops.delete_branch("feature", force=True)
        branches = ops.branches(include_remote=False)
        names = {b.branch_name for b in branches}
        assert "feature" not in names

    def test_delete_nonexistent_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(BranchNotFoundError):
            ops.delete_branch("nonexistent")


class TestCommit:
    def test_stage_and_commit(self, temp_repo: pygit2.Repository) -> None:
        workdir = Path(temp_repo.workdir)
        (workdir / "new.txt").write_text("new content\n")

        ops = GitOps(temp_repo.workdir)
        ops.stage(["new.txt"])
        oid = ops.commit("Add new file")
        assert isinstance(oid, pygit2.Oid)

    def test_commit_nothing_to_commit(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(NothingToCommitError):
            ops.commit("Empty commit")

    def test_commit_allow_empty(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        oid = ops.commit("Empty commit", allow_empty=True)
        assert isinstance(oid, pygit2.Oid)


class TestReset:
    def test_reset_hard(self, repo_with_history: pygit2.Repository) -> None:
        ops = GitOps(repo_with_history.workdir)
        ops.reset("HEAD~1", "hard")
        # Check we moved back
        log = ops.log(limit=10)
        assert len(log) == 5  # Was 6, now 5


class TestMerge:
    def test_merge_fastforward(self, repo_with_branches: pygit2.Repository) -> None:
        ops = GitOps(repo_with_branches.workdir)
        ops.checkout("feature")
        success, oid = ops.merge("main")
        assert success

    def test_merge_conflict(self, repo_with_conflict: tuple[pygit2.Repository, str]) -> None:
        repo, branch = repo_with_conflict
        ops = GitOps(repo.workdir)
        success, oid = ops.merge(branch)
        assert not success

    def test_abort_merge(self, repo_with_conflict: tuple[pygit2.Repository, str]) -> None:
        repo, branch = repo_with_conflict
        ops = GitOps(repo.workdir)
        ops.merge(branch)
        ops.abort_merge()
        assert ops.state() == pygit2.GIT_REPOSITORY_STATE_NONE


class TestStash:
    def test_stash_push_pop(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        ops.unstage(["staged.txt"])

        oid = ops.stash_push(message="Test stash")
        assert isinstance(oid, pygit2.Oid)

        status = ops.status()
        # Modified file should be stashed
        modified_flags = [f for f in status.values() if f & pygit2.GIT_STATUS_WT_MODIFIED]
        assert len(modified_flags) == 0

        ops.stash_pop()
        status = ops.status()
        modified_flags = [f for f in status.values() if f & pygit2.GIT_STATUS_WT_MODIFIED]
        assert len(modified_flags) >= 1

    def test_stash_list(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        ops.unstage(["staged.txt"])
        ops.stash_push(message="First")

        stashes = ops.stash_list()
        assert len(stashes) >= 1
        assert "First" in stashes[0][1]  # Message includes branch prefix

    def test_stash_pop_invalid(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(StashNotFoundError):
            ops.stash_pop(99)


class TestTags:
    def test_create_lightweight_tag(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        oid = ops.create_tag("v1.0.0")
        assert isinstance(oid, pygit2.Oid)

    def test_create_annotated_tag(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        oid = ops.create_tag("v1.0.0", message="Release 1.0.0")
        assert isinstance(oid, pygit2.Oid)

    def test_list_tags(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        ops.create_tag("v1.0.0")
        ops.create_tag("v2.0.0", message="Release 2.0.0")

        tags = ops.tags()
        names = {t[0] for t in tags}
        assert "v1.0.0" in names
        assert "v2.0.0" in names


class TestBlame:
    def test_blame_file(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        blame = ops.blame("README.md")
        assert isinstance(blame, pygit2.Blame)
        assert len(blame) >= 1


class TestRemotes:
    def test_list_remotes(self, repo_with_remote: pygit2.Repository) -> None:
        ops = GitOps(repo_with_remote.workdir)
        remotes = ops.remotes()
        assert len(remotes) == 1
        assert remotes[0].name == "origin"
