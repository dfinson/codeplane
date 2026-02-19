"""Tests for git_reset two-phase hard reset confirmation.

Tests the confirmation flow for destructive git reset --hard operations.
"""

import secrets
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.session import SessionState
from codeplane.mcp.tools.git import _HARD_RESET_TOKEN_KEY

if TYPE_CHECKING:
    from fastmcp import FastMCP


class TestGitResetHardConfirmationUnit:
    """Unit tests for the confirmation token logic."""

    @pytest.fixture
    def session(self) -> SessionState:
        """Create a session with real fingerprints dict."""
        return SessionState(
            session_id="test-session-123",
            created_at=0.0,
            last_active=0.0,
        )

    def test_token_key_constant_exists(self) -> None:
        """Token key should be a valid string constant."""
        assert isinstance(_HARD_RESET_TOKEN_KEY, str)
        assert len(_HARD_RESET_TOKEN_KEY) > 0
        assert "reset" in _HARD_RESET_TOKEN_KEY.lower()

    def test_phase1_stores_token_in_session(self, session: SessionState) -> None:
        """Phase 1 should store a token in session fingerprints."""
        token = secrets.token_urlsafe(16)
        session.fingerprints[_HARD_RESET_TOKEN_KEY] = token

        assert _HARD_RESET_TOKEN_KEY in session.fingerprints
        assert len(session.fingerprints[_HARD_RESET_TOKEN_KEY]) > 0

    def test_phase2_valid_token_clears_fingerprint(self, session: SessionState) -> None:
        """Phase 2 with valid token should clear the stored token."""
        token = secrets.token_urlsafe(16)
        session.fingerprints[_HARD_RESET_TOKEN_KEY] = token

        stored_token = session.fingerprints.get(_HARD_RESET_TOKEN_KEY)
        confirmation_token = token

        if stored_token and confirmation_token == stored_token:
            del session.fingerprints[_HARD_RESET_TOKEN_KEY]

        assert _HARD_RESET_TOKEN_KEY not in session.fingerprints

    def test_phase2_invalid_token_keeps_fingerprint(self, session: SessionState) -> None:
        """Phase 2 with invalid token should keep the stored token."""
        token = secrets.token_urlsafe(16)
        session.fingerprints[_HARD_RESET_TOKEN_KEY] = token

        stored_token = session.fingerprints.get(_HARD_RESET_TOKEN_KEY)
        confirmation_token = "wrong-token"

        assert confirmation_token != stored_token
        assert _HARD_RESET_TOKEN_KEY in session.fingerprints
        assert session.fingerprints[_HARD_RESET_TOKEN_KEY] == token

    def test_phase2_no_pending_token_fails(self, session: SessionState) -> None:
        """Phase 2 without a pending token should fail."""
        assert _HARD_RESET_TOKEN_KEY not in session.fingerprints

        stored_token = session.fingerprints.get(_HARD_RESET_TOKEN_KEY)
        assert stored_token is None

    def test_token_is_single_use(self, session: SessionState) -> None:
        """Token should be invalidated after successful use."""
        token = secrets.token_urlsafe(16)
        session.fingerprints[_HARD_RESET_TOKEN_KEY] = token

        stored_token = session.fingerprints.get(_HARD_RESET_TOKEN_KEY)
        assert stored_token == token
        del session.fingerprints[_HARD_RESET_TOKEN_KEY]

        stored_token_again = session.fingerprints.get(_HARD_RESET_TOKEN_KEY)
        assert stored_token_again is None


class TestGitResetHardConfirmationIntegration:
    """Integration tests using FastMCP tool_manager."""

    @pytest.fixture
    def app_ctx_with_session(self) -> MagicMock:
        """Create mock AppContext with real session state."""
        from codeplane.mcp.session import SessionManager

        ctx = MagicMock()
        ctx.git_ops = MagicMock()
        ctx.git_ops.repo = MagicMock()
        ctx.git_ops.repo.workdir = "/tmp/test-repo"
        ctx.git_ops.status.return_value = {"file1.py": 256}

        session_mgr = SessionManager()
        ctx.session_manager = session_mgr

        return ctx

    async def _call_tool(
        self, mcp: "FastMCP", name: str, arguments: dict[str, object]
    ) -> dict[str, Any]:
        """Helper to call a tool through FastMCP's tool manager.

        Note: When calling tool.fn directly, Pydantic Field defaults aren't
        applied, so we must explicitly include None values for optional params.
        """
        from typing import Any, cast

        from codeplane.mcp._compat import get_tools_sync

        tool = get_tools_sync(mcp)[name]
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.session_id = "test-session"
        # FastMCP's tool.fn is wrapped - get the underlying function
        # and call it directly with kwargs
        fn = tool.fn
        # If fn is a wrapped async function, we need to call it properly
        # FastMCP may wrap tools differently, so we access the raw function
        if hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        return cast(dict[str, Any], await fn(ctx, **arguments))

    @pytest.mark.asyncio
    async def test_soft_reset_executes_immediately(self, app_ctx_with_session: MagicMock) -> None:
        """Soft reset should execute without confirmation."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "soft", "confirmation_token": None}
        )

        app_ctx_with_session.git_ops.reset.assert_called_once_with("HEAD~1", mode="soft")

        assert result["mode"] == "soft"
        assert result["reset_to"] == "HEAD~1"

    @pytest.mark.asyncio
    async def test_mixed_reset_executes_immediately(self, app_ctx_with_session: MagicMock) -> None:
        """Mixed reset should execute without confirmation."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "mixed", "confirmation_token": None}
        )

        app_ctx_with_session.git_ops.reset.assert_called_once_with("HEAD~1", mode="mixed")

        assert result["mode"] == "mixed"

    @pytest.mark.asyncio
    async def test_hard_reset_phase1_blocks_and_returns_token(
        self, app_ctx_with_session: MagicMock
    ) -> None:
        """Hard reset without token should block and return confirmation token."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": None}
        )

        app_ctx_with_session.git_ops.reset.assert_not_called()

        assert result["requires_confirmation"] is True
        assert "confirmation_token" in result
        assert result["mode"] == "hard"
        assert "warning" in result
        assert "DESTRUCTIVE" in result["warning"]
        assert "BLOCKED" in result["summary"]

    @pytest.mark.asyncio
    async def test_hard_reset_phase2_executes_with_valid_token(
        self, app_ctx_with_session: MagicMock
    ) -> None:
        """Hard reset with valid token should execute."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result1 = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": None}
        )
        token = result1["confirmation_token"]

        reason = "User confirmed: resetting to HEAD~1 to discard all uncommitted work"
        result2 = await self._call_tool(
            mcp,
            "git_reset",
            {"ref": "HEAD~1", "mode": "hard", "confirmation_token": token, "gate_reason": reason},
        )

        app_ctx_with_session.git_ops.reset.assert_called_once_with("HEAD~1", mode="hard")

        assert result2["mode"] == "hard"
        assert result2["reset_to"] == "HEAD~1"
        assert "requires_confirmation" not in result2

    @pytest.mark.asyncio
    async def test_hard_reset_fails_with_wrong_token(self, app_ctx_with_session: MagicMock) -> None:
        """Hard reset with wrong token should fail with TOKEN_MISMATCH."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": None}
        )

        result = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": "wrong-token"}
        )

        app_ctx_with_session.git_ops.reset.assert_not_called()

        assert "error" in result
        assert result["error"]["code"] == "GATE_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_hard_reset_fails_without_pending_confirmation(
        self, app_ctx_with_session: MagicMock
    ) -> None:
        """Hard reset with token but no pending confirmation should fail."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": "some-token"}
        )

        app_ctx_with_session.git_ops.reset.assert_not_called()

        assert "error" in result
        assert result["error"]["code"] == "GATE_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_hard_reset_token_is_single_use(self, app_ctx_with_session: MagicMock) -> None:
        """Token should be invalidated after use."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result1 = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": None}
        )
        token = result1["confirmation_token"]

        reason = "User confirmed: resetting to HEAD~1 to discard all uncommitted work"
        await self._call_tool(
            mcp,
            "git_reset",
            {"ref": "HEAD~1", "mode": "hard", "confirmation_token": token, "gate_reason": reason},
        )

        result3 = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": token}
        )

        assert "error" in result3
        assert result3["error"]["code"] == "GATE_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_hard_reset_caps_uncommitted_files_list(
        self, app_ctx_with_session: MagicMock
    ) -> None:
        """Uncommitted files list should be capped at 20."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        app_ctx_with_session.git_ops.status.return_value = {f"file{i}.py": 256 for i in range(30)}

        mcp = FastMCP("test")
        register_tools(mcp, app_ctx_with_session)

        result = await self._call_tool(
            mcp, "git_reset", {"ref": "HEAD~1", "mode": "hard", "confirmation_token": None}
        )

        assert result["uncommitted_files_count"] == 30
        assert len(result["uncommitted_files"]) == 20
