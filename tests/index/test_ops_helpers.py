"""Tests for index/ops.py helper functions and dataclasses.

Tests the utility functions and data structures:
- _matches_glob: Glob pattern matching with ** support
- InitResult: Initialization result dataclass
- IndexStats: Index statistics dataclass
- SearchResult: Search result dataclass
- SearchResponse: Search response dataclass
- SearchMode: Search mode enum
"""

from codeplane.index.ops import (
    IndexStats,
    InitResult,
    SearchMode,
    SearchResponse,
    SearchResult,
    _matches_glob,
)


class TestMatchesGlob:
    """Tests for _matches_glob helper function."""

    def test_exact_match(self) -> None:
        """Matches exact filename."""
        assert _matches_glob("test.py", "test.py") is True

    def test_no_match(self) -> None:
        """No match returns False."""
        assert _matches_glob("test.py", "other.py") is False

    def test_wildcard_extension(self) -> None:
        """Matches *.py pattern."""
        assert _matches_glob("test.py", "*.py") is True
        assert _matches_glob("test.js", "*.py") is False

    def test_wildcard_name(self) -> None:
        """Matches test* pattern."""
        assert _matches_glob("test_foo.py", "test_*") is True
        assert _matches_glob("main.py", "test_*") is False

    def test_double_star_at_start(self) -> None:
        """Matches **/pattern from beginning."""
        # The function removes **/ prefix and then matches
        assert _matches_glob("src/test.py", "**/*.py") is True
        assert _matches_glob("deep/nested/path/file.py", "**/*.py") is True

    def test_double_star_directory(self) -> None:
        """** as prefix matches any directory depth."""
        assert _matches_glob("a/b/c/d/file.txt", "**/file.txt") is True
        # File at root level should also match
        assert _matches_glob("file.txt", "**/file.txt") is True

    def test_question_mark(self) -> None:
        """? matches single character."""
        assert _matches_glob("test1.py", "test?.py") is True
        assert _matches_glob("test12.py", "test?.py") is False

    def test_bracket_character_class(self) -> None:
        """[abc] matches character class."""
        assert _matches_glob("file_a.py", "file_[abc].py") is True
        assert _matches_glob("file_d.py", "file_[abc].py") is False

    def test_empty_pattern(self) -> None:
        """Empty pattern behavior."""
        assert _matches_glob("", "") is True
        assert _matches_glob("file.py", "") is False

    def test_empty_path(self) -> None:
        """Empty path matches only empty pattern."""
        assert _matches_glob("", "*.py") is False
        assert _matches_glob("", "") is True


class TestInitResult:
    """Tests for InitResult dataclass."""

    def test_default_values(self) -> None:
        """Default errors list is empty."""
        result = InitResult(
            contexts_discovered=5,
            contexts_valid=4,
            contexts_failed=1,
            contexts_detached=0,
            files_indexed=100,
            errors=[],
        )
        assert result.contexts_discovered == 5
        assert result.contexts_valid == 4
        assert result.contexts_failed == 1
        assert result.files_indexed == 100
        assert result.errors == []
        assert result.files_by_ext == {}

    def test_with_files_by_ext(self) -> None:
        """Can set files_by_ext."""
        result = InitResult(
            contexts_discovered=3,
            contexts_valid=3,
            contexts_failed=0,
            contexts_detached=0,
            files_indexed=50,
            errors=[],
            files_by_ext={".py": 30, ".js": 20},
        )
        assert result.files_by_ext[".py"] == 30
        assert result.files_by_ext[".js"] == 20

    def test_with_errors(self) -> None:
        """Can include errors."""
        result = InitResult(
            contexts_discovered=2,
            contexts_valid=1,
            contexts_failed=1,
            contexts_detached=0,
            files_indexed=10,
            errors=["Failed to parse context X", "Invalid marker file Y"],
        )
        assert len(result.errors) == 2


class TestIndexStats:
    """Tests for IndexStats dataclass."""

    def test_all_fields(self) -> None:
        """All fields are accessible."""
        stats = IndexStats(
            files_processed=100,
            files_added=50,
            files_updated=30,
            files_removed=20,
            symbols_indexed=500,
            duration_seconds=2.5,
        )
        assert stats.files_processed == 100
        assert stats.files_added == 50
        assert stats.files_updated == 30
        assert stats.files_removed == 20
        assert stats.symbols_indexed == 500
        assert stats.duration_seconds == 2.5

    def test_zero_values(self) -> None:
        """Zero values are valid."""
        stats = IndexStats(
            files_processed=0,
            files_added=0,
            files_updated=0,
            files_removed=0,
            symbols_indexed=0,
            duration_seconds=0.0,
        )
        assert stats.files_processed == 0


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_all_fields(self) -> None:
        """All fields are accessible."""
        result = SearchResult(
            path="src/main.py",
            line=42,
            column=10,
            snippet="def main():",
            score=0.95,
        )
        assert result.path == "src/main.py"
        assert result.line == 42
        assert result.column == 10
        assert result.snippet == "def main():"
        assert result.score == 0.95

    def test_optional_column(self) -> None:
        """Column can be None."""
        result = SearchResult(
            path="test.py",
            line=1,
            column=None,
            snippet="import os",
            score=0.5,
        )
        assert result.column is None


class TestSearchResponse:
    """Tests for SearchResponse dataclass."""

    def test_with_results(self) -> None:
        """Contains list of SearchResult."""
        results = [
            SearchResult(path="a.py", line=1, column=0, snippet="line", score=0.9),
            SearchResult(path="b.py", line=2, column=0, snippet="line", score=0.8),
        ]
        response = SearchResponse(results=results)
        assert len(response.results) == 2
        assert response.fallback_reason is None

    def test_empty_results(self) -> None:
        """Empty results list."""
        response = SearchResponse(results=[])
        assert len(response.results) == 0

    def test_with_fallback_reason(self) -> None:
        """Includes fallback reason when query syntax error."""
        response = SearchResponse(
            results=[],
            fallback_reason="Query syntax error: unmatched quote",
        )
        assert response.fallback_reason == "Query syntax error: unmatched quote"


class TestSearchMode:
    """Tests for SearchMode enum."""

    def test_text_mode(self) -> None:
        """TEXT mode value."""
        assert SearchMode.TEXT == "text"

    def test_symbol_mode(self) -> None:
        """SYMBOL mode value."""
        assert SearchMode.SYMBOL == "symbol"

    def test_path_mode(self) -> None:
        """PATH mode value."""
        assert SearchMode.PATH == "path"
