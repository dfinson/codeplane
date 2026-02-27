"""Comprehensive tests for MCP middleware.

Covers:
- Tool call handling with structured errors
- Validation error handling with schema help
- MCPError handling (expected errors)
- Internal error handling (unexpected exceptions)
- Cancellation handling
- Result summary extraction
- Logging parameter extraction
- Tool schema extraction
- Console output formatting
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.middleware import ToolMiddleware, _timestamp


class TestTimestamp:
    """Tests for _timestamp helper."""

    def test_returns_hms_format(self):
        ts = _timestamp()
        # Should be HH:MM:SS format
        assert len(ts) == 8
        assert ts[2] == ":"
        assert ts[5] == ":"
        # Should be valid time components
        hour, minute, second = ts.split(":")
        assert 0 <= int(hour) <= 23
        assert 0 <= int(minute) <= 59
        assert 0 <= int(second) <= 59


class TestToolMiddleware:
    """Tests for ToolMiddleware."""

    @pytest.fixture
    def middleware(self):
        return ToolMiddleware()

    @pytest.fixture
    def mock_context(self):
        """Create a mock MiddlewareContext."""
        ctx = MagicMock(spec=MiddlewareContext)
        ctx.message = MagicMock()
        ctx.message.name = "test_tool"
        ctx.message.arguments = {"arg1": "value1"}
        ctx.fastmcp_context = MagicMock()
        ctx.fastmcp_context.session_id = "sess_12345678abcdef"
        return ctx

    @pytest.fixture
    def mock_console(self):
        """Create a mock console."""
        return MagicMock()

    # =========================================================================
    # Success Path Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_successful_tool_call(self, middleware, mock_context, mock_console):
        """Test successful tool call returns result with logging."""
        expected_result = ToolResult(structured_content={"data": "test", "summary": "test summary"})

        async def call_next(_ctx):  # noqa: ARG001
            return expected_result

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert result == expected_result
        # Console should have printed success message
        mock_console.print.assert_called()

    @pytest.mark.asyncio
    async def test_successful_tool_call_with_dict_result(
        self, middleware, mock_context, mock_console
    ):
        """Test successful tool call with plain dict result."""
        expected_result = {"data": "test", "summary": "test summary"}

        async def call_next(_ctx):  # noqa: ARG001
            return expected_result

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_truncated_session_id(self, middleware, mock_context, mock_console):
        """Test session ID is truncated to 8 chars for display."""
        mock_context.fastmcp_context.session_id = "very_long_session_id_12345"

        async def call_next(_ctx):  # noqa: ARG001
            return {"result": "ok"}

        with (
            patch("codeplane.core.progress.get_console", return_value=mock_console),
            patch("codeplane.mcp.middleware.log") as mock_log,
        ):
            await middleware.on_call_tool(mock_context, call_next)
            # Verify session_id in log calls is truncated
            call_args = mock_log.info.call_args_list[0]
            assert call_args[1]["session_id"] == "very_lon"

    @pytest.mark.asyncio
    async def test_no_fastmcp_context(self, middleware, mock_context, mock_console):
        """Test handling when fastmcp_context is None."""
        mock_context.fastmcp_context = None

        async def call_next(_ctx):  # noqa: ARG001
            return {"result": "ok"}

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert result == {"result": "ok"}

    # =========================================================================
    # Cancellation Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_cancelled_error(self, middleware, mock_context, mock_console):
        """Test handling of asyncio.CancelledError."""

        async def call_next(_ctx):  # noqa: ARG001
            raise asyncio.CancelledError()

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert isinstance(result, ToolResult)
        content = result.structured_content
        assert content is not None
        assert "error" in content
        assert content["error"]["code"] == "CANCELLED"
        assert "server shutting down" in content["error"]["message"]
        assert content["summary"] == "error: cancelled"

    # =========================================================================
    # Validation Error Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_validation_error(self, middleware, mock_context, mock_console):
        """Test handling of Pydantic ValidationError."""
        # Create a validation error
        from pydantic import BaseModel

        class TestModel(BaseModel):
            required_field: str
            number_field: int

        try:
            TestModel(required_field=123, number_field="not_a_number")
        except ValidationError as e:
            validation_error = e

        async def call_next(_ctx):  # noqa: ARG001
            raise validation_error

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert isinstance(result, ToolResult)
        content = result.structured_content
        assert content is not None
        assert content["error"]["code"] == "VALIDATION_ERROR"
        assert "details" in content["error"]
        assert "agentic_hint" in content
        assert "tool_schema" in content["agentic_hint"]

    @pytest.mark.asyncio
    async def test_validation_error_extracts_field_info(
        self, middleware, mock_context, mock_console
    ):
        """Test validation error includes field-level error details."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(min_length=3)

        try:
            TestModel(name="ab")
        except ValidationError as e:
            validation_error = e

        async def call_next(_ctx):  # noqa: ARG001
            raise validation_error

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        content = result.structured_content
        assert content is not None
        details = content["error"]["details"]
        assert len(details) > 0
        assert "field" in details[0]
        assert "message" in details[0]
        assert "type" in details[0]

    # =========================================================================
    # MCPError Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_mcp_error(self, middleware, mock_context, mock_console):
        """Test handling of MCPError (expected errors)."""

        async def call_next(_ctx):  # noqa: ARG001
            raise MCPError(
                code=MCPErrorCode.FILE_NOT_FOUND,
                message="File not found: test.py",
                path="test.py",
                remediation="Check the file path and try again.",
            )

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert isinstance(result, ToolResult)
        content = result.structured_content
        assert content is not None
        assert content["error"]["code"] == "FILE_NOT_FOUND"
        assert content["summary"] == "error: FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_mcp_error_with_path(self, middleware, mock_context, mock_console):
        """Test MCPError includes path in error response."""

        async def call_next(_ctx):  # noqa: ARG001
            raise MCPError(
                code=MCPErrorCode.INVALID_RANGE,
                message="Invalid range",
                path="src/file.py",
                remediation="Check the line range is within file bounds.",
            )

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        content = result.structured_content
        assert content is not None
        assert content["error"]["code"] == "INVALID_RANGE"

    # =========================================================================
    # Internal Error Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_internal_error(self, middleware, mock_context, mock_console):
        """Test handling of unexpected exceptions."""

        async def call_next(_ctx):  # noqa: ARG001
            raise RuntimeError("Unexpected failure")

        with patch("codeplane.core.progress.get_console", return_value=mock_console):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert isinstance(result, ToolResult)
        content = result.structured_content
        assert content is not None
        assert content["error"]["code"] == "INTERNAL_ERROR"
        assert "Unexpected failure" in content["error"]["message"]
        assert content["error"]["error_type"] == "RuntimeError"
        assert "agentic_hint" in content

    @pytest.mark.asyncio
    async def test_internal_error_prints_to_console(self, middleware, mock_context, mock_console):
        """Test internal error prints to console with log file pointer."""

        async def call_next(_ctx):  # noqa: ARG001
            raise ValueError("Test error")

        with (
            patch("codeplane.core.progress.get_console", return_value=mock_console),
            patch("codeplane.mcp.middleware.log"),
        ):
            await middleware.on_call_tool(mock_context, call_next)

        # Console should have error printed
        mock_console.print.assert_called()
        call_args = str(mock_console.print.call_args)
        assert "failed" in call_args.lower() or "error" in call_args.lower()

    # =========================================================================
    # Log Params Extraction Tests
    # =========================================================================

    def test_extract_log_params_basic(self, middleware):
        """Test basic parameter extraction."""
        kwargs = {"path": "test.py", "action": "read"}
        params = middleware._extract_log_params("test_tool", kwargs)
        assert params == {"path": "test.py", "action": "read"}

    def test_extract_log_params_truncates_long_strings(self, middleware):
        """Test long string values are truncated."""
        long_content = "x" * 100
        kwargs = {"content": long_content}
        params = middleware._extract_log_params("test_tool", kwargs)
        assert len(params["content"]) == 53  # 50 + "..."
        assert params["content"].endswith("...")

    def test_extract_log_params_truncates_long_lists(self, middleware):
        """Test long lists are summarized."""
        kwargs = {"paths": ["a.py", "b.py", "c.py", "d.py", "e.py"]}
        params = middleware._extract_log_params("test_tool", kwargs)
        assert params["paths"] == "[5 items]"

    def test_extract_log_params_skips_session_id(self, middleware):
        """Test session_id is excluded from params."""
        kwargs = {"path": "test.py", "session_id": "secret"}
        params = middleware._extract_log_params("test_tool", kwargs)
        assert "session_id" not in params
        assert params["path"] == "test.py"

    def test_extract_log_params_skips_none_values(self, middleware):
        """Test None values are excluded."""
        kwargs = {"path": "test.py", "optional": None}
        params = middleware._extract_log_params("test_tool", kwargs)
        assert params == {"path": "test.py"}

    # =========================================================================
    # Result Summary Extraction Tests
    # =========================================================================

    def test_extract_result_summary_from_tool_result(self, middleware):
        """Test summary extraction from ToolResult."""
        result = ToolResult(structured_content={"summary": "5 files processed"})
        summary = middleware._extract_result_summary("test_tool", result)
        assert summary["summary"] == "5 files processed"

    def test_extract_result_summary_from_dict(self, middleware):
        """Test summary extraction from plain dict."""
        result = {"summary": "operation complete", "count": 10}
        summary = middleware._extract_result_summary("test_tool", result)
        assert summary["summary"] == "operation complete"

    def test_extract_result_summary_from_display_to_user(self, middleware):
        """Test summary extraction from display_to_user field."""
        result = {"display_to_user": "Files updated successfully"}
        summary = middleware._extract_result_summary("test_tool", result)
        assert summary["summary"] == "Files updated successfully"

    def test_extract_result_summary_common_fields(self, middleware):
        """Test extraction of common result fields."""
        result = {
            "total": 100,
            "count": 50,
            "results": [1, 2, 3],
            "files": ["a.py", "b.py"],
        }
        summary = middleware._extract_result_summary("test_tool", result)
        assert summary["total"] == 100
        assert summary["count"] == 50
        assert summary["results"] == 3
        assert summary["files"] == 2

    def test_extract_result_summary_recon_tool(self, middleware):
        """Test recon-specific summary extraction."""
        result = {"files": [{"path": "a.py"}, {"path": "b.py"}]}
        summary = middleware._extract_result_summary("recon", result)
        assert summary["files_returned"] == 2

    def test_extract_result_summary_refactor_edit(self, middleware):
        """Test refactor_edit-specific summary extraction."""
        result = {"edits": [{"status": "ok"}, {"status": "ok"}, {"status": "error"}]}
        summary = middleware._extract_result_summary("refactor_edit", result)
        assert summary["edits_applied"] == 2

    def test_extract_result_summary_test_run(self, middleware):
        """Test test run summary extraction."""
        result = {
            "tests": {
                "passed": 10,
                "failed": 2,
            }
        }
        summary = middleware._extract_result_summary("checkpoint", result)
        assert summary["passed"] == 10
        assert summary["failed"] == 2

    # =========================================================================
    # Format Tool Summary Tests
    # =========================================================================

    def test_format_tool_summary_uses_summary_field(self, middleware):
        """Test format uses explicit summary field."""
        result = ToolResult(structured_content={"summary": "custom summary"})
        formatted = middleware._format_tool_summary("test_tool", result)
        assert formatted == "custom summary"

    def test_format_tool_summary_recon(self, middleware):
        """Test recon-specific formatting."""
        result = {"files": [1, 2, 3]}
        formatted = middleware._format_tool_summary("recon", result)
        assert formatted == "3 files returned"

    def test_format_tool_summary_recon_resolve(self, middleware):
        """Test recon_resolve-specific formatting."""
        result = {"files": [{"path": "a.py"}, {"path": "b.py"}]}
        formatted = middleware._format_tool_summary("recon_resolve", result)
        assert formatted == "2 files resolved"

    def test_format_tool_summary_refactor_edit(self, middleware):
        """Test refactor_edit-specific formatting."""
        result = {"edits": [{"status": "ok"}, {"status": "ok"}, {"status": "error"}]}
        formatted = middleware._format_tool_summary("refactor_edit", result)
        assert formatted == "2/3 edits applied"

    def test_format_tool_summary_checkpoint(self, middleware):
        """Test checkpoint-specific formatting."""
        result = {"tests": {"passed": 10, "failed": 2}}
        formatted = middleware._format_tool_summary("checkpoint", result)
        assert formatted == "10 passed, 2 failed"

    def test_format_tool_summary_test_run_completed(self, middleware):
        """Test test run completion formatting."""
        result = {
            "tests": {
                "passed": 10,
                "failed": 2,
            }
        }
        formatted = middleware._format_tool_summary("checkpoint", result)
        assert "10 passed" in formatted
        assert "2 failed" in formatted

    def test_format_tool_summary_unknown_tool(self, middleware):
        """Test unknown tool returns empty string."""
        result = {"data": "something"}
        formatted = middleware._format_tool_summary("unknown_tool", result)
        assert formatted == ""

    # =========================================================================
    # Error Printing Tests
    # =========================================================================

    def test_print_error_with_log_file(self, middleware):
        """Test error printing includes log file path."""
        mock_console = MagicMock()

        with patch(
            "codeplane.core.logging.get_log_file_path", return_value="/var/log/codeplane.log"
        ):
            middleware._print_error_with_log_pointer(
                mock_console, "test_tool", "ValueError", "test error message"
            )

        mock_console.print.assert_called_once()
        call_args = str(mock_console.print.call_args)
        assert "test_tool" in call_args
        assert "ValueError" in call_args
        assert "/var/log/codeplane.log" in call_args

    def test_print_error_without_log_file(self, middleware):
        """Test error printing when no log file configured."""
        mock_console = MagicMock()

        with patch("codeplane.core.logging.get_log_file_path", return_value=None):
            middleware._print_error_with_log_pointer(
                mock_console, "test_tool", "ValueError", "test error"
            )

        mock_console.print.assert_called_once()
        call_args = str(mock_console.print.call_args)
        assert "test_tool" in call_args
        assert "for details" not in call_args

    def test_print_error_truncates_long_message(self, middleware):
        """Test long error messages are truncated."""
        mock_console = MagicMock()
        long_msg = "x" * 100

        with patch("codeplane.core.logging.get_log_file_path", return_value=None):
            middleware._print_error_with_log_pointer(mock_console, "test_tool", "Error", long_msg)

        call_args = str(mock_console.print.call_args)
        assert "..." in call_args

    # =========================================================================
    # Tool Schema Extraction Tests
    # =========================================================================

    def test_get_tool_schema_no_context(self, middleware):
        """Test schema extraction returns None when no context."""
        mock_context = MagicMock()
        mock_context.fastmcp_context = None

        schema = middleware._get_tool_schema(mock_context, "test_tool")
        assert schema is None

    def test_get_tool_schema_handles_exception(self, middleware):
        """Test schema extraction handles exceptions in the except branch."""
        from unittest.mock import patch

        mock_context = MagicMock()
        mock_context.fastmcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context.session = MagicMock()
        server = MagicMock()
        server.local_provider = MagicMock()  # Pass the hasattr guard
        mock_context.fastmcp_context._mcp_context.session.app = server

        # Patch get_tools_sync to raise, exercising the except Exception branch
        with patch(
            "codeplane.mcp._compat.get_tools_sync",
            side_effect=RuntimeError("boom"),
        ):
            schema = middleware._get_tool_schema(mock_context, "test_tool")
        assert schema is None

    def test_get_tool_schema_with_real_fastmcp(self, middleware):
        """Test schema extraction with a real FastMCP server."""
        from fastmcp import FastMCP

        mcp = FastMCP("test")

        @mcp.tool()
        def my_test_tool(name: str, count: int = 1) -> str:
            """Test tool description."""
            return f"{name}:{count}"

        # Build mock context chain: context.fastmcp_context._mcp_context.session.app = mcp
        mock_context = MagicMock()
        mock_context.fastmcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context.session = MagicMock()
        mock_context.fastmcp_context._mcp_context.session.app = mcp

        schema = middleware._get_tool_schema(mock_context, "my_test_tool")
        assert schema is not None
        assert schema["name"] == "my_test_tool"
        assert schema["description"] == "Test tool description."
        assert "parameters" in schema
        param_names = {p["name"] for p in schema["parameters"]}
        assert "name" in param_names
        assert "count" in param_names

    def test_get_tool_schema_tool_not_found(self, middleware):
        """Returns None when tool name doesn't match any registered tool."""
        from fastmcp import FastMCP

        mcp = FastMCP("test")

        @mcp.tool()
        def existing_tool() -> str:
            """Exists."""
            return "ok"

        mock_context = MagicMock()
        mock_context.fastmcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context.session = MagicMock()
        mock_context.fastmcp_context._mcp_context.session.app = mcp

        schema = middleware._get_tool_schema(mock_context, "nonexistent_tool")
        assert schema is None

    def test_get_tool_schema_no_local_provider(self, middleware):
        """Returns None when server has no local_provider."""
        mock_context = MagicMock()
        mock_context.fastmcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context = MagicMock()
        mock_context.fastmcp_context._mcp_context.session = MagicMock()
        mock_server = MagicMock(spec=[])  # spec=[] means NO attributes
        mock_context.fastmcp_context._mcp_context.session.app = mock_server

        schema = middleware._get_tool_schema(mock_context, "test_tool")
        assert schema is None


class TestExtractFromDict:
    """Tests for _extract_from_dict helper."""

    @pytest.fixture
    def middleware(self):
        return ToolMiddleware()

    def test_extracts_summary_field(self, middleware):
        result = {"summary": "Test summary", "other": "data"}
        extracted = middleware._extract_from_dict("tool", result)
        assert extracted == {"summary": "Test summary"}

    def test_extracts_display_to_user(self, middleware):
        result = {"display_to_user": "User message", "other": "data"}
        extracted = middleware._extract_from_dict("tool", result)
        assert extracted == {"summary": "User message"}

    def test_extracts_entries(self, middleware):
        result = {"entries": [1, 2, 3]}
        extracted = middleware._extract_from_dict("tool", result)
        assert extracted == {"entries": 3}

    def test_truncates_long_summary(self, middleware):
        long_summary = "x" * 150
        result = {"summary": long_summary}
        extracted = middleware._extract_from_dict("tool", result)
        assert len(extracted["summary"]) == 100


class TestCallToolResultParsing:
    """Tests for parsing CallToolResult content."""

    @pytest.fixture
    def middleware(self):
        return ToolMiddleware()

    def test_parse_call_tool_result_with_text_content(self, middleware):
        """Test parsing CallToolResult with JSON text content."""
        # Create a mock CallToolResult
        mock_content = MagicMock()
        mock_content.text = json.dumps({"summary": "parsed summary"})
        mock_result = MagicMock()
        mock_result.content = [mock_content]
        mock_result.structured_content = None

        summary = middleware._extract_result_summary("tool", mock_result)
        assert summary.get("summary") == "parsed summary"

    def test_parse_call_tool_result_invalid_json(self, middleware):
        """Test parsing handles invalid JSON gracefully."""
        mock_content = MagicMock()
        mock_content.text = "not valid json"
        mock_result = MagicMock()
        mock_result.content = [mock_content]
        mock_result.structured_content = None

        summary = middleware._extract_result_summary("tool", mock_result)
        assert summary == {}  # Empty summary on parse error

    def test_format_tool_summary_parses_call_result(self, middleware):
        """Test format_tool_summary works with CallToolResult."""
        mock_content = MagicMock()
        mock_content.text = json.dumps({"summary": "formatted result"})
        mock_result = MagicMock()
        mock_result.content = [mock_content]

        formatted = middleware._format_tool_summary("tool", mock_result)
        assert formatted == "formatted result"
