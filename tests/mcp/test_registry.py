"""Tests for MCP tool registry."""

import pytest
from pydantic import Field

from codeplane.mcp.registry import ToolRegistry, ToolSpec, registry
from codeplane.mcp.tools.base import BaseParams


class TestToolSpec:
    """Tests for ToolSpec dataclass."""

    def test_create_minimal(self) -> None:
        """ToolSpec with required fields only."""
        spec = ToolSpec(
            name="test_tool",
            description="A test tool",
            params_model=BaseParams,
            handler=lambda _ctx, _params: None,  # type: ignore[arg-type,return-value]
        )
        assert spec.name == "test_tool"
        assert spec.description == "A test tool"
        assert spec.params_model is BaseParams

    def test_schema_extraction(self) -> None:
        """ToolSpec extracts JSON schema from params class."""

        class MyParams(BaseParams):
            name: str = Field(description="The name")
            count: int = Field(default=10, ge=0)

        spec = ToolSpec(
            name="my_tool",
            description="My tool",
            params_model=MyParams,
            handler=lambda _ctx, _params: None,  # type: ignore[arg-type,return-value]
        )
        schema = spec.params_model.model_json_schema()
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "count" in schema["properties"]
        assert schema["properties"]["name"]["description"] == "The name"

    def test_required_fields_in_schema(self) -> None:
        """Schema correctly identifies required fields."""

        class RequiredParams(BaseParams):
            required_field: str
            optional_field: str = "default"

        spec = ToolSpec(
            name="req_tool",
            description="Tool with required",
            params_model=RequiredParams,
            handler=lambda _ctx, _params: None,  # type: ignore[arg-type,return-value]
        )
        schema = spec.params_model.model_json_schema()
        assert "required_field" in schema.get("required", [])
        assert "optional_field" not in schema.get("required", [])


class TestToolRegistry:
    """Tests for ToolRegistry singleton."""

    def test_singleton_behavior(self) -> None:
        """Registry instances are the same object."""
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        assert r1 is r2

    def test_register_decorator(self, clean_registry: ToolRegistry) -> None:
        """Register decorator adds tool to registry."""

        class TestParams(BaseParams):
            value: str

        @clean_registry.register("test_tool", "A test tool", TestParams)
        async def test_handler(_ctx: object, params: TestParams) -> dict[str, str]:
            return {"value": params.value}

        assert "test_tool" in clean_registry._tools
        spec = clean_registry.get("test_tool")
        assert spec is not None
        assert spec.name == "test_tool"
        assert spec.description == "A test tool"

    def test_get_nonexistent_returns_none(self, clean_registry: ToolRegistry) -> None:
        """Getting nonexistent tool returns None."""
        assert clean_registry.get("nonexistent") is None

    def test_get_all_empty(self, clean_registry: ToolRegistry) -> None:
        """Empty registry returns empty list."""
        assert clean_registry.get_all() == []

    def test_get_all_returns_all(self, clean_registry: ToolRegistry) -> None:
        """get_all returns all registered tools."""

        class P1(BaseParams):
            pass

        class P2(BaseParams):
            pass

        @clean_registry.register("tool1", "Tool 1", P1)  # type: ignore[arg-type]
        async def handler1(ctx: object, params: P1) -> None:
            pass

        @clean_registry.register("tool2", "Tool 2", P2)  # type: ignore[arg-type]
        async def handler2(ctx: object, params: P2) -> None:
            pass

        tools = clean_registry.get_all()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"tool1", "tool2"}

    def test_clear_removes_all(self, clean_registry: ToolRegistry) -> None:
        """clear() removes all registered tools."""

        class P(BaseParams):
            pass

        @clean_registry.register("tool", "Tool", P)  # type: ignore[arg-type]
        async def handler(ctx: object, params: P) -> None:
            pass

        assert len(clean_registry.get_all()) == 1
        clean_registry.clear()
        assert len(clean_registry.get_all()) == 0

    def test_register_same_name_overwrites(self, clean_registry: ToolRegistry) -> None:
        """Registering same name overwrites previous."""

        class P(BaseParams):
            pass

        @clean_registry.register("tool", "First description", P)  # type: ignore[arg-type]
        async def handler1(ctx: object, params: P) -> None:
            pass

        @clean_registry.register("tool", "Second description", P)  # type: ignore[arg-type]
        async def handler2(ctx: object, params: P) -> None:
            pass

        spec = clean_registry.get("tool")
        assert spec is not None
        assert spec.description == "Second description"
        assert len(clean_registry.get_all()) == 1


class TestGlobalRegistry:
    """Tests for global registry instance.

    Note: With the FastMCP refactor, tools are no longer auto-registered
    in the global registry at import time. They are registered when
    register_tools(mcp, app_ctx) is called for each tool module.
    """

    def test_global_registry_is_tool_registry(self) -> None:
        """Global registry is a ToolRegistry instance."""
        assert isinstance(registry, ToolRegistry)

    @pytest.mark.skip(reason="Tools now registered via FastMCP, not at import time")
    def test_global_registry_has_tools(self) -> None:
        """Global registry has tools registered from imports."""
        # After importing tools module, registry should have tools
        from codeplane.mcp import tools  # noqa: F401

        all_tools = registry.get_all()
        # Should have at least some tools registered
        assert len(all_tools) > 0

    @pytest.mark.skip(reason="Tools now registered via FastMCP, not at import time")
    def test_search_tool_registered(self) -> None:
        """Search tool is registered in global registry."""
        from codeplane.mcp import tools  # noqa: F401

        spec = registry.get("search")
        assert spec is not None
        assert "search" in spec.name.lower() or "code" in spec.description.lower()

    @pytest.mark.skip(reason="Tools now registered via FastMCP, not at import time")
    def test_read_source_tool_registered(self) -> None:
        """read_source tool is registered."""
        from codeplane.mcp import tools  # noqa: F401

        spec = registry.get("read_source")
        assert spec is not None

    @pytest.mark.skip(reason="Tools now registered via FastMCP, not at import time")
    def test_git_status_tool_registered(self) -> None:
        """git_status tool is registered."""
        from codeplane.mcp import tools  # noqa: F401

        spec = registry.get("git_status")
        assert spec is not None
