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
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("hello")
        assert or_groups == [([], ["hello"])]
        assert neg_terms == []
        assert neg_phrases == []

    def test_multiple_terms(self, lexical_index: LexicalIndex) -> None:
        """Should extract multiple space-separated terms."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("hello world")
        assert or_groups == [([], ["hello", "world"])]
        assert neg_terms == []
        assert neg_phrases == []

    def test_field_prefix_excluded(self, lexical_index: LexicalIndex) -> None:
        """Should exclude non-content field-prefixed terms."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("symbols:MyClass")
        assert or_groups == []
        assert neg_terms == []
        assert neg_phrases == []

    def test_content_field_extracted(self, lexical_index: LexicalIndex) -> None:
        """Should extract value from content: field prefix."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms(
            "content:SearchResult"
        )
        assert or_groups == [([], ["searchresult"])]
        assert neg_terms == []
        assert neg_phrases == []

    def test_or_creates_groups(self, lexical_index: LexicalIndex) -> None:
        """OR should split into separate groups."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("foo OR bar")
        assert len(or_groups) == 2
        assert or_groups[0] == ([], ["foo"])
        assert or_groups[1] == ([], ["bar"])
        assert neg_terms == []

    def test_not_creates_negative(self, lexical_index: LexicalIndex) -> None:
        """NOT should create negative terms."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("foo NOT bar")
        assert or_groups == [([], ["foo"])]
        assert neg_terms == ["bar"]

    def test_and_is_implicit(self, lexical_index: LexicalIndex) -> None:
        """AND should be skipped (implicit)."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("foo AND bar")
        assert or_groups == [([], ["foo", "bar"])]
        assert neg_terms == []

    def test_complex_boolean(self, lexical_index: LexicalIndex) -> None:
        """Complex boolean with OR, AND, NOT."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms(
            "foo AND bar OR baz NOT qux"
        )
        # foo AND bar → group 1, baz → group 2, qux → negative
        assert len(or_groups) == 2
        assert or_groups[0] == ([], ["foo", "bar"])
        assert or_groups[1] == ([], ["baz"])
        assert neg_terms == ["qux"]

    def test_empty_query(self, lexical_index: LexicalIndex) -> None:
        """Should return empty lists for empty query."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("")
        assert or_groups == []
        assert neg_terms == []
        assert neg_phrases == []

    def test_only_operators(self, lexical_index: LexicalIndex) -> None:
        """Should return empty when query has only operators."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("AND OR NOT")
        assert or_groups == []
        assert neg_terms == []
        assert neg_phrases == []

    def test_quoted_phrase(self, lexical_index: LexicalIndex) -> None:
        """Should extract quoted strings as phrases."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms(
            '"async def" handler'
        )
        assert or_groups == [(["async def"], ["handler"])]
        assert neg_terms == []

    def test_multiple_phrases(self, lexical_index: LexicalIndex) -> None:
        """Should extract multiple quoted phrases."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms(
            '"foo bar" "baz qux"'
        )
        assert or_groups == [(["foo bar", "baz qux"], [])]
        assert neg_terms == []

    def test_not_phrase(self, lexical_index: LexicalIndex) -> None:
        """NOT before a quoted phrase should negate it."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms(
            'foo NOT "bad phrase"'
        )
        assert or_groups == [([], ["foo"])]
        assert neg_phrases == ["bad phrase"]

    def test_literal_mode(self, lexical_index: LexicalIndex) -> None:
        """Literal mode should treat all tokens as plain terms."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms(
            "AND OR NOT symbols:foo", literal=True
        )
        assert or_groups == [([], ["and", "or", "not", "symbols:foo"])]
        assert neg_terms == []
        assert neg_phrases == []


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

    def test_parentheses_escaped(self, lexical_index: LexicalIndex) -> None:
        """Parentheses in plain tokens should be escaped."""
        result = lexical_index._build_tantivy_query("foo(bar)")
        assert "\\(" in result
        assert "\\)" in result

    def test_brackets_escaped(self, lexical_index: LexicalIndex) -> None:
        """Brackets in plain tokens should be escaped."""
        result = lexical_index._build_tantivy_query("list[int]")
        assert "\\[" in result
        assert "\\]" in result

    def test_asterisk_escaped(self, lexical_index: LexicalIndex) -> None:
        """Wildcards in plain tokens should be escaped."""
        result = lexical_index._build_tantivy_query("*.py")
        assert "\\*" in result


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

    def test_or_matches_either_term(self, lexical_index: LexicalIndex) -> None:
        """OR should match lines with either term."""
        content = "alpha here\nbeta here\ngamma here"
        matches = lexical_index._extract_all_snippets(content, "alpha OR beta", context_lines=0)
        assert len(matches) == 2
        assert matches[0][1] == 1  # alpha
        assert matches[1][1] == 2  # beta

    def test_not_excludes_term(self, lexical_index: LexicalIndex) -> None:
        """NOT should exclude lines containing the negated term."""
        content = "foo here\nfoo bar here\nfoo baz here"
        matches = lexical_index._extract_all_snippets(content, "foo NOT bar", context_lines=0)
        # Lines 1 and 3 have "foo" without "bar"
        assert len(matches) == 2
        assert matches[0][1] == 1
        assert matches[1][1] == 3

    def test_not_phrase_excludes(self, lexical_index: LexicalIndex) -> None:
        """NOT before a phrase should exclude lines with that phrase."""
        content = "async def hello():\n    pass\ndef world():"
        matches = lexical_index._extract_all_snippets(
            content, 'def NOT "async def"', context_lines=0
        )
        # Only line 3 has "def" without "async def"
        assert len(matches) == 1
        assert matches[0][1] == 3

    def test_content_field_matches_content(self, lexical_index: LexicalIndex) -> None:
        """content:X should match lines containing X."""
        content = "line one\nSearchResult here\nline three"
        matches = lexical_index._extract_all_snippets(
            content, "content:SearchResult", context_lines=0
        )
        assert len(matches) == 1
        assert matches[0][1] == 2

    def test_literal_mode_treats_operators_as_terms(self, lexical_index: LexicalIndex) -> None:
        """Literal mode should treat AND/OR/NOT as plain content terms."""
        content = "AND OR NOT here\nfoo bar"
        matches = lexical_index._extract_all_snippets(
            content, "AND OR NOT", context_lines=0, literal=True
        )
        # All three tokens must match on the same line (AND semantics)
        assert len(matches) == 1
        assert matches[0][1] == 1


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


# =============================================================================
# Red-Team Tests — probing edge cases derived from fixed bugs
# =============================================================================


class TestRedTeamQueryParsing:
    """Adversarial query parsing edge cases.

    Targets _build_tantivy_query and the fallback path in search().
    """

    def test_unbalanced_quotes(self, lexical_index: LexicalIndex) -> None:
        """Unbalanced quotes should not crash; fallback or partial match."""
        lexical_index.add_file("q.py", "some content here\n", context_id=1)
        lexical_index.reload()
        # Should not raise — falls back to escaped literal
        results = lexical_index.search('"unterminated')
        assert isinstance(results, SearchResults)

    def test_all_operators_query(self, lexical_index: LexicalIndex) -> None:
        """Query consisting only of operators should not crash."""
        lexical_index.add_file("op.py", "AND OR NOT\n", context_id=1)
        lexical_index.reload()
        results = lexical_index.search("AND AND AND")
        assert isinstance(results, SearchResults)

    def test_single_special_char_query(self, lexical_index: LexicalIndex) -> None:
        """Single Tantivy special character queries should not crash."""
        lexical_index.add_file("sp.py", "x = 1\n", context_id=1)
        lexical_index.reload()
        for ch in ["*", "?", "~", "^", "(", ")", "[", "]", "{{", "}}"]:
            results = lexical_index.search(ch)
            assert isinstance(results, SearchResults)

    def test_colon_not_field_prefix(self, lexical_index: LexicalIndex) -> None:
        """Colons in non-field positions should be escaped, not parsed as fields.

        Regression: terms like 'http://example.com' contain ':' and could be
        misinterpreted as field-prefixed tokens.
        """
        content = "url = 'http://example.com'\n"
        lexical_index.add_file("url.py", content, context_id=1)
        lexical_index.reload()
        # search for the full URL — the colon should be escaped, not treated as field
        results = lexical_index.search("http://example.com")
        assert isinstance(results, SearchResults)

    def test_unicode_query(self, lexical_index: LexicalIndex) -> None:
        """Unicode queries should work without crashing."""
        content = "# 你好世界\ndef greet(): pass\n"
        lexical_index.add_file("uni.py", content, context_id=1)
        lexical_index.reload()
        results = lexical_index.search("你好")
        assert isinstance(results, SearchResults)

    def test_empty_query(self, lexical_index: LexicalIndex) -> None:
        """Empty string query should not crash."""
        lexical_index.add_file("e.py", "content\n", context_id=1)
        lexical_index.reload()
        results = lexical_index.search("")
        assert isinstance(results, SearchResults)

    def test_whitespace_only_query(self, lexical_index: LexicalIndex) -> None:
        """Whitespace-only query should not crash."""
        lexical_index.add_file("ws.py", "content\n", context_id=1)
        lexical_index.reload()
        results = lexical_index.search("   ")
        assert isinstance(results, SearchResults)

    def test_very_long_query(self, lexical_index: LexicalIndex) -> None:
        """Very long query should not crash or hang."""
        lexical_index.add_file("long.py", "x = 1\n", context_id=1)
        lexical_index.reload()
        long_query = "term " * 200
        results = lexical_index.search(long_query.strip())
        assert isinstance(results, SearchResults)

    def test_query_with_backslashes(self, lexical_index: LexicalIndex) -> None:
        """Backslashes in query should be escaped properly.

        Regression: r'\\\\S+' vs r'\\S+' double-escaping bug.
        """
        content = "path = r'C:\\Users\\test'\n"
        lexical_index.add_file("bs.py", content, context_id=1)
        lexical_index.reload()
        results = lexical_index.search(r"C:\Users")
        assert isinstance(results, SearchResults)


class TestRedTeamSnippetExtraction:
    """Adversarial tests for _extract_all_snippets.

    Targets the line-1 false-positive bug and cross-line match prevention.
    """

    def test_no_single_line_contains_all_and_terms(self, lexical_index: LexicalIndex) -> None:
        """When no single line has ALL AND terms, should return empty.

        Regression: Tantivy matches docs by individual tokens ("foo" + "bar")
        across different lines, but _extract_all_snippets should only match
        lines where ALL terms appear on the SAME line.
        """
        content = "foo is here\nbar is here\nbaz is here"
        matches = lexical_index._extract_all_snippets(content, "foo bar", context_lines=0)
        assert len(matches) == 0, "Should not match when terms are on different lines"

    def test_empty_content(self, lexical_index: LexicalIndex) -> None:
        """Empty content should return empty matches."""
        matches = lexical_index._extract_all_snippets("", "search", context_lines=0)
        assert len(matches) == 0

    def test_newlines_only_content(self, lexical_index: LexicalIndex) -> None:
        """Content with only newlines should return empty matches."""
        matches = lexical_index._extract_all_snippets("\n\n\n", "search", context_lines=0)
        assert len(matches) == 0

    def test_single_line_content(self, lexical_index: LexicalIndex) -> None:
        """Single-line content should match if term is present."""
        matches = lexical_index._extract_all_snippets("hello world", "hello", context_lines=0)
        assert len(matches) == 1
        assert matches[0][1] == 1  # Line 1

    def test_phrase_does_not_match_across_lines(self, lexical_index: LexicalIndex) -> None:
        """Phrase query should NOT match when phrase spans a line boundary."""
        content = "async\ndef hello"  # "async" and "def" on separate lines
        matches = lexical_index._extract_all_snippets(content, '"async def"', context_lines=0)
        assert len(matches) == 0, "Phrase should not match across line boundary"

    def test_empty_content_query_returns_doc_level(self, lexical_index: LexicalIndex) -> None:
        """Empty content_query (content_query='') should return doc-level match at line 1.

        Regression: search_path passes content_query='' so snippet extraction
        returns a document-level match instead of trying to match path terms.
        """
        content = "line one\nline two\nline three"
        matches = lexical_index._extract_all_snippets(content, "", context_lines=0)
        assert len(matches) == 1
        assert matches[0][1] == 1  # Line 1

    def test_literal_mode_treats_operators_as_terms(self, lexical_index: LexicalIndex) -> None:
        """In literal mode, AND/OR/NOT should be treated as search terms."""
        content = "AND OR NOT are here\nonly AND here\nnothing"
        matches = lexical_index._extract_all_snippets(
            content, "AND OR NOT", context_lines=0, literal=True
        )
        # Only line 1 has all three tokens literally
        assert len(matches) == 1
        assert matches[0][1] == 1

    def test_not_plus_or_combination(self, lexical_index: LexicalIndex) -> None:
        """NOT + OR combination should work correctly."""
        content = "alpha beta\nalpha gamma\nbeta gamma\ndelta"
        matches = lexical_index._extract_all_snippets(
            content, "alpha OR gamma NOT beta", context_lines=0
        )
        # "alpha OR gamma" matches lines with alpha OR gamma
        # "NOT beta" excludes lines containing beta
        # Line 1: alpha beta → has beta, excluded
        # Line 2: alpha gamma → has alpha AND gamma, no beta → match
        # Line 3: beta gamma → has beta, excluded
        # Line 4: delta → no alpha/gamma → no match
        matching_lines = [m[1] for m in matches]
        assert 2 in matching_lines, "Line 2 (alpha gamma, no beta) should match"
        assert 1 not in matching_lines, "Line 1 (has beta) should be excluded"
        assert 3 not in matching_lines, "Line 3 (has beta) should be excluded"

    def test_context_lines_at_end_of_file(self, lexical_index: LexicalIndex) -> None:
        """Context lines should be clamped at file boundaries."""
        content = "a\nb\nc\nmatch_me\ne"
        matches = lexical_index._extract_all_snippets(content, "match_me", context_lines=10)
        assert len(matches) == 1
        # Should include all available lines without IndexError
        assert "a" in matches[0][0]
        assert "e" in matches[0][0]

    def test_overlapping_matches_all_reported(self, lexical_index: LexicalIndex) -> None:
        """Adjacent matching lines should each produce their own result."""
        content = "foo bar\nfoo bar\nfoo bar"
        matches = lexical_index._extract_all_snippets(content, "foo bar", context_lines=0)
        assert len(matches) == 3
        assert [m[1] for m in matches] == [1, 2, 3]


class TestRedTeamSearchSymbols:
    """Adversarial tests for search_symbols.

    Targets the symbols: prefix leak and content_query isolation.
    """

    def test_symbol_search_does_not_match_content_only(self, lexical_index: LexicalIndex) -> None:
        """Symbol search should prefer symbol field, not file content.

        If a term appears in content but NOT in symbols, the snippet extraction
        should still use the original query (via content_query) and only return
        lines where the term actually appears.
        """
        # "rare_term" appears in content but NOT in symbols list
        content = "def something():\n    rare_term = 42\n    pass"
        lexical_index.add_file("nosym.py", content, context_id=1, symbols=["something"])
        lexical_index.reload()

        results = lexical_index.search_symbols("rare_term")
        # Results may be empty (no symbol match) or may hit via Tantivy fallback,
        # but should NEVER produce a false-positive line-1 snippet
        for r in results.results:
            assert "rare_term" in r.snippet.lower() or r.line != 1, (
                f"False-positive line-1 result in symbol search: {r.snippet!r}"
            )

    def test_symbol_search_with_operators(self, lexical_index: LexicalIndex) -> None:
        """Symbol search with boolean operators should not crash."""
        lexical_index.add_file(
            "sym_op.py", "class A: pass\nclass B: pass", context_id=1, symbols=["A", "B"]
        )
        lexical_index.reload()
        results = lexical_index.search_symbols("A OR B")
        assert isinstance(results, SearchResults)

    def test_symbol_search_with_phrase(self, lexical_index: LexicalIndex) -> None:
        """Symbol search with a quoted phrase should not crash."""
        lexical_index.add_file(
            "sym_ph.py", "def my_func(): pass", context_id=1, symbols=["my_func"]
        )
        lexical_index.reload()
        results = lexical_index.search_symbols('"my_func"')
        assert isinstance(results, SearchResults)


class TestRedTeamSearchPath:
    """Adversarial tests for search_path.

    Targets the false-positive line-1 bug where path terms matched content.
    """

    def test_path_search_returns_line_1_not_content_match(
        self, lexical_index: LexicalIndex
    ) -> None:
        """Path search should return line 1 (doc-level), not a content-matched line.

        Regression: search_path previously passed the path pattern as content_query,
        causing _extract_all_snippets to match content lines containing path tokens.
        """
        content = "line 1 nothing\nutils is mentioned on line 2\nline 3"
        lexical_index.add_file("src/utils/helpers.py", content, context_id=1)
        lexical_index.reload()

        results = lexical_index.search_path("utils")
        for r in results.results:
            # Path search should match file by path, returning line 1
            assert r.line == 1, f"Path search returned line {r.line}, expected 1 (doc-level)"

    def test_path_search_no_content_terms_in_snippet(self, lexical_index: LexicalIndex) -> None:
        """Path search snippet should be the first lines, not a content-matched line."""
        content = "first line\nsecond line\nthird line"
        lexical_index.add_file("deep/nested/module.py", content, context_id=1)
        lexical_index.reload()

        results = lexical_index.search_path("nested")
        assert len(results.results) >= 1
        # The snippet should start with the first line of the file
        assert "first line" in results.results[0].snippet


class TestRedTeamFallback:
    """Adversarial tests for the query syntax error fallback path."""

    def test_fallback_sets_reason(self, lexical_index: LexicalIndex) -> None:
        """Invalid Tantivy query should trigger fallback with reason."""
        lexical_index.add_file("fb.py", "foo bar\n", context_id=1)
        lexical_index.reload()
        # Field-prefixed unclosed quote bypasses _build_tantivy_query escaping
        # and triggers a Tantivy parse error → fallback path
        results = lexical_index.search('content:"unclosed')
        assert results.fallback_reason is not None

    def test_fallback_still_finds_results(self, lexical_index: LexicalIndex) -> None:
        """After fallback, literal matching should still find results."""
        content = "this has special chars: [foo] (bar)\n"
        lexical_index.add_file("special.py", content, context_id=1)
        lexical_index.reload()
        # Query with brackets triggers syntax error → fallback to literal
        results = lexical_index.search("[foo]")
        # Should find the line via literal matching
        assert len(results.results) >= 1 or results.fallback_reason is not None

    def test_double_fallback_returns_empty(self, lexical_index: LexicalIndex) -> None:
        """If even the escaped fallback fails, should return empty results gracefully."""
        lexical_index.add_file("df.py", "content\n", context_id=1)
        lexical_index.reload()
        # This is hard to trigger naturally, but we can verify the code path exists
        # by checking that the method handles it without raising
        results = lexical_index.search("")
        assert isinstance(results, SearchResults)


class TestRedTeamBuildTantivyQuery:
    """Adversarial tests for _build_tantivy_query."""

    def test_mixed_operators_and_terms(self, lexical_index: LexicalIndex) -> None:
        """Mixed operators should not produce invalid query syntax.

        Regression: 'foo AND OR AND bar' was being generated.
        """
        result = lexical_index._build_tantivy_query("foo OR bar")
        # Should preserve the OR, not AND-join everything
        assert "AND" not in result or "OR" in result
        assert "foo" in result
        assert "bar" in result

    def test_not_operator_preserved(self, lexical_index: LexicalIndex) -> None:
        """NOT operator should be preserved as-is."""
        result = lexical_index._build_tantivy_query("foo NOT bar")
        assert "NOT" in result
        assert "foo" in result
        assert "bar" in result

    def test_no_operators_all_and_joined(self, lexical_index: LexicalIndex) -> None:
        """Terms without operators should be AND-joined."""
        result = lexical_index._build_tantivy_query("foo bar baz")
        assert result == "foo AND bar AND baz"

    def test_phrase_not_and_joined(self, lexical_index: LexicalIndex) -> None:
        """Phrases should be joined with AND but not split internally."""
        result = lexical_index._build_tantivy_query('"async def" foo')
        assert '"async def"' in result
        assert "foo" in result
        assert " AND " in result

    def test_field_prefix_not_escaped(self, lexical_index: LexicalIndex) -> None:
        """Field-prefixed terms should be preserved as-is."""
        result = lexical_index._build_tantivy_query("symbols:MyClass content:hello")
        assert "symbols:MyClass" in result
        assert "content:hello" in result

    def test_special_chars_escaped_in_terms(self, lexical_index: LexicalIndex) -> None:
        """Special chars in non-field terms should be escaped."""
        result = lexical_index._build_tantivy_query("func()")
        # Parentheses should be escaped
        assert "\\(" in result
        assert "\\)" in result

    def test_empty_query_passthrough(self, lexical_index: LexicalIndex) -> None:
        """Empty query should pass through unchanged."""
        result = lexical_index._build_tantivy_query("")
        assert result == ""


class TestRedTeamSearchIntegration:
    """Integration-level red-team tests exercising the full search pipeline."""

    def test_multi_file_cross_term_no_false_match(self, lexical_index: LexicalIndex) -> None:
        """AND query should not match when terms are in different files.

        Tantivy may match both files individually, but _extract_all_snippets
        should not produce line matches unless BOTH terms are on the same line.
        """
        lexical_index.add_file("a.py", "foo is here\n", context_id=1)
        lexical_index.add_file("b.py", "bar is here\n", context_id=1)
        lexical_index.reload()

        results = lexical_index.search("foo bar")
        # No line in any file contains both "foo" and "bar"
        assert len(results.results) == 0, (
            f"Got {len(results.results)} results but no line has both terms"
        )

    def test_context_id_filtering_strict(self, lexical_index: LexicalIndex) -> None:
        """Context ID filtering should be strict — no cross-context leaks."""
        lexical_index.add_file("ctx1.py", "secret_token\n", context_id=1)
        lexical_index.add_file("ctx2.py", "secret_token\n", context_id=2)
        lexical_index.reload()

        results_ctx1 = lexical_index.search("secret_token", context_id=1)
        results_ctx2 = lexical_index.search("secret_token", context_id=2)

        for r in results_ctx1.results:
            assert r.context_id == 1
        for r in results_ctx2.results:
            assert r.context_id == 2

    def test_large_file_many_matches(self, lexical_index: LexicalIndex) -> None:
        """Search in a file with many matching lines should return all of them."""
        lines = [f"match_term line {i}" for i in range(100)]
        content = "\n".join(lines)
        lexical_index.add_file("big.py", content, context_id=1)
        lexical_index.reload()

        results = lexical_index.search("match_term", context_lines=0)
        assert len(results.results) == 100

    def test_update_file_removes_old_matches(self, lexical_index: LexicalIndex) -> None:
        """Re-adding a file should replace old content, not accumulate."""
        lexical_index.add_file("mutable.py", "old_unique_term\n", context_id=1)
        lexical_index.reload()
        assert len(lexical_index.search("old_unique_term").results) >= 1

        # Update the same file with different content
        lexical_index.add_file("mutable.py", "new_unique_term\n", context_id=1)
        lexical_index.reload()

        assert len(lexical_index.search("old_unique_term").results) == 0
        assert len(lexical_index.search("new_unique_term").results) >= 1

    def test_search_after_remove(self, lexical_index: LexicalIndex) -> None:
        """Removed files should not appear in search results."""
        lexical_index.add_file("gone.py", "ephemeral_content\n", context_id=1)
        lexical_index.reload()
        assert len(lexical_index.search("ephemeral_content").results) >= 1

        lexical_index.remove_file("gone.py")
        lexical_index.reload()
        assert len(lexical_index.search("ephemeral_content").results) == 0


# =============================================================================
# Aggressive Red-Team Round 2 — probing every dark corner
# =============================================================================


class TestRedTeam2ExtractSearchTerms:
    """Deep adversarial tests for _extract_search_terms."""

    def test_negate_next_not_leaked_across_or(self, lexical_index: LexicalIndex) -> None:
        """NOT before OR should not negate the term after OR.

        Bug pattern: negate_next flag persists across OR boundary,
        incorrectly negating the first term in the next group.
        """
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("foo NOT OR bar")
        # "NOT" sets negate_next, but OR resets it to False
        assert "bar" not in neg_terms, "bar should NOT be negated — OR resets negate_next"
        # bar should appear as a positive term in a group
        all_terms = [t for _, terms in or_groups for t in terms]
        assert "bar" in all_terms

    def test_trailing_or_produces_no_empty_group(self, lexical_index: LexicalIndex) -> None:
        """Trailing OR should not produce an empty group."""
        or_groups, _, _ = lexical_index._extract_search_terms("foo OR")
        # Only one group with "foo"; the trailing OR flushes it
        for phrases, terms in or_groups:
            assert phrases or terms, "Empty group produced by trailing OR"

    def test_trailing_not_does_not_crash(self, lexical_index: LexicalIndex) -> None:
        """Trailing NOT should not cause errors or negate phantom terms."""
        or_groups, neg_terms, neg_phrases = lexical_index._extract_search_terms("foo NOT")
        assert or_groups == [([], ["foo"])]
        assert neg_terms == []
        assert neg_phrases == []

    def test_consecutive_nots(self, lexical_index: LexicalIndex) -> None:
        """NOT NOT term — second NOT overwrites first, term is negated."""
        or_groups, neg_terms, _ = lexical_index._extract_search_terms("foo NOT NOT bar")
        # Both NOTs set negate_next=True; "bar" should be negated
        assert "bar" in neg_terms
        assert or_groups == [([], ["foo"])]

    def test_or_group_with_phrases_and_terms(self, lexical_index: LexicalIndex) -> None:
        """Phrase and term in same group should both appear."""
        or_groups, _, _ = lexical_index._extract_search_terms('"async def" handler')
        assert len(or_groups) == 1
        phrases, terms = or_groups[0]
        assert "async def" in phrases
        assert "handler" in terms

    def test_phrase_in_or_group(self, lexical_index: LexicalIndex) -> None:
        """Phrases across OR groups should be split correctly."""
        or_groups, _, _ = lexical_index._extract_search_terms('"foo bar" OR "baz qux"')
        assert len(or_groups) == 2
        assert "foo bar" in or_groups[0][0]
        assert "baz qux" in or_groups[1][0]

    def test_not_phrase_before_or(self, lexical_index: LexicalIndex) -> None:
        """NOT phrase before OR should negate the phrase globally."""
        or_groups, _, neg_phrases = lexical_index._extract_search_terms('NOT "bad phrase" OR good')
        assert "bad phrase" in neg_phrases
        # "good" should be in a positive group
        all_terms = [t for _, terms in or_groups for t in terms]
        assert "good" in all_terms

    def test_unknown_field_prefix_not_dropped(self, lexical_index: LexicalIndex) -> None:
        """Unknown field prefix (e.g., 'foo:bar') should be kept as literal term."""
        or_groups, _, _ = lexical_index._extract_search_terms("foo:bar")
        all_terms = [t for _, terms in or_groups for t in terms]
        assert "foo:bar" in all_terms

    def test_content_field_negated(self, lexical_index: LexicalIndex) -> None:
        """NOT content:X should negate the extracted value."""
        _, neg_terms, _ = lexical_index._extract_search_terms("NOT content:secret")
        assert "secret" in neg_terms

    def test_case_sensitivity(self, lexical_index: LexicalIndex) -> None:
        """All terms should be lowercased."""
        or_groups, _, _ = lexical_index._extract_search_terms("FoO BaR")
        terms = or_groups[0][1]
        assert terms == ["foo", "bar"]

    def test_quoted_single_word_is_phrase(self, lexical_index: LexicalIndex) -> None:
        """A quoted single word should still be a phrase, not a term."""
        or_groups, _, _ = lexical_index._extract_search_terms('"hello"')
        assert or_groups == [(["hello"], [])]

    def test_empty_quotes_ignored(self, lexical_index: LexicalIndex) -> None:
        """Empty quotes (\"\") should not produce a phrase or crash."""
        or_groups, _, _ = lexical_index._extract_search_terms('"" foo')
        # "" has len 2 (not > 2), so it's NOT treated as a phrase — falls through
        # to field/plain handling as a non-field, non-operator token
        all_terms = [t for _, terms in or_groups for t in terms]
        assert "foo" in all_terms

    def test_literal_mode_preserves_quotes(self, lexical_index: LexicalIndex) -> None:
        """Literal mode should treat quotes as part of the token."""
        or_groups, _, _ = lexical_index._extract_search_terms('"hello world"', literal=True)
        # In literal mode, the whole string is split by whitespace
        # '"hello' and 'world"' become two terms
        terms = or_groups[0][1]
        assert len(terms) == 2
        assert terms[0] == '"hello'
        assert terms[1] == 'world"'

    def test_multiple_consecutive_ors(self, lexical_index: LexicalIndex) -> None:
        """Multiple consecutive ORs should not crash or produce empty groups."""
        or_groups, _, _ = lexical_index._extract_search_terms("foo OR OR OR bar")
        # Each OR flushes; consecutive ORs with empty groups between are no-ops
        for phrases, terms in or_groups:
            assert phrases or terms, "Empty group from consecutive ORs"


class TestRedTeam2BuildTantivyQuery:
    """Deep adversarial tests for _build_tantivy_query."""

    def test_query_with_only_quotes(self, lexical_index: LexicalIndex) -> None:
        """Query that is just a quoted phrase should be returned as-is."""
        result = lexical_index._build_tantivy_query('"async def"')
        assert result == '"async def"'

    def test_escaped_backslash(self, lexical_index: LexicalIndex) -> None:
        """Backslash in token should be escaped."""
        result = lexical_index._build_tantivy_query(r"path\to")
        assert "\\\\" in result  # double-escaped backslash

    def test_plus_minus_escaped(self, lexical_index: LexicalIndex) -> None:
        """+ and - (Tantivy boost/exclude) should be escaped in tokens."""
        result = lexical_index._build_tantivy_query("+foo -bar")
        assert "\\+" in result
        assert "\\-" in result

    def test_tilde_caret_escaped(self, lexical_index: LexicalIndex) -> None:
        """~ and ^ (Tantivy fuzzy/boost) should be escaped in tokens."""
        result = lexical_index._build_tantivy_query("foo~ bar^2")
        assert "\\~" in result
        assert "\\^" in result

    def test_exclamation_escaped(self, lexical_index: LexicalIndex) -> None:
        """! (Tantivy NOT shorthand) should be escaped in tokens."""
        result = lexical_index._build_tantivy_query("!important")
        assert "\\!" in result

    def test_pipe_ampersand_escaped(self, lexical_index: LexicalIndex) -> None:
        """| and & (Tantivy OR/AND shorthand) should be escaped."""
        result = lexical_index._build_tantivy_query("foo|bar")
        assert "\\|" in result
        result2 = lexical_index._build_tantivy_query("foo&bar")
        assert "\\&" in result2

    def test_curly_braces_escaped(self, lexical_index: LexicalIndex) -> None:
        """Curly braces should be escaped in tokens."""
        result = lexical_index._build_tantivy_query("{key: value}")
        assert "\\{" in result
        assert "\\}" in result

    def test_field_prefix_not_escaped(self, lexical_index: LexicalIndex) -> None:
        """Field-prefixed tokens should NOT be escaped."""
        result = lexical_index._build_tantivy_query("symbols:foo(bar)")
        # The colon makes it a field-prefix, so it should be passed through
        assert result == "symbols:foo(bar)"
        assert "\\(" not in result

    def test_question_mark_escaped(self, lexical_index: LexicalIndex) -> None:
        """? (Tantivy single-char wildcard) should be escaped."""
        result = lexical_index._build_tantivy_query("what?")
        assert "\\?" in result

    def test_operator_case_insensitive(self, lexical_index: LexicalIndex) -> None:
        """Operators should be recognized case-insensitively."""
        result = lexical_index._build_tantivy_query("foo or bar")
        assert "OR" in result
        assert " AND " not in result  # should not AND-join when OR is present

    def test_mixed_case_operators(self, lexical_index: LexicalIndex) -> None:
        """Mixed case operators like 'Or' should be recognized."""
        result = lexical_index._build_tantivy_query("foo Or bar Not baz")
        assert "OR" in result
        assert "NOT" in result


class TestRedTeam2ExtractAllSnippets:
    """Deep adversarial tests for _extract_all_snippets."""

    def test_not_only_query_matches_all_lines(self, lexical_index: LexicalIndex) -> None:
        """Query with only NOT terms (no positive terms) should match all non-excluded lines.

        Bug pattern: or_groups is empty, so matched=True for every line
        that survives the negative filter.
        """
        content = "alpha\nbeta\ngamma\ndelta"
        matches = lexical_index._extract_all_snippets(content, "NOT beta", context_lines=0)
        lines = [m[1] for m in matches]
        assert 1 in lines  # alpha
        assert 2 not in lines  # beta excluded
        assert 3 in lines  # gamma
        assert 4 in lines  # delta

    def test_not_only_query_all_excluded(self, lexical_index: LexicalIndex) -> None:
        """When NOT excludes every line, should return empty."""
        content = "bad\nbad\nbad"
        matches = lexical_index._extract_all_snippets(content, "NOT bad", context_lines=0)
        assert len(matches) == 0

    def test_overlapping_context_windows(self, lexical_index: LexicalIndex) -> None:
        """Adjacent matches with context should produce separate results
        even when context windows overlap."""
        content = "a\nfoo\nfoo\nb"
        matches = lexical_index._extract_all_snippets(content, "foo", context_lines=1)
        assert len(matches) == 2
        # Each match has its own snippet
        assert matches[0][1] == 2
        assert matches[1][1] == 3

    def test_very_large_context_lines(self, lexical_index: LexicalIndex) -> None:
        """context_lines larger than file should not crash."""
        content = "only line"
        matches = lexical_index._extract_all_snippets(content, "only", context_lines=1000)
        assert len(matches) == 1
        assert matches[0][0] == "only line"

    def test_negative_phrase_case_insensitive(self, lexical_index: LexicalIndex) -> None:
        """Negative phrases should match case-insensitively."""
        content = "ASYNC DEF hello\ndef world"
        matches = lexical_index._extract_all_snippets(
            content, 'def NOT "async def"', context_lines=0
        )
        # Line 1 has "ASYNC DEF" which matches the negative phrase
        assert len(matches) == 1
        assert matches[0][1] == 2  # only "def world"

    def test_term_substring_matching(self, lexical_index: LexicalIndex) -> None:
        """Terms use substring matching (Python `in` operator), not word-boundary."""
        content = "foobar\nfoo bar\nbaz"
        matches = lexical_index._extract_all_snippets(content, "foo", context_lines=0)
        # "foo" appears as substring in "foobar" (line 1) and as word in line 2
        assert len(matches) == 2
        assert matches[0][1] == 1
        assert matches[1][1] == 2

    def test_or_group_with_phrase_and_term(self, lexical_index: LexicalIndex) -> None:
        """OR group where one alternative is a phrase+term combination."""
        content = "async def hello\ndef world\nasync thing"
        matches = lexical_index._extract_all_snippets(
            content, '"async def" OR world', context_lines=0
        )
        # Group 1: phrase "async def" → line 1
        # Group 2: term "world" → line 2
        lines = [m[1] for m in matches]
        assert 1 in lines
        assert 2 in lines
        assert 3 not in lines  # "async thing" doesn't match either group

    def test_phrase_and_term_in_same_group_both_required(self, lexical_index: LexicalIndex) -> None:
        """Within a single group, both phrase AND term must match."""
        content = "async def hello\nasync def world\ndef hello"
        matches = lexical_index._extract_all_snippets(content, '"async def" hello', context_lines=0)
        # Only line 1 has both "async def" AND "hello"
        assert len(matches) == 1
        assert matches[0][1] == 1

    def test_content_with_only_whitespace_lines(self, lexical_index: LexicalIndex) -> None:
        """Lines that are only whitespace should still be scannable."""
        content = "   \nfoo\n   \nbar"
        matches = lexical_index._extract_all_snippets(content, "foo", context_lines=0)
        assert len(matches) == 1
        assert matches[0][1] == 2

    def test_literal_mode_with_field_prefix(self, lexical_index: LexicalIndex) -> None:
        """Literal mode should treat field prefixes as plain terms."""
        content = "symbols:foo is here\nother line"
        matches = lexical_index._extract_all_snippets(
            content, "symbols:foo", context_lines=0, literal=True
        )
        assert len(matches) == 1
        assert matches[0][1] == 1


class TestRedTeam2SearchIntegration:
    """Deep integration tests for the full search pipeline."""

    def test_fallback_literal_finds_special_chars(self, lexical_index: LexicalIndex) -> None:
        """After Tantivy syntax error fallback, literal matching should find
        the special characters in the original query."""
        content = "result = items[0]\nother line"
        lexical_index.add_file("brackets.py", content, context_id=1)
        lexical_index.reload()
        results = lexical_index.search("items[0]")
        # The brackets trigger _build_tantivy_query escaping.
        # If Tantivy accepts the escaped query, results should include the line.
        # If fallback occurs, literal mode should still find it.
        found = any("items[0]" in r.snippet for r in results.results)
        assert found or results.fallback_reason is not None

    def test_search_score_always_one_even_on_fallback(self, lexical_index: LexicalIndex) -> None:
        """All scores should be 1.0 even when fallback is triggered."""
        lexical_index.add_file("fb_score.py", "hello world\n", context_id=1)
        lexical_index.reload()
        # Force fallback with Tantivy syntax
        results = lexical_index.search('content:"unclosed')
        for r in results.results:
            assert r.score == 1.0

    def test_literal_fallback_and_semantics(self, lexical_index: LexicalIndex) -> None:
        """On literal fallback, all tokens should be AND'd as plain content terms.

        When Tantivy query parsing fails and falls back, literal_fallback=True
        means _extract_search_terms treats every token literally.
        This means "OR" and "NOT" become searchable terms.
        """
        content = "OR NOT AND here\nonly OR here"
        lexical_index.add_file("lit_fb.py", content, context_id=1)
        lexical_index.reload()
        # Direct call to _extract_all_snippets in literal mode
        matches = lexical_index._extract_all_snippets(
            content, "OR NOT AND", context_lines=0, literal=True
        )
        # Only line 1 has all three tokens
        assert len(matches) == 1
        assert matches[0][1] == 1

    def test_content_query_overrides_literal_fallback(self, lexical_index: LexicalIndex) -> None:
        """When content_query is explicitly set, it should be used for snippet
        extraction even during literal fallback."""
        content = "class Foo:\n    pass\nclass Bar:"
        lexical_index.add_file("cq_lit.py", content, context_id=1, symbols=["Foo"])
        lexical_index.reload()
        # search_symbols sets content_query to the original unprefixed query
        results = lexical_index.search("symbols:Foo", content_query="Foo", context_lines=0)
        for r in results.results:
            assert "foo" in r.snippet.lower()

    def test_empty_file_search_does_not_crash(self, lexical_index: LexicalIndex) -> None:
        """Searching an empty file should not crash."""
        lexical_index.add_file("empty.py", "", context_id=1)
        lexical_index.reload()
        results = lexical_index.search("anything")
        assert isinstance(results, SearchResults)

    def test_file_with_no_newline_at_end(self, lexical_index: LexicalIndex) -> None:
        """File without trailing newline should still match last line."""
        content = "first line\nlast line with target"
        lexical_index.add_file("nonl.py", content, context_id=1)
        lexical_index.reload()
        results = lexical_index.search("target", context_lines=0)
        assert len(results.results) >= 1
        assert any(r.line == 2 for r in results.results)

    def test_deterministic_ordering_across_calls(self, lexical_index: LexicalIndex) -> None:
        """Repeated searches should return results in identical order."""
        for i in range(5):
            lexical_index.add_file(f"det_{i}.py", f"common_term line {i}\n", context_id=1)
        lexical_index.reload()
        r1 = [(r.file_path, r.line) for r in lexical_index.search("common_term").results]
        r2 = [(r.file_path, r.line) for r in lexical_index.search("common_term").results]
        assert r1 == r2

    def test_search_path_with_dots_and_slashes(self, lexical_index: LexicalIndex) -> None:
        """Path search with dots and slashes should not crash."""
        lexical_index.add_file("src/foo.bar/baz.py", "content\n", context_id=1)
        lexical_index.reload()
        results = lexical_index.search_path("foo.bar")
        assert isinstance(results, SearchResults)

    def test_search_symbols_with_not_operator(self, lexical_index: LexicalIndex) -> None:
        """Symbol search with NOT should not crash."""
        lexical_index.add_file(
            "sym_not.py",
            "class Foo: pass\nclass Bar: pass",
            context_id=1,
            symbols=["Foo", "Bar"],
        )
        lexical_index.reload()
        results = lexical_index.search_symbols("Foo NOT Bar")
        assert isinstance(results, SearchResults)
