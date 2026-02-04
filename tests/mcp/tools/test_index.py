"""Tests for MCP index tools (search, map_repo).

Verifies parameter models, summary helpers, and tool handlers.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.mcp.tools.index import (
    GetDefParams,
    GetReferencesParams,
    MapRepoParams,
    SearchParams,
    _get_file_path,
    _serialize_tree,
    _summarize_map,
    _summarize_search,
    map_repo,
    search,
)


class TestSearchParams:
    """Tests for SearchParams model."""

    def test_minimal_params(self) -> None:
        """Should accept minimal params."""
        params = SearchParams(query="test")
        assert params.query == "test"
        assert params.mode == "lexical"
        assert params.limit == 20

    def test_all_params(self) -> None:
        """Should accept all params."""
        params = SearchParams(
            query="test",
            mode="symbol",
            filter_paths=["src/"],
            filter_languages=["python"],
            filter_kinds=["function"],
            limit=50,
            cursor="abc123",
            include_snippets=False,
        )
        assert params.mode == "symbol"
        assert params.filter_paths == ["src/"]
        assert params.include_snippets is False

    def test_mode_options(self) -> None:
        """Should accept all valid modes."""
        for mode in ["lexical", "symbol", "references", "definitions"]:
            params = SearchParams(query="test", mode=mode)  # type: ignore
            assert params.mode == mode

    def test_limit_default(self) -> None:
        """Should have sensible limit default."""
        params = SearchParams(query="test")
        assert params.limit == 20


class TestMapRepoParams:
    """Tests for MapRepoParams model."""

    def test_minimal_params(self) -> None:
        """Should work with no params."""
        params = MapRepoParams()
        assert params.include is None
        assert params.depth == 3
        assert params.limit == 100

    def test_include_options(self) -> None:
        """Should accept include list."""
        params = MapRepoParams(include=["structure", "languages", "entry_points"])
        assert "structure" in params.include  # type: ignore
        assert len(params.include) == 3  # type: ignore

    def test_filtering_options(self) -> None:
        """Should accept glob filtering."""
        params = MapRepoParams(
            include_globs=["src/**"],
            exclude_globs=["**/output/**"],
            respect_gitignore=False,
        )
        assert params.include_globs == ["src/**"]
        assert params.exclude_globs == ["**/output/**"]
        assert params.respect_gitignore is False


class TestGetDefParams:
    """Tests for GetDefParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = GetDefParams(symbol_name="MyClass")
        assert params.symbol_name == "MyClass"
        assert params.context_id is None

    def test_with_context(self) -> None:
        """Should accept context_id."""
        params = GetDefParams(symbol_name="foo", context_id=1)
        assert params.context_id == 1


class TestGetReferencesParams:
    """Tests for GetReferencesParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = GetReferencesParams(symbol="MyClass")
        assert params.symbol == "MyClass"
        assert params.limit == 100

    def test_with_limit(self) -> None:
        """Should accept limit."""
        params = GetReferencesParams(symbol="foo", limit=50)
        assert params.limit == 50


class TestSummarizeSearch:
    """Tests for _summarize_search helper."""

    def test_no_results(self) -> None:
        """Should handle no results."""
        summary = _summarize_search(0, "lexical", "test")
        assert "no" in summary
        assert "test" in summary

    def test_with_results(self) -> None:
        """Should show count."""
        summary = _summarize_search(42, "symbol", "test")
        assert "42" in summary
        assert "symbol" in summary

    def test_long_query_truncation(self) -> None:
        """Should truncate long queries."""
        long_query = "a" * 50
        summary = _summarize_search(1, "lexical", long_query)
        assert "..." in summary


class TestSummarizeMap:
    """Tests for _summarize_map helper."""

    def test_basic_summary(self) -> None:
        """Should show file count."""
        summary = _summarize_map(100, [], False)
        assert "100 files" in summary

    def test_with_sections(self) -> None:
        """Should list sections."""
        summary = _summarize_map(50, ["structure", "languages"], False)
        assert "structure" in summary
        assert "languages" in summary

    def test_with_truncation(self) -> None:
        """Should indicate truncation."""
        summary = _summarize_map(200, [], True)
        assert "truncated" in summary


class TestSerializeTree:
    """Tests for _serialize_tree helper."""

    def test_empty_tree(self) -> None:
        """Serialize empty tree."""
        result = _serialize_tree([])
        assert result == []

    def test_file_node(self) -> None:
        """Serialize file node."""
        node = MagicMock()
        node.name = "test.py"
        node.path = "src/test.py"
        node.is_dir = False
        node.line_count = 42

        result = _serialize_tree([node])
        assert len(result) == 1
        assert result[0]["name"] == "test.py"
        assert result[0]["path"] == "src/test.py"
        assert result[0]["is_dir"] is False
        assert result[0]["line_count"] == 42

    def test_directory_node(self) -> None:
        """Serialize directory node."""
        node = MagicMock()
        node.name = "src"
        node.path = "src"
        node.is_dir = True
        node.file_count = 10
        node.children = []  # Empty children

        result = _serialize_tree([node])
        assert len(result) == 1
        assert result[0]["name"] == "src"
        assert result[0]["is_dir"] is True
        assert result[0]["file_count"] == 10
        assert result[0]["children"] == []

    def test_nested_tree(self) -> None:
        """Serialize nested directory tree."""
        child_file = MagicMock()
        child_file.name = "main.py"
        child_file.path = "src/main.py"
        child_file.is_dir = False
        child_file.line_count = 100

        parent = MagicMock()
        parent.name = "src"
        parent.path = "src"
        parent.is_dir = True
        parent.file_count = 1
        parent.children = [child_file]

        result = _serialize_tree([parent])
        assert len(result) == 1
        assert result[0]["children"][0]["name"] == "main.py"


class TestGetFilePath:
    """Tests for _get_file_path helper."""

    @pytest.mark.asyncio
    async def test_file_found(self) -> None:
        """Returns file path when found."""
        mock_file = MagicMock()
        mock_file.path = "src/test.py"

        mock_session = MagicMock()
        mock_session.get.return_value = mock_file

        mock_db = MagicMock()
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        mock_coordinator = MagicMock()
        mock_coordinator.db = mock_db

        mock_ctx = MagicMock()
        mock_ctx.coordinator = mock_coordinator

        path = await _get_file_path(mock_ctx, 1)
        assert path == "src/test.py"

    @pytest.mark.asyncio
    async def test_file_not_found(self) -> None:
        """Returns 'unknown' when file not found."""
        mock_session = MagicMock()
        mock_session.get.return_value = None

        mock_db = MagicMock()
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        mock_coordinator = MagicMock()
        mock_coordinator.db = mock_db

        mock_ctx = MagicMock()
        mock_ctx.coordinator = mock_coordinator

        path = await _get_file_path(mock_ctx, 999)
        assert path == "unknown"


class TestSearchHandler:
    """Tests for search tool handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        """Create mock context."""
        ctx = MagicMock()
        ctx.coordinator = MagicMock()
        ctx.coordinator.search = AsyncMock(return_value=[])
        ctx.coordinator.get_def = AsyncMock(return_value=None)
        ctx.coordinator.get_references = AsyncMock(return_value=[])
        return ctx

    @pytest.mark.asyncio
    async def test_lexical_search_empty(self, mock_ctx: MagicMock) -> None:
        """Lexical search returns empty results."""
        params = SearchParams(query="nonexistent")
        result = await search(mock_ctx, params)

        assert result["results"] == []
        assert "no lexical results" in result["summary"]

    @pytest.mark.asyncio
    async def test_lexical_search_with_results(self, mock_ctx: MagicMock) -> None:
        """Lexical search returns results."""
        mock_result = MagicMock()
        mock_result.path = "src/test.py"
        mock_result.line = 10
        mock_result.column = 5
        mock_result.snippet = "def test_function"
        mock_result.score = 0.9

        mock_ctx.coordinator.search = AsyncMock(return_value=[mock_result])

        params = SearchParams(query="test")
        result = await search(mock_ctx, params)

        assert len(result["results"]) == 1
        assert result["results"][0]["path"] == "src/test.py"
        assert result["results"][0]["line"] == 10
        assert "1 lexical results" in result["summary"]

    @pytest.mark.asyncio
    async def test_symbol_search(self, mock_ctx: MagicMock) -> None:
        """Symbol search delegates to coordinator."""
        mock_ctx.coordinator.search = AsyncMock(return_value=[])

        params = SearchParams(query="MyClass", mode="symbol")
        result = await search(mock_ctx, params)

        mock_ctx.coordinator.search.assert_called_once()
        assert "symbol" in result["summary"]

    @pytest.mark.asyncio
    async def test_definitions_search_no_results(self, mock_ctx: MagicMock) -> None:
        """Definition search with no results."""
        mock_ctx.coordinator.get_def = AsyncMock(return_value=None)

        params = SearchParams(query="UnknownSymbol", mode="definitions")
        result = await search(mock_ctx, params)

        assert result["results"] == []
        assert "no definitions results" in result["summary"]

    @pytest.mark.asyncio
    async def test_definitions_search_with_result(self, mock_ctx: MagicMock) -> None:
        """Definition search returns result."""
        mock_def = MagicMock()
        mock_def.file_id = 1
        mock_def.start_line = 10
        mock_def.start_col = 0
        mock_def.name = "MyClass"
        mock_def.kind = "class"
        mock_def.qualified_name = "module.MyClass"
        mock_def.display_name = "class MyClass"

        mock_ctx.coordinator.get_def = AsyncMock(return_value=mock_def)

        # Mock _get_file_path
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.path = "src/module.py"
        mock_session.get.return_value = mock_file

        mock_db = MagicMock()
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.coordinator.db = mock_db

        params = SearchParams(query="MyClass", mode="definitions")
        result = await search(mock_ctx, params)

        assert len(result["results"]) == 1
        assert result["results"][0]["symbol"]["name"] == "MyClass"
        assert result["results"][0]["symbol"]["kind"] == "class"
        assert "1 definitions results" in result["summary"]

    @pytest.mark.asyncio
    async def test_references_search_no_def(self, mock_ctx: MagicMock) -> None:
        """References search when def not found."""
        mock_ctx.coordinator.get_def = AsyncMock(return_value=None)

        params = SearchParams(query="UnknownSymbol", mode="references")
        result = await search(mock_ctx, params)

        assert result["results"] == []
        assert "no references results" in result["summary"]

    @pytest.mark.asyncio
    async def test_references_search_with_results(self, mock_ctx: MagicMock) -> None:
        """References search returns refs."""
        mock_def = MagicMock()
        mock_def.file_id = 1

        mock_ref = MagicMock()
        mock_ref.file_id = 2
        mock_ref.start_line = 20
        mock_ref.start_col = 4
        mock_ref.token_text = "MyClass"
        mock_ref.certainty = "CERTAIN"

        mock_ctx.coordinator.get_def = AsyncMock(return_value=mock_def)
        mock_ctx.coordinator.get_references = AsyncMock(return_value=[mock_ref])

        # Mock _get_file_path
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.path = "src/user.py"
        mock_session.get.return_value = mock_file

        mock_db = MagicMock()
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.coordinator.db = mock_db

        params = SearchParams(query="MyClass", mode="references")
        result = await search(mock_ctx, params)

        assert len(result["results"]) == 1
        assert result["results"][0]["line"] == 20
        assert result["results"][0]["score"] == 1.0  # CERTAIN
        assert "1 references results" in result["summary"]

    @pytest.mark.asyncio
    async def test_references_uncertain(self, mock_ctx: MagicMock) -> None:
        """References with uncertain certainty get lower score."""
        mock_def = MagicMock()

        mock_ref = MagicMock()
        mock_ref.file_id = 2
        mock_ref.start_line = 20
        mock_ref.start_col = 4
        mock_ref.token_text = "foo"
        mock_ref.certainty = "UNCERTAIN"  # Not CERTAIN

        mock_ctx.coordinator.get_def = AsyncMock(return_value=mock_def)
        mock_ctx.coordinator.get_references = AsyncMock(return_value=[mock_ref])

        # Mock file lookup
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(path="test.py")
        mock_db = MagicMock()
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.coordinator.db = mock_db

        params = SearchParams(query="foo", mode="references")
        result = await search(mock_ctx, params)

        assert result["results"][0]["score"] == 0.5
        assert result["results"][0]["match_type"] == "fuzzy"


class TestMapRepoHandler:
    """Tests for map_repo tool handler."""

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        """Create mock context with empty map result."""
        ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.structure = None
        mock_result.languages = None
        mock_result.entry_points = None
        mock_result.dependencies = None
        mock_result.test_layout = None
        mock_result.public_api = None
        mock_result.truncated = False
        mock_result.next_cursor = None
        mock_result.total_estimate = None

        ctx.coordinator = MagicMock()
        ctx.coordinator.map_repo = AsyncMock(return_value=mock_result)
        return ctx

    @pytest.mark.asyncio
    async def test_empty_map(self, mock_ctx: MagicMock) -> None:
        """Map returns empty when nothing included."""
        params = MapRepoParams()
        result = await map_repo(mock_ctx, params)

        assert "pagination" in result
        assert result["pagination"]["truncated"] is False
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_map_with_structure(self, mock_ctx: MagicMock) -> None:
        """Map includes structure when available."""
        mock_structure = MagicMock()
        mock_structure.root = "/project"
        mock_structure.tree = []  # Empty tree
        mock_structure.file_count = 42
        mock_structure.contexts = ["main"]

        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.structure = mock_structure

        params = MapRepoParams(include=["structure"])
        result = await map_repo(mock_ctx, params)

        assert "structure" in result
        assert result["structure"]["root"] == "/project"
        assert result["structure"]["file_count"] == 42
        assert "structure" in result["summary"]

    @pytest.mark.asyncio
    async def test_map_with_languages(self, mock_ctx: MagicMock) -> None:
        """Map includes languages when available."""
        mock_lang = MagicMock()
        mock_lang.language = "python"
        mock_lang.file_count = 100
        mock_lang.percentage = 80.0

        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.languages = [mock_lang]

        params = MapRepoParams(include=["languages"])
        result = await map_repo(mock_ctx, params)

        assert "languages" in result
        assert result["languages"][0]["language"] == "python"
        assert result["languages"][0]["percentage"] == 80.0

    @pytest.mark.asyncio
    async def test_map_with_entry_points(self, mock_ctx: MagicMock) -> None:
        """Map includes entry points when available."""
        mock_ep = MagicMock()
        mock_ep.path = "src/main.py"
        mock_ep.kind = "main"
        mock_ep.name = "main"
        mock_ep.qualified_name = "src.main.main"

        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.entry_points = [mock_ep]

        params = MapRepoParams(include=["entry_points"])
        result = await map_repo(mock_ctx, params)

        assert "entry_points" in result
        assert result["entry_points"][0]["path"] == "src/main.py"

    @pytest.mark.asyncio
    async def test_map_with_dependencies(self, mock_ctx: MagicMock) -> None:
        """Map includes dependencies when available."""
        mock_deps = MagicMock()
        mock_deps.external_modules = ["requests", "pytest"]
        mock_deps.import_count = 50

        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.dependencies = mock_deps

        params = MapRepoParams(include=["dependencies"])
        result = await map_repo(mock_ctx, params)

        assert "dependencies" in result
        assert "requests" in result["dependencies"]["external_modules"]

    @pytest.mark.asyncio
    async def test_map_with_test_layout(self, mock_ctx: MagicMock) -> None:
        """Map includes test layout when available."""
        mock_tests = MagicMock()
        mock_tests.test_files = ["tests/test_main.py"]
        mock_tests.test_count = 25

        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.test_layout = mock_tests

        params = MapRepoParams(include=["test_layout"])
        result = await map_repo(mock_ctx, params)

        assert "test_layout" in result
        assert result["test_layout"]["test_count"] == 25

    @pytest.mark.asyncio
    async def test_map_with_public_api(self, mock_ctx: MagicMock) -> None:
        """Map includes public API when available."""
        mock_sym = MagicMock()
        mock_sym.name = "MyClass"
        mock_sym.def_uid = "uid123"
        mock_sym.certainty = "CERTAIN"
        mock_sym.evidence = ["__all__"]

        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.public_api = [mock_sym]

        params = MapRepoParams(include=["public_api"])
        result = await map_repo(mock_ctx, params)

        assert "public_api" in result
        assert result["public_api"][0]["name"] == "MyClass"

    @pytest.mark.asyncio
    async def test_map_truncated(self, mock_ctx: MagicMock) -> None:
        """Map shows truncation in pagination."""
        mock_result = mock_ctx.coordinator.map_repo.return_value
        mock_result.truncated = True
        mock_result.next_cursor = "cursor123"
        mock_result.total_estimate = 500

        params = MapRepoParams()
        result = await map_repo(mock_ctx, params)

        assert result["pagination"]["truncated"] is True
        assert result["pagination"]["next_cursor"] == "cursor123"
        assert result["pagination"]["total_estimate"] == 500

    @pytest.mark.asyncio
    async def test_map_calls_coordinator(self, mock_ctx: MagicMock) -> None:
        """Map passes params to coordinator."""
        params = MapRepoParams(
            include=["structure"],
            depth=5,
            limit=50,
            include_globs=["src/**"],
            exclude_globs=["**/build/**"],
            respect_gitignore=False,
        )
        await map_repo(mock_ctx, params)

        mock_ctx.coordinator.map_repo.assert_called_once_with(
            include=["structure"],
            depth=5,
            limit=50,
            include_globs=["src/**"],
            exclude_globs=["**/build/**"],
            respect_gitignore=False,
        )
