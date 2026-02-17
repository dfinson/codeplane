"""Tests for mcp/server.py module.

Tests the actual exports:
- create_mcp_server() function

Handler tests use conftest.py fixtures for integration testing.
"""

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
        tool_count = len(mcp._tool_manager._tools)
        assert tool_count > 0

    def test_has_expected_tools(self, mock_context: MagicMock) -> None:
        """Has core tools registered."""
        mcp = create_mcp_server(mock_context)
        tool_names = set(mcp._tool_manager._tools.keys())
        assert "read_source" in tool_names
        assert "search" in tool_names
        assert "git_status" in tool_names
