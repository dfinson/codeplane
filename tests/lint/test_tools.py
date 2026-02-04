"""Tests for lint tool registry and detection.

Verifies LintTool dataclass, ToolRegistry, and config detection.
"""

from pathlib import Path

from codeplane.lint.models import Diagnostic, Severity, ToolCategory
from codeplane.lint.tools import LintTool, ToolRegistry, _check_config_exists


class TestLintTool:
    """Tests for LintTool dataclass."""

    def test_create_basic_tool(self) -> None:
        """Should create a tool with minimal fields."""
        tool = LintTool(
            tool_id="test.tool",
            name="Test Tool",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        assert tool.tool_id == "test.tool"
        assert tool.name == "Test Tool"
        assert "python" in tool.languages
        assert tool.category == ToolCategory.LINT
        assert tool.executable == "test"

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        assert tool.config_files == []
        assert tool.check_args == []
        assert tool.fix_args == []
        assert tool.paths_position == "end"
        assert tool.paths_separator is None
        assert tool.output_format == "json"
        assert tool.stderr_has_output is False

    def test_parse_output_without_parser(self) -> None:
        """Should return empty list when no parser set."""
        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        result = tool.parse_output("stdout", "stderr")
        assert result == []

    def test_parse_output_with_parser(self) -> None:
        """Should use parser when set."""

        def mock_parser(stdout: str, _stderr: str) -> list[Diagnostic]:
            return [
                Diagnostic(
                    path="test.py",
                    line=1,
                    message=stdout,
                    source="test.tool",
                    severity=Severity.ERROR,
                    column=1,
                    code="TEST001",
                )
            ]

        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        tool._parser = mock_parser
        result = tool.parse_output("test message", "")
        assert len(result) == 1
        assert result[0].message == "test message"


class TestCheckConfigExists:
    """Tests for _check_config_exists helper."""

    def test_simple_file_exists(self, tmp_path: Path) -> None:
        """Should find existing file."""
        (tmp_path / "config.toml").write_text("content")
        result = _check_config_exists(tmp_path, "config.toml")
        assert result == "config.toml"

    def test_simple_file_not_exists(self, tmp_path: Path) -> None:
        """Should return None for missing file."""
        result = _check_config_exists(tmp_path, "missing.toml")
        assert result is None

    def test_toml_section_exists(self, tmp_path: Path) -> None:
        """Should find TOML section when specified."""
        toml_content = """
[tool.ruff]
line-length = 88
"""
        (tmp_path / "pyproject.toml").write_text(toml_content)
        result = _check_config_exists(tmp_path, "pyproject.toml:tool.ruff")
        assert result == "pyproject.toml"

    def test_toml_section_not_exists(self, tmp_path: Path) -> None:
        """Should return None when TOML section missing."""
        toml_content = """
[tool.black]
line-length = 88
"""
        (tmp_path / "pyproject.toml").write_text(toml_content)
        result = _check_config_exists(tmp_path, "pyproject.toml:tool.ruff")
        assert result is None

    def test_toml_file_not_exists(self, tmp_path: Path) -> None:
        """Should return None when TOML file missing."""
        result = _check_config_exists(tmp_path, "pyproject.toml:tool.ruff")
        assert result is None

    def test_invalid_toml(self, tmp_path: Path) -> None:
        """Should return None for invalid TOML."""
        (tmp_path / "pyproject.toml").write_text("invalid[toml")
        result = _check_config_exists(tmp_path, "pyproject.toml:tool.ruff")
        assert result is None

    def test_nested_section(self, tmp_path: Path) -> None:
        """Should find deeply nested sections."""
        toml_content = """
[tool.pytest.ini_options]
testpaths = ["tests"]
"""
        (tmp_path / "pyproject.toml").write_text(toml_content)
        result = _check_config_exists(tmp_path, "pyproject.toml:tool.pytest.ini_options")
        assert result == "pyproject.toml"


class TestToolRegistry:
    """Tests for ToolRegistry class."""

    def test_register_and_get(self) -> None:
        """Should register and retrieve tools."""
        registry = ToolRegistry()
        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        registry.register(tool)
        retrieved = registry.get("test.tool")
        assert retrieved is tool

    def test_register_with_parser(self) -> None:
        """Should register tool with parser."""
        registry = ToolRegistry()
        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )

        def parser(_stdout: str, _stderr: str) -> list[Diagnostic]:
            return []

        registry.register(tool, parser=parser)
        assert tool._parser is parser

    def test_get_nonexistent(self) -> None:
        """Should return None for nonexistent tool."""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_all_returns_all_tools(self) -> None:
        """Should return all registered tools."""
        registry = ToolRegistry()
        tool1 = LintTool(
            tool_id="test.tool1",
            name="Test1",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test1",
        )
        tool2 = LintTool(
            tool_id="test.tool2",
            name="Test2",
            languages=frozenset({"go"}),
            category=ToolCategory.FORMAT,
            executable="test2",
        )
        registry.register(tool1)
        registry.register(tool2)
        all_tools = registry.all()
        assert len(all_tools) == 2
        assert tool1 in all_tools
        assert tool2 in all_tools

    def test_for_language(self) -> None:
        """Should filter tools by language."""
        registry = ToolRegistry()
        py_tool = LintTool(
            tool_id="test.py",
            name="PyTest",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        go_tool = LintTool(
            tool_id="test.go",
            name="GoTest",
            languages=frozenset({"go"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        registry.register(py_tool)
        registry.register(go_tool)
        python_tools = registry.for_language("python")
        assert py_tool in python_tools
        assert go_tool not in python_tools

    def test_for_category(self) -> None:
        """Should filter tools by category."""
        registry = ToolRegistry()
        lint_tool = LintTool(
            tool_id="test.lint",
            name="Lint",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        fmt_tool = LintTool(
            tool_id="test.fmt",
            name="Fmt",
            languages=frozenset({"python"}),
            category=ToolCategory.FORMAT,
            executable="test",
        )
        registry.register(lint_tool)
        registry.register(fmt_tool)
        lint_tools = registry.for_category(ToolCategory.LINT)
        assert lint_tool in lint_tools
        assert fmt_tool not in lint_tools

    def test_detect(self, tmp_path: Path) -> None:
        """Should detect configured tools."""
        # Create config file
        (tmp_path / "ruff.toml").write_text("line-length = 88")

        registry = ToolRegistry()
        tool = LintTool(
            tool_id="test.ruff",
            name="Ruff",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="ruff",
            config_files=["ruff.toml"],
        )
        registry.register(tool)

        detected = registry.detect(tmp_path)
        assert len(detected) == 1
        assert detected[0][0] is tool
        assert detected[0][1] == "ruff.toml"

    def test_detect_no_match(self, tmp_path: Path) -> None:
        """Should return empty when no configs found."""
        registry = ToolRegistry()
        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
            config_files=["missing.toml"],
        )
        registry.register(tool)
        detected = registry.detect(tmp_path)
        assert detected == []

    def test_clear(self) -> None:
        """Should clear all tools."""
        registry = ToolRegistry()
        tool = LintTool(
            tool_id="test.tool",
            name="Test",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="test",
        )
        registry.register(tool)
        assert len(registry.all()) == 1
        registry.clear()
        assert len(registry.all()) == 0
