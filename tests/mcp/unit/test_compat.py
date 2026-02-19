"""Tests for mcp/_compat.py — FastMCP v3 compatibility helpers."""

from fastmcp import FastMCP

from codeplane.mcp._compat import get_tools_sync


class TestGetToolsSync:
    """Tests for get_tools_sync helper."""

    def test_returns_empty_dict_when_no_tools(self) -> None:
        """Returns empty dict for a server with no registered tools."""
        mcp = FastMCP("test-empty")
        result = get_tools_sync(mcp)
        assert result == {}
        assert isinstance(result, dict)

    def test_returns_registered_tools(self) -> None:
        """Returns registered tools keyed by name."""
        mcp = FastMCP("test")

        @mcp.tool()
        def hello(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        tools = get_tools_sync(mcp)
        assert "hello" in tools
        assert tools["hello"].name == "hello"
        assert tools["hello"].description == "Say hello."

    def test_returns_multiple_tools(self) -> None:
        """Returns all registered tools when multiple exist."""
        mcp = FastMCP("test")

        @mcp.tool()
        def tool_a() -> str:
            """Tool A."""
            return "a"

        @mcp.tool()
        def tool_b() -> str:
            """Tool B."""
            return "b"

        tools = get_tools_sync(mcp)
        assert len(tools) == 2
        assert "tool_a" in tools
        assert "tool_b" in tools

    def test_excludes_non_tool_components(self) -> None:
        """Filters out resources and prompts — only returns tools."""
        mcp = FastMCP("test")

        @mcp.tool()
        def my_tool() -> str:
            """A tool."""
            return "result"

        @mcp.resource("resource://test")
        def my_resource() -> str:
            """A resource."""
            return "data"

        tools = get_tools_sync(mcp)
        assert len(tools) == 1
        assert "my_tool" in tools
        # Resource should NOT appear
        assert "my_resource" not in tools

    def test_tool_has_parameters(self) -> None:
        """Returned tools have parameters schema."""
        mcp = FastMCP("test")

        @mcp.tool()
        def greet(name: str, count: int = 1) -> str:
            """Greet someone."""
            return f"Hello {name}" * count

        tools = get_tools_sync(mcp)
        tool = tools["greet"]
        assert tool.parameters is not None
        props = tool.parameters.get("properties", {})
        assert "name" in props
        assert "count" in props

    def test_tool_fn_is_accessible(self) -> None:
        """Tool's underlying function is accessible via .fn."""
        mcp = FastMCP("test")

        @mcp.tool()
        def echo(msg: str) -> str:
            """Echo."""
            return msg

        tools = get_tools_sync(mcp)
        assert hasattr(tools["echo"], "fn")
