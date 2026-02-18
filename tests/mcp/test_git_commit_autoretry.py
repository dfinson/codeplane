"""Tests for git_commit auto-restage-on-hook-autofix behavior.

Covers:
- Hook passes on first try: normal commit
- Hook fails with no auto-fixes: returns failure, no retry
- Hook fails with auto-fixes, retry passes: commit succeeds with warning
- Hook fails with auto-fixes, retry also fails: returns combined logs from both attempts
- Staging via paths parameter
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from codeplane.git._internal.hooks import HookResult


def _make_hook_result(
    *,
    success: bool,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    modified_files: list[str] | None = None,
) -> HookResult:
    return HookResult(
        success=success,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        modified_files=modified_files or [],
    )


@pytest.fixture
def git_commit_tool(
    mock_context: MagicMock,
) -> Any:
    """Register git tools and return the git_commit function."""
    from fastmcp import FastMCP

    mcp = FastMCP("test")
    from codeplane.mcp.tools.git import register_tools

    register_tools(mcp, mock_context)

    # Retrieve the registered tool function
    tool = mcp._tool_manager._tools["git_commit"]
    return tool.fn  # type: ignore[attr-defined]


@pytest.fixture
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.session_id = "test-session"
    return ctx


class TestGitCommitHookAutoRetry:
    """Tests for auto-restage and retry on pre-commit hook auto-fixes."""

    @pytest.mark.asyncio
    async def test_hook_passes_first_try(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """When hook passes on first attempt, commit succeeds normally."""
        mock_context.git_ops.commit.return_value = "abc1234567890"

        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            result = await git_commit_tool(mock_ctx, message="test commit")

        assert "oid" in result
        assert result["short_oid"] == "abc1234"
        assert "hook_failure" not in result
        assert "hook_warning" not in result

    @pytest.mark.asyncio
    async def test_hook_fails_no_autofix(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Hook fails with no auto-fixed files: returns failure, no retry."""
        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(
                success=False,
                exit_code=1,
                stderr="Error: unused import",
                modified_files=[],
            ),
        ) as mock_run:
            result = await git_commit_tool(mock_ctx, message="test commit")

        assert result["hook_failure"]["code"] == "HOOK_FAILED"
        assert result["hook_failure"]["exit_code"] == 1
        assert "unused import" in result["hook_failure"]["stderr"]
        # Should NOT have retried
        mock_run.assert_called_once()
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_autofix_retry_succeeds(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Hook auto-fixes files, re-stage + retry passes: commit succeeds with warning."""
        mock_context.git_ops.commit.return_value = "def5678901234"

        call_count = 0

        def side_effect(*_args: Any, **_kwargs: Any) -> HookResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_hook_result(
                    success=False,
                    exit_code=1,
                    stdout="ruff: Fixed 2 errors\n",
                    modified_files=["src/a.py", "src/b.py"],
                )
            return _make_hook_result(success=True)

        with patch("codeplane.mcp.tools.git.run_hook", side_effect=side_effect):
            result = await git_commit_tool(mock_ctx, message="test commit")

        # Commit succeeded
        assert result["oid"] == "def5678901234"
        assert result["short_oid"] == "def5678"
        # Warning about auto-fixes included
        assert "hook_warning" in result
        assert result["hook_warning"]["code"] == "HOOK_AUTO_FIXED"
        assert set(result["hook_warning"]["auto_fixed_files"]) == {"src/a.py", "src/b.py"}
        # Re-staged the auto-fixed files
        mock_context.git_ops.stage.assert_called()
        stage_calls = mock_context.git_ops.stage.call_args_list
        restaged_paths = stage_calls[-1][0][0]  # last call, first positional arg
        assert "src/a.py" in restaged_paths
        assert "src/b.py" in restaged_paths

    @pytest.mark.asyncio
    async def test_hook_autofix_retry_also_fails(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Hook auto-fixes but retry also fails: returns combined logs from both attempts."""
        call_count = 0

        def side_effect(*_args: Any, **_kwargs: Any) -> HookResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_hook_result(
                    success=False,
                    exit_code=1,
                    stdout="ruff: Fixed 1 error\n",
                    stderr="mypy: error in types\n",
                    modified_files=["src/a.py"],
                )
            return _make_hook_result(
                success=False,
                exit_code=1,
                stdout="",
                stderr="mypy: error in types (still)\n",
            )

        with patch("codeplane.mcp.tools.git.run_hook", side_effect=side_effect):
            result = await git_commit_tool(mock_ctx, message="test commit")

        assert result["hook_failure"]["code"] == "HOOK_FAILED_AFTER_RETRY"
        attempts = result["hook_failure"]["attempts"]
        assert len(attempts) == 2
        # Attempt 1
        assert attempts[0]["attempt"] == 1
        assert attempts[0]["auto_fixed_files"] == ["src/a.py"]
        assert "ruff" in attempts[0]["stdout"]
        # Attempt 2
        assert attempts[1]["attempt"] == 2
        assert "still" in attempts[1]["stderr"]
        # Commit should NOT have been called
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_paths_staged_before_hook(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock, tmp_path: Any
    ) -> None:
        """When paths are provided, they are staged before running hooks."""
        mock_context.git_ops.commit.return_value = "abc1234567890"
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the files that will be staged
        (tmp_path / "file1.py").write_text("# file1")
        (tmp_path / "file2.py").write_text("# file2")

        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            result = await git_commit_tool(mock_ctx, message="test", paths=["file1.py", "file2.py"])

        assert "oid" in result
        mock_context.git_ops.stage.assert_called_once_with(["file1.py", "file2.py"])

    @pytest.mark.asyncio
    async def test_autofix_restages_both_fixed_and_original_paths(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock, tmp_path: Any
    ) -> None:
        """On retry, both auto-fixed files AND original paths are re-staged."""
        mock_context.git_ops.commit.return_value = "abc1234567890"
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the file that will be staged
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "my_file.py").write_text("# my_file")

        call_count = 0

        def side_effect(*_args: Any, **_kwargs: Any) -> HookResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_hook_result(
                    success=False,
                    exit_code=1,
                    modified_files=["src/formatted.py"],
                )
            return _make_hook_result(success=True)

        with patch("codeplane.mcp.tools.git.run_hook", side_effect=side_effect):
            await git_commit_tool(mock_ctx, message="test", paths=["src/my_file.py"])

        # First call: stage original paths
        # Second call: re-stage both auto-fixed + original
        stage_calls = mock_context.git_ops.stage.call_args_list
        assert len(stage_calls) == 2
        restaged = set(stage_calls[1][0][0])
        assert "src/formatted.py" in restaged
        assert "src/my_file.py" in restaged

    @pytest.mark.asyncio
    async def test_no_paths_no_stage_call(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """When no paths provided and hook passes, stage is not called."""
        mock_context.git_ops.commit.return_value = "abc1234567890"

        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            await git_commit_tool(mock_ctx, message="test")

        mock_context.git_ops.stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_empty_message(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Rejects empty or whitespace-only commit messages."""
        from codeplane.git.errors import EmptyCommitMessageError

        with pytest.raises(EmptyCommitMessageError):
            await git_commit_tool(mock_ctx, message="")

        with pytest.raises(EmptyCommitMessageError):
            await git_commit_tool(mock_ctx, message="   ")

        with pytest.raises(EmptyCommitMessageError):
            await git_commit_tool(mock_ctx, message="\n\t")

        mock_context.git_ops.stage.assert_not_called()
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_paths(
        self, git_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock, tmp_path: Any
    ) -> None:
        """Rejects paths that don't exist with detailed error."""
        from codeplane.git.errors import PathsNotFoundError

        mock_context.git_ops.repo.workdir = str(tmp_path)

        with pytest.raises(PathsNotFoundError) as exc_info:
            await git_commit_tool(mock_ctx, message="test", paths=["missing.py"])
        assert exc_info.value.missing_paths == ["missing.py"]

        mock_context.git_ops.stage.assert_not_called()
        mock_context.git_ops.commit.assert_not_called()
