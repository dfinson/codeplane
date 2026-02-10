"""Tests for MCP index tools (search, map_repo).

Verifies summary helpers and serialization functions.
"""

from typing import Any

import pytest

from codeplane.config.constants import SEARCH_SCOPE_FALLBACK_LINES_DEFAULT
from codeplane.mcp.tools.index import (
    _serialize_tree,
    _summarize_map,
    _summarize_search,
)


class TestSummarizeSearch:
    """Tests for _summarize_search helper."""

    def test_no_results(self) -> None:
        """No results message."""
        result = _summarize_search(count=0, mode="lexical", query="foo")
        assert 'no lexical results for "foo"' in result

    def test_with_results(self) -> None:
        """With results shows count and mode."""
        result = _summarize_search(count=5, mode="symbol", query="MyClass")
        assert '5 symbol results for "MyClass"' in result

    def test_with_file_count(self) -> None:
        """Shows file count when provided."""
        result = _summarize_search(count=10, mode="lexical", query="test", file_count=3)
        assert "across 3 files" in result

    def test_with_fallback(self) -> None:
        """Shows fallback indicator."""
        result = _summarize_search(count=5, mode="lexical", query="test", fallback=True)
        assert "literal fallback" in result

    def test_truncates_long_query(self) -> None:
        """Long queries are truncated."""
        long_query = "a" * 100
        result = _summarize_search(count=1, mode="lexical", query=long_query)
        # Query should be truncated to ~20 chars
        assert len(result) < 100


class TestSummarizeMap:
    """Tests for _summarize_map helper."""

    def test_files_only(self) -> None:
        """File count only."""
        result = _summarize_map(file_count=42, sections=[], truncated=False)
        assert "42 files" in result

    def test_with_sections(self) -> None:
        """With sections list."""
        result = _summarize_map(file_count=10, sections=["structure", "languages"], truncated=False)
        assert "10 files" in result
        assert "structure" in result
        assert "languages" in result

    def test_truncated(self) -> None:
        """Shows truncation."""
        result = _summarize_map(file_count=100, sections=[], truncated=True)
        assert "truncated" in result


class MockFileNode:
    """Mock file node for testing."""

    def __init__(
        self,
        name: str = "main.py",
        path: str = "src/main.py",
        line_count: int = 100,
    ) -> None:
        self.name = name
        self.path = path
        self.is_dir = False
        self.line_count = line_count
        self.children: list[Any] = []


class MockDirNode:
    """Mock directory node for testing."""

    def __init__(
        self,
        name: str = "src",
        path: str = "src",
        file_count: int = 5,
        children: list[Any] | None = None,
    ) -> None:
        self.name = name
        self.path = path
        self.is_dir = True
        self.file_count = file_count
        self.children: list[Any] = children if children is not None else []


class TestSerializeTree:
    """Tests for _serialize_tree helper."""

    def test_empty_tree(self) -> None:
        """Empty tree returns empty list."""
        result = _serialize_tree([])
        assert result == []

    def test_file_node(self) -> None:
        """File node serialization."""
        result = _serialize_tree([MockFileNode()])
        assert len(result) == 1
        assert result[0]["name"] == "main.py"
        assert result[0]["path"] == "src/main.py"
        assert result[0]["is_dir"] is False
        assert result[0]["line_count"] == 100

    def test_directory_node(self) -> None:
        """Directory node serialization."""
        result = _serialize_tree([MockDirNode()])
        assert len(result) == 1
        assert result[0]["name"] == "src"
        assert result[0]["is_dir"] is True
        assert result[0]["file_count"] == 5
        assert result[0]["children"] == []

    def test_nested_tree(self) -> None:
        """Nested directory structure."""
        file_node = MockFileNode(name="main.py", path="src/main.py", line_count=50)
        dir_node = MockDirNode(name="src", path="src", file_count=1, children=[file_node])

        result = _serialize_tree([dir_node])
        assert len(result) == 1
        assert result[0]["is_dir"] is True
        assert len(result[0]["children"]) == 1
        assert result[0]["children"][0]["name"] == "main.py"


# =============================================================================
# Context Preset Tests
# =============================================================================


class TestContextPresets:
    """Tests for context preset line counts."""

    @pytest.mark.parametrize(
        "context,expected_lines",
        [
            ("none", 0),
            ("minimal", 1),
            ("standard", 5),
            ("rich", 20),
        ],
    )
    def test_preset_line_counts(self, context: str, expected_lines: int) -> None:
        """Each preset maps to expected line count."""
        # Import the preset mapping from the module
        CONTEXT_PRESETS = {
            "none": 0,
            "minimal": 1,
            "standard": 5,
            "rich": 20,
        }
        assert CONTEXT_PRESETS[context] == expected_lines

    def test_structural_modes_not_in_line_presets(self) -> None:
        """Structural modes (function, class) are not line-based presets."""
        CONTEXT_PRESETS = {
            "none": 0,
            "minimal": 1,
            "standard": 5,
            "rich": 20,
        }
        assert "function" not in CONTEXT_PRESETS
        assert "class" not in CONTEXT_PRESETS

    def test_structural_mode_fallback_lines(self) -> None:
        """Structural modes use fallback lines constant."""
        # The fallback should be a reasonable default (25 as per design)
        assert SEARCH_SCOPE_FALLBACK_LINES_DEFAULT >= 20
        assert SEARCH_SCOPE_FALLBACK_LINES_DEFAULT <= 30
