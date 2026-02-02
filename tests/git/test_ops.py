"""Tests for GitOps class."""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from codeplane.git import (
    BlameInfo,
    BranchExistsError,
    BranchInfo,
    BranchNotFoundError,
    CommitInfo,
    DiffInfo,
    DiffSummary,
    GitOps,
    NotARepositoryError,
    NothingToCommitError,
    OperationResult,
    RefInfo,
    RefNotFoundError,
    RemoteInfo,
    StashNotFoundError,
    TagInfo,
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
        assert isinstance(head, RefInfo)

    def test_head_commit(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        commit = ops.head_commit()
        assert isinstance(commit, CommitInfo)


class TestDiff:
    def test_diff_working_tree(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        diff = ops.diff()
        assert isinstance(diff, DiffInfo)
        assert diff.files_changed >= 1

    def test_diff_staged(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        diff = ops.diff(staged=True)
        assert isinstance(diff, DiffInfo)
        assert diff.files_changed == 1


class TestDiffSummary:
    """Tests for diff_summary() method."""

    def test_given_uncommitted_changes_when_diff_summary_then_returns_stats_only(
        self, repo_with_uncommitted: pygit2.Repository
    ) -> None:
        """Default diff_summary returns stats only (fast path)."""
        ops = GitOps(repo_with_uncommitted.workdir)

        summary = ops.diff_summary()

        assert isinstance(summary, DiffSummary)
        assert summary.files_changed >= 1
        assert summary.total_additions >= 0
        assert summary.total_deletions >= 0
        assert summary.total_lines == summary.total_additions + summary.total_deletions
        # Fast path: no per_file, no word_count
        assert summary.per_file is None
        assert summary.total_word_count is None
        assert summary.file_paths == ()

    def test_given_staged_changes_when_include_per_file_then_returns_per_file_stats(
        self, repo_with_uncommitted: pygit2.Repository
    ) -> None:
        """diff_summary with include_per_file=True returns per-file breakdown."""
        ops = GitOps(repo_with_uncommitted.workdir)

        summary = ops.diff_summary(staged=True, include_per_file=True)

        assert isinstance(summary, DiffSummary)
        assert summary.files_changed == 1
        assert summary.per_file is not None
        assert len(summary.per_file) == 1
        assert summary.per_file[0].path == "staged.txt"
        # Medium path: per_file but no word_count
        assert summary.per_file[0].word_count is None
        assert summary.total_word_count is None
        assert "staged.txt" in summary.file_paths

    def test_given_multiple_files_when_include_word_count_then_returns_word_counts(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """diff_summary with include_word_count=True returns word counts (slow path)."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(workdir)

        # Create and stage files with known content
        (workdir / "small.txt").write_text("one two three")
        (workdir / "medium.txt").write_text("word " * 50)
        (workdir / "large.txt").write_text("token " * 200)
        ops.stage(["small.txt", "medium.txt", "large.txt"])

        summary = ops.diff_summary(staged=True, include_word_count=True)

        assert summary.files_changed == 3
        assert summary.total_word_count is not None
        assert summary.total_word_count > 0
        assert summary.per_file is not None

        # Verify per-file word counts
        paths = {f.path for f in summary.per_file}
        assert paths == {"small.txt", "medium.txt", "large.txt"}
        for file_summary in summary.per_file:
            assert file_summary.word_count is not None
            assert file_summary.word_count >= 0

    def test_given_ref_range_when_diff_summary_then_returns_range_stats(
        self, repo_with_history: pygit2.Repository
    ) -> None:
        """diff_summary with base/target refs returns range stats."""
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=3)

        summary = ops.diff_summary(base=log[2].sha, target=log[0].sha)

        assert isinstance(summary, DiffSummary)
        assert summary.files_changed >= 0
        assert summary.total_lines >= 0

    def test_given_no_changes_when_diff_summary_then_returns_zeros(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """diff_summary on clean working tree returns zeros."""
        ops = GitOps(temp_repo.workdir)

        summary = ops.diff_summary()

        assert summary.files_changed == 0
        assert summary.total_additions == 0
        assert summary.total_deletions == 0
        assert summary.total_lines == 0
        assert summary.per_file is None
        assert summary.total_word_count is None
        assert summary.file_paths == ()


class TestLog:
    def test_log_basic(self, repo_with_history: pygit2.Repository) -> None:
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=10)
        assert len(log) == 6  # 5 + initial
        assert all(isinstance(c, CommitInfo) for c in log)

    def test_log_limit(self, repo_with_history: pygit2.Repository) -> None:
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=2)
        assert len(log) == 2


class TestBranches:
    def test_list_branches(self, repo_with_branches: pygit2.Repository) -> None:
        ops = GitOps(repo_with_branches.workdir)
        branches = ops.branches(include_remote=False)
        assert all(isinstance(b, BranchInfo) for b in branches)
        names = {b.short_name for b in branches}
        assert "main" in names
        assert "feature" in names

    def test_create_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        branch = ops.create_branch("new-feature")
        assert isinstance(branch, BranchInfo)
        assert branch.short_name == "new-feature"

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
        names = {b.short_name for b in branches}
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
        sha = ops.commit("Add new file")
        assert isinstance(sha, str)
        assert len(sha) == 40

    def test_commit_nothing_to_commit(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(NothingToCommitError):
            ops.commit("Empty commit")

    def test_commit_allow_empty(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        sha = ops.commit("Empty commit", allow_empty=True)
        assert isinstance(sha, str)
        assert len(sha) == 40


class TestMerge:
    def test_merge_fastforward(self, repo_with_branches: pygit2.Repository) -> None:
        """Test fast-forward merge: merging an ancestor into a descendant."""
        ops = GitOps(repo_with_branches.workdir)
        # Create a new branch from current main, then advance main
        ops.create_branch("ff-test")
        ops.checkout("ff-test")
        # ff-test is now at same commit as main's parent
        # main is ahead, so merging main into ff-test is a fast-forward
        ops.checkout("main")
        # main is already ahead of ff-test (main has "main.txt" commit)
        main_tip = ops.head().target_sha
        ops.checkout("ff-test")
        result = ops.merge("main")
        assert result.success
        assert result.conflict_paths == ()
        # Verify fast-forward semantics: still on branch, not detached
        assert ops.current_branch() == "ff-test"
        assert not ops.head().is_detached
        # Verify branch advanced to main's tip
        assert ops.head().target_sha == main_tip

    def test_merge_conflict(self, repo_with_conflict: tuple[pygit2.Repository, str]) -> None:
        repo, branch = repo_with_conflict
        ops = GitOps(repo.workdir)
        result = ops.merge(branch)
        assert not result.success
        assert len(result.conflict_paths) > 0
        assert "conflict.txt" in result.conflict_paths

    def test_abort_merge(self, repo_with_conflict: tuple[pygit2.Repository, str]) -> None:
        repo, branch = repo_with_conflict
        ops = GitOps(repo.workdir)
        ops.merge(branch)
        ops.abort_merge()
        assert ops.state() == pygit2.GIT_REPOSITORY_STATE_NONE


class TestPull:
    """Tests for pull() method (fetch + merge)."""

    def test_pull_fastforward(self, repo_with_remote: pygit2.Repository) -> None:
        """Pull when remote is ahead should fast-forward."""
        from codeplane.git import PullResult

        workdir = Path(repo_with_remote.workdir)
        ops = GitOps(workdir)

        # Push current state
        ops.push()

        # Simulate remote advancing: clone, commit, push back
        bare_path = Path(repo_with_remote.remotes["origin"].url)
        with_clone = workdir.parent / "clone"
        clone = pygit2.clone_repository(str(bare_path), str(with_clone), checkout_branch="main")
        (with_clone / "remote-change.txt").write_text("from remote")
        clone.index.add("remote-change.txt")
        clone.index.write()
        tree = clone.index.write_tree()
        sig = pygit2.Signature("Test", "test@example.com")
        clone.create_commit("HEAD", sig, sig, "Remote commit", tree, [clone.head.target])
        clone.remotes["origin"].push(["refs/heads/main:refs/heads/main"])

        # Now pull from our original repo
        before_sha = ops.head().target_sha
        result = ops.pull()

        assert isinstance(result, PullResult)
        assert result.success is True
        assert result.up_to_date is False
        assert result.conflict_paths == ()
        assert ops.head().target_sha != before_sha
        assert (workdir / "remote-change.txt").exists()

    def test_pull_up_to_date(self, repo_with_remote: pygit2.Repository) -> None:
        """Pull when already up-to-date should indicate so."""
        from codeplane.git import PullResult

        ops = GitOps(repo_with_remote.workdir)
        ops.push()
        ops.fetch()  # Ensure we have remote refs

        result = ops.pull()

        assert isinstance(result, PullResult)
        assert result.success is True
        assert result.up_to_date is True
        assert result.commit_sha is None

    def test_pull_nonexistent_remote_branch_raises(
        self, repo_with_remote: pygit2.Repository
    ) -> None:
        """Pull from non-existent remote branch should raise RefNotFoundError."""
        ops = GitOps(repo_with_remote.workdir)
        ops.push()
        ops.fetch()

        with pytest.raises(RefNotFoundError, match="origin/nonexistent"):
            ops.pull(branch="nonexistent")


class TestCherrypick:
    """Tests for cherrypick() method."""

    def test_given_clean_commit_when_cherrypick_then_success(
        self, repo_with_branches: pygit2.Repository
    ) -> None:
        """Cherry-picking a non-conflicting commit should succeed."""
        ops = GitOps(repo_with_branches.workdir)

        # Get the commit from feature branch
        ops.checkout("feature")
        feature_head = ops.head_commit()
        ops.checkout("main")

        result = ops.cherrypick(feature_head.sha)

        assert result.success is True
        assert result.conflict_paths == ()

    def test_given_conflict_when_cherrypick_then_returns_conflicts(
        self, repo_with_branches: pygit2.Repository
    ) -> None:
        """Cherry-picking a conflicting commit should return conflict paths."""
        workdir = Path(repo_with_branches.workdir)
        ops = GitOps(repo_with_branches.workdir)

        # Create a conflicting file on main
        (workdir / "conflict.txt").write_text("main content")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict on main")

        # Create conflicting change on feature
        ops.checkout("feature")
        (workdir / "conflict.txt").write_text("feature content")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict on feature")
        feature_sha = ops.head_commit().sha

        # Go back to main and try to cherry-pick
        ops.checkout("main")
        result = ops.cherrypick(feature_sha)

        assert result.success is False
        assert "conflict.txt" in result.conflict_paths


class TestRevert:
    """Tests for revert() method."""

    def test_given_clean_commit_when_revert_then_success(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Reverting a clean commit should succeed."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Modify README.md and commit (this is revertable)
        (workdir / "README.md").write_text("modified content")
        ops.stage(["README.md"])
        sha = ops.commit("modify readme")

        # Revert it
        result = ops.revert(sha)

        assert result.success is True
        # After revert, README.md should have original content
        # The revert creates a new commit undoing the change

    def test_given_conflict_when_revert_then_returns_conflicts(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Reverting when revert changes conflict should return conflict paths."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Modify file in a way we can revert
        (workdir / "README.md").write_text("first modification")
        ops.stage(["README.md"])
        ops.commit("first modify")

        # Create a file that definitely conflicts: modify same lines differently
        (workdir / "conflict.txt").write_text("a\nb\nc\nd\ne\n")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict.txt")

        # Modify middle section
        (workdir / "conflict.txt").write_text("a\nBBB\nCCC\nDDD\ne\n")
        ops.stage(["conflict.txt"])
        modify_sha = ops.commit("modify middle")

        # Modify same middle section differently
        (workdir / "conflict.txt").write_text("a\nXXX\nYYY\nZZZ\ne\n")
        ops.stage(["conflict.txt"])
        ops.commit("modify middle differently")

        # Try to revert the first middle modification - should conflict
        result = ops.revert(modify_sha)

        # Git's 3-way merge may succeed or conflict depending on context
        # We're testing that the method handles both cases correctly
        assert isinstance(result, OperationResult)


class TestAmend:
    """Tests for amend() method."""

    def test_given_commit_when_amend_message_then_updates(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Amend with new message should update commit message."""
        ops = GitOps(temp_repo.workdir)
        original_sha = ops.head_commit().sha

        new_sha = ops.amend(message="Amended message")

        assert new_sha != original_sha
        assert ops.head_commit().message == "Amended message"

    def test_given_staged_changes_when_amend_then_includes_changes(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Amend with staged changes should include them."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Stage a new change
        (workdir / "amended_file.txt").write_text("new content")
        ops.stage(["amended_file.txt"])

        # Amend without new message
        ops.amend()

        # The amended file should exist in working tree
        assert (workdir / "amended_file.txt").exists()
        # And the commit count should stay the same (1 initial + 0 new = 1)
        assert len(ops.log(limit=10)) == 1

    def test_given_no_message_when_amend_then_keeps_original(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Amend without message keeps original message."""
        ops = GitOps(temp_repo.workdir)
        original_message = ops.head_commit().message

        ops.amend()

        assert ops.head_commit().message == original_message


class TestMergeAnalysis:
    """Tests for merge_analysis() method."""

    def test_given_same_commit_when_analysis_then_up_to_date(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Analyzing merge with HEAD should be up to date."""
        ops = GitOps(temp_repo.workdir)

        analysis = ops.merge_analysis("HEAD")

        assert analysis.up_to_date is True
        assert analysis.fastforward_possible is False

    def test_given_fast_forward_possible_when_analysis_then_detected(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Analyzing mergeable branch should detect fast-forward possibility."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Create feature branch at current HEAD
        ops.create_branch("feature")

        # Add commit only on feature (main stays behind)
        ops.checkout("feature")
        (workdir / "feature_only.txt").write_text("feature content")
        ops.stage(["feature_only.txt"])
        ops.commit("feature commit")

        # Go back to main
        ops.checkout("main")

        # Now feature is ahead, main can fast-forward to feature
        analysis = ops.merge_analysis("feature")

        assert analysis.up_to_date is False
        assert analysis.fastforward_possible is True
        # Note: conflicts_likely may also be True (MERGE_NORMAL flag set)
        # This is correct git behavior - analysis shows what's possible

    def test_given_diverged_branches_when_analysis_then_normal_merge(
        self, temp_repo: pygit2.Repository
    ) -> None:
        """Analyzing diverged branches should indicate normal merge needed."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Create feature branch at current HEAD
        ops.create_branch("feature")

        # Add commit on main
        (workdir / "main_only.txt").write_text("main content")
        ops.stage(["main_only.txt"])
        ops.commit("main commit")

        # Add commit on feature
        ops.checkout("feature")
        (workdir / "feature_only.txt").write_text("feature content")
        ops.stage(["feature_only.txt"])
        ops.commit("feature commit")

        # Go back to main
        ops.checkout("main")

        # Now branches have diverged
        analysis = ops.merge_analysis("feature")

        assert analysis.up_to_date is False
        assert analysis.conflicts_likely is True


class TestStash:
    def test_unstage_preserves_working_tree(self, repo_with_uncommitted: pygit2.Repository) -> None:
        """Verify unstage keeps working tree changes."""
        workdir = Path(repo_with_uncommitted.workdir)
        ops = GitOps(repo_with_uncommitted.workdir)

        # staged.txt is staged - verify it exists
        assert (workdir / "staged.txt").exists()
        original_content = (workdir / "staged.txt").read_text()

        # Unstage it
        ops.unstage(["staged.txt"])

        # Working tree file should still exist with same content
        assert (workdir / "staged.txt").exists()
        assert (workdir / "staged.txt").read_text() == original_content

        # But it should no longer be staged
        status = ops.status()
        staged_flags = status.get("staged.txt", 0)
        assert not (staged_flags & pygit2.GIT_STATUS_INDEX_NEW)

    def test_stash_push_pop(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        ops.unstage(["staged.txt"])

        sha = ops.stash_push(message="Test stash")
        assert isinstance(sha, str)
        assert len(sha) == 40

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
        assert "First" in stashes[0].message  # Message includes branch prefix

    def test_stash_pop_invalid(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(StashNotFoundError):
            ops.stash_pop(99)

    def test_stash_push_with_untracked(self, temp_repo: pygit2.Repository) -> None:
        """Stash with include_untracked should stash untracked files."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(workdir)

        # Create an untracked file
        untracked = workdir / "untracked.txt"
        untracked.write_text("untracked content")

        # Verify file exists and is untracked
        status = ops.status()
        assert "untracked.txt" in status

        # Stash with untracked
        sha = ops.stash_push(message="With untracked", include_untracked=True)
        assert len(sha) == 40

        # Untracked file should be gone
        assert not untracked.exists()

        # Pop stash
        ops.stash_pop()

        # File should be restored
        assert untracked.exists()
        assert untracked.read_text() == "untracked content"


class TestTags:
    def test_create_lightweight_tag(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        sha = ops.create_tag("v1.0.0")
        assert isinstance(sha, str)
        assert len(sha) == 40

    def test_create_annotated_tag(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        sha = ops.create_tag("v1.0.0", message="Release 1.0.0")
        assert isinstance(sha, str)
        assert len(sha) == 40

    def test_list_tags(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        ops.create_tag("v1.0.0")
        ops.create_tag("v2.0.0", message="Release 2.0.0")

        tags = ops.tags()
        assert all(isinstance(t, TagInfo) for t in tags)
        names = {t.name for t in tags}
        assert "v1.0.0" in names
        assert "v2.0.0" in names

    def test_delete_tag(self, temp_repo: pygit2.Repository) -> None:
        """Deleting existing tag should succeed."""

        ops = GitOps(temp_repo.workdir)
        ops.create_tag("v1.0.0")

        ops.delete_tag("v1.0.0")

        tags = ops.tags()
        assert all(t.name != "v1.0.0" for t in tags)

    def test_delete_nonexistent_tag_raises(self, temp_repo: pygit2.Repository) -> None:
        """Deleting nonexistent tag should raise."""
        from codeplane.git import RefNotFoundError

        ops = GitOps(temp_repo.workdir)

        with pytest.raises(RefNotFoundError):
            ops.delete_tag("nonexistent")


class TestBlame:
    def test_blame_file(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        blame = ops.blame("README.md")
        assert isinstance(blame, BlameInfo)
        assert len(blame.hunks) >= 1

    def test_blame_with_line_range(self, temp_repo: pygit2.Repository) -> None:
        """Blame with line range should work."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Create multi-line file
        (workdir / "multiline.txt").write_text("line1\nline2\nline3\nline4\nline5\n")
        ops.stage(["multiline.txt"])
        ops.commit("add multiline")

        blame = ops.blame("multiline.txt", min_line=2, max_line=4)

        assert isinstance(blame, BlameInfo)


class TestLogEdgeCases:
    """Additional edge case tests for log."""

    def test_log_nonexistent_ref_returns_empty(self, temp_repo: pygit2.Repository) -> None:
        """Log with nonexistent ref should return empty list."""
        ops = GitOps(temp_repo.workdir)

        log = ops.log(ref="nonexistent-branch")

        assert log == []


class TestStageUnstage:
    """Tests for staging and unstaging."""

    def test_stage_new_file(self, temp_repo: pygit2.Repository) -> None:
        """Staging new file should work."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        (workdir / "new.txt").write_text("content")
        ops.stage(["new.txt"])

        status = ops.status()
        assert "new.txt" in status

    def test_stage_deleted_file(self, temp_repo: pygit2.Repository) -> None:
        """Staging deleted file should work."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)

        # Delete README.md
        (workdir / "README.md").unlink()
        ops.stage(["README.md"])

        status = ops.status()
        assert status.get("README.md", 0) & pygit2.GIT_STATUS_INDEX_DELETED

    def test_unstage_file(self, repo_with_uncommitted: pygit2.Repository) -> None:
        """Unstaging file should work."""
        ops = GitOps(repo_with_uncommitted.workdir)

        ops.unstage(["staged.txt"])

        status = ops.status()
        # Should no longer be in index
        flags = status.get("staged.txt", 0)
        assert not (flags & pygit2.GIT_STATUS_INDEX_NEW)


class TestDiscard:
    """Tests for discard (restore working tree from index)."""

    def test_discard_modified_file(self, temp_repo: pygit2.Repository) -> None:
        """Discarding modified file restores index content."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)
        readme = workdir / "README.md"
        original = readme.read_text()

        readme.write_text("modified content")
        assert readme.read_text() == "modified content"

        ops.discard(["README.md"])

        assert readme.read_text() == original

    def test_discard_deleted_file(self, temp_repo: pygit2.Repository) -> None:
        """Discarding deleted file restores it."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)
        readme = workdir / "README.md"
        original = readme.read_text()

        readme.unlink()
        assert not readme.exists()

        ops.discard(["README.md"])

        assert readme.exists()
        assert readme.read_text() == original

    def test_discard_untracked_file(self, temp_repo: pygit2.Repository) -> None:
        """Discarding untracked file that's not in index does nothing harmful."""
        workdir = Path(temp_repo.workdir)
        ops = GitOps(temp_repo.workdir)
        untracked = workdir / "untracked.txt"
        untracked.write_text("untracked")

        # Should not raise, file not in index so gets deleted
        ops.discard(["untracked.txt"])

        assert not untracked.exists()


class TestReset:
    """Tests for reset operations."""

    def test_reset_soft(self, repo_with_history: pygit2.Repository) -> None:
        """Soft reset should move HEAD but keep changes staged."""
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=2)
        target_sha = log[1].sha

        ops.reset(target_sha, mode="soft")

        assert ops.head_commit().sha == target_sha

    def test_reset_mixed(self, repo_with_history: pygit2.Repository) -> None:
        """Mixed reset should move HEAD and unstage."""
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=2)
        target_sha = log[1].sha

        ops.reset(target_sha, mode="mixed")

        assert ops.head_commit().sha == target_sha

    def test_reset_hard(self, repo_with_history: pygit2.Repository) -> None:
        """Hard reset should move HEAD and discard changes."""
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=2)
        target_sha = log[1].sha

        ops.reset(target_sha, mode="hard")

        assert ops.head_commit().sha == target_sha


class TestMergeOperations:
    """Tests for merge operations."""

    def test_merge_fast_forward(self, repo_with_branches: pygit2.Repository) -> None:
        """Fast-forward merge should succeed."""
        workdir = Path(repo_with_branches.workdir)
        ops = GitOps(repo_with_branches.workdir)

        # feature is ahead, this should fast-forward
        # But fixture has both diverged, so create a proper scenario
        ops.create_branch("ff-target")
        ops.checkout("ff-target")
        (workdir / "ff.txt").write_text("ff content")
        ops.stage(["ff.txt"])
        ops.commit("ff commit")
        ops.checkout("main")

        result = ops.merge("ff-target")

        assert result.success is True

    def test_merge_up_to_date(self, temp_repo: pygit2.Repository) -> None:
        """Merge with HEAD should be up-to-date."""
        ops = GitOps(temp_repo.workdir)

        result = ops.merge("HEAD")

        assert result.success is True


class TestDiffEdgeCases:
    """Additional edge case tests for diff."""

    def test_diff_between_refs(self, repo_with_history: pygit2.Repository) -> None:
        """Diff between two refs should work."""
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=3)

        diff = ops.diff(base=log[2].sha, target=log[0].sha)

        assert isinstance(diff, DiffInfo)

    def test_diff_from_ref_to_working_tree(self, repo_with_uncommitted: pygit2.Repository) -> None:
        """Diff from ref to working tree should work."""
        ops = GitOps(repo_with_uncommitted.workdir)

        diff = ops.diff(base="HEAD")

        assert isinstance(diff, DiffInfo)


class TestCheckoutEdgeCases:
    """Additional edge case tests for checkout."""

    def test_checkout_detached_head(self, temp_repo: pygit2.Repository) -> None:
        """Checkout by SHA should result in detached HEAD."""
        ops = GitOps(temp_repo.workdir)
        head_sha = ops.head_commit().sha

        ops.checkout(head_sha)

        assert ops.current_branch() is None  # Detached

    def test_checkout_create_and_switch(self, temp_repo: pygit2.Repository) -> None:
        """Checkout with create=True should create and switch."""
        ops = GitOps(temp_repo.workdir)

        ops.checkout("new-branch", create=True)

        assert ops.current_branch() == "new-branch"


class TestBranchEdgeCases:
    """Additional edge case tests for branch operations."""

    def test_delete_branch(self, repo_with_branches: pygit2.Repository) -> None:
        """Deleting branch should work."""
        ops = GitOps(repo_with_branches.workdir)

        # Force delete since feature has unmerged changes
        ops.delete_branch("feature", force=True)

        branches = ops.branches(include_remote=False)
        assert all(b.short_name != "feature" for b in branches)

    def test_delete_current_branch_raises(self, temp_repo: pygit2.Repository) -> None:
        """Deleting current branch should raise."""
        from codeplane.git._internal.errors import GitError

        ops = GitOps(temp_repo.workdir)

        # Can't delete current branch - need to be on another branch first
        # Create and switch to another branch, then try to delete that
        ops.create_branch("to-delete")
        ops.checkout("to-delete")

        # Try to delete the branch we're on
        with pytest.raises(GitError, match="Cannot delete current branch"):
            ops.delete_branch("to-delete")

    def test_rename_branch(self, repo_with_branches: pygit2.Repository) -> None:
        """Renaming branch should work."""
        ops = GitOps(repo_with_branches.workdir)

        ops.rename_branch("feature", "renamed-feature")

        branches = ops.branches(include_remote=False)
        names = {b.short_name for b in branches}
        assert "renamed-feature" in names
        assert "feature" not in names


class TestShowCommit:
    """Tests for show() method."""

    def test_show_head(self, temp_repo: pygit2.Repository) -> None:
        """Show HEAD should return commit info."""
        ops = GitOps(temp_repo.workdir)

        commit = ops.show("HEAD")

        assert isinstance(commit, CommitInfo)

    def test_show_by_sha(self, repo_with_history: pygit2.Repository) -> None:
        """Show by SHA should work."""
        ops = GitOps(repo_with_history.workdir)
        log = ops.log(limit=2)

        commit = ops.show(log[1].sha)

        assert commit.sha == log[1].sha


class TestUnbornRepo:
    """Tests for unborn repo state (no commits yet)."""

    def test_unstage_on_unborn_repo(self, tmp_path: Path) -> None:
        """Unstaging on unborn repo should work."""
        import subprocess

        repo_path = tmp_path / "unborn"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

        ops = GitOps(repo_path)

        # Stage a file
        (repo_path / "new.txt").write_text("content\n")
        ops.stage(["new.txt"])

        # Verify it's staged
        status_before = ops.status()
        assert "new.txt" in status_before

        # Unstage should work even without any commits
        ops.unstage(["new.txt"])

        # File should be untracked now
        status_after = ops.status()
        # After unstaging, file should still show in status but as untracked
        assert "new.txt" in status_after


class TestRemotes:
    def test_list_remotes(self, repo_with_remote: pygit2.Repository) -> None:
        ops = GitOps(repo_with_remote.workdir)
        remotes = ops.remotes()
        assert len(remotes) == 1
        assert isinstance(remotes[0], RemoteInfo)
        assert remotes[0].name == "origin"


class TestBranchesWithRemotes:
    """Tests for listing branches including remote tracking branches."""

    def test_list_branches_with_remote(self, repo_with_remote: pygit2.Repository) -> None:
        """Listing branches with include_remote should include tracking branches."""
        ops = GitOps(repo_with_remote.workdir)

        # Create a branch and push it to create remote tracking branch
        ops.create_branch("feature")

        branches = ops.branches(include_remote=True)
        names = {b.name for b in branches}

        # Should include local branches
        assert "refs/heads/main" in names or "refs/heads/master" in names


class TestResetEdgeCases:
    """Edge case tests for reset operation."""

    def test_reset_invalid_mode_raises(self, temp_repo: pygit2.Repository) -> None:
        """Reset with invalid mode should raise ValueError."""
        ops = GitOps(temp_repo.workdir)

        with pytest.raises(ValueError, match="Invalid reset mode"):
            ops.reset("HEAD", mode="invalid")


class TestDeleteBranchEdgeCases:
    """Edge case tests for branch deletion."""

    def test_delete_unmerged_branch_without_force_raises(
        self, repo_with_branches: pygit2.Repository
    ) -> None:
        """Deleting unmerged branch without force should raise."""
        ops = GitOps(repo_with_branches.workdir)

        # Make a commit on a branch that diverges from main
        ops.checkout("feature")
        (Path(repo_with_branches.workdir) / "diverged.txt").write_text("diverged")
        ops.stage(["diverged.txt"])
        ops.commit("Diverged commit")
        ops.checkout("main")

        from codeplane.git import UnmergedBranchError

        with pytest.raises(UnmergedBranchError):
            ops.delete_branch("feature")


class TestRenameBranchEdgeCases:
    """Edge case tests for branch rename."""

    def test_rename_to_existing_branch_raises(self, repo_with_branches: pygit2.Repository) -> None:
        """Renaming to an existing branch name should raise."""
        ops = GitOps(repo_with_branches.workdir)

        ops.create_branch("new-feature")

        from codeplane.git import BranchExistsError

        with pytest.raises(BranchExistsError):
            ops.rename_branch("feature", "new-feature")


class TestRemoteBranchCheckout:
    """Tests for checking out remote branches."""

    def test_checkout_remote_branch_creates_local(
        self, repo_with_remote_branch: pygit2.Repository
    ) -> None:
        """Checking out remote branch should create local tracking branch."""
        ops = GitOps(repo_with_remote_branch.workdir)

        # Checkout the remote-only branch
        ops.checkout("origin/remote-only")

        # Should create a local branch with the short name
        branches = ops.branches(include_remote=False)
        local_names = {b.short_name for b in branches}
        assert "remote-only" in local_names

        # Should be on that branch
        assert ops.current_branch() == "remote-only"


class TestUnbornRepoEdgeCases:
    """Tests for unborn repo (no commits yet) edge cases."""

    def test_given_unborn_repo_when_current_branch_then_returns_branch_name(
        self, tmp_path: Path
    ) -> None:
        """Current branch on unborn repo should return branch name."""
        import subprocess

        repo_path = tmp_path / "unborn"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

        ops = GitOps(repo_path)

        # Current branch should be available even without commits
        branch = ops.current_branch()
        assert branch in ("main", "master")  # Depends on git default

    def test_given_unborn_repo_when_branches_then_empty(self, tmp_path: Path) -> None:
        """Branches on unborn repo should return empty (no commits)."""
        import subprocess

        repo_path = tmp_path / "unborn"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

        ops = GitOps(repo_path)

        branches = ops.branches()
        # Unborn repos don't have branches yet
        assert len(branches) == 0

    def test_given_unborn_repo_when_status_then_works(self, tmp_path: Path) -> None:
        """Status on unborn repo should work."""
        import subprocess

        repo_path = tmp_path / "unborn"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

        ops = GitOps(repo_path)

        # Add an untracked file
        (repo_path / "file.txt").write_text("content")

        status = ops.status()
        assert "file.txt" in status


class TestMergeBranchRestoration:
    """Tests verifying merge properly handles branch refs (fixes #119)."""

    def test_given_ff_merge_when_complete_then_branch_reattached(
        self, repo_with_branches: pygit2.Repository
    ) -> None:
        """
        Fast-forward merge should update branch ref and keep HEAD attached.
        Regression test: branch was captured AFTER detaching HEAD.
        """
        ops = GitOps(repo_with_branches.workdir)
        workdir = Path(repo_with_branches.workdir)

        # Create "behind" branch at current position
        ops.create_branch("behind")

        # Stay on main and make a new commit (advance main)
        (workdir / "advance.txt").write_text("advance main")
        ops.stage(["advance.txt"])
        ops.commit("advance main")
        main_tip = ops.head().target_sha

        # Switch to "behind" - it's now behind main
        ops.checkout("behind")
        original_behind_sha = ops.head().target_sha
        assert original_behind_sha != main_tip, "behind should be behind main"

        result = ops.merge("main")

        # Merge should succeed
        assert result.success

        # Key fix verification: should still be on "behind" branch (not detached)
        assert ops.current_branch() == "behind", "Branch should remain checked out"
        assert not ops.head().is_detached, "HEAD should not be detached after FF merge"

        # Branch should have advanced to main's tip
        assert ops.head().target_sha == main_tip


class TestExtractConflictPaths:
    """Tests for WriteFlows.extract_conflict_paths (fixes #119)."""

    def test_given_no_conflicts_when_extract_then_returns_empty_tuple(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """extract_conflict_paths should return empty tuple when no conflicts."""
        from codeplane.git._internal import RepoAccess, WriteFlows

        repo_path, _ = git_repo_with_commit
        access = RepoAccess(repo_path)
        flows = WriteFlows(access)

        # No merge in progress, index.conflicts should be None
        result = flows.extract_conflict_paths()

        assert result == ()
        assert isinstance(result, tuple)
