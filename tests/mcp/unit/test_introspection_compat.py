"""Tests for describe() tool in introspection.py.

Targets the get_tools_sync()-based code paths:
  - action='tool' with name NOT in TOOL_DOCS (fallback to get_tools_sync)
  - action='tool' with name NOT registered (tool-not-found branch)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from codeplane.mcp._compat import get_tools_sync
from codeplane.mcp.tools import introspection


@pytest.fixture
def mcp_with_dummy_tool() -> FastMCP:
    """Create a FastMCP server with a tool that has no TOOL_DOCS entry."""
    mcp = FastMCP("test")

    @mcp.tool()
    def my_undocumented_tool(x: int) -> str:
        """A tool not in TOOL_DOCS."""
        return str(x)

    return mcp


@pytest.fixture
def app_ctx() -> MagicMock:
    """Minimal AppContext mock for describe()."""
    ctx = MagicMock()
    ctx.session_manager.get_or_create.return_value = MagicMock()
    ctx.coordinator.get_current_epoch.return_value = 1
    ctx.coordinator._initialized = True
    return ctx


@pytest.fixture
def fastmcp_ctx() -> MagicMock:
    """Minimal FastMCP Context mock."""
    ctx = MagicMock(spec=["session_id"])
    ctx.session_id = "test-session"
    return ctx


class TestDescribeToolAction:
    """Tests for describe(action='tool') with get_tools_sync fallback."""

    @pytest.mark.asyncio
    async def test_tool_not_in_docs_but_registered(
        self, mcp_with_dummy_tool: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """When tool has no TOOL_DOCS entry but IS registered, returns basic info."""
        introspection.register_tools(mcp_with_dummy_tool, app_ctx)
        tools = get_tools_sync(mcp_with_dummy_tool)
        describe_fn = tools["describe"].fn

        result: dict[str, Any] = await describe_fn(
            ctx=fastmcp_ctx,
            action="tool",
            name="my_undocumented_tool",
        )

        assert result["found"] is True
        assert result["name"] == "my_undocumented_tool"
        assert result["extended_docs"] is False
        assert "description" in result

    @pytest.mark.asyncio
    async def test_tool_not_found(
        self, mcp_with_dummy_tool: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """When tool is not in TOOL_DOCS and not registered, returns error."""
        introspection.register_tools(mcp_with_dummy_tool, app_ctx)
        tools = get_tools_sync(mcp_with_dummy_tool)
        describe_fn = tools["describe"].fn

        result: dict[str, Any] = await describe_fn(
            ctx=fastmcp_ctx,
            action="tool",
            name="nonexistent_tool",
        )

        assert result["found"] is False
        assert "available_tools" in result
        assert "my_undocumented_tool" in result["available_tools"]
