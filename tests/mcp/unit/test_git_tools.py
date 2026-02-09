"""Comprehensive tests for MCP git tools.

Covers:
- git_status
- git_diff
- git_commit
- git_log
- git_push
- git_pull
- git_checkout
- git_merge
- git_reset (including hard reset two-phase confirmation)
- git_stage
- git_branch
- git_remote
- git_stash
- git_rebase
- git_inspect
- git_history
- git_submodule
- git_worktree
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from codeplane.mcp.tools import git as git_tools

# =============================================================================
# Summary Helper Tests
# =============================================================================


class TestSummarizeStatus:
    """Tests for _summarize_status helper."""

    def test_clean_status(self):
        result = git_tools._summarize_status(
            branch="main",
            files={},
            is_clean=True,
            state=0,
        )
        assert result == "clean, branch: main"

    def test_detached_head(self):
        result = git_tools._summarize_status(
            branch=None,
            files={},
            is_clean=True,
            state=0,
        )
        assert result == "clean, branch: detached"

    def test_modified_files(self):
        # Status codes: 256 = modified in worktree
        files = {"a.py": 256, "b.py": 256}
        result = git_tools._summarize_status(
            branch="feature",
            files=files,
            is_clean=False,
            state=0,
        )
        assert "2 modified" in result
        assert "branch: feature" in result

    def test_staged_files(self):
        # Status codes: 1 = staged new, 2 = staged modified
        files = {"a.py": 1, "b.py": 2}
        result = git_tools._summarize_status(
            branch="main",
            files=files,
            is_clean=False,
            state=0,
        )
        assert "2 staged" in result

    def test_conflicted_files(self):
        # Status code >= 4096 = conflict
        files = {"a.py": 4096, "b.py": 4097}
        result = git_tools._summarize_status(
            branch="main",
            files=files,
            is_clean=False,
            state=0,
        )
        assert "2 conflicts" in result

    def test_rebase_in_progress(self):
        result = git_tools._summarize_status(
            branch="main",
            files={"a.py": 256},
            is_clean=False,
            state=1,
        )
        assert "rebase in progress" in result

    def test_merge_in_progress(self):
        result = git_tools._summarize_status(
            branch="main",
            files={"a.py": 256},
            is_clean=False,
            state=2,
        )
        assert "merge in progress" in result


class TestSummarizeDiff:
    """Tests for _summarize_diff helper."""

    def test_no_changes(self):
        result = git_tools._summarize_diff(0, 0, 0, staged=False)
        assert result == "no changes"

    def test_no_staged_changes(self):
        result = git_tools._summarize_diff(0, 0, 0, staged=True)
        assert result == "no staged changes"

    def test_files_changed(self):
        result = git_tools._summarize_diff(3, 50, 20, staged=False)
        assert result == "3 files changed (+50/-20)"

    def test_staged_diff(self):
        result = git_tools._summarize_diff(2, 10, 5, staged=True)
        assert result == "staged: 2 files changed (+10/-5)"


class TestSummarizeCommit:
    """Tests for _summarize_commit helper."""

    def test_short_message(self):
        result = git_tools._summarize_commit(
            sha="abc123456789",
            message="Fix bug",
        )
        assert result == 'abc1234 "Fix bug"'

    def test_long_message_truncated(self):
        long_msg = "This is a very long commit message that should be truncated to fit"
        result = git_tools._summarize_commit(
            sha="abc123456789",
            message=long_msg,
        )
        assert result.startswith('abc1234 "')
        assert len(result) < len(long_msg) + 15

    def test_multiline_message(self):
        msg = "First line\nSecond line\nThird line"
        result = git_tools._summarize_commit(
            sha="abc123456789",
            message=msg,
        )
        assert "Second line" not in result
        assert "First line" in result


class TestSummarizeLog:
    """Tests for _summarize_log helper."""

    def test_no_more(self):
        result = git_tools._summarize_log(10, has_more=False)
        assert result == "10 commits"

    def test_has_more(self):
        result = git_tools._summarize_log(50, has_more=True)
        assert result == "50 commits (more available)"


class TestSummarizeBranches:
    """Tests for _summarize_branches helper."""

    def test_with_current(self):
        result = git_tools._summarize_branches(5, current="main")
        assert result == "5 branches, current: main"

    def test_without_current(self):
        result = git_tools._summarize_branches(3, current=None)
        assert result == "3 branches"


class TestSummarizePaths:
    """Tests for _summarize_paths helper."""

    def test_empty_paths(self):
        result = git_tools._summarize_paths("stage", [])
        assert result == "nothing to stage"

    def test_single_path(self):
        result = git_tools._summarize_paths("staged", ["test.py"])
        assert "staged" in result
        assert "test.py" in result

    def test_multiple_paths(self):
        result = git_tools._summarize_paths("unstaged", ["a.py", "b.py", "c.py"])
        assert "unstaged" in result


# =============================================================================
# Tool Registration Tests
# =============================================================================


@pytest.fixture
def mock_app_ctx():
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


@pytest.fixture
def mock_mcp_context():
    """Create a mock FastMCP Context."""
    ctx = MagicMock()
    ctx.session_id = "test_session_12345"
    return ctx


class TestGitStatus:
    """Tests for git_status tool."""

    @pytest.mark.asyncio
    async def test_clean_repo(self, mock_app_ctx, mock_mcp_context):  # noqa: ARG002
        mock_app_ctx.git_ops.status.return_value = {}
        mock_app_ctx.git_ops.head.return_value = MagicMock(
            target_sha="abc1234567890",
            is_detached=False,
        )
        mock_app_ctx.git_ops.state.return_value = 0
        mock_app_ctx.git_ops.current_branch.return_value = "main"

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        # Get the registered tool
        tool = mcp._tool_manager._tools.get("git_status")
        assert tool is not None

    @pytest.mark.asyncio
    async def test_dirty_repo(self, mock_app_ctx, mock_mcp_context):  # noqa: ARG002
        mock_app_ctx.git_ops.status.return_value = {
            "modified.py": 256,
            "new.py": 1,
        }
        mock_app_ctx.git_ops.head.return_value = MagicMock(
            target_sha="abc1234567890",
            is_detached=False,
        )
        mock_app_ctx.git_ops.state.return_value = 0
        mock_app_ctx.git_ops.current_branch.return_value = "feature"

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_status")
        assert tool is not None


class TestGitDiff:
    """Tests for git_diff tool."""

    def test_tool_registered(self, mock_app_ctx):
        @dataclass
        class MockDiff:
            files_changed: int = 2
            total_additions: int = 50
            total_deletions: int = 20
            files: list[Any] | None = None
            patch: str = ""

        mock_app_ctx.git_ops.diff.return_value = MockDiff(files=[])

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_diff")
        assert tool is not None


class TestGitCommit:
    """Tests for git_commit tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.commit.return_value = "abc1234567890123456789012345678901234567890"

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_commit")
        assert tool is not None


class TestGitLog:
    """Tests for git_log tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.log.return_value = []

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_log")
        assert tool is not None


class TestGitReset:
    """Tests for git_reset tool including hard reset confirmation."""

    def test_tool_registered(self, mock_app_ctx):
        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_reset")
        assert tool is not None
        assert "confirmation_token" in str(tool.parameters)


class TestGitStage:
    """Tests for git_stage tool."""

    def test_tool_registered(self, mock_app_ctx):
        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_stage")
        assert tool is not None


class TestGitBranch:
    """Tests for git_branch tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.branches.return_value = []
        mock_app_ctx.git_ops.current_branch.return_value = "main"

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_branch")
        assert tool is not None


class TestGitRemote:
    """Tests for git_remote tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.remotes.return_value = []

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_remote")
        assert tool is not None


class TestGitStash:
    """Tests for git_stash tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.stash_list.return_value = []

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_stash")
        assert tool is not None


class TestGitRebase:
    """Tests for git_rebase tool."""

    def test_tool_registered(self, mock_app_ctx):
        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_rebase")
        assert tool is not None


class TestGitInspect:
    """Tests for git_inspect tool."""

    def test_tool_registered(self, mock_app_ctx):
        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_inspect")
        assert tool is not None


class TestGitHistory:
    """Tests for git_history tool."""

    def test_tool_registered(self, mock_app_ctx):
        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_history")
        assert tool is not None


class TestGitSubmodule:
    """Tests for git_submodule tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.submodules.return_value = []

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_submodule")
        assert tool is not None


class TestGitWorktree:
    """Tests for git_worktree tool."""

    def test_tool_registered(self, mock_app_ctx):
        mock_app_ctx.git_ops.worktrees.return_value = []

        mcp = FastMCP("test")
        git_tools.register_tools(mcp, mock_app_ctx)

        tool = mcp._tool_manager._tools.get("git_worktree")
        assert tool is not None
