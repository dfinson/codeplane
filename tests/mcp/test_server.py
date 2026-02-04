"""Tests for mcp/server.py module.

Covers:
- ToolResponse model
- create_mcp_server() function
- _wire_tool() function
- Server creation and tool registration
- _extract_log_params() function (NEW - Issue #7)
- _extract_result_summary() function (NEW - Issue #7)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from codeplane.mcp.server import (
    ToolResponse,
    _extract_log_params,
    _extract_result_summary,
    create_mcp_server,
)


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


class TestExtractLogParams:
    """Tests for _extract_log_params function (Issue #7)."""

    def test_skips_session_id(self) -> None:
        """Skips session_id from params."""
        kwargs = {"query": "search", "session_id": "sess123"}
        result = _extract_log_params("search", kwargs)
        assert "session_id" not in result
        assert result["query"] == "search"

    def test_truncates_long_strings(self) -> None:
        """Truncates strings longer than 50 characters."""
        long_string = "x" * 100
        kwargs = {"content": long_string}
        result = _extract_log_params("write", kwargs)
        assert len(result["content"]) == 53  # 50 + "..."
        assert result["content"].endswith("...")

    def test_keeps_short_strings(self) -> None:
        """Keeps strings 50 characters or shorter."""
        kwargs = {"query": "short query"}
        result = _extract_log_params("search", kwargs)
        assert result["query"] == "short query"

    def test_summarizes_long_lists(self) -> None:
        """Summarizes lists longer than 3 items."""
        kwargs = {"paths": ["a.py", "b.py", "c.py", "d.py", "e.py"]}
        result = _extract_log_params("read", kwargs)
        assert result["paths"] == "[5 items]"

    def test_keeps_short_lists(self) -> None:
        """Keeps lists with 3 or fewer items."""
        kwargs = {"paths": ["a.py", "b.py"]}
        result = _extract_log_params("read", kwargs)
        assert result["paths"] == ["a.py", "b.py"]

    def test_skips_none_values(self) -> None:
        """Skips parameters with None values."""
        kwargs = {"query": "test", "filter": None}
        result = _extract_log_params("search", kwargs)
        assert "filter" not in result
        assert result["query"] == "test"

    def test_preserves_numbers(self) -> None:
        """Preserves numeric values."""
        kwargs = {"limit": 100, "offset": 50}
        result = _extract_log_params("list", kwargs)
        assert result["limit"] == 100
        assert result["offset"] == 50

    def test_preserves_booleans(self) -> None:
        """Preserves boolean values."""
        kwargs = {"recursive": True, "dry_run": False}
        result = _extract_log_params("list", kwargs)
        assert result["recursive"] is True
        assert result["dry_run"] is False

    def test_empty_kwargs(self) -> None:
        """Returns empty dict for empty kwargs."""
        result = _extract_log_params("tool", {})
        assert result == {}


class TestExtractResultSummary:
    """Tests for _extract_result_summary function (Issue #7)."""

    def test_extracts_total(self) -> None:
        """Extracts 'total' from result."""
        result = {"total": 42, "other": "data"}
        summary = _extract_result_summary("list", result)
        assert summary["total"] == 42

    def test_extracts_count(self) -> None:
        """Extracts 'count' from result."""
        result = {"count": 10}
        summary = _extract_result_summary("list", result)
        assert summary["count"] == 10

    def test_counts_results_list(self) -> None:
        """Counts items in 'results' list."""
        result = {"results": [{"a": 1}, {"b": 2}, {"c": 3}]}
        summary = _extract_result_summary("search", result)
        assert summary["results"] == 3

    def test_counts_files_list(self) -> None:
        """Counts items in 'files' list."""
        result = {"files": ["a.py", "b.py"]}
        summary = _extract_result_summary("list", result)
        assert summary["files"] == 2

    def test_counts_entries_list(self) -> None:
        """Counts items in 'entries' list."""
        result = {"entries": [{}, {}, {}, {}]}
        summary = _extract_result_summary("list", result)
        assert summary["entries"] == 4

    def test_extracts_query_time(self) -> None:
        """Extracts query_time_ms."""
        result = {"query_time_ms": 15.5}
        summary = _extract_result_summary("search", result)
        assert summary["query_time_ms"] == 15.5

    def test_extracts_passed_failed(self) -> None:
        """Extracts passed/failed counts."""
        result = {"passed": 10, "failed": 2}
        summary = _extract_result_summary("test", result)
        assert summary["passed"] == 10
        assert summary["failed"] == 2

    def test_search_tool_adds_matches(self) -> None:
        """Search tool adds 'matches' count."""
        result = {"results": [{}, {}, {}]}
        summary = _extract_result_summary("search", result)
        assert summary["matches"] == 3

    def test_write_files_extracts_delta(self) -> None:
        """write_files extracts files_changed from delta."""
        result = {"delta": {"files_changed": 5, "insertions": 100}}
        summary = _extract_result_summary("write_files", result)
        assert summary["files_changed"] == 5

    def test_test_targets_extracts_summary(self) -> None:
        """Test tools extract passed/failed from summary."""
        result = {"summary": {"passed": 8, "failed": 1}}
        summary = _extract_result_summary("run_test_targets", result)
        assert summary["passed"] == 8
        assert summary["failed"] == 1

    def test_get_test_run_status_extracts_summary(self) -> None:
        """get_test_run_status extracts from summary."""
        result = {"summary": {"passed": 5, "failed": 0}}
        summary = _extract_result_summary("get_test_run_status", result)
        assert summary["passed"] == 5
        assert summary["failed"] == 0

    def test_empty_result(self) -> None:
        """Returns empty dict for empty result."""
        summary = _extract_result_summary("tool", {})
        assert summary == {}

    def test_non_list_results_ignored(self) -> None:
        """Non-list 'results' is not counted."""
        result = {"results": "not a list"}
        summary = _extract_result_summary("search", result)
        assert "results" not in summary

    def test_non_list_files_ignored(self) -> None:
        """Non-list 'files' is not counted."""
        result = {"files": {"a.py": "content"}}
        summary = _extract_result_summary("list", result)
        assert "files" not in summary
