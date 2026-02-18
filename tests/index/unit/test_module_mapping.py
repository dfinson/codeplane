"""Unit tests for module_mapping.py.

Tests cover:
- path_to_module: file paths → dotted module names
- module_to_candidate_paths: dotted modules → candidate lookup keys
- resolve_module_to_path: dotted module → file path via index
- build_module_index: file path list → module key map
"""

from __future__ import annotations

import pytest

from codeplane.index._internal.indexing.module_mapping import (
    build_module_index,
    file_to_import_candidates,
    file_to_import_sql_patterns,
    module_to_candidate_paths,
    path_to_module,
    resolve_module_to_path,
)

# ---------------------------------------------------------------------------
# path_to_module
# ---------------------------------------------------------------------------


class TestPathToModule:
    """Tests for path_to_module."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/codeplane/refactor/ops.py", "src.codeplane.refactor.ops"),
            ("codeplane/refactor/ops.py", "codeplane.refactor.ops"),
            ("foo.py", "foo"),
            ("src/codeplane/__init__.py", "src.codeplane"),
            ("a/b/__init__.py", "a.b"),
            # Non-Python files with known source extensions get module keys
            ("src/utils/helper.ts", "src.utils.helper"),
            ("lib/core.rs", "lib.core"),
            # Data/doc/config format extensions return None
            ("README.md", None),
            ("data/config.json", None),
            # Unknown extensions and empty strings return None
            ("", None),
        ],
    )
    def test_conversion(self, path: str, expected: str | None) -> None:
        assert path_to_module(path) == expected

    def test_backslash_normalised(self) -> None:
        """Windows-style paths are normalised to dots."""
        result = path_to_module("src\\codeplane\\ops.py")
        assert result == "src.codeplane.ops"


# ---------------------------------------------------------------------------
# module_to_candidate_paths
# ---------------------------------------------------------------------------


class TestModuleToCandidatePaths:
    """Tests for module_to_candidate_paths."""

    def test_basic_candidates(self) -> None:
        candidates = module_to_candidate_paths("codeplane.refactor.ops")
        assert "codeplane.refactor.ops" in candidates
        assert "src.codeplane.refactor.ops" in candidates
        # Slash-form candidates should NOT exist (path_to_module uses dots)
        assert "codeplane/refactor/ops" not in candidates
        assert "src/codeplane/refactor/ops" not in candidates

    def test_single_segment(self) -> None:
        candidates = module_to_candidate_paths("utils")
        assert "utils" in candidates
        assert "src.utils" in candidates


# ---------------------------------------------------------------------------
# build_module_index + resolve_module_to_path
# ---------------------------------------------------------------------------


class TestBuildAndResolve:
    """Tests for build_module_index and resolve_module_to_path."""

    @pytest.fixture
    def sample_index(self) -> dict[str, str]:
        return build_module_index(
            [
                "src/codeplane/refactor/ops.py",
                "src/codeplane/__init__.py",
                "tests/test_ops.py",
                "README.md",
            ]
        )

    def test_index_contains_python_files(self, sample_index: dict[str, str]) -> None:
        # Python files are indexed
        assert "src.codeplane.refactor.ops" in sample_index
        assert "src.codeplane" in sample_index
        assert "tests.test_ops" in sample_index
        # Data/doc format files (e.g. .md) are NOT indexed
        assert "README" not in sample_index

    def test_resolve_direct(self, sample_index: dict[str, str]) -> None:
        """Resolve with exact module key match."""
        result = resolve_module_to_path("src.codeplane.refactor.ops", sample_index)
        assert result == "src/codeplane/refactor/ops.py"

    def test_resolve_without_src_prefix(self, sample_index: dict[str, str]) -> None:
        """Resolve via src. prefix candidate."""
        result = resolve_module_to_path("codeplane.refactor.ops", sample_index)
        assert result == "src/codeplane/refactor/ops.py"

    def test_resolve_package_init(self, sample_index: dict[str, str]) -> None:
        result = resolve_module_to_path("codeplane", sample_index)
        assert result == "src/codeplane/__init__.py"

    def test_resolve_not_found(self, sample_index: dict[str, str]) -> None:
        result = resolve_module_to_path("nonexistent.module", sample_index)
        assert result is None


# ---------------------------------------------------------------------------
# file_to_import_candidates
# ---------------------------------------------------------------------------


class TestFileToImportCandidates:
    """Tests for file_to_import_candidates.

    This function generates all source_literal values that could import a file.
    It's the inverse of import resolution.
    """

    def test_python_src_layout(self) -> None:
        """Python files in src/ layout should generate both variants."""
        candidates = file_to_import_candidates(
            "src/codeplane/refactor/ops.py", language_family="python"
        )
        # Should include both with and without src. prefix
        assert "src.codeplane.refactor.ops" in candidates
        assert "codeplane.refactor.ops" in candidates

    def test_python_no_src_prefix(self) -> None:
        """Python files not in src/ should only have one variant."""
        candidates = file_to_import_candidates(
            "codeplane/refactor/ops.py", language_family="python"
        )
        assert "codeplane.refactor.ops" in candidates
        # Should NOT have src. prefix variant
        assert "src.codeplane.refactor.ops" not in candidates

    def test_python_init_file(self) -> None:
        """Python __init__.py should resolve to package."""
        candidates = file_to_import_candidates(
            "src/codeplane/__init__.py", language_family="python"
        )
        assert "src.codeplane" in candidates
        assert "codeplane" in candidates

    def test_lua_similar_to_python(self) -> None:
        """Lua uses same dotted path logic as Python."""
        candidates = file_to_import_candidates("src/game/utils.lua", language_family="lua")
        assert "src.game.utils" in candidates
        assert "game.utils" in candidates

    def test_declared_module_used(self) -> None:
        """Declaration-based langs use declared_module."""
        candidates = file_to_import_candidates(
            "pkg/util/helper.go",
            language_family="go",
            declared_module="github.com/user/repo/pkg/util",
        )
        assert "github.com/user/repo/pkg/util" in candidates

    def test_js_ts_no_candidates_without_declared_module(self) -> None:
        """JS/TS without declared_module returns empty (uses relative paths)."""
        candidates = file_to_import_candidates("src/utils/helper.ts", language_family="typescript")
        # JS/TS uses relative paths which require importer context
        # So no candidates from file path alone
        assert candidates == []

    def test_none_language_defaults_to_python_logic(self) -> None:
        """When language_family is None, use Python/path-based logic."""
        candidates = file_to_import_candidates("src/utils/helper.py", language_family=None)
        assert "src.utils.helper" in candidates
        assert "utils.helper" in candidates


# ---------------------------------------------------------------------------
# file_to_import_sql_patterns
# ---------------------------------------------------------------------------


class TestFileToImportSqlPatterns:
    """Tests for file_to_import_sql_patterns."""

    def test_python_generates_exact_and_prefix(self) -> None:
        """Python should generate exact matches and prefix patterns."""
        exact, prefixes = file_to_import_sql_patterns(
            "src/codeplane/refactor/ops.py", language_family="python"
        )
        # Exact matches
        assert "src.codeplane.refactor.ops" in exact
        assert "codeplane.refactor.ops" in exact
        # Prefix patterns (for submodule imports)
        assert "src.codeplane.refactor.ops." in prefixes
        assert "codeplane.refactor.ops." in prefixes

    def test_rust_uses_double_colon_separator(self) -> None:
        """Rust should use :: as separator for prefix patterns."""
        exact, prefixes = file_to_import_sql_patterns(
            "src/module/lib.rs",
            language_family="rust",
            declared_module="crate::module",
        )
        assert "crate::module" in exact
        assert "crate::module::" in prefixes

    def test_go_uses_slash_separator(self) -> None:
        """Go should use / as separator for prefix patterns."""
        exact, prefixes = file_to_import_sql_patterns(
            "pkg/util/helper.go",
            language_family="go",
            declared_module="github.com/user/repo/pkg/util",
        )
        assert "github.com/user/repo/pkg/util" in exact
        assert "github.com/user/repo/pkg/util/" in prefixes
