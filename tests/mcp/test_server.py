"""Tests for mcp/server.py module.

Tests the actual exports:
- create_mcp_server() function

Handler tests use conftest.py fixtures for integration testing.
"""

import builtins
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.server import create_mcp_server


class TestCreateMcpServer:
    """Tests for create_mcp_server function."""

    @pytest.fixture
    def mock_context(self, tmp_path: Path) -> MagicMock:
        """Create a mock AppContext."""
        context = MagicMock()
        context.repo_root = tmp_path
        context.session_manager = MagicMock()
        mock_session = MagicMock()
        mock_session.session_id = "test-session"
        context.session_manager.get_or_create.return_value = mock_session
        return context

    def test_creates_fastmcp_server(self, mock_context: MagicMock) -> None:
        """Creates a FastMCP server instance."""
        mcp = create_mcp_server(mock_context)
        assert mcp is not None
        assert mcp.name == "codeplane"

    def test_registers_tools(self, mock_context: MagicMock) -> None:
        """Registers tools from all tool modules."""
        mcp = create_mcp_server(mock_context)
        from codeplane.mcp._compat import get_tools_sync

        assert len(get_tools_sync(mcp)) > 0

    def test_has_expected_tools(self, mock_context: MagicMock) -> None:
        """Has core tools registered."""
        mcp = create_mcp_server(mock_context)
        from codeplane.mcp._compat import get_tools_sync

        tool_names = set(get_tools_sync(mcp).keys())
        assert "read_source" in tool_names
        assert "search" in tool_names
        assert "checkpoint" in tool_names


class TestPatchFastmcpDocket:
    """Tests for _patch_fastmcp_docket defensive logic."""

    def test_patches_when_docket_lifespan_exists(self) -> None:
        """Patches _docket_lifespan when it exists on FastMCP."""
        from fastmcp import FastMCP

        from codeplane.mcp.server import _patch_fastmcp_docket

        # Clean up any prior patch markers
        if hasattr(FastMCP, "_docket_patched"):
            del FastMCP._docket_patched

        _patch_fastmcp_docket()
        # Should have set the patched marker
        assert getattr(FastMCP, "_docket_patched", False) is True

    def test_no_op_when_already_patched(self) -> None:
        """Does not re-patch when already patched."""
        from fastmcp import FastMCP

        from codeplane.mcp.server import _patch_fastmcp_docket

        # Ensure it's marked as patched
        FastMCP._docket_patched = True  # type: ignore[attr-defined]
        original = getattr(FastMCP, "_docket_lifespan", None)
        _patch_fastmcp_docket()
        # Should not have changed the lifespan
        assert getattr(FastMCP, "_docket_lifespan", None) is original

    def test_skips_when_no_docket_lifespan(self) -> None:
        """Gracefully skips when _docket_lifespan doesn't exist."""
        from unittest.mock import patch

        from fastmcp import FastMCP

        from codeplane.mcp.server import _patch_fastmcp_docket

        # Remove _docket_patched so the guard isn't short-circuited
        had_patched = hasattr(FastMCP, "_docket_patched")
        if had_patched:
            delattr(FastMCP, "_docket_patched")

        try:
            # Mock hasattr to return False for _docket_lifespan
            original_hasattr = builtins.hasattr

            def _mock_hasattr(obj: object, name: str) -> bool:
                if obj is FastMCP and name == "_docket_lifespan":
                    return False
                return original_hasattr(obj, name)

            with patch("builtins.hasattr", side_effect=_mock_hasattr):
                _patch_fastmcp_docket()  # Should not raise

            # Should NOT have set patched marker
            assert not hasattr(FastMCP, "_docket_patched")
        finally:
            # Restore
            if had_patched:
                FastMCP._docket_patched = True  # type: ignore[attr-defined]


class TestEnrichToolDescriptions:
    """Tests for _enrich_tool_descriptions helper."""

    def test_enriches_tool_with_docs(self) -> None:
        """Enriches tool description when TOOL_DOCS has an entry."""
        from fastmcp import FastMCP

        from codeplane.mcp._compat import get_tools_sync
        from codeplane.mcp.server import _enrich_tool_descriptions

        mcp = FastMCP("test")

        @mcp.tool()
        def search(query: str) -> str:
            """Original description."""
            return query

        _enrich_tool_descriptions(mcp)
        tools = get_tools_sync(mcp)
        # 'search' has TOOL_DOCS entry, description should be enriched
        desc = tools["search"].description or ""
        assert len(desc) > len("Original description.")

    def test_no_change_for_unknown_tool(self) -> None:
        """Doesn't crash or modify tools without TOOL_DOCS entry."""
        from fastmcp import FastMCP

        from codeplane.mcp._compat import get_tools_sync
        from codeplane.mcp.server import _enrich_tool_descriptions

        mcp = FastMCP("test")

        @mcp.tool()
        def my_custom_tool_xyz() -> str:
            """My description."""
            return "result"

        _enrich_tool_descriptions(mcp)
        tools = get_tools_sync(mcp)
        assert tools["my_custom_tool_xyz"].description == "My description."
