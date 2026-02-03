"""Tests for MCP tool registry."""

from pydantic import Field

from codeplane.mcp.registry import ToolRegistry, ToolSpec, registry
from codeplane.mcp.tools.base import BaseParams


class TestToolSpec:
    """Tests for ToolSpec dataclass."""

    def test_create_minimal(self):
        """ToolSpec with required fields only."""
        spec = ToolSpec(
            name="test_tool",
            description="A test tool",
            params_model=BaseParams,
            handler=lambda _ctx, _params: None,
        )
        assert spec.name == "test_tool"
        assert spec.description == "A test tool"
        assert spec.params_model is BaseParams

    def test_schema_extraction(self):
        """ToolSpec extracts JSON schema from params class."""

        class MyParams(BaseParams):
            name: str = Field(description="The name")
            count: int = Field(default=10, ge=0)

        spec = ToolSpec(
            name="my_tool",
            description="My tool",
            params_model=MyParams,
            handler=lambda _ctx, _params: None,
        )
        schema = spec.params_model.model_json_schema()
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "count" in schema["properties"]
        assert schema["properties"]["name"]["description"] == "The name"

    def test_required_fields_in_schema(self):
        """Schema correctly identifies required fields."""

        class RequiredParams(BaseParams):
            required_field: str
            optional_field: str = "default"

        spec = ToolSpec(
            name="req_tool",
            description="Tool with required",
            params_model=RequiredParams,
            handler=lambda _ctx, _params: None,
        )
        schema = spec.params_model.model_json_schema()
        assert "required_field" in schema.get("required", [])
        assert "optional_field" not in schema.get("required", [])


class TestToolRegistry:
    """Tests for ToolRegistry singleton."""

    def test_singleton_behavior(self):
        """Registry instances are the same object."""
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        assert r1 is r2

    def test_register_decorator(self, clean_registry: ToolRegistry):
        """Register decorator adds tool to registry."""

        class TestParams(BaseParams):
            value: str

        @clean_registry.register("test_tool", "A test tool", TestParams)
        async def test_handler(_ctx, params):
            return {"value": params.value}

        assert "test_tool" in clean_registry._tools
        spec = clean_registry.get("test_tool")
        assert spec is not None
        assert spec.name == "test_tool"
        assert spec.description == "A test tool"

    def test_get_nonexistent_returns_none(self, clean_registry: ToolRegistry):
        """Getting nonexistent tool returns None."""
        assert clean_registry.get("nonexistent") is None

    def test_get_all_empty(self, clean_registry: ToolRegistry):
        """Empty registry returns empty list."""
        assert clean_registry.get_all() == []

    def test_get_all_returns_all(self, clean_registry: ToolRegistry):
        """get_all returns all registered tools."""

        class P1(BaseParams):
            pass

        class P2(BaseParams):
            pass

        @clean_registry.register("tool1", "Tool 1", P1)
        async def handler1(ctx, params):
            pass

        @clean_registry.register("tool2", "Tool 2", P2)
        async def handler2(ctx, params):
            pass

        tools = clean_registry.get_all()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"tool1", "tool2"}

    def test_clear_removes_all(self, clean_registry: ToolRegistry):
        """clear() removes all registered tools."""

        class P(BaseParams):
            pass

        @clean_registry.register("tool", "Tool", P)
        async def handler(ctx, params):
            pass

        assert len(clean_registry.get_all()) == 1
        clean_registry.clear()
        assert len(clean_registry.get_all()) == 0

    def test_register_same_name_overwrites(self, clean_registry: ToolRegistry):
        """Registering same name overwrites previous."""

        class P(BaseParams):
            pass

        @clean_registry.register("tool", "First description", P)
        async def handler1(ctx, params):
            pass

        @clean_registry.register("tool", "Second description", P)
        async def handler2(ctx, params):
            pass

        spec = clean_registry.get("tool")
        assert spec is not None
        assert spec.description == "Second description"
        assert len(clean_registry.get_all()) == 1


class TestGlobalRegistry:
    """Tests for global registry instance."""

    def test_global_registry_is_tool_registry(self):
        """Global registry is a ToolRegistry instance."""
        assert isinstance(registry, ToolRegistry)

    def test_global_registry_has_tools(self):
        """Global registry has tools registered from imports."""
        # After importing tools module, registry should have tools
        from codeplane.mcp import tools  # noqa: F401

        all_tools = registry.get_all()
        # Should have at least some tools registered
        assert len(all_tools) > 0

    def test_search_tool_registered(self):
        """Search tool is registered in global registry."""
        from codeplane.mcp import tools  # noqa: F401

        spec = registry.get("search")
        assert spec is not None
        assert "search" in spec.name.lower() or "code" in spec.description.lower()

    def test_read_files_tool_registered(self):
        """read_files tool is registered."""
        from codeplane.mcp import tools  # noqa: F401

        spec = registry.get("read_files")
        assert spec is not None

    def test_git_status_tool_registered(self):
        """git_status tool is registered."""
        from codeplane.mcp import tools  # noqa: F401

        spec = registry.get("git_status")
        assert spec is not None
