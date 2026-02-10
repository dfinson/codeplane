"""Tests for git_stage_and_commit tool.

Covers:
- Successful stage + commit (hook passes)
- Hook fails with no auto-fixes
- Hook auto-fixes, retry succeeds with warning
- Hook auto-fixes, retry also fails
- paths is required
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
def git_stage_and_commit_tool(
    mock_context: MagicMock,
) -> Any:
    """Register git tools and return the git_stage_and_commit function."""
    from fastmcp import FastMCP

    mcp = FastMCP("test")
    from codeplane.mcp.tools.git import register_tools

    register_tools(mcp, mock_context)

    # Retrieve the registered tool function
    tool = mcp._tool_manager._tools["git_stage_and_commit"]
    return tool.fn


@pytest.fixture
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.session_id = "test-session"
    return ctx


class TestGitStageAndCommit:
    """Tests for git_stage_and_commit tool."""

    @pytest.mark.asyncio
    async def test_stages_and_commits_on_hook_pass(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Stages paths, runs hook, commits when hook passes."""
        mock_context.git_ops.commit.return_value = "abc1234567890"
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the files that will be staged
        (tmp_path / "file1.py").write_text("# file1")
        (tmp_path / "file2.py").write_text("# file2")

        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            result = await git_stage_and_commit_tool(
                mock_ctx, message="test commit", paths=["file1.py", "file2.py"]
            )

        # Verify staging happened
        mock_context.git_ops.stage.assert_called_once_with(["file1.py", "file2.py"])
        # Verify commit
        assert result["oid"] == "abc1234567890"
        assert result["short_oid"] == "abc1234"
        assert "hook_failure" not in result
        assert "hook_warning" not in result

    @pytest.mark.asyncio
    async def test_hook_fails_no_autofix(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Hook fails with no auto-fixed files: returns failure, no retry."""
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the file that will be staged
        (tmp_path / "file.py").write_text("# file")

        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(
                success=False,
                exit_code=1,
                stderr="Error: syntax error",
                modified_files=[],
            ),
        ) as mock_run:
            result = await git_stage_and_commit_tool(
                mock_ctx, message="test commit", paths=["file.py"]
            )

        assert result["hook_failure"]["code"] == "HOOK_FAILED"
        assert result["hook_failure"]["exit_code"] == 1
        assert "syntax error" in result["hook_failure"]["stderr"]
        # Should have staged, but not retried
        mock_context.git_ops.stage.assert_called_once()
        mock_run.assert_called_once()
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_autofix_retry_succeeds(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Hook auto-fixes files, re-stage + retry passes: commit succeeds with warning."""
        mock_context.git_ops.commit.return_value = "def5678901234"
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the file that will be staged
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "a.py").write_text("# a")

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
            result = await git_stage_and_commit_tool(
                mock_ctx, message="test commit", paths=["src/a.py"]
            )

        # Commit succeeded
        assert result["oid"] == "def5678901234"
        assert result["short_oid"] == "def5678"
        # Warning about auto-fixes included
        assert "hook_warning" in result
        assert result["hook_warning"]["code"] == "HOOK_AUTO_FIXED"
        assert set(result["hook_warning"]["auto_fixed_files"]) == {"src/a.py", "src/b.py"}
        # First stage: original paths, second stage: auto-fixed + original
        stage_calls = mock_context.git_ops.stage.call_args_list
        assert len(stage_calls) == 2
        # First call was the original paths
        assert stage_calls[0][0][0] == ["src/a.py"]
        # Second call includes both auto-fixed and original
        restaged_paths = set(stage_calls[1][0][0])
        assert "src/a.py" in restaged_paths
        assert "src/b.py" in restaged_paths

    @pytest.mark.asyncio
    async def test_hook_autofix_retry_also_fails(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Hook auto-fixes but retry also fails: returns combined logs from both attempts."""
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the file that will be staged
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("# main")

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
            result = await git_stage_and_commit_tool(
                mock_ctx, message="test commit", paths=["src/main.py"]
            )

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
    async def test_allow_empty_passed_to_commit(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """allow_empty flag is passed to commit."""
        mock_context.git_ops.commit.return_value = "abc1234567890"
        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Create the file that will be staged
        (tmp_path / "file.py").write_text("# file")

        with patch(
            "codeplane.mcp.tools.git.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            await git_stage_and_commit_tool(
                mock_ctx, message="empty commit", paths=["file.py"], allow_empty=True
            )

        mock_context.git_ops.commit.assert_called_once_with("empty commit", allow_empty=True)

    @pytest.mark.asyncio
    async def test_rejects_empty_message(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Rejects empty or whitespace-only commit messages."""
        from codeplane.git.errors import EmptyCommitMessageError

        mock_context.git_ops.repo.workdir = str(tmp_path)
        (tmp_path / "file.py").write_text("# file")

        with pytest.raises(EmptyCommitMessageError):
            await git_stage_and_commit_tool(mock_ctx, message="", paths=["file.py"])

        with pytest.raises(EmptyCommitMessageError):
            await git_stage_and_commit_tool(mock_ctx, message="   ", paths=["file.py"])

        with pytest.raises(EmptyCommitMessageError):
            await git_stage_and_commit_tool(mock_ctx, message="\n\t", paths=["file.py"])

        mock_context.git_ops.stage.assert_not_called()
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_paths(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Rejects paths that don't exist with detailed error."""
        from codeplane.git.errors import PathsNotFoundError

        mock_context.git_ops.repo.workdir = str(tmp_path)

        # Single missing path
        with pytest.raises(PathsNotFoundError) as exc_info:
            await git_stage_and_commit_tool(mock_ctx, message="test", paths=["nonexistent.py"])
        assert exc_info.value.missing_paths == ["nonexistent.py"]
        assert "Path not found: nonexistent.py" in str(exc_info.value)

        # Multiple missing paths
        with pytest.raises(PathsNotFoundError) as exc_info:
            await git_stage_and_commit_tool(
                mock_ctx, message="test", paths=["a.py", "b.py", "c.py"]
            )
        assert set(exc_info.value.missing_paths) == {"a.py", "b.py", "c.py"}
        assert "Paths not found:" in str(exc_info.value)

        mock_context.git_ops.stage.assert_not_called()
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_partial_nonexistent_paths(
        self,
        git_stage_and_commit_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
        tmp_path: Any,
    ) -> None:
        """Rejects if any path doesn't exist, even if some do."""
        from codeplane.git.errors import PathsNotFoundError

        mock_context.git_ops.repo.workdir = str(tmp_path)
        (tmp_path / "exists.py").write_text("# exists")

        with pytest.raises(PathsNotFoundError) as exc_info:
            await git_stage_and_commit_tool(
                mock_ctx, message="test", paths=["exists.py", "missing.py"]
            )
        assert exc_info.value.missing_paths == ["missing.py"]

        mock_context.git_ops.stage.assert_not_called()
