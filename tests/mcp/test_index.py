"""Tests for MCP index tools (search, map_repo)."""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.index import (
    MapRepoParams,
    SearchParams,
)


class TestSearchParams:
    """Tests for SearchParams model."""

    def test_query_required(self):
        """query is required."""
        with pytest.raises(ValidationError):
            SearchParams()

    def test_query_provided(self):
        """Accepts query string."""
        params = SearchParams(query="def main")
        assert params.query == "def main"

    def test_mode_default(self):
        """mode defaults to lexical."""
        params = SearchParams(query="test")
        assert params.mode == "lexical"

    def test_mode_options(self):
        """mode accepts valid options."""
        for mode in ["lexical", "symbol", "references", "definitions"]:
            params = SearchParams(query="test", mode=mode)
            assert params.mode == mode

    def test_mode_invalid(self):
        """mode rejects invalid value."""
        with pytest.raises(ValidationError):
            SearchParams(query="test", mode="fuzzy")

    def test_limit_default(self):
        """limit defaults to 20."""
        params = SearchParams(query="test")
        assert params.limit == 20

    def test_limit_bounds(self):
        """limit is bounded 1-100."""
        params = SearchParams(query="test", limit=1)
        assert params.limit == 1
        params = SearchParams(query="test", limit=100)
        assert params.limit == 100

    def test_limit_max_exceeded(self):
        """limit cannot exceed 100."""
        with pytest.raises(ValidationError):
            SearchParams(query="test", limit=101)

    def test_filter_paths(self):
        """Can filter by paths."""
        params = SearchParams(query="test", filter_paths=["src/", "lib/"])
        assert params.filter_paths == ["src/", "lib/"]

    def test_filter_languages(self):
        """Can filter by languages."""
        params = SearchParams(query="test", filter_languages=["python", "typescript"])
        assert params.filter_languages == ["python", "typescript"]

    def test_filter_kinds(self):
        """Can filter by symbol kinds."""
        params = SearchParams(query="test", filter_kinds=["function", "class"])
        assert params.filter_kinds == ["function", "class"]

    def test_include_snippets_default(self):
        """include_snippets defaults to True."""
        params = SearchParams(query="test")
        assert params.include_snippets is True

    def test_include_snippets_false(self):
        """include_snippets can be False."""
        params = SearchParams(query="test", include_snippets=False)
        assert params.include_snippets is False

    def test_cursor_pagination(self):
        """cursor for pagination."""
        params = SearchParams(query="test", cursor="page_2")
        assert params.cursor == "page_2"


class TestMapRepoParams:
    """Tests for MapRepoParams model."""

    def test_all_optional(self):
        """All fields are optional."""
        params = MapRepoParams()
        assert params.include is None
        assert params.depth == 3
        assert params.limit == 100

    def test_include_options(self):
        """include accepts valid components."""
        params = MapRepoParams(
            include=[
                "structure",
                "languages",
                "entry_points",
                "dependencies",
                "test_layout",
                "public_api",
            ]
        )
        assert len(params.include) == 6

    def test_include_invalid(self):
        """include rejects invalid components."""
        with pytest.raises(ValidationError):
            MapRepoParams(include=["invalid_component"])

    def test_depth_default(self):
        """depth defaults to 3."""
        params = MapRepoParams()
        assert params.depth == 3

    def test_depth_bounds(self):
        """depth is bounded 1-10."""
        params = MapRepoParams(depth=1)
        assert params.depth == 1
        params = MapRepoParams(depth=10)
        assert params.depth == 10

    def test_depth_maximum(self):
        """depth cannot exceed 10."""
        with pytest.raises(ValidationError):
            MapRepoParams(depth=11)

    def test_limit_default(self):
        """limit defaults to 100."""
        params = MapRepoParams()
        assert params.limit == 100

    def test_limit_bounds(self):
        """limit bounded 1-1000."""
        params = MapRepoParams(limit=1000)
        assert params.limit == 1000

    def test_limit_maximum(self):
        """limit cannot exceed 1000."""
        with pytest.raises(ValidationError):
            MapRepoParams(limit=1001)

    def test_include_globs(self):
        """include_globs accepts glob patterns."""
        params = MapRepoParams(include_globs=["src/**", "lib/**"])
        assert params.include_globs == ["src/**", "lib/**"]

    def test_exclude_globs(self):
        """exclude_globs accepts glob patterns."""
        params = MapRepoParams(exclude_globs=["**/node_modules/**", "**/__pycache__/**"])
        assert params.exclude_globs == ["**/node_modules/**", "**/__pycache__/**"]

    def test_respect_gitignore_default(self):
        """respect_gitignore defaults to True."""
        params = MapRepoParams()
        assert params.respect_gitignore is True

    def test_respect_gitignore_false(self):
        """respect_gitignore can be False."""
        params = MapRepoParams(respect_gitignore=False)
        assert params.respect_gitignore is False


class TestSearchHandler:
    """Tests for search handler - parameter validation."""

    def test_search_params_mode_combinations(self):
        """Various search modes work."""
        for mode in ["lexical", "symbol", "references", "definitions"]:
            params = SearchParams(query="test", mode=mode)
            assert params.mode == mode

    def test_search_params_filters(self):
        """Filter params are accepted."""
        params = SearchParams(
            query="test",
            filter_paths=["src/"],
            filter_languages=["python"],
            filter_kinds=["function"],
        )
        assert params.filter_paths == ["src/"]
        assert params.filter_languages == ["python"]
        assert params.filter_kinds == ["function"]


class TestMapRepoHandler:
    """Tests for map_repo handler - parameter validation."""

    def test_map_params_include_options(self):
        """All include options are accepted."""
        params = MapRepoParams(
            include=[
                "structure",
                "languages",
                "entry_points",
                "dependencies",
                "test_layout",
                "public_api",
            ]
        )
        assert len(params.include) == 6

    def test_map_params_glob_patterns(self):
        """Glob patterns are accepted."""
        params = MapRepoParams(
            include_globs=["src/**/*.py"],
            exclude_globs=["**/test_*.py"],
        )
        assert params.include_globs == ["src/**/*.py"]
        assert params.exclude_globs == ["**/test_*.py"]
