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
        """Search returns all matches; callers handle limiting."""
        files = [
            {"path": f"src/file_{i}.py", "content": f"common_term = {i}\n", "context_id": 1}
            for i in range(20)
        ]
        lexical_index.add_files_batch(files)
        lexical_index.reload()

        results = lexical_index.search("common_term", limit=5)
        # All 20 files match; search returns everything, callers apply limits
        assert len(results.results) == 20

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


class TestExtractSearchTerms:
    """Tests for _extract_search_terms helper method."""

    def test_simple_term(self, lexical_index: LexicalIndex) -> None:
        """Should extract simple search terms."""
        terms = lexical_index._extract_search_terms("hello")
        assert terms == ["hello"]

    def test_multiple_terms(self, lexical_index: LexicalIndex) -> None:
        """Should extract multiple space-separated terms."""
        terms = lexical_index._extract_search_terms("hello world")
        assert terms == ["hello", "world"]

    def test_field_prefix_extraction(self, lexical_index: LexicalIndex) -> None:
        """Should extract value from field:value syntax."""
        terms = lexical_index._extract_search_terms("symbols:MyClass")
        assert terms == ["myclass"]  # lowercased

    def test_removes_boolean_operators(self, lexical_index: LexicalIndex) -> None:
        """Should remove AND, OR, NOT operators."""
        terms = lexical_index._extract_search_terms("foo AND bar OR baz NOT qux")
        assert "and" not in terms
        assert "or" not in terms
        assert "not" not in terms
        assert "foo" in terms
        assert "bar" in terms

    def test_empty_query(self, lexical_index: LexicalIndex) -> None:
        """Should return empty list for empty query."""
        terms = lexical_index._extract_search_terms("")
        assert terms == []

    def test_only_operators(self, lexical_index: LexicalIndex) -> None:
        """Should return empty list when query has only operators."""
        terms = lexical_index._extract_search_terms("AND OR NOT")
        assert terms == []


class TestExtractAllSnippets:
    """Tests for _extract_all_snippets method."""

    def test_single_occurrence(self, lexical_index: LexicalIndex) -> None:
        """Should return single match when term appears once."""
        content = "line one\nline two with target\nline three"
        matches = lexical_index._extract_all_snippets(content, "target")
        assert len(matches) == 1
        assert matches[0][1] == 2  # line number (1-indexed)

    def test_multiple_occurrences(self, lexical_index: LexicalIndex) -> None:
        """Should return all lines containing the term."""
        content = "target here\nsomething else\ntarget again\nmore stuff\ntarget third"
        matches = lexical_index._extract_all_snippets(content, "target")
        assert len(matches) == 3
        assert [m[1] for m in matches] == [1, 3, 5]  # lines 1, 3, 5

    def test_context_lines_default(self, lexical_index: LexicalIndex) -> None:
        """Should include 1 line of context by default."""
        content = "line 1\nline 2\ntarget line\nline 4\nline 5"
        matches = lexical_index._extract_all_snippets(content, "target")
        assert len(matches) == 1
        snippet = matches[0][0]
        # Default context_lines=1: 1 before + match + 1 after = 3 lines
        assert "line 2" in snippet
        assert "target line" in snippet
        assert "line 4" in snippet

    def test_context_lines_zero(self, lexical_index: LexicalIndex) -> None:
        """Should return only matching line when context_lines=0."""
        content = "line 1\nline 2\ntarget line\nline 4\nline 5"
        matches = lexical_index._extract_all_snippets(content, "target", context_lines=0)
        snippet = matches[0][0]
        assert snippet == "target line"
        assert "line 2" not in snippet
        assert "line 4" not in snippet

    def test_context_lines_expanded(self, lexical_index: LexicalIndex) -> None:
        """Should respect larger context_lines value."""
        content = "line 1\nline 2\nline 3\ntarget\nline 5\nline 6\nline 7"
        matches = lexical_index._extract_all_snippets(content, "target", context_lines=2)
        snippet = matches[0][0]
        # context_lines=2: 2 before + match + 2 after = 5 lines
        assert "line 2" in snippet
        assert "line 3" in snippet
        assert "target" in snippet
        assert "line 5" in snippet
        assert "line 6" in snippet

    def test_no_match_returns_first_lines(self, lexical_index: LexicalIndex) -> None:
        """Should return first lines when no match found."""
        content = "line 1\nline 2\nline 3\nline 4\nline 5"
        matches = lexical_index._extract_all_snippets(content, "nonexistent")
        assert len(matches) == 1
        assert matches[0][1] == 1  # line 1

    def test_case_insensitive_matching(self, lexical_index: LexicalIndex) -> None:
        """Should match case-insensitively."""
        content = "TARGET here\nTarGeT there\ntarget everywhere"
        matches = lexical_index._extract_all_snippets(content, "target")
        assert len(matches) == 3

    def test_boundary_at_file_start(self, lexical_index: LexicalIndex) -> None:
        """Should handle match at start of file without negative indexing."""
        content = "target first\nline 2\nline 3"
        matches = lexical_index._extract_all_snippets(content, "target", context_lines=2)
        assert len(matches) == 1
        assert matches[0][1] == 1

    def test_boundary_at_file_end(self, lexical_index: LexicalIndex) -> None:
        """Should handle match at end of file without overflow."""
        content = "line 1\nline 2\ntarget last"
        matches = lexical_index._extract_all_snippets(content, "target", context_lines=2)
        assert len(matches) == 1
        assert matches[0][1] == 3


class TestSearchMultipleOccurrences:
    """Tests for search returning multiple results per file."""

    def test_search_returns_all_line_occurrences(self, lexical_index: LexicalIndex) -> None:
        """Search should return one result per line occurrence, not per file."""
        content = """def foo():
    foo_helper()
    return foo_value

def bar():
    pass

def foo_again():
    foo_final()
"""
        lexical_index.add_file("multi.py", content, context_id=1)
        lexical_index.reload()

        results = lexical_index.search("foo")
        # "foo" appears on lines 1, 2, 3, 8, 9 (5 occurrences)
        assert len(results.results) >= 5
        # All results should be from the same file
        assert all(r.file_path == "multi.py" for r in results.results)
        # Should have different line numbers
        lines = [r.line for r in results.results]
        assert len(set(lines)) >= 5  # At least 5 distinct lines

    def test_search_multiple_files_multiple_occurrences(self, lexical_index: LexicalIndex) -> None:
        """Search should return all occurrences across multiple files."""
        lexical_index.add_file("file1.py", "target\nother\ntarget", context_id=1)
        lexical_index.add_file("file2.py", "target here\ntarget there", context_id=1)
        lexical_index.reload()

        results = lexical_index.search("target")
        # file1: lines 1, 3 (2 occurrences)
        # file2: lines 1, 2 (2 occurrences)
        # Total: 4 occurrences
        assert len(results.results) >= 4

        file1_results = [r for r in results.results if r.file_path == "file1.py"]
        file2_results = [r for r in results.results if r.file_path == "file2.py"]
        assert len(file1_results) >= 2
        assert len(file2_results) >= 2


class TestContextLinesParameter:
    """Tests for context_lines parameter in search methods."""

    def test_search_respects_context_lines(self, lexical_index: LexicalIndex) -> None:
        """Search should pass context_lines to snippet extraction."""
        content = "line 1\nline 2\nTARGET\nline 4\nline 5\nline 6"
        lexical_index.add_file("ctx.py", content, context_id=1)
        lexical_index.reload()

        # With context_lines=0, snippet should be just the matching line
        results_no_ctx = lexical_index.search("TARGET", context_lines=0)
        assert len(results_no_ctx.results) >= 1
        snippet_no_ctx = results_no_ctx.results[0].snippet
        assert "TARGET" in snippet_no_ctx
        # Should NOT include surrounding lines
        assert "line 2" not in snippet_no_ctx
        assert "line 4" not in snippet_no_ctx

        # With context_lines=2, snippet should include surrounding lines
        results_ctx = lexical_index.search("TARGET", context_lines=2)
        snippet_ctx = results_ctx.results[0].snippet
        assert "line 2" in snippet_ctx
        assert "TARGET" in snippet_ctx
        assert "line 4" in snippet_ctx

    def test_search_symbols_respects_context_lines(self, lexical_index: LexicalIndex) -> None:
        """search_symbols should respect context_lines parameter."""
        lexical_index.add_file(
            "syms.py",
            "# comment\nclass MySymbol:\n    pass\n# end",
            context_id=1,
            symbols=["MySymbol"],
        )
        lexical_index.reload()

        results = lexical_index.search_symbols("MySymbol", context_lines=0)
        assert len(results.results) >= 1

    def test_search_path_respects_context_lines(self, lexical_index: LexicalIndex) -> None:
        """search_path should respect context_lines parameter."""
        lexical_index.add_file("src/deep/path.py", "content", context_id=1)
        lexical_index.reload()

        results = lexical_index.search_path("deep", context_lines=0)
        assert len(results.results) >= 1


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


class TestStagedCommitEquivalence:
    """Tests that stage_file + commit_staged produces equivalent results to add_file."""

    def test_staged_content_searchable(self, temp_dir: Path) -> None:
        """Files indexed via stage_file + commit_staged should be searchable."""
        index = LexicalIndex(temp_dir / "staged_idx")

        index.stage_file("src/main.py", "def hello(): pass\n", context_id=1, symbols=["hello"])
        index.commit_staged()
        index.reload()

        results = index.search("hello")
        assert len(results.results) >= 1
        assert any("main.py" in r.file_path for r in results.results)

    def test_staged_symbols_searchable(self, temp_dir: Path) -> None:
        """Symbols indexed via stage_file should be searchable via search_symbols."""
        index = LexicalIndex(temp_dir / "staged_sym_idx")

        index.stage_file(
            "src/utils.py",
            "def foo(): pass\nclass Bar: pass\n",
            context_id=1,
            symbols=["foo", "Bar"],
        )
        index.commit_staged()
        index.reload()

        for name in ["foo", "Bar"]:
            results = index.search_symbols(name)
            assert len(results.results) >= 1

    def test_staged_vs_add_file_equivalence(self, temp_dir: Path) -> None:
        """stage_file + commit_staged should produce identical search results to add_file."""
        files = [
            ("src/a.py", "def alpha(): pass\nALPHA_CONST = 1\n", ["alpha"]),
            ("src/b.py", "class Beta:\n    def method(self): pass\n", ["Beta", "method"]),
            ("src/c.py", "import os\nGAMMA = os.getcwd()\n", ["GAMMA"]),
        ]

        # Index via add_file (old API)
        idx_add = LexicalIndex(temp_dir / "add_idx")
        for path, content, symbols in files:
            idx_add.add_file(path, content, context_id=1, symbols=symbols)
        idx_add.reload()

        # Index via stage_file + commit_staged (new API)
        idx_staged = LexicalIndex(temp_dir / "staged_idx2")
        for path, content, symbols in files:
            idx_staged.stage_file(path, content, context_id=1, symbols=symbols)
        idx_staged.commit_staged()
        idx_staged.reload()

        # Both should have same doc count
        assert idx_add.doc_count() == idx_staged.doc_count()

        # Content search should return same files
        for query in ["alpha", "Beta", "GAMMA", "os"]:
            add_results = idx_add.search(query)
            staged_results = idx_staged.search(query)
            add_paths = sorted(r.file_path for r in add_results.results)
            staged_paths = sorted(r.file_path for r in staged_results.results)
            assert add_paths == staged_paths, f"Mismatch for query '{query}'"

        # Symbol search should return same files
        for sym in ["alpha", "Beta", "GAMMA"]:
            add_results = idx_add.search_symbols(sym)
            staged_results = idx_staged.search_symbols(sym)
            add_paths = sorted(r.file_path for r in add_results.results)
            staged_paths = sorted(r.file_path for r in staged_results.results)
            assert add_paths == staged_paths, f"Symbol mismatch for '{sym}'"

    def test_staged_batch_single_commit(self, temp_dir: Path) -> None:
        """Multiple stage_file calls should be committed atomically in one commit."""
        index = LexicalIndex(temp_dir / "batch_idx")

        # Stage 5 files
        for i in range(5):
            index.stage_file(f"file_{i}.py", f"content_{i}\n", context_id=1)

        # Before commit: nothing visible
        index.reload()
        assert index.doc_count() == 0

        # After single commit: all 5 visible
        count = index.commit_staged()
        index.reload()

        assert count == 5
        assert index.doc_count() == 5

    def test_staged_context_id_filtering(self, temp_dir: Path) -> None:
        """Staged files should respect context_id for filtered searches."""
        index = LexicalIndex(temp_dir / "ctx_idx")

        index.stage_file("ctx1.py", "shared_term", context_id=1)
        index.stage_file("ctx2.py", "shared_term", context_id=2)
        index.commit_staged()
        index.reload()

        results = index.search("shared_term", context_id=1)
        assert all(r.context_id == 1 for r in results.results)

    def test_commit_staged_empty_is_noop(self, temp_dir: Path) -> None:
        """commit_staged with no staged files should return 0."""
        index = LexicalIndex(temp_dir / "empty_idx")

        count = index.commit_staged()
        assert count == 0
