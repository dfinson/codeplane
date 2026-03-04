"""Tests for mcp/docs.py module.

Covers:
- ToolCategory enum
- BehaviorFlags dataclass
- ToolDocumentation dataclass
- TOOL_DOCS registry
- get_tool_documentation()
- get_tools_by_category()
- get_common_workflows()
"""

from __future__ import annotations

from codeplane.mcp.docs import (
    TOOL_DOCS,
    BehaviorFlags,
    ToolCategory,
    ToolDocumentation,
    get_common_workflows,
    get_tool_documentation,
    get_tools_by_category,
)


class TestToolCategory:
    """Tests for ToolCategory enum."""

    def test_all_categories(self) -> None:
        """All expected categories exist."""
        expected = {
            "mutation",
            "refactor",
            "session",
            "introspection",
        }
        actual = {cat.value for cat in ToolCategory}
        assert actual == expected

    def test_is_str_enum(self) -> None:
        """Categories are string-based (StrEnum)."""
        assert ToolCategory.MUTATION.value == "mutation"
        # StrEnum: str() returns the value, not the qualified name
        assert str(ToolCategory.REFACTOR) == "refactor"


class TestBehaviorFlags:
    """Tests for BehaviorFlags dataclass."""

    def test_default_values(self) -> None:
        """Defaults are sensible."""
        flags = BehaviorFlags()
        assert flags.idempotent is False
        assert flags.has_side_effects is True
        assert flags.atomic is False
        assert flags.may_be_slow is False

    def test_custom_values(self) -> None:
        """Can set custom values."""
        flags = BehaviorFlags(
            idempotent=True,
            has_side_effects=False,
            atomic=True,
            may_be_slow=True,
        )
        assert flags.idempotent is True
        assert flags.has_side_effects is False
        assert flags.atomic is True
        assert flags.may_be_slow is True


class TestToolDocumentation:
    """Tests for ToolDocumentation dataclass."""

    def test_minimal_creation(self) -> None:
        """Create with minimal required fields."""
        doc = ToolDocumentation(
            name="test_tool",
            description="A test tool.",
            category=ToolCategory.INTROSPECTION,
        )
        assert doc.name == "test_tool"
        assert doc.description == "A test tool."
        assert doc.category == ToolCategory.INTROSPECTION

    def test_default_lists_are_empty(self) -> None:
        """Default list fields are empty."""
        doc = ToolDocumentation(
            name="x",
            description="y",
            category=ToolCategory.MUTATION,
        )
        assert doc.when_to_use == []
        assert doc.when_not_to_use == []
        assert doc.alternatives == []
        assert doc.commonly_preceded_by == []
        assert doc.commonly_followed_by == []
        assert doc.possible_errors == []
        assert doc.examples == []

    def test_to_dict(self) -> None:
        """Converts to dictionary."""
        doc = ToolDocumentation(
            name="recon",
            description="Task-aware file discovery.",
            category=ToolCategory.INTROSPECTION,
            when_to_use=["Starting a task"],
            behavior=BehaviorFlags(idempotent=False),
        )
        data = doc.to_dict()

        assert data["name"] == "recon"
        assert data["description"] == "Task-aware file discovery."
        assert data["category"] == "introspection"
        assert data["when_to_use"] == ["Starting a task"]
        assert data["behavior"]["idempotent"] is False

    def test_to_dict_includes_all_fields(self) -> None:
        """to_dict includes all documentation fields."""
        doc = ToolDocumentation(
            name="x",
            description="y",
            category=ToolCategory.MUTATION,
            hints_before="Do this first",
            hints_after="Do this after",
        )
        data = doc.to_dict()

        assert "hints" in data
        assert data["hints"]["before_calling"] == "Do this first"
        assert data["hints"]["after_calling"] == "Do this after"
        assert "related_tools" in data
        assert "behavior" in data


class TestToolDocs:
    """Tests for TOOL_DOCS registry."""

    def test_is_dict(self) -> None:
        """TOOL_DOCS is a dictionary."""
        assert isinstance(TOOL_DOCS, dict)

    def test_contains_core_tools(self) -> None:
        """Contains documentation for core tools."""
        expected = ["recon", "refactor_plan", "refactor_edit", "checkpoint"]
        for tool in expected:
            assert tool in TOOL_DOCS, f"Missing doc for {tool}"

    def test_all_docs_are_tool_documentation(self) -> None:
        """All values are ToolDocumentation instances."""
        for name, doc in TOOL_DOCS.items():
            assert isinstance(doc, ToolDocumentation), f"{name} is not ToolDocumentation"

    def test_names_match_keys(self) -> None:
        """Documentation name matches registry key."""
        for key, doc in TOOL_DOCS.items():
            assert doc.name == key, f"Name mismatch: {key} vs {doc.name}"


class TestGetToolDocumentation:
    """Tests for get_tool_documentation function."""

    def test_returns_documentation_for_known_tool(self) -> None:
        """Returns documentation for known tools."""
        doc = get_tool_documentation("recon")
        assert doc is not None
        assert doc.name == "recon"

    def test_returns_none_for_unknown_tool(self) -> None:
        """Returns None for unknown tools."""
        doc = get_tool_documentation("nonexistent_tool")
        assert doc is None


class TestGetToolsByCategory:
    """Tests for get_tools_by_category function."""

    def test_returns_dict(self) -> None:
        """Returns a dictionary."""
        result = get_tools_by_category()
        assert isinstance(result, dict)

    def test_groups_by_category(self) -> None:
        """Tools are grouped by their category."""
        result = get_tools_by_category()

        # Check that introspection category exists and contains expected tools
        if "introspection" in result:
            assert "recon" in result["introspection"]

    def test_all_tools_categorized(self) -> None:
        """All documented tools appear in exactly one category."""
        result = get_tools_by_category()

        all_tools = set()
        for tools in result.values():
            all_tools.update(tools)

        assert all_tools == set(TOOL_DOCS.keys())


class TestGetCommonWorkflows:
    """Tests for get_common_workflows function."""

    def test_returns_list(self) -> None:
        """Returns a list."""
        result = get_common_workflows()
        assert isinstance(result, list)

    def test_workflows_have_required_fields(self) -> None:
        """Each workflow has name, description, and tools."""
        for workflow in get_common_workflows():
            assert "name" in workflow
            assert "description" in workflow
            assert "tools" in workflow
            assert isinstance(workflow["tools"], list)

    def test_contains_expected_workflows(self) -> None:
        """Contains expected workflow names."""
        workflows = get_common_workflows()
        names = {w["name"] for w in workflows}

        expected = {"exploration", "modification", "refactoring", "review"}
        assert expected.issubset(names)
