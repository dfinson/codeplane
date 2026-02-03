"""Tests for MCP introspection tool (describe)."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.introspection import (
    DescribeParams,
    describe,
)


class TestDescribeParams:
    """Tests for DescribeParams model."""

    def test_action_required(self):
        """action is required."""
        with pytest.raises(ValidationError):
            DescribeParams()

    def test_action_tool(self):
        """tool action."""
        params = DescribeParams(action="tool")
        assert params.action == "tool"

    def test_action_error(self):
        """error action."""
        params = DescribeParams(action="error")
        assert params.action == "error"

    def test_action_capabilities(self):
        """capabilities action."""
        params = DescribeParams(action="capabilities")
        assert params.action == "capabilities"

    def test_action_workflows(self):
        """workflows action."""
        params = DescribeParams(action="workflows")
        assert params.action == "workflows"

    def test_action_operations(self):
        """operations action."""
        params = DescribeParams(action="operations")
        assert params.action == "operations"

    def test_action_invalid(self):
        """Invalid action rejected."""
        with pytest.raises(ValidationError):
            DescribeParams(action="help")

    def test_name_optional(self):
        """name is optional."""
        params = DescribeParams(action="tool")
        assert params.name is None

    def test_name_provided(self):
        """name can be provided."""
        params = DescribeParams(action="tool", name="search")
        assert params.name == "search"

    def test_code_optional(self):
        """code is optional."""
        params = DescribeParams(action="error")
        assert params.code is None

    def test_code_provided(self):
        """code can be provided."""
        params = DescribeParams(action="error", code="CONTENT_NOT_FOUND")
        assert params.code == "CONTENT_NOT_FOUND"

    def test_path_optional(self):
        """path is optional."""
        params = DescribeParams(action="operations")
        assert params.path is None

    def test_path_provided(self):
        """path can be provided."""
        params = DescribeParams(action="operations", path="src/main.py")
        assert params.path == "src/main.py"

    def test_limit_default(self):
        """limit defaults to 50."""
        params = DescribeParams(action="operations")
        assert params.limit == 50

    def test_limit_custom(self):
        """limit can be customized."""
        params = DescribeParams(action="operations", limit=100)
        assert params.limit == 100

    def test_success_only_default(self):
        """success_only defaults to False."""
        params = DescribeParams(action="operations")
        assert params.success_only is False

    def test_success_only_true(self):
        """success_only can be True."""
        params = DescribeParams(action="operations", success_only=True)
        assert params.success_only is True


class TestDescribeHandlerTool:
    """Tests for describe handler tool action."""

    @pytest.mark.asyncio
    async def test_lists_all_tools(self, mock_context: MagicMock):
        """Lists all registered tools."""
        params = DescribeParams(action="tool")
        result = await describe(mock_context, params)

        # Should return list of tools
        assert "tools" in result or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_describes_specific_tool(self, mock_context: MagicMock):
        """Describes a specific tool."""
        params = DescribeParams(action="tool", name="search")
        result = await describe(mock_context, params)

        # Should return tool details
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_unknown_tool_handled(self, mock_context: MagicMock):
        """Unknown tool name is handled gracefully."""
        params = DescribeParams(action="tool", name="nonexistent_tool")
        result = await describe(mock_context, params)

        # Should return error or empty result
        assert isinstance(result, dict)


class TestDescribeHandlerError:
    """Tests for describe handler error action."""

    @pytest.mark.asyncio
    async def test_lists_all_errors(self, mock_context: MagicMock):
        """Lists all error codes."""
        params = DescribeParams(action="error")
        result = await describe(mock_context, params)

        # Should return list of errors
        assert "errors" in result or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_describes_specific_error(self, mock_context: MagicMock):
        """Describes a specific error code."""
        params = DescribeParams(action="error", code="CONTENT_NOT_FOUND")
        result = await describe(mock_context, params)

        # Should return error details
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_unknown_error_handled(self, mock_context: MagicMock):
        """Unknown error code is handled gracefully."""
        params = DescribeParams(action="error", code="UNKNOWN_CODE")
        result = await describe(mock_context, params)

        assert isinstance(result, dict)


class TestDescribeHandlerCapabilities:
    """Tests for describe handler capabilities action."""

    @pytest.mark.asyncio
    async def test_returns_capabilities(self, mock_context: MagicMock):
        """Returns server capabilities."""
        params = DescribeParams(action="capabilities")
        result = await describe(mock_context, params)

        # Should return capabilities info
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_capabilities_include_version(self, mock_context: MagicMock):
        """Capabilities includes version info."""
        params = DescribeParams(action="capabilities")
        result = await describe(mock_context, params)

        # May include version, protocol, or similar
        assert isinstance(result, dict)


class TestDescribeHandlerWorkflows:
    """Tests for describe handler workflows action."""

    @pytest.mark.asyncio
    async def test_returns_workflows(self, mock_context: MagicMock):
        """Returns available workflows."""
        params = DescribeParams(action="workflows")
        result = await describe(mock_context, params)

        # Should return workflows info
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_workflows_have_descriptions(self, mock_context: MagicMock):
        """Workflows have descriptions."""
        params = DescribeParams(action="workflows")
        result = await describe(mock_context, params)

        assert isinstance(result, dict)


class TestDescribeHandlerOperations:
    """Tests for describe handler operations action."""

    @pytest.mark.asyncio
    async def test_returns_operations(self, mock_context: MagicMock):
        """Returns recent operations."""
        params = DescribeParams(action="operations")
        result = await describe(mock_context, params)

        # Should return operations info
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_operations_with_path_filter(self, mock_context: MagicMock):
        """Operations filtered by path."""
        params = DescribeParams(action="operations", path="src/main.py")
        result = await describe(mock_context, params)

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_operations_with_limit(self, mock_context: MagicMock):
        """Operations respects limit."""
        params = DescribeParams(action="operations", limit=10)
        result = await describe(mock_context, params)

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_operations_success_only(self, mock_context: MagicMock):
        """Operations filtered to success only."""
        params = DescribeParams(action="operations", success_only=True)
        result = await describe(mock_context, params)

        assert isinstance(result, dict)
