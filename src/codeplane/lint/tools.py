"""Lint tool registry - definitions for all supported tools."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.lint.models import ParseResult, ToolCategory

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
    # For pyproject.toml, use "pyproject.toml:tool.ruff" to require [tool.ruff] section
    config_files: list[str] = field(default_factory=list)

    # Command arguments
    check_args: list[str] = field(default_factory=list)  # Check-only mode
    fix_args: list[str] = field(default_factory=list)  # Fix mode (default)
    dry_run_args: list[str] = field(default_factory=list)  # Show diff without modifying

    # Some tools need paths passed differently
    paths_position: str = "end"  # "end", "after_executable", "none"
    paths_separator: str | None = None  # For tools that want comma-separated paths

    # Flag to force the tool to respect its exclude config even for explicitly passed paths.
    # e.g. ruff's --force-exclude.  Injected only when explicit file paths are given.
    force_exclude_flag: str | None = None

    # Output parsing
    output_format: str = "json"  # "json", "sarif", "custom"
    stderr_has_output: bool = False  # Some tools write to stderr

    # Parser function (set by register)
    _parser: Callable[[str, str], ParseResult] | None = None

    def parse_output(self, stdout: str, stderr: str) -> ParseResult:
        """Parse tool output into diagnostics."""
        if self._parser is None:
            return ParseResult.ok([])
        return self._parser(stdout, stderr)


def _check_config_exists(workspace_root: Path, config_spec: str) -> str | None:
    """Check if a config file exists and optionally contains required section.

    Args:
        workspace_root: Repository root path
        config_spec: Either "filename" or "filename:section.path" for TOML files

    Returns:
        The config file path if found, None otherwise
    """
    if ":" in config_spec:
        # Section-aware check: "pyproject.toml:tool.ruff"
        filename, section_path = config_spec.split(":", 1)
        file_path = workspace_root / filename
        if not file_path.exists():
            return None

        # Only parse TOML files for section checks
        if filename.endswith(".toml"):
            try:
                content = file_path.read_text(encoding="utf-8")
                data = tomllib.loads(content)
                # Navigate to section (e.g., "tool.ruff" -> data["tool"]["ruff"])
                parts = section_path.split(".")
                node = data
                for part in parts:
                    if not isinstance(node, dict) or part not in node:
                        return None
                    node = node[part]
                return filename
            except OSError:
                # File unreadable - treated as "config not found"
                return None
            except tomllib.TOMLDecodeError as e:
                # Parse error - log warning and treat as "config not found"
                # User may want to fix the invalid TOML
                import structlog

                structlog.get_logger().warning(
                    "lint_config_parse_error",
                    file=str(file_path),
                    section=section_path,
                    error=str(e),
                )
                return None
            except KeyError:
                # Section not found - normal "config not found" case
                return None
        else:
            # Non-TOML files: just check existence
            return filename if file_path.exists() else None
    else:
        # Simple existence check
        file_path = workspace_root / config_spec
        return config_spec if file_path.exists() else None


class ToolRegistry:
    """Registry of lint tools."""

    def __init__(self) -> None:
        self._tools: dict[str, LintTool] = {}

    def register(
        self,
        tool: LintTool,
        parser: Callable[[str, str], ParseResult] | None = None,
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

    def detect(self, workspace_root: Path) -> list[tuple[LintTool, str]]:
        """Detect which tools are configured for this workspace.

        Returns:
            List of (tool, config_file) tuples for detected tools
        """
        detected: list[tuple[LintTool, str]] = []
        for tool in self._tools.values():
            for config_spec in tool.config_files:
                config_file = _check_config_exists(workspace_root, config_spec)
                if config_file:
                    detected.append((tool, config_file))
                    break
        return detected

    def clear(self) -> None:
        """Clear all registered tools."""
        self._tools.clear()


# Global registry
registry = ToolRegistry()
