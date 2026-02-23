"""Tests for index/_internal/indexing/resolver.py module.

Covers:
- ResolutionStats dataclass
- ReferenceResolver class
- resolve_references() convenience function
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codeplane.index._internal.indexing.resolver import (
    ReferenceResolver,
    ResolutionStats,
    resolve_references,
)


class TestResolutionStats:
    """Tests for ResolutionStats dataclass."""

    def test_default_values(self) -> None:
        """Default values are all zero."""
        stats = ResolutionStats()
        assert stats.refs_processed == 0
        assert stats.refs_resolved == 0
        assert stats.refs_unresolved == 0
        assert stats.refs_ambiguous == 0

    def test_custom_values(self) -> None:
        """Can set custom values."""
        stats = ResolutionStats(
            refs_processed=100,
            refs_resolved=80,
            refs_unresolved=15,
            refs_ambiguous=5,
        )
        assert stats.refs_processed == 100
        assert stats.refs_resolved == 80
        assert stats.refs_unresolved == 15
        assert stats.refs_ambiguous == 5

    def test_values_are_mutable(self) -> None:
        """Stats values can be modified."""
        stats = ResolutionStats()
        stats.refs_processed = 10
        stats.refs_resolved = 5
        assert stats.refs_processed == 10
        assert stats.refs_resolved == 5


class TestReferenceResolver:
    """Tests for ReferenceResolver class."""

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database."""
        db = MagicMock()
        session = MagicMock()
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)
        return db

    def test_init(self, mock_db: MagicMock) -> None:
        """Can create resolver."""
        resolver = ReferenceResolver(mock_db)
        assert resolver._db == mock_db

    def test_resolve_all_empty(self, mock_db: MagicMock) -> None:
        """Returns empty stats when no refs."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session
        session.exec.return_value.all.return_value = []

        resolver = ReferenceResolver(mock_db)
        stats = resolver.resolve_all()

        assert stats.refs_processed == 0
        assert stats.refs_resolved == 0

    def test_path_to_module_py_file(self, mock_db: MagicMock) -> None:
        """Converts .py file path to module."""
        resolver = ReferenceResolver(mock_db)

        assert resolver._path_to_module("src/foo.py") == "src.foo"
        assert resolver._path_to_module("bar.py") == "bar"

    def test_path_to_module_init(self, mock_db: MagicMock) -> None:
        """Handles __init__.py files."""
        resolver = ReferenceResolver(mock_db)

        assert resolver._path_to_module("src/foo/__init__.py") == "src.foo"

    def test_path_to_module_non_python(self, mock_db: MagicMock) -> None:
        """Returns None for non-Python files."""
        resolver = ReferenceResolver(mock_db)

        assert resolver._path_to_module("src/foo.txt") is None
        assert resolver._path_to_module("README.md") is None

    def test_path_to_module_js_ts(self, mock_db: MagicMock) -> None:
        """Converts JS/TS file paths to module."""
        resolver = ReferenceResolver(mock_db)

        assert resolver._path_to_module("src/foo.ts") == "src.foo"
        assert resolver._path_to_module("src/foo.tsx") == "src.foo"
        assert resolver._path_to_module("src/foo.js") == "src.foo"
        assert resolver._path_to_module("src/foo.jsx") == "src.foo"
        assert resolver._path_to_module("src/foo.mjs") == "src.foo"
        assert resolver._path_to_module("src/foo.cjs") == "src.foo"

    def test_path_to_module_js_index(self, mock_db: MagicMock) -> None:
        """Handles JS index.ts files."""
        resolver = ReferenceResolver(mock_db)

        assert resolver._path_to_module("src/foo/index.ts") == "src.foo"
        assert resolver._path_to_module("src/foo/index.js") == "src.foo"

    def test_path_to_module_rust(self, mock_db: MagicMock) -> None:
        """Converts Rust file paths to module."""
        resolver = ReferenceResolver(mock_db)

        assert resolver._path_to_module("src/foo/bar.rs") == "src::foo::bar"
        assert resolver._path_to_module("src/foo/mod.rs") == "src::foo"
        assert resolver._path_to_module("src/foo/lib.rs") == "src::foo"

    def test_path_to_module_windows_path(self, mock_db: MagicMock) -> None:
        """Handles Windows-style paths."""
        resolver = ReferenceResolver(mock_db)

        result = resolver._path_to_module("src\\foo.py")
        assert result == "src.foo"

    def test_find_module_file_direct(self, mock_db: MagicMock) -> None:
        """Finds file with direct module path match."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"foo.bar": 42}
        resolver._file_paths = {}

        result = resolver._find_module_file("foo.bar")
        assert result == 42

    def test_find_module_file_not_found(self, mock_db: MagicMock) -> None:
        """Returns None when module not found."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {}
        resolver._file_paths = {}

        result = resolver._find_module_file("nonexistent.module")
        assert result is None

    def test_find_module_file_python_relative_single_dot(self, mock_db: MagicMock) -> None:
        """Resolves Python single-dot relative import."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"src.pkg.utils": 10}
        resolver._file_paths = {5: "src/pkg/main.py"}

        # from .utils import X in src/pkg/main.py → src.pkg.utils
        result = resolver._find_module_file(".utils", importing_file_id=5)
        assert result == 10

    def test_find_module_file_python_relative_double_dot(self, mock_db: MagicMock) -> None:
        """Resolves Python double-dot relative import."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"src.pkg.core.base_model": 20}
        resolver._file_paths = {15: "src/pkg/eval/model_evaluator.py"}

        # from ..core.base_model import X in src/pkg/eval/model_evaluator.py
        result = resolver._find_module_file("..core.base_model", importing_file_id=15)
        assert result == 20

    def test_find_module_file_python_relative_package(self, mock_db: MagicMock) -> None:
        """Resolves Python relative import to package __init__."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"src.pkg.config": 30}
        resolver._file_paths = {15: "src/pkg/eval/model_evaluator.py"}

        # from ..config import Config → src.pkg.config
        result = resolver._find_module_file("..config", importing_file_id=15)
        assert result == 30

    def test_find_module_file_js_relative(self, mock_db: MagicMock) -> None:
        """Resolves JS/TS path-relative import."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"src.components.button": 40}
        resolver._file_paths = {35: "src/components/app.ts"}

        result = resolver._find_module_file("./button", importing_file_id=35)
        assert result == 40

    def test_find_module_file_js_relative_parent(self, mock_db: MagicMock) -> None:
        """Resolves JS/TS parent-directory relative import."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"src.utils.helpers": 50}
        resolver._file_paths = {45: "src/components/app.ts"}

        result = resolver._find_module_file("../utils/helpers", importing_file_id=45)
        assert result == 50

    def test_find_module_file_rust_super(self, mock_db: MagicMock) -> None:
        """Resolves Rust super:: relative import."""
        resolver = ReferenceResolver(mock_db)
        resolver._module_to_file = {"src::bar::utils": 60}
        resolver._file_paths = {55: "src/bar/baz.rs"}

        result = resolver._find_module_file("super::utils", importing_file_id=55)
        assert result == 60


class TestResolveReferencesFunction:
    """Tests for resolve_references convenience function."""

    def test_calls_resolve_all_when_no_file_ids(self) -> None:
        """Calls resolve_all when file_ids is None."""
        with patch.object(ReferenceResolver, "resolve_all") as mock_resolve:
            mock_resolve.return_value = ResolutionStats(refs_processed=10)

            mock_db = MagicMock()
            stats = resolve_references(mock_db, file_ids=None)

            mock_resolve.assert_called_once()
            assert stats.refs_processed == 10

    def test_calls_resolve_for_files_when_file_ids_provided(self) -> None:
        """Calls resolve_for_files when file_ids is provided."""
        with patch.object(ReferenceResolver, "resolve_for_files") as mock_resolve:
            mock_resolve.return_value = ResolutionStats(refs_resolved=5)

            mock_db = MagicMock()
            stats = resolve_references(mock_db, file_ids=[1, 2, 3])

            mock_resolve.assert_called_once_with([1, 2, 3], None)
            assert stats.refs_resolved == 5
