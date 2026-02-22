"""Tests for checkpoint tool helpers (formerly git tools).

After tool consolidation, commit is part of checkpoint.
All other git operations are handled by the agent via terminal commands.

Covers:
- _validate_commit_message helper
- _validate_paths_exist helper
- _run_hook_with_retry helper
- _summarize_commit helper
- checkpoint tool registration
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from codeplane.mcp.tools import checkpoint as checkpoint_tools

# =============================================================================
# Validation Helper Tests
# =============================================================================


class TestValidateCommitMessage:
    """Tests for _validate_commit_message helper."""

    def test_valid_message(self) -> None:
        checkpoint_tools._validate_commit_message("fix: resolve issue")

    def test_empty_string_raises(self) -> None:
        from codeplane.git.errors import EmptyCommitMessageError

        with pytest.raises(EmptyCommitMessageError):
            checkpoint_tools._validate_commit_message("")

    def test_whitespace_only_raises(self) -> None:
        from codeplane.git.errors import EmptyCommitMessageError

        with pytest.raises(EmptyCommitMessageError):
            checkpoint_tools._validate_commit_message("   \n\t  ")


class TestValidatePathsExist:
    """Tests for _validate_paths_exist helper."""

    def test_empty_paths_ok(self, tmp_path: Path) -> None:
        checkpoint_tools._validate_paths_exist(tmp_path, [])

    def test_existing_paths_ok(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").touch()
        checkpoint_tools._validate_paths_exist(tmp_path, ["a.py"])

    def test_missing_paths_raises(self, tmp_path: Path) -> None:
        from codeplane.git.errors import PathsNotFoundError

        with pytest.raises(PathsNotFoundError):
            checkpoint_tools._validate_paths_exist(tmp_path, ["nonexistent.py"])


# =============================================================================
# Hook Helper Tests
# =============================================================================


class TestRunHookWithRetry:
    """Tests for _run_hook_with_retry helper."""

    def test_success_first_try(self, tmp_path: Path) -> None:
        """Hook passes on first try — no retry needed."""
        from unittest.mock import patch

        mock_result = MagicMock()
        mock_result.success = True

        with patch("codeplane.mcp.tools.checkpoint.run_hook", return_value=mock_result):
            hook_result, failure = checkpoint_tools._run_hook_with_retry(tmp_path, ["a.py"], MagicMock())

        assert failure is None
        assert hook_result.success is True

    def test_failure_no_autofix(self, tmp_path: Path) -> None:
        """Hook fails with no auto-fixed files — returns failure."""
        from unittest.mock import patch

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.modified_files = []
        mock_result.exit_code = 1
        mock_result.stdout = "error output"
        mock_result.stderr = ""

        with patch("codeplane.mcp.tools.checkpoint.run_hook", return_value=mock_result):
            _, failure = checkpoint_tools._run_hook_with_retry(tmp_path, ["a.py"], MagicMock())

        assert failure is not None
        assert failure["hook_failure"]["code"] == "HOOK_FAILED"

    def test_autofix_then_retry_success(self, tmp_path: Path) -> None:
        """Hook auto-fixes files, retry succeeds."""
        from unittest.mock import patch

        first_result = MagicMock()
        first_result.success = False
        first_result.modified_files = ["a.py"]
        first_result.exit_code = 1
        first_result.stdout = "fixed"
        first_result.stderr = ""

        retry_result = MagicMock()
        retry_result.success = True

        stage_fn = MagicMock()

        with patch(
            "codeplane.mcp.tools.checkpoint.run_hook",
            side_effect=[first_result, retry_result],
        ):
            hook_result, failure = checkpoint_tools._run_hook_with_retry(tmp_path, ["a.py"], stage_fn)

        assert failure is None
        stage_fn.assert_called_once()

    def test_autofix_then_retry_failure(self, tmp_path: Path) -> None:
        """Hook auto-fixes files, retry also fails."""
        from unittest.mock import patch

        first_result = MagicMock()
        first_result.success = False
        first_result.modified_files = ["a.py"]
        first_result.exit_code = 1
        first_result.stdout = "first attempt"
        first_result.stderr = ""

        retry_result = MagicMock()
        retry_result.success = False
        retry_result.exit_code = 1
        retry_result.stdout = "second attempt"
        retry_result.stderr = ""
        retry_result.modified_files = []

        with patch(
            "codeplane.mcp.tools.checkpoint.run_hook",
            side_effect=[first_result, retry_result],
        ):
            _, failure = checkpoint_tools._run_hook_with_retry(tmp_path, ["a.py"], MagicMock())

        assert failure is not None
        assert failure["hook_failure"]["code"] == "HOOK_FAILED_AFTER_RETRY"
        assert len(failure["hook_failure"]["attempts"]) == 2


# =============================================================================
# Summary Helper Tests
# =============================================================================


class TestSummarizeCommit:
    """Tests for _summarize_commit helper."""

    def test_short_message(self) -> None:
        result = checkpoint_tools._summarize_commit(
            sha="abc123456789",
            message="Fix bug",
        )
        assert result == 'abc1234 "Fix bug"'

    def test_long_message_truncated(self) -> None:
        long_msg = "This is a very long commit message that should be truncated to fit"
        result = checkpoint_tools._summarize_commit(
            sha="abc123456789",
            message=long_msg,
        )
        assert result.startswith('abc1234 "')
        assert len(result) < len(long_msg) + 15

    def test_multiline_message(self) -> None:
        msg = "First line\nSecond line\nThird line"
        result = checkpoint_tools._summarize_commit(
            sha="abc123456789",
            message=msg,
        )
        assert "Second line" not in result
        assert "First line" in result


# =============================================================================
# Tool Registration Tests
# =============================================================================


@pytest.fixture
def mock_app_ctx() -> MagicMock:
    """Create a mock AppContext with all needed attributes."""
    ctx = MagicMock()
    ctx.session_manager = MagicMock()
    ctx.session_manager.get_or_create.return_value = MagicMock(
        session_id="test_session",
        fingerprints={},
    )

    # Git ops mock
    ctx.git_ops = MagicMock()
    ctx.git_ops.repo = MagicMock()
    ctx.git_ops.repo.workdir = "/tmp/test-repo"

    return ctx


class TestCheckpointTool:
    """Tests for the checkpoint tool registration."""

    def test_tool_registered(self, mock_app_ctx: MagicMock) -> None:
        mcp = FastMCP("test")
        checkpoint_tools.register_tools(mcp, mock_app_ctx)

        from codeplane.mcp._compat import get_tools_sync

        tool = get_tools_sync(mcp).get("checkpoint")
        assert tool is not None

    def test_no_old_tools_registered(self, mock_app_ctx: MagicMock) -> None:
        """Verify deleted tools are NOT registered."""
        mcp = FastMCP("test")
        checkpoint_tools.register_tools(mcp, mock_app_ctx)

        from codeplane.mcp._compat import get_tools_sync

        tools = get_tools_sync(mcp)
        deleted = [
            "git_status",
            "git_diff",
            "git_commit",
            "git_log",
            "git_push",
            "git_pull",
            "git_checkout",
            "git_merge",
            "git_reset",
            "git_stage",
            "git_branch",
            "git_remote",
            "git_stash",
            "git_rebase",
            "git_inspect",
            "git_history",
            "git_submodule",
            "git_worktree",
            "git_stage_and_commit",
            "commit",
            "verify",
        ]
        for name in deleted:
            assert name not in tools, f"{name} should have been deleted"

    def test_checkpoint_has_push_param(self, mock_app_ctx: MagicMock) -> None:
        """The checkpoint tool has a push parameter."""
        mcp = FastMCP("test")
        checkpoint_tools.register_tools(mcp, mock_app_ctx)

        from codeplane.mcp._compat import get_tools_sync

        tool = get_tools_sync(mcp).get("checkpoint")
        assert tool is not None
        params_str = str(tool.parameters)
        assert "push" in params_str
        assert "commit_message" in params_str
        assert "changed_files" in params_str

    def test_checkpoint_has_lint_param(self, mock_app_ctx: MagicMock) -> None:
        """The checkpoint tool has a 'lint' parameter."""
        mcp = FastMCP("test")
        checkpoint_tools.register_tools(mcp, mock_app_ctx)

        from codeplane.mcp._compat import get_tools_sync

        tool = get_tools_sync(mcp).get("checkpoint")
        assert tool is not None
        params_str = str(tool.parameters)
        assert "lint" in params_str
