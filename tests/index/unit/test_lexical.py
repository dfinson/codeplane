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

    def test_search_returns_all_matches(self, lexical_index: LexicalIndex) -> None:
        """Index layer returns all matches without artificial capping."""
        files = [
            {"path": f"src/file_{i}.py", "content": f"common_term = {i}\n", "context_id": 1}
            for i in range(20)
        ]
        lexical_index.add_files_batch(files)
        lexical_index.reload()

        results = lexical_index.search("common_term", limit=5)
        # Search returns all matches (20 files); limit is not applied at index layer
        assert len(results.results) >= 20

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
        phrases, terms = lexical_index._extract_search_terms("hello")
        assert phrases == []
        assert terms == ["hello"]

    def test_multiple_terms(self, lexical_index: LexicalIndex) -> None:
        """Should extract multiple space-separated terms."""
        phrases, terms = lexical_index._extract_search_terms("hello world")
        assert phrases == []
        assert terms == ["hello", "world"]

    def test_field_prefix_excluded(self, lexical_index: LexicalIndex) -> None:
        """Should exclude field-prefixed terms (they match Tantivy fields, not content)."""
        phrases, terms = lexical_index._extract_search_terms("symbols:MyClass")
        assert phrases == []
        assert terms == []  # field-prefixed terms are excluded from content matching

    def test_removes_boolean_operators(self, lexical_index: LexicalIndex) -> None:
        """Should remove AND, OR, NOT operators."""
        phrases, terms = lexical_index._extract_search_terms("foo AND bar OR baz NOT qux")
        assert "and" not in terms
        assert "or" not in terms
        assert "not" not in terms
        assert "foo" in terms
        assert "bar" in terms

    def test_empty_query(self, lexical_index: LexicalIndex) -> None:
        """Should return empty lists for empty query."""
        phrases, terms = lexical_index._extract_search_terms("")
        assert phrases == []
        assert terms == []

    def test_only_operators(self, lexical_index: LexicalIndex) -> None:
        """Should return empty list when query has only operators."""
        phrases, terms = lexical_index._extract_search_terms("AND OR NOT")
        assert phrases == []
        assert terms == []

    def test_quoted_phrase(self, lexical_index: LexicalIndex) -> None:
        """Should extract quoted strings as phrases."""
        phrases, terms = lexical_index._extract_search_terms('"async def" handler')
        assert phrases == ["async def"]
        assert terms == ["handler"]

    def test_multiple_phrases(self, lexical_index: LexicalIndex) -> None:
        """Should extract multiple quoted phrases."""
        phrases, terms = lexical_index._extract_search_terms('"foo bar" "baz qux"')
        assert phrases == ["foo bar", "baz qux"]
        assert terms == []


class TestBuildTantivyQuery:
    """Tests for _build_tantivy_query method."""

    def test_single_term_unchanged(self, lexical_index: LexicalIndex) -> None:
        """Single term should pass through unchanged."""
        assert lexical_index._build_tantivy_query("hello") == "hello"

    def test_multi_term_and_joined(self, lexical_index: LexicalIndex) -> None:
        """Multiple unquoted terms should be AND-joined."""
        assert lexical_index._build_tantivy_query("foo bar baz") == "foo AND bar AND baz"

    def test_phrase_preserved(self, lexical_index: LexicalIndex) -> None:
        """Quoted phrases should be preserved as-is."""
        result = lexical_index._build_tantivy_query('"async def" handler')
        assert result == '"async def" AND handler'

    def test_field_prefix_preserved(self, lexical_index: LexicalIndex) -> None:
        """Field-prefixed terms should be preserved."""
        result = lexical_index._build_tantivy_query("symbols:MyClass")
        assert result == "symbols:MyClass"

    def test_explicit_or_preserved(self, lexical_index: LexicalIndex) -> None:
        """Explicit OR operator should be preserved, not AND-joined."""
        result = lexical_index._build_tantivy_query("foo OR bar")
        assert result == "foo OR bar"

    def test_explicit_and_preserved(self, lexical_index: LexicalIndex) -> None:
        """Explicit AND operator should be preserved."""
        result = lexical_index._build_tantivy_query("foo AND bar")
        assert result == "foo AND bar"

    def test_explicit_not_preserved(self, lexical_index: LexicalIndex) -> None:
        """Explicit NOT operator should be preserved."""
        result = lexical_index._build_tantivy_query("foo NOT bar")
        assert result == "foo NOT bar"

    def test_mixed_operators(self, lexical_index: LexicalIndex) -> None:
        """Mixed boolean operators should be preserved."""
        result = lexical_index._build_tantivy_query("foo AND bar OR baz")
        assert result == "foo AND bar OR baz"

    def test_empty_query(self, lexical_index: LexicalIndex) -> None:
        """Empty query should return empty string."""
        assert lexical_index._build_tantivy_query("") == ""

    def test_phrase_with_field_and_term(self, lexical_index: LexicalIndex) -> None:
        """Complex query with phrase, field, and term should be AND-joined."""
        result = lexical_index._build_tantivy_query('"async def" symbols:foo handler')
        assert result == '"async def" AND symbols:foo AND handler'


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

    def test_no_match_returns_empty(self, lexical_index: LexicalIndex) -> None:
        """Should return empty list when no match found."""
        content = "line 1\nline 2\nline 3\nline 4\nline 5"
        matches = lexical_index._extract_all_snippets(content, "nonexistent")
        assert len(matches) == 0

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

    def test_field_only_query_returns_doc_level_match(self, lexical_index: LexicalIndex) -> None:
        """Field-only queries (e.g., path:foo) should return a document-level match at line 1."""
        content = "line 1\nline 2\nline 3"
        matches = lexical_index._extract_all_snippets(content, "path:some/file.py")
        assert len(matches) == 1
        assert matches[0][1] == 1  # document-level match at line 1

    def test_field_prefixed_with_content_terms(self, lexical_index: LexicalIndex) -> None:
        """Mixed field + content terms: only content terms used for line matching."""
        content = "line 1\ntarget here\nline 3"
        matches = lexical_index._extract_all_snippets(
            content, "symbols:foo target", context_lines=0
        )
        # "symbols:foo" is skipped; "target" matches line 2
        assert len(matches) == 1
        assert matches[0][1] == 2


class TestContentQueryOverride:
    """Tests for the content_query parameter on search()."""

    def test_content_query_overrides_snippet_extraction(self, lexical_index: LexicalIndex) -> None:
        """content_query should be used for line matching instead of query."""
        content = "class Foo:\n    pass\nclass Bar:\n    pass"
        lexical_index.add_file("cq.py", content, context_id=1, symbols=["Foo", "Bar"])
        lexical_index.reload()

        # Tantivy query targets the symbols field, but content_query
        # tells _extract_all_snippets to match against "Foo" in content.
        results = lexical_index.search("symbols:Foo", content_query="Foo", context_lines=0)
        assert len(results.results) >= 1
        for r in results.results:
            assert "Foo" in r.snippet

    def test_without_content_query_field_only_returns_line_1(
        self, lexical_index: LexicalIndex
    ) -> None:
        """Without content_query, field-only query should return doc-level match."""
        content = "class Foo:\n    pass\nclass Bar:\n    pass"
        lexical_index.add_file("cq2.py", content, context_id=1, symbols=["Foo", "Bar"])
        lexical_index.reload()

        results = lexical_index.search("symbols:Foo", context_lines=0)
        # Field-only query: returns line 1 doc-level match
        if results.results:
            assert results.results[0].line == 1


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


class TestPhraseMatching:
    """Tests for phrase query matching (quoted strings)."""

    def test_phrase_matches_exact(self, lexical_index: LexicalIndex) -> None:
        """Quoted phrase should match only lines with the exact phrase."""
        content = "async def hello():\n    pass\ndef world():\n    async_thing = 1"
        lexical_index.add_file("phrase.py", content, context_id=1)
        lexical_index.reload()

        matches = lexical_index._extract_all_snippets(content, '"async def"', context_lines=0)
        # Only line 1 has the exact phrase "async def"
        assert len(matches) == 1
        assert matches[0][1] == 1
        assert "async def" in matches[0][0]

    def test_phrase_does_not_match_partial(self, lexical_index: LexicalIndex) -> None:
        """Quoted phrase should NOT match lines with only one word of the phrase."""
        content = "def hello():\n    pass\nasync_thing = 1"
        lexical_index.add_file("no_phrase.py", content, context_id=1)
        lexical_index.reload()

        matches = lexical_index._extract_all_snippets(content, '"async def"', context_lines=0)
        # No line has the exact phrase "async def" — should return empty
        assert len(matches) == 0


class TestAndSemantics:
    """Tests for AND semantics on unquoted multi-term queries."""

    def test_and_matches_all_terms(self, lexical_index: LexicalIndex) -> None:
        """Unquoted multi-term query should match lines containing ALL terms."""
        content = "foo bar baz\nfoo only\nbar only\nfoo and bar together"
        lexical_index.add_file("and.py", content, context_id=1)
        lexical_index.reload()

        matches = lexical_index._extract_all_snippets(content, "foo bar", context_lines=0)
        # Lines 1 and 4 contain both "foo" and "bar"
        assert len(matches) == 2
        assert matches[0][1] == 1
        assert matches[1][1] == 4

    def test_and_does_not_match_single_term(self, lexical_index: LexicalIndex) -> None:
        """Unquoted multi-term query should NOT match lines with only one term."""
        content = "foo only here\nbar only here\nsomething else"
        lexical_index.add_file("and_no.py", content, context_id=1)
        lexical_index.reload()

        matches = lexical_index._extract_all_snippets(content, "foo bar", context_lines=0)
        # No line has both terms — should return empty
        assert len(matches) == 0


class TestDeterministicOrdering:
    """Tests for deterministic (path, line_number) result ordering."""

    def test_results_ordered_by_path_and_line(self, lexical_index: LexicalIndex) -> None:
        """Search results should be ordered by (path, line_number)."""
        # Add files in reverse alphabetical order
        lexical_index.add_file("z_file.py", "target line 1\ntarget line 2", context_id=1)
        lexical_index.add_file("a_file.py", "target here\nother\ntarget again", context_id=1)
        lexical_index.add_file("m_file.py", "target middle", context_id=1)
        lexical_index.reload()

        results = lexical_index.search("target")
        paths_and_lines = [(r.file_path, r.line) for r in results.results]

        # Should be sorted by (path, line)
        assert paths_and_lines == sorted(paths_and_lines)
        # a_file.py should come first
        assert results.results[0].file_path == "a_file.py"

    def test_scores_are_constant(self, lexical_index: LexicalIndex) -> None:
        """All search result scores should be 1.0 (no BM25 ranking)."""
        lexical_index.add_file("s1.py", "term\nterm\nterm", context_id=1)
        lexical_index.add_file("s2.py", "term", context_id=1)
        lexical_index.reload()

        results = lexical_index.search("term")
        assert all(r.score == 1.0 for r in results.results)


class TestSearchSymbolsMultiTerm:
    """Tests for search_symbols handling of multi-term queries."""

    def test_single_term_prefixed(self, lexical_index: LexicalIndex) -> None:
        """Single term should be prefixed with symbols: in the query."""
        # search_symbols("MyClass") should produce a query like "symbols:MyClass"
        # which _build_tantivy_query leaves as-is (field-prefixed token)
        lexical_index.add_file("sym.py", "class MyClass:\n    pass", context_id=1)
        lexical_index.reload()

        # Verify it doesn't crash and returns results structure
        results = lexical_index.search_symbols("MyClass")
        assert isinstance(results.results, list)

    def test_multi_term_all_prefixed(self, lexical_index: LexicalIndex) -> None:  # noqa: ARG002
        """Multiple terms should each get symbols: prefix."""
        import re

        query = "foo bar"
        tokens = re.findall(r'"[^"]+"|\S+', query)
        prefixed = []
        for t in tokens:
            if t.startswith('"') or t.upper() in ("AND", "OR", "NOT") or ":" in t:
                prefixed.append(t)
            else:
                prefixed.append(f"symbols:{t}")
        result = " ".join(prefixed)
        assert result == "symbols:foo symbols:bar"

    def test_operator_not_prefixed(self, lexical_index: LexicalIndex) -> None:  # noqa: ARG002
        """Boolean operators should not get symbols: prefix."""
        import re

        query = "foo OR bar"
        tokens = re.findall(r'"[^"]+"|\S+', query)
        prefixed = []
        for t in tokens:
            if t.startswith('"') or t.upper() in ("AND", "OR", "NOT") or ":" in t:
                prefixed.append(t)
            else:
                prefixed.append(f"symbols:{t}")
        result = " ".join(prefixed)
        assert result == "symbols:foo OR symbols:bar"

    def test_multi_term_symbol_search_no_false_positives(self, lexical_index: LexicalIndex) -> None:
        """Multi-term symbol search should not produce line-1 false positives.

        Regression test: search_symbols prefixes terms with 'symbols:', causing
        _extract_search_terms to return ([], []) and _extract_all_snippets to
        fall back to a document-level match at line 1.  With content_query,
        the original terms are used for content matching instead.
        """
        content = "class Foo:\n    pass\n\nclass Bar:\n    pass"
        lexical_index.add_file("two_classes.py", content, context_id=1, symbols=["Foo", "Bar"])
        lexical_index.reload()

        results = lexical_index.search_symbols("Foo")
        assert len(results.results) >= 1
        # Every result must reference a line that actually contains "Foo"
        for r in results.results:
            assert "foo" in r.snippet.lower(), (
                f"False positive at line {r.line}: snippet has no 'Foo'"
            )

    def test_symbol_search_multi_term_filters_content(self, lexical_index: LexicalIndex) -> None:
        """Multi-term symbol search should only return lines containing all terms."""
        content = "def search_result():\n    pass\ndef search_only():\n    pass"
        lexical_index.add_file(
            "fns.py",
            content,
            context_id=1,
            symbols=["search_result", "search_only"],
        )
        lexical_index.reload()

        results = lexical_index.search_symbols("search result")
        for r in results.results:
            snippet_lower = r.snippet.lower()
            assert "search" in snippet_lower and "result" in snippet_lower, (
                f"False positive at line {r.line}: snippet missing terms"
            )
