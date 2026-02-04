"""Tests for mcp/tools/git.py module.

Covers:
- Parameter models (GitStatusParams, GitDiffParams, etc.)
- Summary helper functions
- Tool handlers (git_status, git_diff, etc.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from codeplane.git.models import (
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffInfo,
    MergeResult,
    OperationResult,
    PullResult,
    RebasePlan,
    RebaseResult,
    Signature,
    SubmoduleInfo,
    SubmoduleUpdateResult,
)
from codeplane.mcp.tools.git import (
    GitBranchParams,
    GitCheckoutParams,
    GitCommitParams,
    GitDiffParams,
    GitHistoryParams,
    GitInspectParams,
    GitLogParams,
    GitMergeParams,
    GitPullParams,
    GitPushParams,
    GitRebaseParams,
    GitRemoteParams,
    GitResetParams,
    GitStageParams,
    GitStashParams,
    GitStatusParams,
    GitSubmoduleParams,
    GitWorktreeParams,
    _summarize_branches,
    _summarize_commit,
    _summarize_diff,
    _summarize_log,
    _summarize_paths,
    _summarize_status,
    git_branch,
    git_checkout,
    git_commit,
    git_diff,
    git_history,
    git_inspect,
    git_log,
    git_merge,
    git_pull,
    git_push,
    git_rebase,
    git_remote,
    git_reset,
    git_stage,
    git_stash,
    git_status,
    git_submodule,
    git_worktree,
)

# =============================================================================
# Test Fixtures - Real Dataclass Factories
# =============================================================================


def make_signature() -> Signature:
    """Create a test signature."""
    return Signature(name="Test User", email="test@example.com", time=datetime.now(UTC))


def make_commit_info(sha: str = "abc1234567890", message: str = "Test commit") -> CommitInfo:
    """Create a test commit info."""
    sig = make_signature()
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        message=message,
        author=sig,
        committer=sig,
        parent_shas=("parent123",),
    )


def make_diff_info(files_changed: int = 2, additions: int = 10, deletions: int = 5) -> DiffInfo:
    """Create a test diff info."""
    return DiffInfo(
        files=(),
        total_additions=additions,
        total_deletions=deletions,
        files_changed=files_changed,
        patch=None,
    )


def make_merge_result(conflict_paths: list[str] | None = None) -> MergeResult:
    """Create a test merge result."""
    return MergeResult(
        success=conflict_paths is None or len(conflict_paths) == 0,
        commit_sha="abc123",
        conflict_paths=tuple(conflict_paths or []),
    )


def make_pull_result() -> PullResult:
    """Create a test pull result."""
    return PullResult(
        success=True,
        commit_sha="abc123",
        up_to_date=False,
    )


def make_branch_info(name: str = "feature") -> BranchInfo:
    """Create a test branch info."""
    return BranchInfo(
        name=f"refs/heads/{name}",
        short_name=name,
        target_sha="abc123",
        is_remote=False,
        upstream=None,
    )


def make_rebase_plan() -> RebasePlan:
    """Create a test rebase plan."""
    return RebasePlan(upstream="main", onto="main", steps=())


def make_rebase_result() -> RebaseResult:
    """Create a test rebase result."""
    return RebaseResult(success=True, completed_steps=0, total_steps=0, state="done")


def make_operation_result() -> OperationResult:
    """Create a test operation result for cherry-pick/revert."""
    return OperationResult(success=True, conflict_paths=())


def make_submodule_info(path: str = "ext/lib") -> SubmoduleInfo:
    """Create a test submodule info."""
    return SubmoduleInfo(
        name="lib",
        path=path,
        url="https://github.com/test/lib.git",
        head_sha="abc123",
        branch=None,
        status="clean",
    )


def make_submodule_update_result() -> SubmoduleUpdateResult:
    """Create a test submodule update result."""
    return SubmoduleUpdateResult(
        updated=(),
        failed=(),
        already_current=(),
    )


def make_blame_info() -> BlameInfo:
    """Create a test blame info."""
    return BlameInfo(path="file.py", hunks=())


class TestGitStatusParams:
    """Tests for GitStatusParams."""

    def test_defaults(self) -> None:
        """Default values."""
        params = GitStatusParams()
        assert params.paths is None

    def test_with_paths(self) -> None:
        """With paths."""
        params = GitStatusParams(paths=["src/", "tests/"])
        assert params.paths == ["src/", "tests/"]


class TestGitDiffParams:
    """Tests for GitDiffParams."""

    def test_defaults(self) -> None:
        """Default values."""
        params = GitDiffParams()
        assert params.base is None
        assert params.target is None
        assert params.staged is False

    def test_staged_flag(self) -> None:
        """Staged flag."""
        params = GitDiffParams(staged=True)
        assert params.staged is True


class TestGitCommitParams:
    """Tests for GitCommitParams."""

    def test_required_message(self) -> None:
        """Message is required."""
        params = GitCommitParams(message="Fix bug")
        assert params.message == "Fix bug"

    def test_allow_empty_default(self) -> None:
        """Allow empty defaults to False."""
        params = GitCommitParams(message="test")
        assert params.allow_empty is False


class TestGitLogParams:
    """Tests for GitLogParams."""

    def test_defaults(self) -> None:
        """Default values."""
        params = GitLogParams()
        assert params.ref == "HEAD"
        assert params.limit == 50

    def test_limit_max(self) -> None:
        """Limit has maximum."""
        with pytest.raises(ValidationError):
            GitLogParams(limit=101)  # > GIT_LOG_MAX


class TestGitStageParams:
    """Tests for GitStageParams."""

    def test_add_action(self) -> None:
        """Add action."""
        params = GitStageParams(action="add", paths=["file.py"])
        assert params.action == "add"

    def test_remove_action(self) -> None:
        """Remove action."""
        params = GitStageParams(action="remove", paths=["file.py"])
        assert params.action == "remove"

    def test_all_action(self) -> None:
        """All action."""
        params = GitStageParams(action="all")
        assert params.action == "all"

    def test_discard_action(self) -> None:
        """Discard action."""
        params = GitStageParams(action="discard", paths=["file.py"])
        assert params.action == "discard"

    def test_invalid_action(self) -> None:
        """Invalid action rejected."""
        with pytest.raises(ValidationError):
            GitStageParams(action="invalid")  # type: ignore[arg-type]


class TestGitBranchParams:
    """Tests for GitBranchParams."""

    def test_list_action(self) -> None:
        """List action."""
        params = GitBranchParams(action="list")
        assert params.action == "list"

    def test_create_action(self) -> None:
        """Create action with name."""
        params = GitBranchParams(action="create", name="feature")
        assert params.action == "create"
        assert params.name == "feature"

    def test_delete_action(self) -> None:
        """Delete action."""
        params = GitBranchParams(action="delete", name="old-branch", force=True)
        assert params.force is True


class TestGitResetParams:
    """Tests for GitResetParams."""

    def test_mode_options(self) -> None:
        """Mode options."""
        for mode in ["soft", "mixed", "hard"]:
            params = GitResetParams(ref="HEAD~1", mode=mode)  # type: ignore[arg-type]
            assert params.mode == mode

    def test_default_mode(self) -> None:
        """Default mode is mixed."""
        params = GitResetParams(ref="HEAD~1")
        assert params.mode == "mixed"


class TestSummarizeStatus:
    """Tests for _summarize_status helper."""

    def test_clean_status(self) -> None:
        """Clean status message."""
        summary = _summarize_status("main", {}, True, 0)
        assert "clean" in summary
        assert "main" in summary

    def test_detached_head(self) -> None:
        """Detached HEAD."""
        summary = _summarize_status(None, {}, True, 0)
        assert "detached" in summary

    def test_with_changes(self) -> None:
        """With modified files."""
        files = {"file1.py": 256, "file2.py": 512}
        summary = _summarize_status("main", files, False, 0)
        assert "modified" in summary

    def test_rebase_state(self) -> None:
        """Rebase in progress."""
        summary = _summarize_status("main", {}, False, 1)
        assert "rebase" in summary

    def test_merge_state(self) -> None:
        """Merge in progress."""
        summary = _summarize_status("main", {}, False, 2)
        assert "merge" in summary


class TestSummarizeDiff:
    """Tests for _summarize_diff helper."""

    def test_no_changes(self) -> None:
        """No changes."""
        summary = _summarize_diff(0, 0, 0, False)
        assert "no changes" in summary

    def test_staged_no_changes(self) -> None:
        """No staged changes."""
        summary = _summarize_diff(0, 0, 0, True)
        assert "no staged changes" in summary

    def test_with_changes(self) -> None:
        """With changes."""
        summary = _summarize_diff(3, 100, 50, False)
        assert "3 files" in summary
        assert "+100" in summary
        assert "-50" in summary

    def test_staged_prefix(self) -> None:
        """Staged prefix."""
        summary = _summarize_diff(1, 10, 5, True)
        assert summary.startswith("staged:")


class TestSummarizeCommit:
    """Tests for _summarize_commit helper."""

    def test_short_sha_and_message(self) -> None:
        """Short SHA and message."""
        summary = _summarize_commit("abc1234567890", "Fix bug")
        assert "abc1234" in summary
        assert "Fix bug" in summary

    def test_long_message_truncated(self) -> None:
        """Long message is truncated."""
        long_msg = "x" * 100
        summary = _summarize_commit("abc1234567890", long_msg)
        assert "..." in summary


class TestSummarizeLog:
    """Tests for _summarize_log helper."""

    def test_basic(self) -> None:
        """Basic log summary."""
        summary = _summarize_log(5, False)
        assert "5 commits" in summary

    def test_with_more(self) -> None:
        """With more available."""
        summary = _summarize_log(10, True)
        assert "more available" in summary


class TestSummarizeBranches:
    """Tests for _summarize_branches helper."""

    def test_with_current(self) -> None:
        """With current branch."""
        summary = _summarize_branches(5, "main")
        assert "5 branches" in summary
        assert "current: main" in summary

    def test_without_current(self) -> None:
        """Without current branch."""
        summary = _summarize_branches(3, None)
        assert "3 branches" in summary
        assert "current" not in summary


class TestSummarizePaths:
    """Tests for _summarize_paths helper."""

    def test_single_path(self) -> None:
        """Single path."""
        summary = _summarize_paths("staged", ["file.py"])
        assert "staged file.py" in summary

    def test_few_paths(self) -> None:
        """Few paths listed."""
        summary = _summarize_paths("staged", ["a.py", "b.py"])
        assert "2 files" in summary
        assert "a.py" in summary
        assert "b.py" in summary

    def test_many_paths_truncated(self) -> None:
        """Many paths truncated."""
        summary = _summarize_paths("staged", ["a.py", "b.py", "c.py", "d.py", "e.py"])
        assert "+3 more" in summary


# =============================================================================
# Tool Handler Tests
# =============================================================================


class TestGitStatusHandler:
    """Tests for git_status handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        """Create mock context with git_ops."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.status.return_value = {}
        ctx.git_ops.head.return_value = MagicMock(target_sha="abc1234", is_detached=False)
        ctx.git_ops.state.return_value = 0
        ctx.git_ops.current_branch.return_value = "main"
        return ctx

    @pytest.mark.asyncio
    async def test_clean_status(self, mock_ctx: MagicMock) -> None:
        """Returns clean status."""
        params = GitStatusParams()
        result = await git_status(mock_ctx, params)

        assert result["is_clean"] is True
        assert result["branch"] == "main"
        assert "clean" in result["summary"]

    @pytest.mark.asyncio
    async def test_dirty_status(self, mock_ctx: MagicMock) -> None:
        """Returns dirty status with files."""
        mock_ctx.git_ops.status.return_value = {"file.py": 256}
        params = GitStatusParams()
        result = await git_status(mock_ctx, params)

        assert result["is_clean"] is False
        assert "file.py" in result["files"]

    @pytest.mark.asyncio
    async def test_detached_head(self, mock_ctx: MagicMock) -> None:
        """Returns detached head status."""
        mock_ctx.git_ops.head.return_value = MagicMock(target_sha="abc", is_detached=True)
        mock_ctx.git_ops.current_branch.return_value = None
        params = GitStatusParams()
        result = await git_status(mock_ctx, params)

        assert result["is_detached"] is True


class TestGitDiffHandler:
    """Tests for git_diff handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.diff.return_value = make_diff_info()
        return ctx

    @pytest.mark.asyncio
    async def test_working_tree_diff(self, mock_ctx: MagicMock) -> None:
        """Returns working tree diff."""
        params = GitDiffParams()
        result = await git_diff(mock_ctx, params)

        mock_ctx.git_ops.diff.assert_called_once_with(
            base=None, target=None, staged=False, include_patch=True
        )
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_staged_diff(self, mock_ctx: MagicMock) -> None:
        """Returns staged diff."""
        params = GitDiffParams(staged=True)
        await git_diff(mock_ctx, params)

        mock_ctx.git_ops.diff.assert_called_once_with(
            base=None, target=None, staged=True, include_patch=True
        )


class TestGitCommitHandler:
    """Tests for git_commit handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.commit.return_value = "abc1234567890"
        ctx.git_ops.repo = MagicMock()
        ctx.git_ops.repo.workdir = "/tmp/repo"
        return ctx

    @pytest.mark.asyncio
    async def test_commit_success(self, mock_ctx: MagicMock) -> None:
        """Creates commit successfully."""
        with patch("codeplane.mcp.tools.git.run_hook") as mock_hook:
            mock_hook.return_value = MagicMock(success=True)
            params = GitCommitParams(message="Test commit")
            result = await git_commit(mock_ctx, params)

            assert result["oid"] == "abc1234567890"
            assert result["short_oid"] == "abc1234"

    @pytest.mark.asyncio
    async def test_commit_with_paths(self, mock_ctx: MagicMock) -> None:
        """Stages paths before commit."""
        with patch("codeplane.mcp.tools.git.run_hook") as mock_hook:
            mock_hook.return_value = MagicMock(success=True)
            params = GitCommitParams(message="Test", paths=["file.py"])
            await git_commit(mock_ctx, params)

            mock_ctx.git_ops.stage.assert_called_once_with(["file.py"])

    @pytest.mark.asyncio
    async def test_commit_hook_failure(self, mock_ctx: MagicMock) -> None:
        """Raises on hook failure."""
        from codeplane.mcp.errors import HookFailedError

        with patch("codeplane.mcp.tools.git.run_hook") as mock_hook:
            mock_hook.return_value = MagicMock(
                success=False, exit_code=1, stdout="", stderr="err", modified_files=[]
            )
            params = GitCommitParams(message="Test")

            with pytest.raises(HookFailedError):
                await git_commit(mock_ctx, params)


class TestGitLogHandler:
    """Tests for git_log handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_empty_log(self, mock_ctx: MagicMock) -> None:
        """Returns empty log."""
        mock_ctx.git_ops.log.return_value = []
        params = GitLogParams()
        result = await git_log(mock_ctx, params)

        assert result["results"] == []
        assert "0 commits" in result["summary"]

    @pytest.mark.asyncio
    async def test_log_with_commits(self, mock_ctx: MagicMock) -> None:
        """Returns commits."""
        mock_ctx.git_ops.log.return_value = [make_commit_info()]
        params = GitLogParams()
        result = await git_log(mock_ctx, params)

        assert len(result["results"]) == 1

    @pytest.mark.asyncio
    async def test_log_pagination(self, mock_ctx: MagicMock) -> None:
        """Returns pagination when more commits."""
        # Return limit+1 commits to trigger has_more
        commits = [make_commit_info(sha=f"sha{i:04d}00000") for i in range(52)]
        mock_ctx.git_ops.log.return_value = commits
        params = GitLogParams(limit=50)
        result = await git_log(mock_ctx, params)

        assert len(result["results"]) == 50
        assert "next_cursor" in result["pagination"]


class TestGitPushHandler:
    """Tests for git_push handler."""

    @pytest.mark.asyncio
    async def test_push(self) -> None:
        """Push to remote."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        params = GitPushParams(remote="origin")
        result = await git_push(ctx, params)

        ctx.git_ops.push.assert_called_once_with(remote="origin", force=False)
        assert "origin" in result["summary"]

    @pytest.mark.asyncio
    async def test_force_push(self) -> None:
        """Force push."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        params = GitPushParams(remote="origin", force=True)
        result = await git_push(ctx, params)

        ctx.git_ops.push.assert_called_once_with(remote="origin", force=True)
        assert "force" in result["summary"]


class TestGitPullHandler:
    """Tests for git_pull handler."""

    @pytest.mark.asyncio
    async def test_pull(self) -> None:
        """Pull from remote."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.pull.return_value = make_pull_result()
        params = GitPullParams()
        result = await git_pull(ctx, params)

        ctx.git_ops.pull.assert_called_once_with(remote="origin")
        assert "pulled" in result["summary"]


class TestGitCheckoutHandler:
    """Tests for git_checkout handler."""

    @pytest.mark.asyncio
    async def test_checkout(self) -> None:
        """Checkout branch."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        params = GitCheckoutParams(ref="feature")
        result = await git_checkout(ctx, params)

        ctx.git_ops.checkout.assert_called_once_with("feature", create=False)
        assert "checked out" in result["summary"]

    @pytest.mark.asyncio
    async def test_checkout_create(self) -> None:
        """Create and checkout branch."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        params = GitCheckoutParams(ref="new-branch", create=True)
        result = await git_checkout(ctx, params)

        ctx.git_ops.checkout.assert_called_once_with("new-branch", create=True)
        assert "created" in result["summary"]


class TestGitMergeHandler:
    """Tests for git_merge handler."""

    @pytest.mark.asyncio
    async def test_merge_success(self) -> None:
        """Merge without conflicts."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.merge.return_value = make_merge_result()
        params = GitMergeParams(ref="feature")
        result = await git_merge(ctx, params)

        assert "merged feature" in result["summary"]

    @pytest.mark.asyncio
    async def test_merge_conflicts(self) -> None:
        """Merge with conflicts."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.merge.return_value = make_merge_result(conflict_paths=["file.py"])
        params = GitMergeParams(ref="feature")
        result = await git_merge(ctx, params)

        assert "conflicts" in result["summary"]


class TestGitResetHandler:
    """Tests for git_reset handler."""

    @pytest.mark.asyncio
    async def test_reset(self) -> None:
        """Reset HEAD."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        params = GitResetParams(ref="HEAD~1", mode="hard")
        result = await git_reset(ctx, params)

        ctx.git_ops.reset.assert_called_once_with("HEAD~1", mode="hard")
        assert "hard" in result["summary"]


class TestGitStageHandler:
    """Tests for git_stage handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_add(self, mock_ctx: MagicMock) -> None:
        """Stage files."""
        params = GitStageParams(action="add", paths=["file.py"])
        result = await git_stage(mock_ctx, params)

        mock_ctx.git_ops.stage.assert_called_once_with(["file.py"])
        assert "staged" in result["summary"]

    @pytest.mark.asyncio
    async def test_add_no_paths(self, mock_ctx: MagicMock) -> None:
        """Raises when add without paths."""
        params = GitStageParams(action="add")
        with pytest.raises(ValueError, match="paths required"):
            await git_stage(mock_ctx, params)

    @pytest.mark.asyncio
    async def test_remove(self, mock_ctx: MagicMock) -> None:
        """Unstage files."""
        params = GitStageParams(action="remove", paths=["file.py"])
        await git_stage(mock_ctx, params)

        mock_ctx.git_ops.unstage.assert_called_once_with(["file.py"])

    @pytest.mark.asyncio
    async def test_all(self, mock_ctx: MagicMock) -> None:
        """Stage all."""
        mock_ctx.git_ops.stage_all.return_value = ["a.py", "b.py"]
        params = GitStageParams(action="all")
        await git_stage(mock_ctx, params)

        mock_ctx.git_ops.stage_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_discard(self, mock_ctx: MagicMock) -> None:
        """Discard changes."""
        params = GitStageParams(action="discard", paths=["file.py"])
        await git_stage(mock_ctx, params)

        mock_ctx.git_ops.discard.assert_called_once_with(["file.py"])


class TestGitBranchHandler:
    """Tests for git_branch handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.current_branch.return_value = "main"
        return ctx

    @pytest.mark.asyncio
    async def test_list(self, mock_ctx: MagicMock) -> None:
        """List branches."""
        mock_ctx.git_ops.branches.return_value = []
        params = GitBranchParams(action="list")
        await git_branch(mock_ctx, params)

        mock_ctx.git_ops.branches.assert_called_once_with(include_remote=True)

    @pytest.mark.asyncio
    async def test_create(self, mock_ctx: MagicMock) -> None:
        """Create branch."""
        mock_ctx.git_ops.create_branch.return_value = make_branch_info("feature")
        params = GitBranchParams(action="create", name="feature")
        result = await git_branch(mock_ctx, params)

        mock_ctx.git_ops.create_branch.assert_called_once_with("feature", ref="HEAD")
        assert "created branch feature" in result["summary"]

    @pytest.mark.asyncio
    async def test_create_no_name(self, mock_ctx: MagicMock) -> None:
        """Raises when create without name."""
        params = GitBranchParams(action="create")
        with pytest.raises(ValueError, match="name required"):
            await git_branch(mock_ctx, params)

    @pytest.mark.asyncio
    async def test_delete(self, mock_ctx: MagicMock) -> None:
        """Delete branch."""
        params = GitBranchParams(action="delete", name="old", force=True)
        await git_branch(mock_ctx, params)

        mock_ctx.git_ops.delete_branch.assert_called_once_with("old", force=True)


class TestGitRemoteHandler:
    """Tests for git_remote handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_list(self, mock_ctx: MagicMock) -> None:
        """List remotes."""
        mock_ctx.git_ops.remotes.return_value = []
        params = GitRemoteParams(action="list")
        await git_remote(mock_ctx, params)

        mock_ctx.git_ops.remotes.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch(self, mock_ctx: MagicMock) -> None:
        """Fetch from remote."""
        params = GitRemoteParams(action="fetch", remote="upstream")
        await git_remote(mock_ctx, params)

        mock_ctx.git_ops.fetch.assert_called_once_with(remote="upstream")

    @pytest.mark.asyncio
    async def test_tags(self, mock_ctx: MagicMock) -> None:
        """List tags."""
        mock_ctx.git_ops.tags.return_value = []
        params = GitRemoteParams(action="tags")
        await git_remote(mock_ctx, params)

        mock_ctx.git_ops.tags.assert_called_once()


class TestGitStashHandler:
    """Tests for git_stash handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_push(self, mock_ctx: MagicMock) -> None:
        """Push to stash."""
        mock_ctx.git_ops.stash_push.return_value = "abc123"
        params = GitStashParams(action="push", message="WIP")
        result = await git_stash(mock_ctx, params)

        mock_ctx.git_ops.stash_push.assert_called_once()
        assert "stashed" in result["summary"]

    @pytest.mark.asyncio
    async def test_pop(self, mock_ctx: MagicMock) -> None:
        """Pop from stash."""
        params = GitStashParams(action="pop", index=1)
        await git_stash(mock_ctx, params)

        mock_ctx.git_ops.stash_pop.assert_called_once_with(index=1)

    @pytest.mark.asyncio
    async def test_list(self, mock_ctx: MagicMock) -> None:
        """List stash entries."""
        mock_ctx.git_ops.stash_list.return_value = []
        params = GitStashParams(action="list")
        await git_stash(mock_ctx, params)

        mock_ctx.git_ops.stash_list.assert_called_once()


class TestGitRebaseHandler:
    """Tests for git_rebase handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_plan(self, mock_ctx: MagicMock) -> None:
        """Plan rebase."""
        mock_ctx.git_ops.rebase_plan.return_value = make_rebase_plan()
        params = GitRebaseParams(action="plan", upstream="main")
        await git_rebase(mock_ctx, params)

        mock_ctx.git_ops.rebase_plan.assert_called_once_with("main", onto=None)

    @pytest.mark.asyncio
    async def test_plan_no_upstream(self, mock_ctx: MagicMock) -> None:
        """Raises when plan without upstream."""
        params = GitRebaseParams(action="plan")
        with pytest.raises(ValueError, match="upstream required"):
            await git_rebase(mock_ctx, params)

    @pytest.mark.asyncio
    async def test_continue(self, mock_ctx: MagicMock) -> None:
        """Continue rebase."""
        mock_ctx.git_ops.rebase_continue.return_value = make_rebase_result()
        params = GitRebaseParams(action="continue")
        await git_rebase(mock_ctx, params)

        mock_ctx.git_ops.rebase_continue.assert_called_once()

    @pytest.mark.asyncio
    async def test_abort(self, mock_ctx: MagicMock) -> None:
        """Abort rebase."""
        params = GitRebaseParams(action="abort")
        await git_rebase(mock_ctx, params)

        mock_ctx.git_ops.rebase_abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip(self, mock_ctx: MagicMock) -> None:
        """Skip commit."""
        mock_ctx.git_ops.rebase_skip.return_value = make_rebase_result()
        params = GitRebaseParams(action="skip")
        await git_rebase(mock_ctx, params)

        mock_ctx.git_ops.rebase_skip.assert_called_once()


class TestGitInspectHandler:
    """Tests for git_inspect handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_show(self, mock_ctx: MagicMock) -> None:
        """Show commit."""
        mock_ctx.git_ops.show.return_value = make_commit_info()
        params = GitInspectParams(action="show", ref="HEAD")
        await git_inspect(mock_ctx, params)

        mock_ctx.git_ops.show.assert_called_once_with(ref="HEAD")

    @pytest.mark.asyncio
    async def test_blame(self, mock_ctx: MagicMock) -> None:
        """File blame."""
        mock_ctx.git_ops.blame.return_value = make_blame_info()
        params = GitInspectParams(action="blame", path="file.py")
        await git_inspect(mock_ctx, params)

        mock_ctx.git_ops.blame.assert_called_once()

    @pytest.mark.asyncio
    async def test_blame_no_path(self, mock_ctx: MagicMock) -> None:
        """Raises when blame without path."""
        params = GitInspectParams(action="blame")
        with pytest.raises(ValueError, match="path required"):
            await git_inspect(mock_ctx, params)


class TestGitHistoryHandler:
    """Tests for git_history handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_amend(self, mock_ctx: MagicMock) -> None:
        """Amend commit."""
        mock_ctx.git_ops.amend.return_value = "abc1234"
        params = GitHistoryParams(action="amend", message="Updated")
        await git_history(mock_ctx, params)

        mock_ctx.git_ops.amend.assert_called_once_with(message="Updated")

    @pytest.mark.asyncio
    async def test_cherrypick(self, mock_ctx: MagicMock) -> None:
        """Cherry-pick commit."""
        mock_ctx.git_ops.cherrypick.return_value = make_operation_result()
        params = GitHistoryParams(action="cherrypick", commit="abc1234")
        await git_history(mock_ctx, params)

        mock_ctx.git_ops.cherrypick.assert_called_once_with("abc1234")

    @pytest.mark.asyncio
    async def test_cherrypick_no_commit(self, mock_ctx: MagicMock) -> None:
        """Raises when cherrypick without commit."""
        params = GitHistoryParams(action="cherrypick")
        with pytest.raises(ValueError, match="commit required"):
            await git_history(mock_ctx, params)

    @pytest.mark.asyncio
    async def test_revert(self, mock_ctx: MagicMock) -> None:
        """Revert commit."""
        mock_ctx.git_ops.revert.return_value = make_operation_result()
        params = GitHistoryParams(action="revert", commit="abc1234")
        await git_history(mock_ctx, params)

        mock_ctx.git_ops.revert.assert_called_once_with("abc1234")


class TestGitSubmoduleHandler:
    """Tests for git_submodule handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_list(self, mock_ctx: MagicMock) -> None:
        """List submodules."""
        mock_ctx.git_ops.submodules.return_value = []
        params = GitSubmoduleParams(action="list")
        await git_submodule(mock_ctx, params)

        mock_ctx.git_ops.submodules.assert_called_once()

    @pytest.mark.asyncio
    async def test_add(self, mock_ctx: MagicMock) -> None:
        """Add submodule."""
        mock_ctx.git_ops.submodule_add.return_value = make_submodule_info()
        params = GitSubmoduleParams(
            action="add", url="https://github.com/test/repo.git", path="ext"
        )
        await git_submodule(mock_ctx, params)

        mock_ctx.git_ops.submodule_add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_missing_params(self, mock_ctx: MagicMock) -> None:
        """Raises when add without url/path."""
        params = GitSubmoduleParams(action="add")
        with pytest.raises(ValueError, match="url and path required"):
            await git_submodule(mock_ctx, params)

    @pytest.mark.asyncio
    async def test_update(self, mock_ctx: MagicMock) -> None:
        """Update submodules."""
        mock_ctx.git_ops.submodule_update.return_value = make_submodule_update_result()
        params = GitSubmoduleParams(action="update")
        await git_submodule(mock_ctx, params)

        mock_ctx.git_ops.submodule_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_init(self, mock_ctx: MagicMock) -> None:
        """Init submodules."""
        mock_ctx.git_ops.submodule_init.return_value = []
        params = GitSubmoduleParams(action="init")
        await git_submodule(mock_ctx, params)

        mock_ctx.git_ops.submodule_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove(self, mock_ctx: MagicMock) -> None:
        """Remove submodule."""
        params = GitSubmoduleParams(action="remove", path="ext")
        await git_submodule(mock_ctx, params)

        mock_ctx.git_ops.submodule_remove.assert_called_once_with("ext")


class TestGitWorktreeHandler:
    """Tests for git_worktree handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_list(self, mock_ctx: MagicMock) -> None:
        """List worktrees."""
        mock_ctx.git_ops.worktrees.return_value = []
        params = GitWorktreeParams(action="list")
        await git_worktree(mock_ctx, params)

        mock_ctx.git_ops.worktrees.assert_called_once()

    @pytest.mark.asyncio
    async def test_add(self, mock_ctx: MagicMock) -> None:
        """Add worktree."""
        params = GitWorktreeParams(action="add", path="/tmp/wt", ref="feature")
        await git_worktree(mock_ctx, params)

        mock_ctx.git_ops.worktree_add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_missing_params(self, mock_ctx: MagicMock) -> None:
        """Raises when add without path/ref."""
        params = GitWorktreeParams(action="add")
        with pytest.raises(ValueError, match="path and ref required"):
            await git_worktree(mock_ctx, params)

    @pytest.mark.asyncio
    async def test_remove(self, mock_ctx: MagicMock) -> None:
        """Remove worktree."""
        params = GitWorktreeParams(action="remove", name="wt")
        await git_worktree(mock_ctx, params)

        mock_ctx.git_ops.worktree_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_lock(self, mock_ctx: MagicMock) -> None:
        """Lock worktree."""
        params = GitWorktreeParams(action="lock", name="wt", reason="busy")
        await git_worktree(mock_ctx, params)

        mock_ctx.git_ops.worktree_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_unlock(self, mock_ctx: MagicMock) -> None:
        """Unlock worktree."""
        params = GitWorktreeParams(action="unlock", name="wt")
        await git_worktree(mock_ctx, params)

        mock_ctx.git_ops.worktree_unlock.assert_called_once()

    @pytest.mark.asyncio
    async def test_prune(self, mock_ctx: MagicMock) -> None:
        """Prune worktrees."""
        mock_ctx.git_ops.worktree_prune.return_value = []
        params = GitWorktreeParams(action="prune")
        await git_worktree(mock_ctx, params)

        mock_ctx.git_ops.worktree_prune.assert_called_once()
