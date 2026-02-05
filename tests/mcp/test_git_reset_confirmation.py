"""Tests for git_reset two-phase hard reset confirmation.

Tests the confirmation flow for destructive git reset --hard operations.
"""

from unittest.mock import MagicMock

import pytest

from codeplane.mcp.tools.git import _HARD_RESET_TOKEN_KEY


class TestGitResetHardConfirmation:
    """Tests for git_reset hard mode two-phase confirmation."""

    @pytest.fixture
    def mock_app_ctx(self):
        """Create a mock AppContext with session support."""
        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.repo = MagicMock()
        ctx.git_ops.repo.workdir = "/tmp/test-repo"

        # Create a real dict for fingerprints to test token storage
        session = MagicMock()
        session.fingerprints = {}
        ctx.session_manager = MagicMock()
        ctx.session_manager.get_or_create.return_value = session

        return ctx, session

    @pytest.fixture
    def mock_fastmcp_context(self):
        """Create a mock FastMCP Context."""
        ctx = MagicMock()
        ctx.session_id = "test-session-123"
        return ctx

    @pytest.mark.asyncio
    async def test_soft_reset_executes_immediately(self, mock_app_ctx, mock_fastmcp_context):
        """Soft reset should execute without confirmation."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        # Get the registered tool
        git_reset = mcp._tool_manager._tools["git_reset"].fn

        result = await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="soft")

        assert "requires_confirmation" not in result
        assert result["mode"] == "soft"
        assert result["reset_to"] == "HEAD~1"
        app_ctx.git_ops.reset.assert_called_once_with("HEAD~1", mode="soft")

    @pytest.mark.asyncio
    async def test_mixed_reset_executes_immediately(self, mock_app_ctx, mock_fastmcp_context):
        """Mixed reset should execute without confirmation."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        result = await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="mixed")

        assert "requires_confirmation" not in result
        assert result["mode"] == "mixed"
        app_ctx.git_ops.reset.assert_called_once_with("HEAD~1", mode="mixed")

    @pytest.mark.asyncio
    async def test_hard_reset_phase1_returns_warning(self, mock_app_ctx, mock_fastmcp_context):
        """Hard reset without token should return warning and token."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        app_ctx.git_ops.status.return_value = {"file1.py": 256, "file2.py": 256}

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        result = await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="hard")

        # Should NOT execute the reset
        app_ctx.git_ops.reset.assert_not_called()

        # Should return confirmation requirements
        assert result["requires_confirmation"] is True
        assert "confirmation_token" in result
        assert result["mode"] == "hard"
        assert result["target_ref"] == "HEAD~1"
        assert result["uncommitted_files_count"] == 2
        assert "warning" in result
        assert "DESTRUCTIVE" in result["warning"]
        assert "agentic_hint" in result
        assert "STOP" in result["agentic_hint"]
        assert "BLOCKED" in result["summary"]

        # Token should be stored in session
        assert _HARD_RESET_TOKEN_KEY in session.fingerprints

    @pytest.mark.asyncio
    async def test_hard_reset_phase2_executes_with_valid_token(
        self, mock_app_ctx, mock_fastmcp_context
    ):
        """Hard reset with valid token should execute."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        app_ctx.git_ops.status.return_value = {"file1.py": 256}

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        # Phase 1: Get token
        result1 = await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="hard")
        token = result1["confirmation_token"]

        # Phase 2: Execute with token
        result2 = await git_reset(
            mock_fastmcp_context, ref="HEAD~1", mode="hard", confirmation_token=token
        )

        # Should execute the reset
        app_ctx.git_ops.reset.assert_called_once_with("HEAD~1", mode="hard")
        assert result2["mode"] == "hard"
        assert result2["reset_to"] == "HEAD~1"
        assert "requires_confirmation" not in result2

        # Token should be cleared from session
        assert _HARD_RESET_TOKEN_KEY not in session.fingerprints

    @pytest.mark.asyncio
    async def test_hard_reset_fails_with_invalid_token(self, mock_app_ctx, mock_fastmcp_context):
        """Hard reset with wrong token should fail."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        app_ctx.git_ops.status.return_value = {}

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        # Phase 1: Get token
        await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="hard")

        # Phase 2: Try with wrong token
        result = await git_reset(
            mock_fastmcp_context,
            ref="HEAD~1",
            mode="hard",
            confirmation_token="wrong-token",
        )

        # Should NOT execute the reset
        app_ctx.git_ops.reset.assert_not_called()

        # Should return error
        assert "error" in result
        assert result["error"]["code"] == "TOKEN_MISMATCH"

    @pytest.mark.asyncio
    async def test_hard_reset_fails_with_token_but_no_pending(
        self, mock_app_ctx, mock_fastmcp_context
    ):
        """Hard reset with token but no pending confirmation should fail."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        # Try to use a token without requesting one first
        result = await git_reset(
            mock_fastmcp_context,
            ref="HEAD~1",
            mode="hard",
            confirmation_token="some-token",
        )

        # Should NOT execute the reset
        app_ctx.git_ops.reset.assert_not_called()

        # Should return error
        assert "error" in result
        assert result["error"]["code"] == "INVALID_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_hard_reset_token_is_single_use(self, mock_app_ctx, mock_fastmcp_context):
        """Token should be invalidated after use."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        app_ctx.git_ops.status.return_value = {}

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        # Phase 1: Get token
        result1 = await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="hard")
        token = result1["confirmation_token"]

        # Phase 2: Use token successfully
        await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="hard", confirmation_token=token)

        # Phase 3: Try to reuse the same token
        result3 = await git_reset(
            mock_fastmcp_context, ref="HEAD~1", mode="hard", confirmation_token=token
        )

        # Should fail because token was consumed
        assert "error" in result3
        assert result3["error"]["code"] == "INVALID_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_hard_reset_caps_uncommitted_files_list(self, mock_app_ctx, mock_fastmcp_context):
        """Uncommitted files list should be capped at 20."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx, session = mock_app_ctx
        # Create 30 uncommitted files
        app_ctx.git_ops.status.return_value = {f"file{i}.py": 256 for i in range(30)}

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx)

        git_reset = mcp._tool_manager._tools["git_reset"].fn

        result = await git_reset(mock_fastmcp_context, ref="HEAD~1", mode="hard")

        # Count should be accurate
        assert result["uncommitted_files_count"] == 30

        # List should be capped at 20
        assert len(result["uncommitted_files"]) == 20
