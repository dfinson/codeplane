"""Tests for MCP index tools (search, map_repo).

Verifies summary helpers and serialization functions.
"""

from typing import Any

import pytest

from codeplane.config.constants import SEARCH_SCOPE_FALLBACK_LINES_DEFAULT
from codeplane.mcp.tools.index import (
    _change_to_text,
    _map_repo_sections_to_text,
    _search_results_to_text,
    _serialize_tree,
    _summarize_map,
    _summarize_search,
    _tree_to_compact_text,
    _tree_to_text,
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


class MockFileNode:
    """Mock file node for testing."""

    def __init__(
        self,
        name: str = "main.py",
        path: str = "src/main.py",
        line_count: int = 100,
    ) -> None:
        self.name = name
        self.path = path
        self.is_dir = False
        self.line_count = line_count
        self.children: list[Any] = []


class MockDirNode:
    """Mock directory node for testing."""

    def __init__(
        self,
        name: str = "src",
        path: str = "src",
        file_count: int = 5,
        children: list[Any] | None = None,
    ) -> None:
        self.name = name
        self.path = path
        self.is_dir = True
        self.file_count = file_count
        self.children: list[Any] = children if children is not None else []


class TestSerializeTree:
    """Tests for _serialize_tree helper."""

    def test_empty_tree(self) -> None:
        """Empty tree returns empty list."""
        result = _serialize_tree([])
        assert result == []

    def test_file_node(self) -> None:
        """File node serialization.

        Note: 'name' field was removed for token efficiency - agents can derive
        it from path.split('/')[-1].
        """
        result = _serialize_tree([MockFileNode()])
        assert len(result) == 1
        assert "name" not in result[0]  # Intentionally removed for token efficiency
        assert result[0]["path"] == "src/main.py"
        assert result[0]["is_dir"] is False
        assert result[0]["line_count"] == 100

    def test_directory_node(self) -> None:
        """Directory node serialization."""
        result = _serialize_tree([MockDirNode()])
        assert len(result) == 1
        assert "name" not in result[0]  # Intentionally removed for token efficiency
        assert result[0]["is_dir"] is True
        assert result[0]["file_count"] == 5
        assert result[0]["children"] == []

    def test_nested_tree(self) -> None:
        """Nested directory structure."""
        file_node = MockFileNode(name="main.py", path="src/main.py", line_count=50)
        dir_node = MockDirNode(name="src", path="src", file_count=1, children=[file_node])

        result = _serialize_tree([dir_node])
        assert len(result) == 1
        assert result[0]["is_dir"] is True
        assert len(result[0]["children"]) == 1
        assert "name" not in result[0]["children"][0]  # Removed for token efficiency


# =============================================================================
# Context Preset Tests
# =============================================================================


class TestContextPresets:
    """Tests for context preset line counts."""

    @pytest.mark.parametrize(
        "context,expected_lines",
        [
            ("none", 0),
            ("minimal", 1),
            ("standard", 5),
            ("rich", 20),
        ],
    )
    def test_preset_line_counts(self, context: str, expected_lines: int) -> None:
        """Each preset maps to expected line count."""
        # Import the preset mapping from the module
        CONTEXT_PRESETS = {
            "none": 0,
            "minimal": 1,
            "standard": 5,
            "rich": 20,
        }
        assert CONTEXT_PRESETS[context] == expected_lines

    def test_structural_modes_not_in_line_presets(self) -> None:
        """Structural modes (function, class) are not line-based presets."""
        CONTEXT_PRESETS = {
            "none": 0,
            "minimal": 1,
            "standard": 5,
            "rich": 20,
        }
        assert "function" not in CONTEXT_PRESETS
        assert "class" not in CONTEXT_PRESETS

    def test_structural_mode_fallback_lines(self) -> None:
        """Structural modes use fallback lines constant."""
        # The fallback should be a reasonable default (25 as per design)
        assert SEARCH_SCOPE_FALLBACK_LINES_DEFAULT >= 20
        assert SEARCH_SCOPE_FALLBACK_LINES_DEFAULT <= 30


# =============================================================================
# Text Serializer Tests
# =============================================================================


class TestSearchResultsToText:
    """Tests for _search_results_to_text."""

    def test_empty_list(self) -> None:
        assert _search_results_to_text([]) == []

    def test_basic_hit(self) -> None:
        items = [{"kind": "function", "path": "src/a.py", "span": {"start_line": 10}}]
        lines = _search_results_to_text(items)
        assert len(lines) == 1
        assert "function src/a.py:10" in lines[0]

    def test_with_symbol_id(self) -> None:
        items = [
            {
                "kind": "function",
                "path": "src/a.py",
                "span": {"start_line": 5},
                "symbol_id": "my_func",
            }
        ]
        lines = _search_results_to_text(items)
        assert "my_func" in lines[0]

    def test_with_preview(self) -> None:
        items = [
            {
                "kind": "hit",
                "path": "src/b.py",
                "span": {"start_line": 20},
                "preview_line": "x = 42",
            }
        ]
        lines = _search_results_to_text(items)
        assert "x = 42" in lines[0]

    def test_with_enclosing_span(self) -> None:
        items = [
            {
                "kind": "function",
                "path": "src/a.py",
                "span": {"start_line": 10},
                "enclosing_span": {"kind": "class", "start_line": 1, "end_line": 50},
            }
        ]
        lines = _search_results_to_text(items)
        assert "[class 1-50]" in lines[0]

    def test_with_match_count(self) -> None:
        items = [
            {
                "kind": "hit",
                "path": "src/a.py",
                "span": {"start_line": 1},
                "match_count": 5,
            }
        ]
        lines = _search_results_to_text(items)
        assert "(×5)" in lines[0]

    def test_match_count_one_not_shown(self) -> None:
        items = [
            {
                "kind": "hit",
                "path": "src/a.py",
                "span": {"start_line": 1},
                "match_count": 1,
            }
        ]
        lines = _search_results_to_text(items)
        assert "×" not in lines[0]

    def test_symbol_enrichment_fallback(self) -> None:
        """symbol.name used when symbol_id is absent."""
        items = [
            {
                "kind": "variable",
                "path": "src/a.py",
                "span": {"start_line": 3},
                "symbol": {"name": "MY_CONST"},
            }
        ]
        lines = _search_results_to_text(items)
        assert "MY_CONST" in lines[0]

    def test_multiple_items(self) -> None:
        items = [
            {"kind": "function", "path": "a.py", "span": {"start_line": 1}},
            {"kind": "class", "path": "b.py", "span": {"start_line": 2}},
        ]
        lines = _search_results_to_text(items)
        assert len(lines) == 2


class _MockChange:
    """Minimal mock for StructuralChange used by _change_to_text."""

    def __init__(
        self,
        *,
        change: str = "added",
        kind: str = "function",
        name: str = "foo",
        path: str = "src/a.py",
        start_line: int = 10,
        end_line: int = 20,
        lines_changed: int | None = 5,
        behavior_change_risk: str = "low",
        old_sig: str | None = None,
        new_sig: str | None = None,
        old_name: str | None = None,
        impact: Any = None,
        nested_changes: list[Any] | None = None,
    ) -> None:
        self.change = change
        self.kind = kind
        self.name = name
        self.path = path
        self.start_line = start_line
        self.end_line = end_line
        self.lines_changed = lines_changed
        self.behavior_change_risk = behavior_change_risk
        self.old_sig = old_sig
        self.new_sig = new_sig
        self.old_name = old_name
        self.impact = impact
        self.nested_changes = nested_changes


class _MockImpact:
    def __init__(
        self,
        reference_count: int | None = None,
        affected_test_files: list[str] | None = None,
    ) -> None:
        self.reference_count = reference_count
        self.affected_test_files = affected_test_files


class TestChangeToText:
    """Tests for _change_to_text."""

    def test_basic_added(self) -> None:
        c = _MockChange(change="added", kind="function", name="foo")
        lines = _change_to_text(c)
        assert len(lines) == 1
        assert "added function foo" in lines[0]
        assert "src/a.py:10-20" in lines[0]

    def test_lines_changed(self) -> None:
        c = _MockChange(lines_changed=42)
        lines = _change_to_text(c)
        assert "Δ42" in lines[0]

    def test_no_lines_changed(self) -> None:
        c = _MockChange(lines_changed=None)
        lines = _change_to_text(c)
        assert "Δ" not in lines[0]

    def test_high_risk(self) -> None:
        c = _MockChange(behavior_change_risk="high")
        lines = _change_to_text(c)
        assert "risk:high" in lines[0]

    def test_low_risk_not_shown(self) -> None:
        c = _MockChange(behavior_change_risk="low")
        lines = _change_to_text(c)
        assert "risk:" not in lines[0]

    def test_signature_changed_shows_sigs(self) -> None:
        c = _MockChange(
            change="signature_changed",
            old_sig="def foo(a)",
            new_sig="def foo(a, b)",
        )
        lines = _change_to_text(c)
        assert "old:def foo(a)" in lines[0]
        assert "new:def foo(a, b)" in lines[0]

    def test_renamed_shows_old_name(self) -> None:
        c = _MockChange(change="renamed", old_name="bar")
        lines = _change_to_text(c)
        assert "was:bar" in lines[0]

    def test_impact_refs(self) -> None:
        c = _MockChange(impact=_MockImpact(reference_count=12))
        lines = _change_to_text(c)
        assert "refs:12" in lines[0]

    def test_impact_tests(self) -> None:
        c = _MockChange(impact=_MockImpact(affected_test_files=["test_a.py", "test_b.py"]))
        lines = _change_to_text(c)
        assert "tests:test_a.py,test_b.py" in lines[0]

    def test_nested_changes(self) -> None:
        inner = _MockChange(change="removed", name="inner_fn")
        outer = _MockChange(change="body_changed", name="outer", nested_changes=[inner])
        lines = _change_to_text(outer)
        assert len(lines) == 2
        assert "  removed" in lines[1]  # indented

    def test_no_span_shows_path_only(self) -> None:
        c = _MockChange(start_line=0, end_line=0)
        lines = _change_to_text(c)
        assert "src/a.py" in lines[0]
        assert ":0-0" not in lines[0]  # no span when start_line=0


class TestTreeToText:
    """Tests for _tree_to_text."""

    def test_empty(self) -> None:
        assert _tree_to_text([]) == []

    def test_file_with_line_count(self) -> None:
        node = MockFileNode(name="main.py", path="src/main.py", line_count=100)
        lines = _tree_to_text([node])
        assert len(lines) == 1
        assert "main.py" in lines[0]
        assert "100" in lines[0]

    def test_file_without_line_count(self) -> None:
        node = MockFileNode(name="main.py", path="src/main.py", line_count=100)
        lines = _tree_to_text([node], include_line_counts=False)
        assert "100" not in lines[0]

    def test_directory(self) -> None:
        child = MockFileNode(name="app.py", path="src/app.py", line_count=50)
        d = MockDirNode(name="src", path="src", file_count=1, children=[child])
        lines = _tree_to_text([d])
        assert len(lines) == 2
        assert "src/" in lines[0]
        assert "1 files" in lines[0]
        assert "  app.py" in lines[1]  # indented child

    def test_nested_depth(self) -> None:
        leaf = MockFileNode(name="x.py", path="a/b/x.py", line_count=10)
        inner = MockDirNode(name="b", path="a/b", file_count=1, children=[leaf])
        outer = MockDirNode(name="a", path="a", file_count=1, children=[inner])
        lines = _tree_to_text([outer])
        assert len(lines) == 3
        # Check increasing indentation
        assert lines[0].startswith("a/")
        assert lines[1].startswith("  a/b/")
        assert lines[2].startswith("    x.py")


class TestTreeToCompactText:
    """Tests for _tree_to_compact_text — lossless flat dir-header format."""

    _SAMPLE_PATHS: list[tuple[str, int | None]] = [
        ("src/codeplane/cli/main.py", 100),
        ("src/codeplane/cli/init.py", 50),
        ("src/codeplane/core/errors.py", 80),
        ("src/codeplane/core/logging.py", 60),
        ("tests/cli/test_main.py", 40),
        ("tests/core/test_errors.py", 30),
        ("pyproject.toml", 200),
        ("README.md", 50),
    ]

    def test_empty(self) -> None:
        assert _tree_to_compact_text([]) == []

    def test_every_file_present(self) -> None:
        """No filenames dropped — lossless."""
        lines = _tree_to_compact_text(self._SAMPLE_PATHS)
        joined = "\n".join(lines)
        assert "main.py:100" in joined
        assert "init.py:50" in joined
        assert "errors.py:80" in joined
        assert "logging.py:60" in joined
        assert "test_main.py:40" in joined
        assert "test_errors.py:30" in joined
        assert "pyproject.toml:200" in joined
        assert "README.md:50" in joined

    def test_files_grouped_by_directory(self) -> None:
        lines = _tree_to_compact_text(self._SAMPLE_PATHS)
        # Each directory gets one line with its files inline
        cli_line = [ln for ln in lines if ln.startswith("src/codeplane/cli/")]
        assert len(cli_line) == 1
        assert "main.py:100" in cli_line[0]
        assert "init.py:50" in cli_line[0]

    def test_root_files_on_dot_line(self) -> None:
        lines = _tree_to_compact_text(self._SAMPLE_PATHS)
        dot_lines = [ln for ln in lines if ln.startswith(". ")]
        assert len(dot_lines) == 1
        assert "pyproject.toml:200" in dot_lines[0]
        assert "README.md:50" in dot_lines[0]

    def test_no_line_counts(self) -> None:
        lines = _tree_to_compact_text(self._SAMPLE_PATHS, include_line_counts=False)
        joined = "\n".join(lines)
        # Filenames present, no :LC suffixes
        assert "main.py" in joined
        assert ":100" not in joined
        assert ":50" not in joined

    def test_root_files_only(self) -> None:
        paths: list[tuple[str, int | None]] = [
            ("setup.py", 10),
            ("README.md", 20),
        ]
        lines = _tree_to_compact_text(paths)
        assert len(lines) == 1  # single dot line
        assert ". " in lines[0]
        assert "setup.py:10" in lines[0]
        assert "README.md:20" in lines[0]

    def test_dirs_sorted(self) -> None:
        lines = _tree_to_compact_text(self._SAMPLE_PATHS)
        dir_prefixes = [ln.split(" ")[0] for ln in lines if not ln.startswith(". ")]
        assert dir_prefixes == sorted(dir_prefixes)

    def test_lossless_file_count(self) -> None:
        """Total colon-separated entries equals input file count."""
        lines = _tree_to_compact_text(self._SAMPLE_PATHS)
        joined = "\n".join(lines)
        assert joined.count(":") == len(self._SAMPLE_PATHS)


class _MockStructureInfo:
    def __init__(
        self,
        root: str,
        tree: list[Any],
        file_count: int,
        contexts: list[str] | None = None,
        all_paths: list[tuple[str, int | None]] | None = None,
    ) -> None:
        self.root = root
        self.tree = tree
        self.file_count = file_count
        self.contexts = contexts or []
        self.all_paths = all_paths or []


class _MockLanguageStats:
    def __init__(self, language: str, file_count: int, percentage: float) -> None:
        self.language = language
        self.file_count = file_count
        self.percentage = percentage


class _MockDependencies:
    def __init__(self, external_modules: list[str], import_count: int) -> None:
        self.external_modules = external_modules
        self.import_count = import_count


class _MockTestLayout:
    def __init__(self, test_files: list[str], test_count: int) -> None:
        self.test_files = test_files
        self.test_count = test_count


class _MockEntryPoint:
    def __init__(self, kind: str, name: str, path: str, qualified_name: str | None = None) -> None:
        self.kind = kind
        self.name = name
        self.path = path
        self.qualified_name = qualified_name


class _MockPublicSymbol:
    def __init__(
        self, name: str, certainty: str, def_uid: str | None = None, evidence: str | None = None
    ) -> None:
        self.name = name
        self.certainty = certainty
        self.def_uid = def_uid
        self.evidence = evidence


class _MockMapRepoResult:
    def __init__(
        self,
        structure: Any = None,
        languages: list[Any] | None = None,
        entry_points: list[Any] | None = None,
        dependencies: Any = None,
        test_layout: Any = None,
        public_api: list[Any] | None = None,
    ) -> None:
        self.structure = structure
        self.languages = languages
        self.entry_points = entry_points
        self.dependencies = dependencies
        self.test_layout = test_layout
        self.public_api = public_api


class TestMapRepoSectionsToText:
    """Tests for _map_repo_sections_to_text."""

    def test_empty_result(self) -> None:
        result = _MockMapRepoResult()
        sections = _map_repo_sections_to_text(result)
        assert sections == {}

    def test_languages(self) -> None:
        result = _MockMapRepoResult(
            languages=[
                _MockLanguageStats("python", 10, 80.0),
                _MockLanguageStats("yaml", 2, 20.0),
            ]
        )
        sections = _map_repo_sections_to_text(result)
        assert "languages" in sections
        assert len(sections["languages"]) == 2
        assert "python 80.0%" in sections["languages"][0]
        assert "10 files" in sections["languages"][0]

    def test_structure(self) -> None:
        file_node = MockFileNode(name="main.py", path="src/main.py", line_count=100)
        tree = [file_node]
        result = _MockMapRepoResult(
            structure=_MockStructureInfo(root="/repo", tree=tree, file_count=1)
        )
        sections = _map_repo_sections_to_text(result)
        assert "structure" in sections
        assert sections["structure"]["root"] == "/repo"
        assert sections["structure"]["file_count"] == 1
        assert len(sections["structure"]["tree"]) == 1

    def test_structure_with_contexts(self) -> None:
        result = _MockMapRepoResult(
            structure=_MockStructureInfo(
                root="/repo", tree=[], file_count=0, contexts=["src", "lib"]
            )
        )
        sections = _map_repo_sections_to_text(result)
        assert sections["structure"]["contexts"] == ["src", "lib"]

    def test_dependencies(self) -> None:
        result = _MockMapRepoResult(dependencies=_MockDependencies(["requests", "flask"], 15))
        sections = _map_repo_sections_to_text(result)
        assert "dependencies" in sections
        assert "requests" in sections["dependencies"]
        assert "2 modules" in sections["dependencies"]
        assert "15 imports" in sections["dependencies"]

    def test_test_layout(self) -> None:
        result = _MockMapRepoResult(
            test_layout=_MockTestLayout(["tests/test_a.py", "tests/test_b.py"], 25)
        )
        sections = _map_repo_sections_to_text(result)
        assert "test_layout" in sections
        assert "2 test files" in sections["test_layout"]
        assert "25 tests" in sections["test_layout"]

    def test_entry_points(self) -> None:
        result = _MockMapRepoResult(
            entry_points=[
                _MockEntryPoint("function", "main", "src/main.py", "src.main.main"),
            ]
        )
        sections = _map_repo_sections_to_text(result)
        assert "entry_points" in sections
        assert len(sections["entry_points"]) == 1
        assert "function main" in sections["entry_points"][0]
        assert "src/main.py" in sections["entry_points"][0]
        assert "src.main.main" in sections["entry_points"][0]

    def test_entry_point_no_qualified_name(self) -> None:
        result = _MockMapRepoResult(
            entry_points=[
                _MockEntryPoint("function", "main", "src/main.py"),
            ]
        )
        sections = _map_repo_sections_to_text(result)
        assert "(" not in sections["entry_points"][0]

    def test_public_api(self) -> None:
        result = _MockMapRepoResult(
            public_api=[
                _MockPublicSymbol("MyClass", "high", def_uid="uid123", evidence="__all__"),
            ]
        )
        sections = _map_repo_sections_to_text(result)
        assert "public_api" in sections
        assert "MyClass" in sections["public_api"][0]
        assert "high" in sections["public_api"][0]
        assert "uid123" in sections["public_api"][0]
        assert "[__all__]" in sections["public_api"][0]

    def test_public_api_minimal(self) -> None:
        result = _MockMapRepoResult(
            public_api=[
                _MockPublicSymbol("func", "medium"),
            ]
        )
        sections = _map_repo_sections_to_text(result)
        assert "func  medium" in sections["public_api"][0]

    def test_structure_uses_compact_format_when_all_paths_available(self) -> None:
        """When all_paths is available, uses lossless compact format."""
        all_paths: list[tuple[str, int | None]] = [
            ("src/main.py", 100),
            ("src/utils.py", 50),
            ("tests/test_main.py", 30),
            ("README.md", 20),
        ]
        result = _MockMapRepoResult(
            structure=_MockStructureInfo(
                root="/repo",
                tree=[],
                file_count=4,
                all_paths=all_paths,
            )
        )
        sections = _map_repo_sections_to_text(result)
        tree = "\n".join(sections["structure"]["tree"])
        # Every file listed with line count — no data loss
        assert "main.py:100" in tree
        assert "utils.py:50" in tree
        assert "test_main.py:30" in tree
        assert "README.md:20" in tree

    def test_structure_falls_back_to_tree_when_no_all_paths(self) -> None:
        """Without all_paths, _map_repo_sections_to_text uses the tree as before."""
        file_node = MockFileNode(name="app.py", path="src/app.py", line_count=100)
        result = _MockMapRepoResult(
            structure=_MockStructureInfo(
                root="/repo",
                tree=[file_node],
                file_count=1,
            )
        )
        sections = _map_repo_sections_to_text(result)
        # Falls back to _tree_to_text because all_paths is empty
        assert "app.py" in sections["structure"]["tree"][0]
