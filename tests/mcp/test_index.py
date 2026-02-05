"""Tests for MCP index tools (search, map_repo).

Tests the actual exports:
- _summarize_search() helper
- _summarize_map() helper
- _serialize_tree() helper

Handler tests use conftest.py fixtures for integration testing.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.index import (
    _serialize_tree,
    _summarize_map,
    _summarize_search,
)


class TestSummarizeSearch:
    """Tests for _summarize_search helper."""

    def test_no_results(self):
        """No results message."""
        result = _summarize_search(0, "lexical", "test query")
        assert "no lexical results" in result
        assert "test query" in result

    def test_with_results(self):
        """Shows count and query."""
        result = _summarize_search(5, "symbol", "MyClass")
        assert "5 symbol results" in result
        assert "MyClass" in result

    def test_long_query_truncated(self):
        """Long query truncated to 30 chars."""
        long_query = "x" * 50
        result = _summarize_search(3, "lexical", long_query)
        assert "..." in result

    def test_fallback_suffix(self):
        """Fallback mode adds suffix."""
        result = _summarize_search(2, "lexical", "test", fallback=True)
        assert "(literal fallback)" in result


class TestSummarizeMap:
    """Tests for _summarize_map helper."""

    def test_basic(self):
        """Shows file count."""
        result = _summarize_map(100, [], False)
        assert "100 files" in result

    def test_with_sections(self):
        """Shows sections."""
        result = _summarize_map(50, ["structure", "languages"], False)
        assert "50 files" in result
        assert "structure" in result

    def test_truncated(self):
        """Shows truncated indicator."""
        result = _summarize_map(1000, [], True)
        assert "(truncated)" in result


class TestSerializeTree:
    """Tests for _serialize_tree helper."""

    def test_empty_tree(self):
        """Empty tree returns empty list."""
        result = _serialize_tree([])
        assert result == []

    def test_file_node(self):
        """File node serialized correctly."""
        node = MagicMock()
        node.name = "file.py"
        node.path = "src/file.py"
        node.is_dir = False
        node.line_count = 100

        result = _serialize_tree([node])
        assert len(result) == 1
        assert result[0]["name"] == "file.py"
        assert result[0]["is_dir"] is False

    def test_directory_node(self):
        """Directory node serialized with children."""
        child = MagicMock()
        child.name = "child.py"
        child.path = "src/dir/child.py"
        child.is_dir = False
        child.line_count = 50

        node = MagicMock()
        node.name = "dir"
        node.path = "src/dir"
        node.is_dir = True
        node.file_count = 1
        node.children = [child]

        result = _serialize_tree([node])
        assert result[0]["is_dir"] is True
        assert len(result[0]["children"]) == 1
