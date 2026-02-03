"""Lint tool registry - definitions for all supported tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.lint.models import Diagnostic, ToolCategory

if TYPE_CHECKING:
    pass


@dataclass
class LintTool:
    """Definition of a lint/format/type-check tool."""

    tool_id: str
    name: str
    languages: frozenset[str]
    category: ToolCategory
    executable: str

    # Config files that indicate this tool is configured
    config_files: list[str] = field(default_factory=list)

    # Command arguments
    check_args: list[str] = field(default_factory=list)  # Check-only mode
    fix_args: list[str] = field(default_factory=list)  # Fix mode (default)
    dry_run_args: list[str] = field(default_factory=list)  # Show diff without modifying

    # Some tools need paths passed differently
    paths_position: str = "end"  # "end", "after_executable", "none"
    paths_separator: str | None = None  # For tools that want comma-separated paths

    # Output parsing
    output_format: str = "json"  # "json", "sarif", "custom"
    stderr_has_output: bool = False  # Some tools write to stderr

    # Parser function (set by register)
    _parser: Callable[[str, str], list[Diagnostic]] | None = None

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse tool output into diagnostics."""
        if self._parser is None:
            return []
        return self._parser(stdout, stderr)


class ToolRegistry:
    """Registry of lint tools."""

    def __init__(self) -> None:
        self._tools: dict[str, LintTool] = {}

    def register(
        self,
        tool: LintTool,
        parser: Callable[[str, str], list[Diagnostic]] | None = None,
    ) -> None:
        """Register a tool."""
        if parser is not None:
            tool._parser = parser
        self._tools[tool.tool_id] = tool

    def get(self, tool_id: str) -> LintTool | None:
        """Get tool by ID."""
        return self._tools.get(tool_id)

    def all(self) -> list[LintTool]:
        """Get all registered tools."""
        return list(self._tools.values())

    def for_language(self, language: str) -> list[LintTool]:
        """Get tools that support a language."""
        return [t for t in self._tools.values() if language in t.languages]

    def for_category(self, category: ToolCategory) -> list[LintTool]:
        """Get tools in a category."""
        return [t for t in self._tools.values() if t.category == category]

    def detect(self, workspace_root: Path) -> list[LintTool]:
        """Detect which tools are configured for this workspace."""
        detected: list[LintTool] = []
        for tool in self._tools.values():
            for config_file in tool.config_files:
                if (workspace_root / config_file).exists():
                    detected.append(tool)
                    break
        return detected


# Global registry
registry = ToolRegistry()
