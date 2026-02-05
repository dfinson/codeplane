"""Tests for git.models module.

Tests the serializable data models for git operations.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codeplane.git.models import (
    BlameHunk,
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffFile,
    DiffInfo,
    DiffSummary,
    FileDiffSummary,
    MergeAnalysis,
    MergeResult,
    OperationResult,
    PullResult,
    RebasePlan,
    RebaseResult,
    RebaseStep,
    RefInfo,
    RemoteInfo,
    Signature,
    StashEntry,
    SubmoduleInfo,
    SubmoduleUpdateResult,
    TagInfo,
    WorktreeInfo,
    _count_words,
)

# =============================================================================
# Tests for helper functions
# =============================================================================


class TestCountWords:
    """Tests for _count_words helper function."""

    def test_empty_string(self) -> None:
        """Empty string returns 0 words."""
        assert _count_words("") == 0

    def test_single_word(self) -> None:
        """Single word is counted."""
        assert _count_words("hello") == 1

    def test_multiple_words(self) -> None:
        """Multiple words are counted."""
        assert _count_words("hello world foo bar") == 4

    def test_whitespace_only(self) -> None:
        """Whitespace-only string returns 0."""
        assert _count_words("   \t\n  ") == 0

    def test_mixed_whitespace(self) -> None:
        """Mixed whitespace separators work."""
        assert _count_words("one\ttwo\nthree  four") == 4


# =============================================================================
# Tests for Signature
# =============================================================================


class TestSignature:
    """Tests for Signature dataclass."""

    def test_creation(self) -> None:
        """Signature can be created with all fields."""
        now = datetime.now(tz=UTC)
        sig = Signature(name="John Doe", email="john@example.com", time=now)
        assert sig.name == "John Doe"
        assert sig.email == "john@example.com"
        assert sig.time == now

    def test_immutable(self) -> None:
        """Signature is frozen/immutable."""
        sig = Signature(name="Test", email="test@test.com", time=datetime.now(tz=UTC))
        with pytest.raises(AttributeError):
            sig.name = "New Name"  # type: ignore[misc]


# =============================================================================
# Tests for CommitInfo
# =============================================================================


class TestCommitInfo:
    """Tests for CommitInfo dataclass."""

    def test_creation(self) -> None:
        """CommitInfo can be created."""
        now = datetime.now(tz=UTC)
        author = Signature(name="Author", email="author@test.com", time=now)
        committer = Signature(name="Committer", email="committer@test.com", time=now)

        commit = CommitInfo(
            sha="abc123def456",
            short_sha="abc123d",
            message="test commit\n\nBody text",
            author=author,
            committer=committer,
            parent_shas=("parent1", "parent2"),
        )

        assert commit.sha == "abc123def456"
        assert commit.short_sha == "abc123d"
        assert "test commit" in commit.message
        assert len(commit.parent_shas) == 2


# =============================================================================
# Tests for BranchInfo
# =============================================================================


class TestBranchInfo:
    """Tests for BranchInfo dataclass."""

    def test_local_branch(self) -> None:
        """Local branch creation."""
        branch = BranchInfo(
            name="refs/heads/main",
            short_name="main",
            target_sha="abc123",
            is_remote=False,
            upstream="origin/main",
        )
        assert branch.short_name == "main"
        assert not branch.is_remote
        assert branch.upstream == "origin/main"

    def test_remote_branch(self) -> None:
        """Remote branch creation."""
        branch = BranchInfo(
            name="refs/remotes/origin/main",
            short_name="origin/main",
            target_sha="abc123",
            is_remote=True,
        )
        assert branch.is_remote
        assert branch.upstream is None


# =============================================================================
# Tests for TagInfo
# =============================================================================


class TestTagInfo:
    """Tests for TagInfo dataclass."""

    def test_lightweight_tag(self) -> None:
        """Lightweight tag creation."""
        tag = TagInfo(
            name="v1.0.0",
            target_sha="abc123",
            is_annotated=False,
        )
        assert tag.name == "v1.0.0"
        assert not tag.is_annotated
        assert tag.message is None

    def test_annotated_tag(self) -> None:
        """Annotated tag with message."""
        now = datetime.now(tz=UTC)
        tagger = Signature(name="Tagger", email="tag@test.com", time=now)
        tag = TagInfo(
            name="v2.0.0",
            target_sha="def456",
            is_annotated=True,
            message="Release 2.0.0",
            tagger=tagger,
        )
        assert tag.is_annotated
        assert tag.message == "Release 2.0.0"
        assert tag.tagger is not None


# =============================================================================
# Tests for DiffFile
# =============================================================================


class TestDiffFile:
    """Tests for DiffFile dataclass."""

    def test_added_file(self) -> None:
        """Added file has only new_path."""
        diff = DiffFile(
            old_path=None,
            new_path="new_file.py",
            status="added",
            additions=10,
            deletions=0,
        )
        assert diff.status == "added"
        assert diff.old_path is None
        assert diff.new_path == "new_file.py"

    def test_deleted_file(self) -> None:
        """Deleted file has only old_path."""
        diff = DiffFile(
            old_path="old_file.py",
            new_path=None,
            status="deleted",
            additions=0,
            deletions=50,
        )
        assert diff.status == "deleted"
        assert diff.deletions == 50

    def test_modified_file(self) -> None:
        """Modified file has both paths."""
        diff = DiffFile(
            old_path="file.py",
            new_path="file.py",
            status="modified",
            additions=5,
            deletions=3,
        )
        assert diff.status == "modified"
        assert diff.old_path == diff.new_path

    def test_renamed_file(self) -> None:
        """Renamed file has different paths."""
        diff = DiffFile(
            old_path="old_name.py",
            new_path="new_name.py",
            status="renamed",
            additions=0,
            deletions=0,
        )
        assert diff.status == "renamed"
        assert diff.old_path != diff.new_path


# =============================================================================
# Tests for DiffInfo
# =============================================================================


class TestDiffInfo:
    """Tests for DiffInfo dataclass."""

    def test_creation(self) -> None:
        """DiffInfo aggregates file diffs."""
        files = (
            DiffFile(None, "a.py", "added", 10, 0),
            DiffFile("b.py", "b.py", "modified", 5, 3),
        )
        diff = DiffInfo(
            files=files,
            total_additions=15,
            total_deletions=3,
            files_changed=2,
        )
        assert len(diff.files) == 2
        assert diff.total_additions == 15
        assert diff.files_changed == 2

    def test_with_patch(self) -> None:
        """DiffInfo can include patch text."""
        diff = DiffInfo(
            files=(),
            total_additions=0,
            total_deletions=0,
            files_changed=0,
            patch="diff --git a/file.py\n...",
        )
        assert diff.patch is not None


# =============================================================================
# Tests for FileDiffSummary
# =============================================================================


class TestFileDiffSummary:
    """Tests for FileDiffSummary dataclass."""

    def test_creation(self) -> None:
        """FileDiffSummary captures per-file stats."""
        summary = FileDiffSummary(
            path="src/main.py",
            status="modified",
            additions=20,
            deletions=5,
        )
        assert summary.path == "src/main.py"
        assert summary.word_count is None

    def test_with_word_count(self) -> None:
        """FileDiffSummary can include word count."""
        summary = FileDiffSummary(
            path="readme.md",
            status="modified",
            additions=100,
            deletions=50,
            word_count=250,
        )
        assert summary.word_count == 250


# =============================================================================
# Tests for DiffSummary
# =============================================================================


class TestDiffSummary:
    """Tests for DiffSummary dataclass."""

    def test_basic_summary(self) -> None:
        """DiffSummary with basic stats."""
        summary = DiffSummary(
            files_changed=3,
            total_additions=50,
            total_deletions=20,
            total_lines=70,
        )
        assert summary.files_changed == 3
        assert summary.total_lines == 70
        assert summary.per_file is None
        assert summary.total_word_count is None

    def test_with_per_file(self) -> None:
        """DiffSummary with per-file details."""
        per_file = (
            FileDiffSummary("a.py", "added", 10, 0),
            FileDiffSummary("b.py", "modified", 5, 3),
        )
        summary = DiffSummary(
            files_changed=2,
            total_additions=15,
            total_deletions=3,
            total_lines=18,
            per_file=per_file,
        )
        assert summary.per_file is not None
        assert len(summary.per_file) == 2

    def test_file_paths_property(self) -> None:
        """file_paths returns paths from per_file."""
        per_file = (
            FileDiffSummary("src/a.py", "added", 10, 0),
            FileDiffSummary("src/b.py", "modified", 5, 3),
        )
        summary = DiffSummary(
            files_changed=2,
            total_additions=15,
            total_deletions=3,
            total_lines=18,
            per_file=per_file,
        )
        paths = summary.file_paths
        assert paths == ("src/a.py", "src/b.py")

    def test_file_paths_empty_without_per_file(self) -> None:
        """file_paths returns empty tuple without per_file."""
        summary = DiffSummary(
            files_changed=2,
            total_additions=15,
            total_deletions=3,
            total_lines=18,
        )
        assert summary.file_paths == ()

    def test_with_word_count(self) -> None:
        """DiffSummary with word count."""
        summary = DiffSummary(
            files_changed=1,
            total_additions=100,
            total_deletions=50,
            total_lines=150,
            total_word_count=500,
        )
        assert summary.total_word_count == 500


# =============================================================================
# Tests for BlameHunk and BlameInfo
# =============================================================================


class TestBlameInfo:
    """Tests for blame-related dataclasses."""

    def test_blame_hunk(self) -> None:
        """BlameHunk captures blame information."""
        now = datetime.now(tz=UTC)
        author = Signature("Author", "author@test.com", now)
        hunk = BlameHunk(
            commit_sha="abc123",
            author=author,
            start_line=1,
            line_count=10,
            original_start_line=1,
        )
        assert hunk.commit_sha == "abc123"
        assert hunk.line_count == 10

    def test_blame_info(self) -> None:
        """BlameInfo aggregates hunks."""
        now = datetime.now(tz=UTC)
        author = Signature("Author", "author@test.com", now)
        hunks = (
            BlameHunk("abc", author, 1, 10, 1),
            BlameHunk("def", author, 11, 5, 11),
        )
        blame = BlameInfo(path="file.py", hunks=hunks)
        assert blame.path == "file.py"
        assert len(blame.hunks) == 2


# =============================================================================
# Tests for operation result types
# =============================================================================


class TestOperationResults:
    """Tests for various operation result types."""

    def test_merge_result_success(self) -> None:
        """MergeResult for successful merge."""
        result = MergeResult(
            success=True,
            commit_sha="abc123",
        )
        assert result.success
        assert result.conflict_paths == ()

    def test_merge_result_conflict(self) -> None:
        """MergeResult with conflicts."""
        result = MergeResult(
            success=False,
            commit_sha=None,
            conflict_paths=("file1.py", "file2.py"),
        )
        assert not result.success
        assert len(result.conflict_paths) == 2

    def test_pull_result_up_to_date(self) -> None:
        """PullResult when already up to date."""
        result = PullResult(
            success=True,
            commit_sha=None,
            up_to_date=True,
        )
        assert result.up_to_date

    def test_merge_analysis(self) -> None:
        """MergeAnalysis fields."""
        analysis = MergeAnalysis(
            up_to_date=False,
            fastforward_possible=True,
            conflicts_likely=False,
        )
        assert analysis.fastforward_possible

    def test_operation_result(self) -> None:
        """OperationResult for cherrypick/revert."""
        result = OperationResult(success=True)
        assert result.success
        assert result.conflict_paths == ()


# =============================================================================
# Tests for rebase types
# =============================================================================


class TestRebaseTypes:
    """Tests for rebase-related types."""

    def test_rebase_step(self) -> None:
        """RebaseStep captures a single step."""
        step = RebaseStep(
            action="pick",
            commit_sha="abc123",
            message="Original message",
        )
        assert step.action == "pick"

    def test_rebase_plan(self) -> None:
        """RebasePlan contains steps."""
        steps = (
            RebaseStep("pick", "abc", "First"),
            RebaseStep("squash", "def", "Second"),
        )
        plan = RebasePlan(
            upstream="main",
            onto="main",
            steps=steps,
        )
        assert len(plan.steps) == 2

    def test_rebase_result(self) -> None:
        """RebaseResult captures outcome."""
        result = RebaseResult(
            success=True,
            completed_steps=3,
            total_steps=3,
            state="done",
            new_head="abc123",
        )
        assert result.state == "done"
        assert result.completed_steps == result.total_steps


# =============================================================================
# Tests for worktree and submodule types
# =============================================================================


class TestWorktreeSubmoduleTypes:
    """Tests for worktree and submodule types."""

    def test_worktree_info(self) -> None:
        """WorktreeInfo captures worktree state."""
        wt = WorktreeInfo(
            name="feature",
            path="/repo/.worktrees/feature",
            head_ref="feature-branch",
            head_sha="abc123",
            is_main=False,
            is_bare=False,
            is_locked=False,
            lock_reason=None,
            is_prunable=False,
        )
        assert wt.name == "feature"
        assert not wt.is_main

    def test_submodule_info(self) -> None:
        """SubmoduleInfo captures submodule state."""
        sub = SubmoduleInfo(
            name="vendor/lib",
            path="vendor/lib",
            url="https://github.com/example/lib.git",
            branch="main",
            head_sha="abc123",
            status="clean",
        )
        assert sub.status == "clean"

    def test_submodule_update_result(self) -> None:
        """SubmoduleUpdateResult captures update outcomes."""
        result = SubmoduleUpdateResult(
            updated=("vendor/lib",),
            failed=(("vendor/other", "Network error"),),
            already_current=("vendor/stable",),
        )
        assert len(result.updated) == 1
        assert len(result.failed) == 1


# =============================================================================
# Tests for RefInfo and other types
# =============================================================================


class TestMiscTypes:
    """Tests for miscellaneous types."""

    def test_remote_info(self) -> None:
        """RemoteInfo captures remote configuration."""
        remote = RemoteInfo(
            name="origin",
            url="https://github.com/user/repo.git",
            push_url="git@github.com:user/repo.git",
        )
        assert remote.name == "origin"
        assert remote.push_url is not None

    def test_ref_info(self) -> None:
        """RefInfo captures reference state."""
        ref = RefInfo(
            name="HEAD",
            target_sha="abc123",
            shorthand="main",
            is_detached=False,
        )
        assert not ref.is_detached

    def test_stash_entry(self) -> None:
        """StashEntry captures stash information."""
        stash = StashEntry(
            index=0,
            message="WIP on main: abc123 feature work",
            commit_sha="stash123",
        )
        assert stash.index == 0
