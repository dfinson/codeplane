"""Tests for MCP index tools (search, map_repo).

Verifies summary helpers and serialization functions.
"""

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


class TestSerializeTree:
    """Tests for _serialize_tree helper."""

    def test_empty_tree(self) -> None:
        """Empty tree returns empty list."""
        result = _serialize_tree([])
        assert result == []

    def test_file_node(self) -> None:
        """File node serialization."""

        # Create a mock file node
        class MockFileNode:
            name = "main.py"
            path = "src/main.py"
            is_dir = False
            line_count = 100
            children = []  # Files don't have children

        result = _serialize_tree([MockFileNode()])
        assert len(result) == 1
        assert result[0]["name"] == "main.py"
        assert result[0]["path"] == "src/main.py"
        assert result[0]["is_dir"] is False
        assert result[0]["line_count"] == 100

    def test_directory_node(self) -> None:
        """Directory node serialization."""

        class MockDirNode:
            name = "src"
            path = "src"
            is_dir = True
            file_count = 5
            children = []

        result = _serialize_tree([MockDirNode()])
        assert len(result) == 1
        assert result[0]["name"] == "src"
        assert result[0]["is_dir"] is True
        assert result[0]["file_count"] == 5
        assert result[0]["children"] == []

    def test_nested_tree(self) -> None:
        """Nested directory structure."""

        class MockFileNode:
            name = "main.py"
            path = "src/main.py"
            is_dir = False
            line_count = 50
            children = []

        class MockDirNode:
            name = "src"
            path = "src"
            is_dir = True
            file_count = 1

            def __init__(self):
                self.children = [MockFileNode()]

        result = _serialize_tree([MockDirNode()])
        assert len(result) == 1
        assert result[0]["is_dir"] is True
        assert len(result[0]["children"]) == 1
        assert result[0]["children"][0]["name"] == "main.py"
