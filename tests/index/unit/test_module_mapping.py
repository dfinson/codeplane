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
            # Non-Python files return None
            ("README.md", None),
            ("data/config.json", None),
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
        # Non-Python files are excluded
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
