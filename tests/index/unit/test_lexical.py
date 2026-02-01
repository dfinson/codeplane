"""Unit tests for Lexical Index (lexical.py).

Tests cover:
- Index creation and configuration
- Document indexing (add_file, add_files_batch)
- Document removal (remove_file)
- Search operations (text, symbol, path)
- Index management (clear, reload, doc_count)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.indexing import (
    LexicalIndex,
    SearchResult,
    SearchResults,
    create_index,
)


@pytest.fixture
def lexical_index(temp_dir: Path) -> LexicalIndex:
    """Create a fresh LexicalIndex for testing."""
    index_path = temp_dir / "tantivy_index"
    return LexicalIndex(index_path)


class TestLexicalIndexCreation:
    """Tests for lexical index creation."""

    def test_create_index(self, temp_dir: Path) -> None:
        """Should create a new Tantivy index."""
        index_path = temp_dir / "new_index"
        index = create_index(index_path)

        assert index is not None
        # Force initialization by adding a file
        index.add_file("init.py", "", context_id=1)
        assert index_path.exists()

    def test_create_index_idempotent(self, temp_dir: Path) -> None:
        """Creating index twice should not fail."""
        index_path = temp_dir / "idempotent_index"

        index1 = create_index(index_path)
        index1.add_file("file1.py", "x = 1", context_id=1)

        index2 = create_index(index_path)
        index2.add_file("file2.py", "y = 2", context_id=1)

        assert index1 is not None
        assert index2 is not None


class TestAddFile:
    """Tests for adding files to the index."""

    def test_add_file_basic(self, lexical_index: LexicalIndex) -> None:
        """Should add a file to the index and commit automatically."""
        lexical_index.add_file(
            file_path="src/main.py",
            content="def hello():\n    return 'hello'\n",
            context_id=1,
            symbols=["hello"],
        )
        lexical_index.reload()

        results = lexical_index.search("hello")
        assert len(results.results) >= 1

    def test_add_file_with_multiple_symbols(self, lexical_index: LexicalIndex) -> None:
        """Should index all symbols."""
        lexical_index.add_file(
            file_path="src/utils.py",
            content="def foo(): pass\ndef bar(): pass\nclass Baz: pass\n",
            context_id=1,
            symbols=["foo", "bar", "Baz"],
        )
        lexical_index.reload()

        for name in ["foo", "bar", "Baz"]:
            results = lexical_index.search_symbols(name)
            assert len(results.results) >= 1

    def test_add_file_empty_content(self, lexical_index: LexicalIndex) -> None:
        """Should handle empty content."""
        lexical_index.add_file(
            file_path="src/empty.py",
            content="",
            context_id=1,
            symbols=[],
        )
        lexical_index.reload()

        # Empty files are indexed but have no searchable content.
        # Verify via doc_count instead of search.
        assert lexical_index.doc_count() == 1

    def test_add_file_with_file_id(self, lexical_index: LexicalIndex) -> None:
        """Should accept optional file_id parameter."""
        lexical_index.add_file(
            file_path="src/with_id.py",
            content="content_with_id\n",
            context_id=1,
            file_id=42,
            symbols=[],
        )
        lexical_index.reload()

        results = lexical_index.search("content_with_id")
        assert len(results.results) >= 1


class TestAddFilesBatch:
    """Tests for batch file addition."""

    def test_add_files_batch(self, lexical_index: LexicalIndex) -> None:
        """Should add multiple files in a single batch."""
        files = [
            {
                "path": "src/a.py",
                "content": "batch_a",
                "context_id": 1,
                "file_id": 1,
                "symbols": ["a"],
            },
            {
                "path": "src/b.py",
                "content": "batch_b",
                "context_id": 1,
                "file_id": 2,
                "symbols": ["b"],
            },
            {
                "path": "src/c.py",
                "content": "batch_c",
                "context_id": 1,
                "file_id": 3,
                "symbols": ["c"],
            },
        ]
        count = lexical_index.add_files_batch(files)
        lexical_index.reload()

        assert count == 3
        for letter in ["a", "b", "c"]:
            results = lexical_index.search(f"batch_{letter}")
            assert len(results.results) >= 1

    def test_add_files_batch_empty(self, lexical_index: LexicalIndex) -> None:
        """Should handle empty batch."""
        count = lexical_index.add_files_batch([])
        assert count == 0


class TestRemoveFile:
    """Tests for removing files from the index."""

    def test_remove_file(self, lexical_index: LexicalIndex) -> None:
        """Should remove file from index."""
        lexical_index.add_file(
            file_path="src/to_remove.py",
            content="def remove_me(): pass\n",
            context_id=1,
            symbols=["remove_me"],
        )
        lexical_index.reload()

        # Verify file was added via content search
        results = lexical_index.search("remove_me")
        assert len(results.results) >= 1
        assert lexical_index.doc_count() == 1

        removed = lexical_index.remove_file("src/to_remove.py")
        lexical_index.reload()

        assert removed is True
        assert lexical_index.doc_count() == 0

    def test_remove_nonexistent_file(self, lexical_index: LexicalIndex) -> None:
        """Should return False when removing non-existent file."""
        removed = lexical_index.remove_file("nonexistent.py")
        assert removed is False


class TestSearch:
    """Tests for search operations."""

    def test_search_content(self, lexical_index: LexicalIndex) -> None:
        """Should search file content."""
        lexical_index.add_file(
            file_path="src/searchable.py",
            content="# This is a unique searchable string XYZ123\n",
            context_id=1,
            symbols=[],
        )
        lexical_index.reload()

        results = lexical_index.search("XYZ123")
        assert len(results.results) >= 1
        assert any("searchable.py" in r.file_path for r in results.results)

    def test_search_symbols(self, lexical_index: LexicalIndex) -> None:
        """Should search by symbol name."""
        lexical_index.add_file(
            file_path="src/symbols.py",
            content="class UniqueClassName: pass\n",
            context_id=1,
            symbols=["UniqueClassName"],
        )
        lexical_index.reload()

        results = lexical_index.search_symbols("UniqueClassName")
        assert len(results.results) >= 1

    def test_search_path(self, lexical_index: LexicalIndex) -> None:
        """Should search by exact file path (raw tokenizer requires exact match)."""
        lexical_index.add_file(
            file_path="src/unique_path_name.py",
            content="x = 1\n",
            context_id=1,
            symbols=[],
        )
        lexical_index.reload()

        # Path field uses raw tokenizer, so exact match is required
        results = lexical_index.search_path("src/unique_path_name.py")
        assert len(results.results) >= 1

    def test_search_with_limit(self, lexical_index: LexicalIndex) -> None:
        """Should respect result limit."""
        files = [
            {"path": f"src/file_{i}.py", "content": f"common_term = {i}\n", "context_id": 1}
            for i in range(20)
        ]
        lexical_index.add_files_batch(files)
        lexical_index.reload()

        results = lexical_index.search("common_term", limit=5)
        assert len(results.results) <= 5

    def test_search_with_context_id(self, lexical_index: LexicalIndex) -> None:
        """Should filter by context_id."""
        lexical_index.add_file("src/ctx1.py", "shared_term", context_id=1)
        lexical_index.add_file("src/ctx2.py", "shared_term", context_id=2)
        lexical_index.reload()

        results = lexical_index.search("shared_term", context_id=1)
        assert all(r.context_id == 1 for r in results.results)

    def test_search_no_results(self, lexical_index: LexicalIndex) -> None:
        """Should return empty results when nothing matches."""
        lexical_index.add_file(
            file_path="src/unrelated.py",
            content="x = 1\n",
            context_id=1,
            symbols=[],
        )
        lexical_index.reload()

        results = lexical_index.search("nonexistent_search_term_xyz")
        assert len(results.results) == 0


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_search_result_structure(self) -> None:
        """SearchResult should have expected fields."""
        result = SearchResult(
            file_path="src/test.py",
            line=10,
            column=5,
            snippet="def test(): pass",
            score=1.5,
        )
        assert result.file_path == "src/test.py"
        assert result.line == 10
        assert result.column == 5
        assert result.snippet == "def test(): pass"
        assert result.score == 1.5
        assert result.context_id is None

    def test_search_result_with_context_id(self) -> None:
        """SearchResult should support optional context_id."""
        result = SearchResult(
            file_path="src/test.py",
            line=1,
            column=0,
            snippet="x",
            score=1.0,
            context_id=42,
        )
        assert result.context_id == 42


class TestSearchResults:
    """Tests for SearchResults dataclass."""

    def test_search_results_structure(self) -> None:
        """SearchResults should have results list and metadata."""
        results = SearchResults(
            results=[
                SearchResult(
                    file_path="a.py",
                    line=1,
                    column=0,
                    snippet="x",
                    score=1.0,
                ),
            ],
            total_hits=1,
            query_time_ms=5,
        )
        assert len(results.results) == 1
        assert results.total_hits == 1
        assert results.query_time_ms == 5

    def test_search_results_defaults(self) -> None:
        """SearchResults should have sensible defaults."""
        results = SearchResults()
        assert results.results == []
        assert results.total_hits == 0
        assert results.query_time_ms == 0


class TestClear:
    """Tests for clearing the index."""

    def test_clear_removes_all(self, lexical_index: LexicalIndex) -> None:
        """Clear should remove all documents."""
        files = [
            {"path": f"src/clear_{i}.py", "content": f"clear_{i}\n", "context_id": 1}
            for i in range(5)
        ]
        lexical_index.add_files_batch(files)
        lexical_index.reload()

        assert lexical_index.doc_count() == 5

        lexical_index.clear()
        lexical_index.reload()

        assert lexical_index.doc_count() == 0


class TestDocCount:
    """Tests for doc_count method."""

    def test_doc_count_empty(self, lexical_index: LexicalIndex) -> None:
        """Empty index should have zero documents."""
        assert lexical_index.doc_count() == 0

    def test_doc_count_after_adds(self, lexical_index: LexicalIndex) -> None:
        """Should count added documents."""
        lexical_index.add_file("a.py", "a", context_id=1)
        lexical_index.add_file("b.py", "b", context_id=1)
        lexical_index.reload()

        assert lexical_index.doc_count() == 2


class TestReload:
    """Tests for reload method."""

    def test_reload_sees_changes(self, lexical_index: LexicalIndex) -> None:
        """Reload should make recent changes visible to search."""
        lexical_index.add_file("src/reload_test.py", "reload_content", context_id=1)
        lexical_index.reload()

        results = lexical_index.search("reload_content")
        assert len(results.results) >= 1
