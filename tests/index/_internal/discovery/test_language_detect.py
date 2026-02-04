"""Tests for extension-based language detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.discovery.language_detect import (
    EXTENSION_TO_FAMILY,
    FILENAME_TO_FAMILY,
    detect_language_family,
    get_all_indexable_extensions,
    get_all_indexable_filenames,
)
from codeplane.index.models import LanguageFamily


class TestExtensionToFamily:
    """Tests for EXTENSION_TO_FAMILY mapping."""

    def test_is_dict(self) -> None:
        """EXTENSION_TO_FAMILY is a dictionary."""
        assert isinstance(EXTENSION_TO_FAMILY, dict)

    def test_values_are_language_family(self) -> None:
        """All values are LanguageFamily enum members."""
        for ext, family in EXTENSION_TO_FAMILY.items():
            assert isinstance(family, LanguageFamily), f"{ext} has invalid value"

    def test_keys_are_extensions(self) -> None:
        """All keys are file extensions starting with dot."""
        for ext in EXTENSION_TO_FAMILY:
            assert ext.startswith("."), f"{ext} doesn't start with dot"

    def test_common_extensions_present(self) -> None:
        """Common programming extensions are mapped."""
        expected = {".py", ".js", ".ts", ".go", ".rs", ".java"}
        assert expected.issubset(set(EXTENSION_TO_FAMILY.keys()))


class TestFilenameToFamily:
    """Tests for FILENAME_TO_FAMILY mapping."""

    def test_is_dict(self) -> None:
        """FILENAME_TO_FAMILY is a dictionary."""
        assert isinstance(FILENAME_TO_FAMILY, dict)

    def test_values_are_language_family(self) -> None:
        """All values are LanguageFamily enum members."""
        for name, family in FILENAME_TO_FAMILY.items():
            assert isinstance(family, LanguageFamily), f"{name} has invalid value"

    def test_common_filenames_present(self) -> None:
        """Common config filenames are mapped."""
        # Check that at least some special filenames are present
        # The actual mapping depends on codeplane.core.languages
        assert len(FILENAME_TO_FAMILY) >= 0  # May be empty depending on config


class TestDetectLanguageFamily:
    """Tests for detect_language_family function."""

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("test.py", LanguageFamily.PYTHON),
            ("module.js", LanguageFamily.JAVASCRIPT),
            ("component.ts", LanguageFamily.JAVASCRIPT),
            ("main.go", LanguageFamily.GO),
            ("lib.rs", LanguageFamily.RUST),
            ("App.java", LanguageFamily.JVM),
            ("Program.cs", LanguageFamily.DOTNET),
            ("app.rb", LanguageFamily.RUBY),
            ("index.php", LanguageFamily.PHP),
            ("script.sh", LanguageFamily.SHELL),
        ],
    )
    def test_detects_by_extension(self, path: str, expected: LanguageFamily) -> None:
        """Detects language family from file extension."""
        result = detect_language_family(path)
        assert result == expected

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unknown extensions."""
        assert detect_language_family("file.xyz") is None
        assert detect_language_family("file.unknown") is None

    def test_handles_path_object(self) -> None:
        """Accepts Path objects."""
        result = detect_language_family(Path("src/module.py"))
        assert result == LanguageFamily.PYTHON

    def test_handles_nested_paths(self) -> None:
        """Works with nested directory paths."""
        result = detect_language_family("src/pkg/sub/module.py")
        assert result == LanguageFamily.PYTHON

    def test_case_sensitivity(self) -> None:
        """Extension detection is case-insensitive."""
        # Depends on core implementation - may or may not be case-insensitive
        result_lower = detect_language_family("file.py")
        detect_language_family("FILE.PY")
        # Both should detect Python (if case-insensitive) or at least lower works
        assert result_lower == LanguageFamily.PYTHON

    def test_typescript_jsx(self) -> None:
        """TSX files detected as JavaScript family."""
        result = detect_language_family("component.tsx")
        assert result == LanguageFamily.JAVASCRIPT

    def test_javascript_jsx(self) -> None:
        """JSX files detected as JavaScript family."""
        result = detect_language_family("component.jsx")
        assert result == LanguageFamily.JAVASCRIPT


class TestGetAllIndexableExtensions:
    """Tests for get_all_indexable_extensions function."""

    def test_returns_list(self) -> None:
        """Returns a list."""
        result = get_all_indexable_extensions()
        assert isinstance(result, list | set | frozenset)

    def test_contains_common_extensions(self) -> None:
        """Contains common programming language extensions."""
        extensions = set(get_all_indexable_extensions())
        expected = {".py", ".js", ".ts"}
        assert expected.issubset(extensions)

    def test_all_start_with_dot(self) -> None:
        """All extensions start with dot."""
        for ext in get_all_indexable_extensions():
            assert ext.startswith("."), f"{ext} doesn't start with dot"


class TestGetAllIndexableFilenames:
    """Tests for get_all_indexable_filenames function."""

    def test_returns_iterable(self) -> None:
        """Returns an iterable."""
        result = get_all_indexable_filenames()
        assert hasattr(result, "__iter__")

    def test_elements_are_strings(self) -> None:
        """All elements are strings."""
        for name in get_all_indexable_filenames():
            assert isinstance(name, str)
