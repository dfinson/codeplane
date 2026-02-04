"""Tests for lint tool definitions.

Verifies all registered lint tools have valid configurations.
"""

from codeplane.lint.definitions import registry
from codeplane.lint.models import ToolCategory


class TestToolRegistry:
    """Tests for tool registry and registered tools."""

    def test_registry_has_tools(self) -> None:
        """Registry should have multiple tools registered."""
        all_tools = registry.all()
        assert len(all_tools) > 0

    def test_all_tools_have_required_fields(self) -> None:
        """All registered tools should have required fields."""
        for tool in registry.all():
            assert tool.tool_id, "Tool missing tool_id"
            assert tool.name, f"Tool {tool.tool_id} missing name"
            assert tool.languages, f"Tool {tool.tool_id} missing languages"
            assert tool.category, f"Tool {tool.tool_id} missing category"
            assert tool.executable, f"Tool {tool.tool_id} missing executable"

    def test_tool_ids_are_unique(self) -> None:
        """All tool IDs should be unique."""
        tool_ids = [t.tool_id for t in registry.all()]
        assert len(tool_ids) == len(set(tool_ids))

    def test_tool_categories_are_valid(self) -> None:
        """All tools should have valid category values."""
        valid_categories = set(ToolCategory)
        for tool in registry.all():
            assert tool.category in valid_categories


class TestPythonTools:
    """Tests for Python lint tools."""

    def test_ruff_registered(self) -> None:
        """Ruff tool should be registered."""
        tool = registry.get("python.ruff")
        assert tool is not None
        assert tool.name == "Ruff"
        assert "python" in tool.languages
        assert tool.category == ToolCategory.LINT
        assert tool.executable == "ruff"

    def test_mypy_registered(self) -> None:
        """mypy tool should be registered."""
        tool = registry.get("python.mypy")
        assert tool is not None
        assert tool.name == "mypy"
        assert "python" in tool.languages
        assert tool.category == ToolCategory.TYPE_CHECK

    def test_black_registered(self) -> None:
        """Black formatter should be registered."""
        tool = registry.get("python.black")
        assert tool is not None
        assert tool.category == ToolCategory.FORMAT

    def test_bandit_registered(self) -> None:
        """Bandit security tool should be registered."""
        tool = registry.get("python.bandit")
        assert tool is not None
        assert tool.category == ToolCategory.SECURITY


class TestJavaScriptTools:
    """Tests for JavaScript/TypeScript lint tools."""

    def test_eslint_registered(self) -> None:
        """ESLint should be registered."""
        tool = registry.get("js.eslint")
        assert tool is not None
        assert "javascript" in tool.languages
        assert "typescript" in tool.languages
        assert tool.category == ToolCategory.LINT

    def test_prettier_registered(self) -> None:
        """Prettier should be registered."""
        tool = registry.get("js.prettier")
        assert tool is not None
        assert tool.category == ToolCategory.FORMAT
        # Prettier supports many languages
        assert "javascript" in tool.languages
        assert "typescript" in tool.languages
        assert "json" in tool.languages

    def test_tsc_registered(self) -> None:
        """TypeScript compiler should be registered."""
        tool = registry.get("js.tsc")
        assert tool is not None
        assert tool.category == ToolCategory.TYPE_CHECK


class TestGoTools:
    """Tests for Go lint tools."""

    def test_go_vet_registered(self) -> None:
        """go vet should be registered."""
        tool = registry.get("go.vet")
        assert tool is not None
        assert "go" in tool.languages
        assert tool.category == ToolCategory.LINT

    def test_gofmt_registered(self) -> None:
        """gofmt should be registered."""
        tool = registry.get("go.gofmt")
        assert tool is not None
        assert tool.category == ToolCategory.FORMAT

    def test_golangci_lint_registered(self) -> None:
        """golangci-lint should be registered."""
        tool = registry.get("go.golangci-lint")
        assert tool is not None


class TestRustTools:
    """Tests for Rust lint tools."""

    def test_clippy_registered(self) -> None:
        """Clippy should be registered."""
        tool = registry.get("rust.clippy")
        assert tool is not None
        assert "rust" in tool.languages
        assert tool.category == ToolCategory.LINT

    def test_rustfmt_registered(self) -> None:
        """rustfmt should be registered."""
        tool = registry.get("rust.rustfmt")
        assert tool is not None
        assert tool.category == ToolCategory.FORMAT

    def test_cargo_audit_registered(self) -> None:
        """cargo-audit should be registered."""
        tool = registry.get("rust.cargo-audit")
        assert tool is not None
        assert tool.category == ToolCategory.SECURITY


class TestToolQueries:
    """Tests for registry query methods."""

    def test_for_language_python(self) -> None:
        """Should find tools supporting Python."""
        python_tools = registry.for_language("python")
        assert len(python_tools) > 0
        tool_ids = {t.tool_id for t in python_tools}
        assert "python.ruff" in tool_ids
        assert "python.mypy" in tool_ids

    def test_for_language_unknown(self) -> None:
        """Should return empty for unknown language."""
        tools = registry.for_language("nonexistent_language_xyz")
        assert tools == []

    def test_for_category_lint(self) -> None:
        """Should find all lint tools."""
        lint_tools = registry.for_category(ToolCategory.LINT)
        assert len(lint_tools) > 0
        for tool in lint_tools:
            assert tool.category == ToolCategory.LINT

    def test_for_category_format(self) -> None:
        """Should find all format tools."""
        format_tools = registry.for_category(ToolCategory.FORMAT)
        assert len(format_tools) > 0
        for tool in format_tools:
            assert tool.category == ToolCategory.FORMAT


class TestToolParsers:
    """Tests that all tools have parsers."""

    def test_all_tools_have_parsers(self) -> None:
        """All registered tools should have a parser."""
        for tool in registry.all():
            assert tool._parser is not None, f"Tool {tool.tool_id} missing parser"

    def test_parse_output_returns_list(self) -> None:
        """parse_output should return a list of diagnostics."""
        for tool in registry.all():
            result = tool.parse_output("", "")
            assert isinstance(result, list)
