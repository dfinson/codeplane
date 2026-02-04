"""Tests for mcp/server.py module.

Covers:
- ToolResponse model
- create_mcp_server() function
- _wire_tool() function
- Server creation and tool registration
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from codeplane.mcp.server import ToolResponse, create_mcp_server


class TestToolResponse:
    """Tests for ToolResponse model."""

    def test_create_success_response(self) -> None:
        """Create a successful response."""
        response = ToolResponse(success=True, result={"data": "value"})
        assert response.success is True
        assert response.result == {"data": "value"}
        assert response.error is None
        assert response.meta == {}

    def test_create_error_response(self) -> None:
        """Create an error response."""
        response = ToolResponse(success=False, error="Something failed")
        assert response.success is False
        assert response.result is None
        assert response.error == "Something failed"

    def test_response_with_meta(self) -> None:
        """Create response with metadata."""
        response = ToolResponse(
            success=True,
            result={"count": 5},
            meta={"session_id": "abc123", "timestamp": 1234567890},
        )
        assert response.meta["session_id"] == "abc123"
        assert response.meta["timestamp"] == 1234567890

    def test_model_dump(self) -> None:
        """Can serialize to dict."""
        response = ToolResponse(success=True, result={"key": "val"})
        data = response.model_dump()
        assert data["success"] is True
        assert data["result"] == {"key": "val"}
        assert data["error"] is None

    def test_result_can_be_any_type(self) -> None:
        """Result field accepts any type."""
        # List
        r1 = ToolResponse(success=True, result=[1, 2, 3])
        assert r1.result == [1, 2, 3]

        # String
        r2 = ToolResponse(success=True, result="text")
        assert r2.result == "text"

        # None explicitly
        r3 = ToolResponse(success=True, result=None)
        assert r3.result is None


class TestCreateMcpServer:
    """Tests for create_mcp_server function."""

    @pytest.fixture
    def mock_context(self, tmp_path: Path) -> MagicMock:
        """Create a mock AppContext."""
        context = MagicMock()
        context.repo_root = tmp_path
        context.session_manager = MagicMock()

        # Mock session
        mock_session = MagicMock()
        mock_session.session_id = "test-session"
        context.session_manager.get_or_create.return_value = mock_session

        return context

    def test_creates_fastmcp_server(self, mock_context: MagicMock) -> None:
        """Creates a FastMCP server instance."""
        with patch("codeplane.mcp.registry.registry") as mock_registry:
            mock_registry.get_all.return_value = []
            mcp = create_mcp_server(mock_context)
            assert mcp is not None
            # FastMCP has a name attribute
            assert mcp.name == "codeplane"

    def test_registers_tools_from_registry(self, mock_context: MagicMock) -> None:
        """Registers all tools from the registry."""
        with patch("codeplane.mcp.registry.registry") as mock_registry:
            # Create mock tool specs
            mock_spec = MagicMock()
            mock_spec.name = "test_tool"
            mock_spec.description = "Test tool description"
            mock_spec.params_model = type(
                "TestParams",
                (BaseModel,),
                {
                    "__annotations__": {
                        "param1": str,
                        "session_id": str | None,
                    },
                    "session_id": None,
                },
            )
            mock_spec.handler = MagicMock(return_value={})

            mock_registry.get_all.return_value = [mock_spec]

            create_mcp_server(mock_context)

            # Verify registry was queried
            mock_registry.get_all.assert_called_once()

    def test_imports_tool_modules(self, mock_context: MagicMock) -> None:
        """Imports tool modules to trigger registration."""
        with (
            patch("codeplane.mcp.registry.registry") as mock_registry,
            patch.dict("sys.modules"),
        ):
            mock_registry.get_all.return_value = []

            # This should not raise - modules should import successfully
            mcp = create_mcp_server(mock_context)
            assert mcp is not None


class TestToolResponseIntegration:
    """Integration tests for tool response handling."""

    def test_response_json_serializable(self) -> None:
        """ToolResponse can be JSON serialized."""
        import json

        response = ToolResponse(
            success=True,
            result={"files": ["a.py", "b.py"]},
            meta={"session_id": "sess123"},
        )

        # Should not raise
        json_str = json.dumps(response.model_dump())
        parsed = json.loads(json_str)

        assert parsed["success"] is True
        assert parsed["result"]["files"] == ["a.py", "b.py"]

    def test_error_response_structure(self) -> None:
        """Error responses have expected structure."""
        response = ToolResponse(
            success=False,
            result=None,
            error="File not found: /path/to/file",
            meta={
                "error": {
                    "code": "not_found",
                    "path": "/path/to/file",
                },
            },
        )

        data = response.model_dump()
        assert data["success"] is False
        assert "not_found" in str(data["meta"])
