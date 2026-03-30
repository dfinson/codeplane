"""Tests for GitService — git operations via subprocess."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.config import CPLConfig
from backend.services.git_service import GitError, GitService

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def config() -> CPLConfig:
    return CPLConfig()


@pytest.fixture
def git_service(config: CPLConfig) -> GitService:
    return GitService(config)


def _mock_subprocess(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> AsyncMock:
    """Create a mock for asyncio.create_subprocess_exec that returns a process."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    mock_proc.returncode = returncode
    return mock_proc


# ------------------------------------------------------------------
# Static / utility methods (original tests preserved)
# ------------------------------------------------------------------


class TestIsRemoteUrl:
    def test_https_url(self) -> None:
        assert GitService.is_remote_url("https://github.com/org/repo.git") is True

    def test_ssh_url(self) -> None:
        assert GitService.is_remote_url("git@github.com:org/repo.git") is True

    def test_ssh_protocol_url(self) -> None:
        assert GitService.is_remote_url("ssh://git@github.com/org/repo.git") is True

    def test_local_path_not_remote(self) -> None:
        assert GitService.is_remote_url("/repos/test") is False

    def test_relative_path_not_remote(self) -> None:
        assert GitService.is_remote_url("./repos/test") is False

    def test_http_url(self) -> None:
        assert GitService.is_remote_url("http://example.com/repo.git") is True

    def test_empty_string(self) -> None:
        assert GitService.is_remote_url("") is False

    def test_bare_name(self) -> None:
        assert GitService.is_remote_url("my-repo") is False


# ------------------------------------------------------------------
# _run_git
# ------------------------------------------------------------------


class TestRunGit:
    @pytest.mark.asyncio
    async def test_run_git_success(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="output\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service._run_git("status", cwd="/repo")
        assert result == "output"

    @pytest.mark.asyncio
    async def test_run_git_strips_whitespace(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="  trimmed  \n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service._run_git("status", cwd="/repo")
        assert result == "trimmed"

    @pytest.mark.asyncio
    async def test_run_git_failure_raises_git_error(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stderr="fatal: not a repo", returncode=128)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(GitError, match="git status failed"),
        ):
            await git_service._run_git("status", cwd="/repo")

    @pytest.mark.asyncio
    async def test_run_git_error_includes_stderr(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stderr="error detail", returncode=1)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(GitError) as exc_info,
        ):
            await git_service._run_git("push", cwd="/repo")
        assert exc_info.value.stderr == "error detail"

    @pytest.mark.asyncio
    async def test_run_git_executable_not_found(self, git_service: GitService, tmp_path: Path) -> None:
        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError(2, "No such file or directory", "git"),
            ),
            pytest.raises(GitError, match="git executable not found"),
        ):
            await git_service._run_git("status", cwd=tmp_path)

    @pytest.mark.asyncio
    async def test_run_git_missing_working_directory(self, git_service: GitService) -> None:
        missing_repo = "/missing/repo"
        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError(2, "No such file or directory", missing_repo),
            ),
            pytest.raises(GitError, match="working directory does not exist"),
        ):
            await git_service._run_git("status", cwd=missing_repo)

    @pytest.mark.asyncio
    async def test_run_git_os_error(self, git_service: GitService, tmp_path: Path) -> None:
        with (
            patch("asyncio.create_subprocess_exec", side_effect=OSError("spawn failed")),
            pytest.raises(GitError, match="git failed to start"),
        ):
            await git_service._run_git("status", cwd=tmp_path)

    @pytest.mark.asyncio
    async def test_run_git_sets_terminal_prompt_env(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service._run_git("status", cwd="/repo")
        call_kwargs = mock_exec.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env["GIT_TERMINAL_PROMPT"] == "0"


# ------------------------------------------------------------------
# validate_repo
# ------------------------------------------------------------------


class TestValidateRepo:
    @pytest.mark.asyncio
    async def test_valid_repo(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout=".git")
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch.object(Path, "is_dir", return_value=True),
        ):
            result = await git_service.validate_repo("/valid/repo")
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_path_not_dir(self, git_service: GitService) -> None:
        with patch.object(Path, "is_dir", return_value=False):
            result = await git_service.validate_repo("/not/a/dir")
        assert result is False

    @pytest.mark.asyncio
    async def test_not_a_git_repo(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stderr="not a git repo", returncode=128)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch.object(Path, "is_dir", return_value=True),
        ):
            result = await git_service.validate_repo("/not/git")
        assert result is False


# ------------------------------------------------------------------
# get_default_branch
# ------------------------------------------------------------------


class TestGetDefaultBranch:
    @pytest.mark.asyncio
    async def test_detects_from_symbolic_ref(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="origin/main")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_default_branch("/repo")
        assert result == "main"

    @pytest.mark.asyncio
    async def test_symbolic_ref_without_slash(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="main")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_default_branch("/repo")
        assert result == "main"

    @pytest.mark.asyncio
    async def test_falls_back_to_main(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                # symbolic-ref fails
                return _mock_subprocess(stderr="not found", returncode=1)
            # rev-parse --verify main succeeds
            return _mock_subprocess(stdout="abc123")

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.get_default_branch("/repo")
        assert result == "main"

    @pytest.mark.asyncio
    async def test_falls_back_to_master(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] <= 2:
                # symbolic-ref and rev-parse main both fail
                return _mock_subprocess(stderr="not found", returncode=1)
            # rev-parse --verify master succeeds
            return _mock_subprocess(stdout="abc123")

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.get_default_branch("/repo")
        assert result == "master"

    @pytest.mark.asyncio
    async def test_falls_back_to_current_branch(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] <= 3:
                # symbolic-ref, main, master all fail
                return _mock_subprocess(stderr="not found", returncode=1)
            # rev-parse --abbrev-ref HEAD
            return _mock_subprocess(stdout="develop")

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.get_default_branch("/repo")
        assert result == "develop"


# ------------------------------------------------------------------
# get_origin_url
# ------------------------------------------------------------------


class TestGetOriginUrl:
    @pytest.mark.asyncio
    async def test_returns_url(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="https://github.com/org/repo.git")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_origin_url("/repo")
        assert result == "https://github.com/org/repo.git"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_remote(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stderr="no remote", returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_origin_url("/repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_url(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_origin_url("/repo")
        assert result is None


# ------------------------------------------------------------------
# diff / merge_base / simple delegation methods
# ------------------------------------------------------------------


class TestDiff:
    @pytest.mark.asyncio
    async def test_returns_diff_output(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="diff --git a/f b/f\n+line")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.diff("HEAD~1", cwd="/repo")
        assert "+line" in result


class TestMergeBase:
    @pytest.mark.asyncio
    async def test_returns_merge_base_commit(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="abc1234")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.merge_base("main", "feature", cwd="/repo")
        assert result == "abc1234"


class TestIsMergeInProgress:
    @pytest.mark.asyncio
    async def test_returns_true_when_merge_head_exists(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="deadbeef")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.is_merge_in_progress(cwd="/repo")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_merge_head(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.is_merge_in_progress(cwd="/repo")
        assert result is False


class TestIsAncestor:
    @pytest.mark.asyncio
    async def test_returns_true_when_ancestor(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.is_ancestor("abc", "def", cwd="/repo")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_ancestor(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="not ancestor")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.is_ancestor("abc", "def", cwd="/repo")
        assert result is False


class TestGetConflictFiles:
    @pytest.mark.asyncio
    async def test_returns_conflict_file_list(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="file1.py\nfile2.py\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_conflict_files(cwd="/repo")
        assert result == ["file1.py", "file2.py"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="error")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_conflict_files(cwd="/repo")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_empty_lines(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="file1.py\n\n  \nfile2.py\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_conflict_files(cwd="/repo")
        assert result == ["file1.py", "file2.py"]


# ------------------------------------------------------------------
# Merge operations
# ------------------------------------------------------------------


class TestMergeOperations:
    @pytest.mark.asyncio
    async def test_checkout(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.checkout("feature", cwd="/repo")
        assert "checkout" in str(mock_exec.call_args)

    @pytest.mark.asyncio
    async def test_merge_ff_only(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.merge_ff_only("feature", cwd="/repo")
        args = mock_exec.call_args[0]
        assert "--ff-only" in args

    @pytest.mark.asyncio
    async def test_merge_with_message(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.merge("feature", cwd="/repo", message="merge msg")
        args = mock_exec.call_args[0]
        assert "-m" in args
        assert "merge msg" in args

    @pytest.mark.asyncio
    async def test_merge_without_message(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.merge("feature", cwd="/repo")
        args = mock_exec.call_args[0]
        assert "-m" not in args

    @pytest.mark.asyncio
    async def test_merge_abort_suppresses_error(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="no merge")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Should not raise
            await git_service.merge_abort(cwd="/repo")

    @pytest.mark.asyncio
    async def test_cherry_pick(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.cherry_pick("abc..def", cwd="/repo")
        args = mock_exec.call_args[0]
        assert "cherry-pick" in args
        assert "-x" in args

    @pytest.mark.asyncio
    async def test_cherry_pick_abort_suppresses_error(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="no cherry-pick")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await git_service.cherry_pick_abort(cwd="/repo")


# ------------------------------------------------------------------
# Commit / stash / push operations
# ------------------------------------------------------------------


class TestCommitOperations:
    @pytest.mark.asyncio
    async def test_add_all(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.add_all(cwd="/repo")
        args = mock_exec.call_args[0]
        assert "add" in args and "-A" in args

    @pytest.mark.asyncio
    async def test_commit_basic(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.commit("msg", cwd="/repo")
        args = mock_exec.call_args[0]
        assert "commit" in args and "-m" in args

    @pytest.mark.asyncio
    async def test_commit_allow_empty(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.commit("msg", cwd="/repo", allow_empty=True)
        args = mock_exec.call_args[0]
        assert "--allow-empty" in args

    @pytest.mark.asyncio
    async def test_auto_commit_when_dirty(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                # status --porcelain: dirty
                return _mock_subprocess(stdout="M file.py")
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.auto_commit(cwd="/repo")
        assert result is True

    @pytest.mark.asyncio
    async def test_auto_commit_when_clean(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.auto_commit(cwd="/repo")
        assert result is False

    @pytest.mark.asyncio
    async def test_stash_when_dirty(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                return _mock_subprocess(stdout="M file.py")
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.stash(cwd="/repo")
        assert result is True

    @pytest.mark.asyncio
    async def test_stash_when_clean(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.stash(cwd="/repo")
        assert result is False

    @pytest.mark.asyncio
    async def test_stash_pop_suppresses_error(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="no stash")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await git_service.stash_pop(cwd="/repo")

    @pytest.mark.asyncio
    async def test_push(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.push("feature", cwd="/repo")
        args = mock_exec.call_args[0]
        assert "push" in args and "origin" in args

    @pytest.mark.asyncio
    async def test_push_force(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.push("feature", cwd="/repo", force=True)
        args = mock_exec.call_args[0]
        assert "--force-with-lease" in args


# ------------------------------------------------------------------
# Worktree operations
# ------------------------------------------------------------------


class TestHasActiveWorktree:
    @pytest.mark.asyncio
    async def test_no_worktrees_dir(self, git_service: GitService, tmp_path: Path) -> None:
        result = await git_service.has_active_worktree(str(tmp_path))
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_worktrees_dir(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        result = await git_service.has_active_worktree(str(tmp_path))
        assert result is False

    @pytest.mark.asyncio
    async def test_has_worktree(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        (wt_dir / "job-1").mkdir()
        result = await git_service.has_active_worktree(str(tmp_path))
        assert result is True


class TestGetActiveWorktreeCount:
    @pytest.mark.asyncio
    async def test_no_worktrees_dir(self, git_service: GitService, tmp_path: Path) -> None:
        result = await git_service.get_active_worktree_count(str(tmp_path))
        assert result == 0

    @pytest.mark.asyncio
    async def test_counts_directories_only(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        (wt_dir / "job-1").mkdir()
        (wt_dir / "job-2").mkdir()
        (wt_dir / "not-a-dir.txt").touch()
        result = await git_service.get_active_worktree_count(str(tmp_path))
        assert result == 2


class TestCreateWorktree:
    @pytest.mark.asyncio
    async def test_create_worktree_success(self, git_service: GitService, tmp_path: Path) -> None:
        """End-to-end worktree creation with auto-generated branch."""
        calls = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            wt_path, branch = await git_service.create_worktree(str(tmp_path), "job-1", "main")

        assert branch == "cpl/job-1"
        assert "job-1" in wt_path

    @pytest.mark.asyncio
    async def test_create_worktree_custom_branch(self, git_service: GitService, tmp_path: Path) -> None:
        async def mock_exec(*args, **kwargs):
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            wt_path, branch = await git_service.create_worktree(str(tmp_path), "job-1", "main", branch="custom-branch")

        assert branch == "custom-branch"

    @pytest.mark.asyncio
    async def test_create_worktree_git_failure(self, git_service: GitService, tmp_path: Path) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if "worktree" in args and "add" in args:
                return _mock_subprocess(stderr="already exists", returncode=128)
            return _mock_subprocess()

        with (
            patch("asyncio.create_subprocess_exec", side_effect=mock_exec),
            pytest.raises(GitError, match="Failed to create secondary worktree"),
        ):
            await git_service.create_worktree(str(tmp_path), "job-1", "main")


class TestResolveRef:
    @pytest.mark.asyncio
    async def test_resolves_local_ref(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="abc123")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service._resolve_ref("/repo", "main")
        assert result == "main"

    @pytest.mark.asyncio
    async def test_falls_back_to_remote_ref(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                # Local ref fails
                return _mock_subprocess(stderr="not found", returncode=1)
            # Remote ref succeeds
            return _mock_subprocess(stdout="abc123")

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service._resolve_ref("/repo", "main")
        assert result == "origin/main"

    @pytest.mark.asyncio
    async def test_raises_when_both_fail(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stderr="not found", returncode=1)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(GitError, match="Cannot resolve ref"),
        ):
            await git_service._resolve_ref("/repo", "nonexistent")


class TestReattachWorktree:
    @pytest.mark.asyncio
    async def test_reattach_existing_path_returns_immediately(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        wt_path = wt_dir / "job-1"
        wt_path.mkdir()
        result = await git_service.reattach_worktree(str(tmp_path), "job-1", "feature")
        assert result == str(wt_path)

    @pytest.mark.asyncio
    async def test_reattach_creates_worktree(self, git_service: GitService, tmp_path: Path) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.reattach_worktree(str(tmp_path), "job-1", "feature")
        assert "job-1" in result

    @pytest.mark.asyncio
    async def test_reattach_failure_raises(self, git_service: GitService, tmp_path: Path) -> None:
        mock_proc = _mock_subprocess(stderr="branch gone", returncode=128)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(GitError, match="Failed to reattach worktree"),
        ):
            await git_service.reattach_worktree(str(tmp_path), "job-1", "gone-branch")


class TestRemoveWorktree:
    @pytest.mark.asyncio
    async def test_remove_nonexistent_path_is_noop(self, git_service: GitService) -> None:
        await git_service.remove_worktree("/repo", "/repo/.codeplane-worktrees/nope")

    @pytest.mark.asyncio
    async def test_remove_path_outside_worktrees_dir(self, git_service: GitService, tmp_path: Path) -> None:
        """Paths outside the worktrees directory are rejected for safety."""
        outside = tmp_path / "outside"
        outside.mkdir()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            await git_service.remove_worktree(str(tmp_path), str(outside))
        # Should not have called any git commands
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_worktree_success(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        wt_path = wt_dir / "job-1"
        wt_path.mkdir()

        calls = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
            if "rev-parse" in args and "--abbrev-ref" in args:
                return _mock_subprocess(stdout="cpl/job-1")
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            await git_service.remove_worktree(str(tmp_path), str(wt_path))

    @pytest.mark.asyncio
    async def test_remove_worktree_skips_protected_branches(self, git_service: GitService, tmp_path: Path) -> None:
        """Should not delete main/master/HEAD branches."""
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        wt_path = wt_dir / "job-1"
        wt_path.mkdir()

        calls = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
            if "rev-parse" in args and "--abbrev-ref" in args:
                return _mock_subprocess(stdout="main")
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            await git_service.remove_worktree(str(tmp_path), str(wt_path))

        # branch -D should NOT have been called for main
        branch_delete_calls = [c for c in calls if "branch" in c and "-D" in c]
        assert len(branch_delete_calls) == 0

    @pytest.mark.asyncio
    async def test_remove_worktree_fallback_to_rmtree(self, git_service: GitService, tmp_path: Path) -> None:
        """When git worktree remove fails, falls back to shutil.rmtree."""
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        wt_path = wt_dir / "job-1"
        wt_path.mkdir()

        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if "rev-parse" in args:
                return _mock_subprocess(stdout="cpl/job-1")
            if "worktree" in args and "remove" in args:
                return _mock_subprocess(stderr="locked", returncode=1)
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec), patch("shutil.rmtree"):
            await git_service.remove_worktree(str(tmp_path), str(wt_path))


class TestCleanupWorktrees:
    @pytest.mark.asyncio
    async def test_cleanup_no_worktrees_dir(self, git_service: GitService, tmp_path: Path) -> None:
        result = await git_service.cleanup_worktrees(str(tmp_path))
        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_all(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        (wt_dir / "job-1").mkdir()
        (wt_dir / "job-2").mkdir()

        async def mock_exec(*args, **kwargs):
            if "rev-parse" in args and "--abbrev-ref" in args:
                return _mock_subprocess(stdout="cpl/job-x")
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.cleanup_worktrees(str(tmp_path))

        assert result == 2

    @pytest.mark.asyncio
    async def test_cleanup_skips_symlinks(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (wt_dir / "symlink").symlink_to(real_dir)
        (wt_dir / "job-1").mkdir()

        async def mock_exec(*args, **kwargs):
            if "rev-parse" in args:
                return _mock_subprocess(stdout="cpl/job-1")
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.cleanup_worktrees(str(tmp_path))

        # Only the real directory should be removed, symlink skipped
        assert result == 1


# ------------------------------------------------------------------
# clone_repo
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# init_repo
# ------------------------------------------------------------------


class TestInitRepo:
    @pytest.mark.asyncio
    async def test_init_creates_directory_and_repo(self, git_service: GitService, tmp_path: Path) -> None:
        target = tmp_path / "new-project"
        calls: list[tuple[str, ...]] = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.init_repo(str(target))

        assert result == str(target)
        assert target.is_dir()
        # Should have called git init, then git commit --allow-empty
        git_args = [c[1:] for c in calls if c[0] == "git"]
        assert ("init",) in git_args
        assert ("commit", "--allow-empty", "-m", "Initial commit") in git_args

    @pytest.mark.asyncio
    async def test_init_existing_directory(self, git_service: GitService, tmp_path: Path) -> None:
        target = tmp_path / "existing"
        target.mkdir()
        (target / "README.md").write_text("# Hello\n")

        async def mock_exec(*args, **kwargs):
            return _mock_subprocess()

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.init_repo(str(target))

        assert result == str(target)

    @pytest.mark.asyncio
    async def test_init_failure_raises_git_error(self, git_service: GitService, tmp_path: Path) -> None:
        target = tmp_path / "fail-project"
        mock_proc = _mock_subprocess(stderr="permission denied", returncode=128)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(GitError),
        ):
            await git_service.init_repo(str(target))


# ------------------------------------------------------------------
# clone_repo
# ------------------------------------------------------------------


class TestCloneRepo:
    @pytest.mark.asyncio
    async def test_clone_success(self, git_service: GitService, tmp_path: Path) -> None:
        target = tmp_path / "cloned"
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.clone_repo("https://example.com/repo.git", str(target))
        assert result == str(target)

    @pytest.mark.asyncio
    async def test_clone_failure(self, git_service: GitService, tmp_path: Path) -> None:
        target = tmp_path / "cloned"
        mock_proc = _mock_subprocess(stderr="auth failed", returncode=128)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(GitError, match="git clone failed"),
        ):
            await git_service.clone_repo("https://example.com/repo.git", str(target))

    @pytest.mark.asyncio
    async def test_clone_git_not_found(self, git_service: GitService, tmp_path: Path) -> None:
        target = tmp_path / "cloned"
        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError(2, "No such file or directory", "git"),
            ),
            pytest.raises(GitError, match="git executable not found"),
        ):
            await git_service.clone_repo("https://example.com/repo.git", str(target))


# ------------------------------------------------------------------
# list_branches / list_worktree_names
# ------------------------------------------------------------------


class TestListBranches:
    @pytest.mark.asyncio
    async def test_lists_local_and_remote_branches(self, git_service: GitService) -> None:
        calls = [0]

        async def mock_exec(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                # Local branches
                return _mock_subprocess(stdout="main\nfeature\n")
            # Remote branches
            return _mock_subprocess(stdout="origin/main\norigin/develop\n")

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await git_service.list_branches("/repo")

        assert "main" in result
        assert "feature" in result
        assert "develop" in result
        assert "origin/main" in result
        assert "origin/develop" in result

    @pytest.mark.asyncio
    async def test_handles_git_errors_gracefully(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="error")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.list_branches("/repo")
        assert result == set()


class TestListWorktreeNames:
    @pytest.mark.asyncio
    async def test_no_worktrees_dir(self, git_service: GitService, tmp_path: Path) -> None:
        result = await git_service.list_worktree_names(str(tmp_path))
        assert result == set()

    @pytest.mark.asyncio
    async def test_returns_directory_names(self, git_service: GitService, tmp_path: Path) -> None:
        wt_dir = tmp_path / ".codeplane-worktrees"
        wt_dir.mkdir()
        (wt_dir / "job-1").mkdir()
        (wt_dir / "job-2").mkdir()
        (wt_dir / "not-dir.txt").touch()
        result = await git_service.list_worktree_names(str(tmp_path))
        assert result == {"job-1", "job-2"}


# ------------------------------------------------------------------
# GitError
# ------------------------------------------------------------------


class TestGitError:
    def test_git_error_stores_stderr(self) -> None:
        err = GitError("failed", stderr="detail")
        assert str(err) == "failed"
        assert err.stderr == "detail"

    def test_git_error_default_stderr(self) -> None:
        err = GitError("failed")
        assert err.stderr == ""


# ------------------------------------------------------------------
# Other methods
# ------------------------------------------------------------------


class TestAddIntentToAdd:
    @pytest.mark.asyncio
    async def test_suppresses_error(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(returncode=1, stderr="error")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Should not raise
            await git_service.add_intent_to_add(cwd="/repo")


class TestRevParse:
    @pytest.mark.asyncio
    async def test_resolves_ref(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="abc123def456")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.rev_parse("HEAD", cwd="/repo")
        assert result == "abc123def456"


class TestUpdateRef:
    @pytest.mark.asyncio
    async def test_updates_ref(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess()
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await git_service.update_ref("refs/heads/main", "abc123", cwd="/repo")
        args = mock_exec.call_args[0]
        assert "update-ref" in args


class TestGetCurrentBranch:
    @pytest.mark.asyncio
    async def test_returns_branch_name(self, git_service: GitService) -> None:
        mock_proc = _mock_subprocess(stdout="feature-branch")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await git_service.get_current_branch(cwd="/repo")
        assert result == "feature-branch"
