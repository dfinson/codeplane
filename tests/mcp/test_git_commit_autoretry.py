"""Tests for checkpoint commit auto-restage-on-hook-autofix behavior.

Covers:
- Hook passes on first try: normal commit
- Hook fails with no auto-fixes: returns failure, no retry
- Hook fails with auto-fixes, retry passes: commit succeeds with warning
- Hook fails with auto-fixes, retry also fails: returns combined logs from both attempts
- Staging via paths parameter
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
def checkpoint_commit_tool(
    mock_context: MagicMock,
) -> Any:
    """Register checkpoint tool and return a wrapper for commit testing.

    Uses checkpoint with lint=False, tests=False to exercise only the commit path.
    Wraps the raw tool function to resolve Pydantic Field defaults,
    since calling tool.fn() directly bypasses FastMCP's parameter parsing.
    """
    from fastmcp import FastMCP

    mcp = FastMCP("test")
    from codeplane.mcp.tools.checkpoint import register_tools

    register_tools(mcp, mock_context)

    # Retrieve the registered tool function
    from codeplane.mcp._compat import get_tools_sync

    raw_fn = get_tools_sync(mcp)["checkpoint"].fn

    async def _wrapper(
        ctx: Any,
        *,
        message: str,
        paths: list[str] | None = None,
        push: bool = False,
    ) -> Any:
        return await raw_fn(
            ctx,
            changed_files=paths or [],
            lint=False,
            autofix=False,
            tests=False,
            commit_message=message,
            push=push,
        )

    return _wrapper


@pytest.fixture
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.session_id = "test-session"
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    return ctx


class TestCheckpointCommitHookAutoRetry:
    """Tests for auto-restage and retry on pre-commit hook auto-fixes."""

    @pytest.mark.asyncio
    async def test_hook_passes_first_try(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """When hook passes on first attempt, commit succeeds normally."""
        mock_context.git_ops.commit.return_value = "abc1234567890"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        with patch(
            "codeplane.mcp.tools.checkpoint.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            result = await checkpoint_commit_tool(mock_ctx, message="test commit")

        assert result["passed"] is True
        assert "commit" in result
        assert result["commit"]["oid"] == "abc1234567890"
        assert result["commit"]["short_oid"] == "abc1234"
        assert "hook_failure" not in result["commit"]
        assert "hook_warning" not in result["commit"]

    @pytest.mark.asyncio
    async def test_hook_fails_no_autofix(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Hook fails with no auto-fixed files: returns failure in commit section."""
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        with patch(
            "codeplane.mcp.tools.checkpoint.run_hook",
            return_value=_make_hook_result(
                success=False,
                exit_code=1,
                stderr="Error: unused import",
                modified_files=[],
            ),
        ) as mock_run:
            result = await checkpoint_commit_tool(mock_ctx, message="test commit")

        # Checkpoint passed checks but commit failed due to hooks
        assert result["passed"] is True
        assert result["commit"]["hook_failure"]["code"] == "HOOK_FAILED"
        assert result["commit"]["hook_failure"]["exit_code"] == 1
        assert "unused import" in result["commit"]["hook_failure"]["stderr"]
        # Should NOT have retried
        mock_run.assert_called_once()
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_autofix_retry_succeeds(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Hook auto-fixes files, re-stage + retry passes: commit succeeds with warning."""
        mock_context.git_ops.commit.return_value = "def5678901234"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

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

        with patch("codeplane.mcp.tools.checkpoint.run_hook", side_effect=side_effect):
            result = await checkpoint_commit_tool(mock_ctx, message="test commit")

        # Commit succeeded
        assert result["passed"] is True
        assert result["commit"]["oid"] == "def5678901234"
        assert result["commit"]["short_oid"] == "def5678"
        # Warning about auto-fixes included
        assert "hook_warning" in result["commit"]
        assert result["commit"]["hook_warning"]["code"] == "HOOK_AUTO_FIXED"
        assert set(result["commit"]["hook_warning"]["auto_fixed_files"]) == {"src/a.py", "src/b.py"}
        # Re-staged the auto-fixed files
        mock_context.git_ops.stage.assert_called()

    @pytest.mark.asyncio
    async def test_hook_autofix_retry_also_fails(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Hook auto-fixes but retry also fails: returns combined logs from both attempts."""
        mock_context.git_ops.repo.workdir = "/tmp/repo"

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

        with patch("codeplane.mcp.tools.checkpoint.run_hook", side_effect=side_effect):
            result = await checkpoint_commit_tool(mock_ctx, message="test commit")

        assert result["passed"] is True
        assert result["commit"]["hook_failure"]["code"] == "HOOK_FAILED_AFTER_RETRY"
        attempts = result["commit"]["hook_failure"]["attempts"]
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
    async def test_stage_all_called_before_hooks(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Checkpoint calls stage_all before running hooks."""
        mock_context.git_ops.commit.return_value = "abc1234567890"
        mock_context.git_ops.repo.workdir = "/tmp/repo"

        with patch(
            "codeplane.mcp.tools.checkpoint.run_hook",
            return_value=_make_hook_result(success=True),
        ):
            result = await checkpoint_commit_tool(mock_ctx, message="test")

        assert result["passed"] is True
        assert "commit" in result
        mock_context.git_ops.stage_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_string_skips_commit(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Empty string is falsy — checkpoint skips commit (same as no message)."""
        result = await checkpoint_commit_tool(mock_ctx, message="")
        assert result["passed"] is True
        assert "commit" not in result
        mock_context.git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_whitespace_only_message(
        self, checkpoint_commit_tool: Any, mock_ctx: MagicMock, mock_context: MagicMock
    ) -> None:
        """Whitespace-only strings are truthy but semantically empty — raises."""
        from codeplane.git.errors import EmptyCommitMessageError

        with pytest.raises(EmptyCommitMessageError):
            await checkpoint_commit_tool(mock_ctx, message="   ")

        with pytest.raises(EmptyCommitMessageError):
            await checkpoint_commit_tool(mock_ctx, message="\n\t")

        mock_context.git_ops.commit.assert_not_called()
