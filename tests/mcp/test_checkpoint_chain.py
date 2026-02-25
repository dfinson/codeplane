"""Tests for checkpoint tool: lint → test → commit → semantic-diff pipeline.

Covers:
- checkpoint passes + commit_message set → commits and returns semantic_diff
- checkpoint fails + commit_message set → skips commit
- checkpoint passes + no commit_message → returns agentic_hint suggesting checkpoint
- checkpoint with commit_message → returns semantic_diff on success
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeplane.git._internal.hooks import HookResult


def _hook_ok() -> HookResult:
    return HookResult(success=True, exit_code=0, stdout="", stderr="", modified_files=[])


def _lint_clean() -> MagicMock:
    return MagicMock(
        action="check",
        status="clean",
        total_diagnostics=0,
        total_files_modified=0,
        duration_seconds=0.1,
        tools_run=[],
        agentic_hint=None,
    )


def _lint_dirty() -> MagicMock:
    return MagicMock(
        action="check",
        status="issues",
        total_diagnostics=3,
        total_files_modified=0,
        duration_seconds=0.1,
        tools_run=[],
        agentic_hint=None,
    )


def _test_result_ok() -> MagicMock:
    from codeplane.testing.models import TestResult, TestRunStatus

    status = MagicMock(spec=TestRunStatus)
    status.status = "completed"
    status.duration_seconds = 0.5
    status.progress = MagicMock(passed=2, failed=0, skipped=0)
    status.diagnostics = None
    return MagicMock(
        spec=TestResult,
        run_status=status,
        test_cases=[],
        display_to_user="2 passed",
        agentic_hint=None,
    )


# ---- Fixtures ---------------------------------------------------------------


@pytest.fixture
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.session_id = "test-session"
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    return ctx


@pytest.fixture
def checkpoint_tool(mock_context: MagicMock) -> Any:
    """Register checkpoint tool and return a wrapper that resolves Field defaults."""
    from fastmcp import FastMCP

    from codeplane.mcp.tools.checkpoint import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, mock_context)

    from codeplane.mcp._compat import get_tools_sync

    raw_fn = get_tools_sync(mcp)["checkpoint"].fn

    async def _wrapper(
        ctx: Any,
        *,
        changed_files: list[str],
        lint: bool = True,
        autofix: bool = True,
        tests: bool = True,
        commit_message: str | None = None,
        push: bool = False,
    ) -> Any:
        return await raw_fn(
            ctx,
            changed_files=changed_files,
            lint=lint,
            autofix=autofix,
            tests=tests,
            commit_message=commit_message,
            push=push,
        )

    return _wrapper


# ---- Tests -------------------------------------------------------------------


class TestCheckpointCommitChain:
    """Checkpoint with commit_message chains commit on pass."""

    @pytest.mark.asyncio
    async def test_pass_with_commit(
        self,
        checkpoint_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """Checkpoint passes → auto-commit → result includes commit + semantic_diff."""
        mock_context.lint_ops.check = AsyncMock(return_value=_lint_clean())
        mock_context.test_ops.run = AsyncMock(return_value=_test_result_ok())
        mock_context.git_ops.commit.return_value = "aaa1111222233334444"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        fake_diff = MagicMock()
        fake_diff.summary = "1 changed"
        fake_diff.structural_changes = []
        fake_diff.non_structural_changes = []
        fake_diff.scope = None
        fake_diff.base_description = "HEAD~1"
        fake_diff.target_description = "HEAD"
        fake_diff.breaking_summary = None
        fake_diff.files_analyzed = 1

        with (
            patch("codeplane.mcp.tools.checkpoint._validate_paths_exist"),
            patch("codeplane.mcp.tools.checkpoint._run_hook_with_retry") as mock_hook,
            patch("codeplane.mcp.tools.diff._run_git_diff", return_value=fake_diff),
        ):
            mock_hook.return_value = (_hook_ok(), None)
            result = await checkpoint_tool(
                mock_ctx,
                changed_files=["src/foo.py"],
                lint=False,
                tests=False,
                commit_message="feat: add foo",
            )

        assert result["passed"] is True
        assert "commit" in result
        assert result["commit"]["oid"] == "aaa1111222233334444"
        assert "diff" in result["commit"]
        assert result["commit"]["diff"] == "1 changed"
        assert "committed" in result["agentic_hint"].lower()

    @pytest.mark.asyncio
    async def test_fail_skips_commit(
        self,
        checkpoint_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """Checkpoint fails → commit is skipped even if commit_message is set."""
        mock_context.lint_ops.check = AsyncMock(return_value=_lint_dirty())
        mock_context.test_ops.run = AsyncMock(return_value=_test_result_ok())

        result = await checkpoint_tool(
            mock_ctx,
            changed_files=["src/foo.py"],
            tests=False,
            commit_message="should not happen",
        )

        assert result["passed"] is False
        assert "commit" not in result
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_pass_no_commit_message(
        self,
        checkpoint_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """Checkpoint passes with no commit_message → agentic_hint suggests checkpoint."""
        mock_context.lint_ops.check = AsyncMock(return_value=_lint_clean())
        mock_context.test_ops.run = AsyncMock(return_value=_test_result_ok())

        result = await checkpoint_tool(
            mock_ctx,
            changed_files=["src/foo.py"],
            lint=False,
            tests=False,
        )

        assert result["passed"] is True
        assert "commit" not in result
        assert "commit_message" in result["agentic_hint"]

    @pytest.mark.asyncio
    async def test_pass_commit_with_push(
        self,
        checkpoint_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """Checkpoint passes + push=True → commits and pushes."""
        mock_context.lint_ops.check = AsyncMock(return_value=_lint_clean())
        mock_context.test_ops.run = AsyncMock(return_value=_test_result_ok())
        mock_context.git_ops.commit.return_value = "bbb2222333344445555"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        with (
            patch("codeplane.mcp.tools.checkpoint._validate_paths_exist"),
            patch("codeplane.mcp.tools.checkpoint._run_hook_with_retry") as mock_hook,
            patch("codeplane.mcp.tools.diff._run_git_diff", side_effect=Exception("no index")),
        ):
            mock_hook.return_value = (_hook_ok(), None)
            result = await checkpoint_tool(
                mock_ctx,
                changed_files=["src/foo.py"],
                lint=False,
                tests=False,
                commit_message="chore: update",
                push=True,
            )

        assert result["passed"] is True
        assert result["commit"]["pushed"] == "origin"
        mock_context.git_ops.push.assert_called_once_with(remote="origin", force=False)
        # semantic_diff failed gracefully
        assert "diff" not in result["commit"]


class TestCheckpointSemanticDiff:
    """Checkpoint with commit_message returns semantic_diff on success."""

    @pytest.mark.asyncio
    async def test_checkpoint_commit_includes_semantic_diff(
        self,
        checkpoint_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """Checkpoint with commit_message returns a lean semantic_diff summary."""
        mock_context.lint_ops.check = AsyncMock(return_value=_lint_clean())
        mock_context.test_ops.run = AsyncMock(return_value=_test_result_ok())
        mock_context.git_ops.commit.return_value = "ccc3333444455556666"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        fake_diff = MagicMock()
        fake_diff.summary = "2 files changed"
        fake_diff.structural_changes = []
        fake_diff.non_structural_changes = []
        fake_diff.scope = None
        fake_diff.base_description = "HEAD~1"
        fake_diff.target_description = "HEAD"
        fake_diff.breaking_summary = None
        fake_diff.files_analyzed = 2

        with (
            patch("codeplane.mcp.tools.checkpoint._validate_paths_exist"),
            patch("codeplane.mcp.tools.checkpoint._run_hook_with_retry") as mock_hook,
            patch("codeplane.mcp.tools.diff._run_git_diff", return_value=fake_diff),
        ):
            mock_hook.return_value = (_hook_ok(), None)
            result = await checkpoint_tool(
                mock_ctx,
                changed_files=["src/foo.py"],
                lint=False,
                tests=False,
                commit_message="test commit",
            )

        assert result["passed"] is True
        assert result["commit"]["oid"] == "ccc3333444455556666"
        assert "diff" in result["commit"]
        assert result["commit"]["diff"] == "2 files changed"

    @pytest.mark.asyncio
    async def test_checkpoint_commit_semantic_diff_failure_is_silent(
        self,
        checkpoint_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """If semantic_diff raises, checkpoint commit still succeeds without it."""
        mock_context.lint_ops.check = AsyncMock(return_value=_lint_clean())
        mock_context.test_ops.run = AsyncMock(return_value=_test_result_ok())
        mock_context.git_ops.commit.return_value = "ddd4444555566667777"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        with (
            patch("codeplane.mcp.tools.checkpoint._validate_paths_exist"),
            patch("codeplane.mcp.tools.checkpoint._run_hook_with_retry") as mock_hook,
            patch("codeplane.mcp.tools.diff._run_git_diff", side_effect=RuntimeError("boom")),
        ):
            mock_hook.return_value = (_hook_ok(), None)
            result = await checkpoint_tool(
                mock_ctx,
                changed_files=["src/foo.py"],
                lint=False,
                tests=False,
                commit_message="test commit",
            )

        assert result["passed"] is True
        assert result["commit"]["oid"] == "ddd4444555566667777"
        assert "diff" not in result["commit"]


# ---- _validate_paths_exist unit tests ----------------------------------------


class TestValidatePathsExist:
    """Unit tests for _validate_paths_exist with git-aware deletion support."""

    def test_existing_file_passes(self, tmp_path: Path) -> None:
        from codeplane.mcp.tools.checkpoint import _validate_paths_exist

        (tmp_path / "foo.py").write_text("x = 1")
        _validate_paths_exist(tmp_path, ["foo.py"])  # should not raise

    def test_missing_untracked_file_raises(self, tmp_path: Path) -> None:
        from codeplane.git.errors import PathsNotFoundError
        from codeplane.mcp.tools.checkpoint import _validate_paths_exist

        with pytest.raises(PathsNotFoundError):
            _validate_paths_exist(tmp_path, ["typo.py"])

    def test_deleted_tracked_file_passes(self, tmp_path: Path) -> None:
        """A file that doesn't exist on disk but is tracked by git is a valid deletion."""
        from codeplane.mcp.tools.checkpoint import _validate_paths_exist

        tracked = {"deleted.py", "other.py"}
        _validate_paths_exist(tmp_path, ["deleted.py"], tracked_files=tracked)  # should not raise

    def test_deleted_untracked_file_raises(self, tmp_path: Path) -> None:
        """A file that doesn't exist on disk AND isn't tracked is a typo."""
        from codeplane.git.errors import PathsNotFoundError
        from codeplane.mcp.tools.checkpoint import _validate_paths_exist

        tracked = {"other.py"}
        with pytest.raises(PathsNotFoundError):
            _validate_paths_exist(tmp_path, ["never_existed.py"], tracked_files=tracked)

    def test_mixed_existing_and_deleted_tracked(self, tmp_path: Path) -> None:
        """Mix of existing files and tracked deletions should pass."""
        from codeplane.mcp.tools.checkpoint import _validate_paths_exist

        (tmp_path / "alive.py").write_text("x = 1")
        tracked = {"alive.py", "deleted.py"}
        _validate_paths_exist(
            tmp_path, ["alive.py", "deleted.py"], tracked_files=tracked
        )  # should not raise

    def test_empty_paths_is_noop(self, tmp_path: Path) -> None:
        from codeplane.mcp.tools.checkpoint import _validate_paths_exist

        _validate_paths_exist(tmp_path, [])  # should not raise
