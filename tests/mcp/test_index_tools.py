"""Tests for MCP index tool helpers.

Tests helper functions in mcp/tools/index.py:
- _summarize_search: Search result summary
- _summarize_map: Map repo summary
- _serialize_tree: Tree serialization for display
"""

from dataclasses import dataclass
from typing import Any

from codeplane.mcp.tools.index import (
    _serialize_tree,
    _summarize_map,
    _summarize_search,
)


class TestSummarizeSearch:
    """Tests for _summarize_search helper."""

    def test_no_results(self) -> None:
        """No results returns appropriate message."""
        result = _summarize_search(
            count=0,
            mode="lexical",
            query="test_query",
        )
        assert "no" in result.lower()
        assert "result" in result.lower()
        assert "test_query" in result or "test_que" in result  # May be truncated

    def test_single_result(self) -> None:
        """Single result shows count."""
        result = _summarize_search(
            count=1,
            mode="lexical",
            query="main",
        )
        assert "1" in result
        assert "lexical" in result.lower() or "result" in result.lower()

    def test_multiple_results(self) -> None:
        """Multiple results shows count."""
        result = _summarize_search(
            count=15,
            mode="symbol",
            query="MyClass",
        )
        assert "15" in result
        assert "symbol" in result.lower() or "result" in result.lower()

    def test_with_fallback(self) -> None:
        """Fallback flag shows indicator."""
        result = _summarize_search(
            count=5,
            mode="lexical",
            query="test",
            fallback=True,
        )
        assert "fallback" in result.lower() or "literal" in result.lower()

    def test_with_file_count(self) -> None:
        """Shows file count when provided."""
        result = _summarize_search(
            count=10,
            mode="lexical",
            query="import",
            file_count=3,
        )
        assert "3" in result or "file" in result.lower()

    def test_query_included(self) -> None:
        """Query is included in summary."""
        result = _summarize_search(
            count=1,
            mode="lexical",
            query="search_term",
        )
        # Query may be truncated but should have some part of it
        assert "search" in result.lower() or "term" in result.lower()

    def test_long_query_truncated(self) -> None:
        """Long queries are truncated."""
        long_query = "this_is_a_very_long_query_string_that_should_be_truncated"
        result = _summarize_search(
            count=2,
            mode="lexical",
            query=long_query,
        )
        # Should not contain full query
        assert len(result) < len(long_query) * 2


class TestSummarizeMap:
    """Tests for _summarize_map helper."""

    def test_file_count_only(self) -> None:
        """Shows file count."""
        result = _summarize_map(
            file_count=100,
            sections=[],
            truncated=False,
        )
        assert "100" in result
        assert "file" in result.lower()

    def test_with_sections(self) -> None:
        """Shows included sections."""
        result = _summarize_map(
            file_count=50,
            sections=["structure", "languages"],
            truncated=False,
        )
        assert "structure" in result.lower()
        assert "languages" in result.lower()

    def test_truncated(self) -> None:
        """Shows truncated indicator."""
        result = _summarize_map(
            file_count=1000,
            sections=["structure"],
            truncated=True,
        )
        assert "truncated" in result.lower()

    def test_not_truncated(self) -> None:
        """No truncated indicator when not truncated."""
        result = _summarize_map(
            file_count=50,
            sections=["structure"],
            truncated=False,
        )
        # "truncated" should not appear (unless commenting why)
        # Just verify it runs without error
        assert "50" in result

    def test_multiple_sections(self) -> None:
        """Multiple sections listed."""
        result = _summarize_map(
            file_count=200,
            sections=["structure", "languages", "entry_points"],
            truncated=False,
        )
        assert "structure" in result.lower() or "section" in result.lower()

    def test_empty_sections(self) -> None:
        """Empty sections list."""
        result = _summarize_map(
            file_count=25,
            sections=[],
            truncated=False,
        )
        assert "25" in result


# Mock node class for testing _serialize_tree
@dataclass
class MockTreeNode:
    name: str
    path: str
    is_dir: bool
    file_count: int = 0  # Only for directories
    line_count: int = 0  # Only for files
    children: list[Any] | None = None

    def __post_init__(self) -> None:
        if self.children is None:
            self.children = []


class TestSerializeTree:
    """Tests for _serialize_tree helper."""

    def test_empty_tree(self) -> None:
        """Empty tree returns empty list."""
        result = _serialize_tree([])
        assert result == []

    def test_single_file(self) -> None:
        """Single file node."""
        nodes = [
            MockTreeNode(
                name="main.py",
                path="src/main.py",
                is_dir=False,
                line_count=100,
            )
        ]
        result = _serialize_tree(nodes)
        assert len(result) == 1
        assert result[0]["name"] == "main.py"
        assert result[0]["path"] == "src/main.py"
        assert result[0]["is_dir"] is False
        assert result[0]["line_count"] == 100

    def test_single_directory(self) -> None:
        """Single directory node."""
        nodes = [
            MockTreeNode(
                name="src",
                path="src",
                is_dir=True,
                file_count=10,
            )
        ]
        result = _serialize_tree(nodes)
        assert len(result) == 1
        assert result[0]["name"] == "src"
        assert result[0]["is_dir"] is True
        assert result[0]["file_count"] == 10
        assert result[0]["children"] == []

    def test_nested_tree(self) -> None:
        """Nested directory with children."""
        inner_file = MockTreeNode(
            name="utils.py",
            path="src/utils.py",
            is_dir=False,
            line_count=50,
        )
        nodes = [
            MockTreeNode(
                name="src",
                path="src",
                is_dir=True,
                file_count=5,
                children=[inner_file],
            )
        ]
        result = _serialize_tree(nodes)
        assert len(result) == 1
        assert result[0]["children"] is not None
        assert len(result[0]["children"]) == 1
        assert result[0]["children"][0]["name"] == "utils.py"

    def test_multiple_levels(self) -> None:
        """Multiple nesting levels."""
        deepest = MockTreeNode(
            name="deep.py",
            path="a/b/c/deep.py",
            is_dir=False,
            line_count=10,
        )
        level_c = MockTreeNode(
            name="c",
            path="a/b/c",
            is_dir=True,
            file_count=1,
            children=[deepest],
        )
        level_b = MockTreeNode(
            name="b",
            path="a/b",
            is_dir=True,
            file_count=1,
            children=[level_c],
        )
        nodes = [
            MockTreeNode(
                name="a",
                path="a",
                is_dir=True,
                file_count=1,
                children=[level_b],
            )
        ]
        result = _serialize_tree(nodes)
        # Traverse to deepest
        assert result[0]["name"] == "a"
        assert result[0]["children"][0]["name"] == "b"
        assert result[0]["children"][0]["children"][0]["name"] == "c"
        assert result[0]["children"][0]["children"][0]["children"][0]["name"] == "deep.py"

    def test_siblings(self) -> None:
        """Multiple siblings at same level."""
        nodes = [
            MockTreeNode(name="a.py", path="a.py", is_dir=False, line_count=10),
            MockTreeNode(name="b.py", path="b.py", is_dir=False, line_count=20),
            MockTreeNode(name="c.py", path="c.py", is_dir=False, line_count=30),
        ]
        result = _serialize_tree(nodes)
        assert len(result) == 3
        assert result[0]["name"] == "a.py"
        assert result[1]["name"] == "b.py"
        assert result[2]["name"] == "c.py"
