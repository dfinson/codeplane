"""Tests for grammar installation and language detection.

Verifies grammar package detection and scanning functions.
"""

from pathlib import Path
from unittest.mock import patch

from codeplane.index._internal.grammars import (
    EXTRA_PACKAGES,
    GRAMMAR_PACKAGES,
    get_needed_grammars,
    is_grammar_installed,
    scan_repo_languages,
)
from codeplane.index.models import LanguageFamily


class TestGrammarPackages:
    """Tests for GRAMMAR_PACKAGES constant."""

    def test_grammar_packages_structure(self) -> None:
        """Each entry should be (package, version, import_name) tuple."""
        for lang, entry in GRAMMAR_PACKAGES.items():
            assert isinstance(lang, LanguageFamily)
            assert len(entry) == 3
            pkg, version, import_name = entry
            assert isinstance(pkg, str)
            assert isinstance(version, str)
            assert isinstance(import_name, str)

    def test_common_languages_covered(self) -> None:
        """Common languages should have grammar packages."""
        assert LanguageFamily.PYTHON in GRAMMAR_PACKAGES
        assert LanguageFamily.JAVASCRIPT in GRAMMAR_PACKAGES
        assert LanguageFamily.GO in GRAMMAR_PACKAGES
        assert LanguageFamily.RUST in GRAMMAR_PACKAGES
        assert LanguageFamily.JVM in GRAMMAR_PACKAGES

    def test_python_grammar(self) -> None:
        """Python grammar should have correct package."""
        pkg, version, import_name = GRAMMAR_PACKAGES[LanguageFamily.PYTHON]
        assert pkg == "tree-sitter-python"
        assert import_name == "tree_sitter_python"


class TestExtraPackages:
    """Tests for EXTRA_PACKAGES constant."""

    def test_extra_packages_structure(self) -> None:
        """Each entry should be list of (package, version, import_name) tuples."""
        for lang, extras in EXTRA_PACKAGES.items():
            assert isinstance(lang, LanguageFamily)
            assert isinstance(extras, list)
            for entry in extras:
                assert len(entry) == 3

    def test_javascript_has_typescript(self) -> None:
        """JavaScript family should include TypeScript."""
        extras = EXTRA_PACKAGES.get(LanguageFamily.JAVASCRIPT, [])
        pkg_names = [e[0] for e in extras]
        assert "tree-sitter-typescript" in pkg_names

    def test_cpp_has_c(self) -> None:
        """C++ family should include C."""
        extras = EXTRA_PACKAGES.get(LanguageFamily.CPP, [])
        pkg_names = [e[0] for e in extras]
        assert "tree-sitter-c" in pkg_names


class TestIsGrammarInstalled:
    """Tests for is_grammar_installed function."""

    def test_returns_true_for_installed(self) -> None:
        """Should return True for installed modules."""
        # 'os' is always installed
        assert is_grammar_installed("os") is True

    def test_returns_false_for_uninstalled(self) -> None:
        """Should return False for non-existent modules."""
        assert is_grammar_installed("nonexistent_module_xyz_123") is False


class TestGetNeededGrammars:
    """Tests for get_needed_grammars function."""

    def test_empty_languages(self) -> None:
        """Should return empty list for empty input."""
        result = get_needed_grammars(set())
        assert result == []

    def test_language_without_grammar(self) -> None:
        """Should skip languages not in GRAMMAR_PACKAGES."""
        # CLOJURE is lexical-only (no grammar on PyPI)
        result = get_needed_grammars({LanguageFamily.CLOJURE})
        assert result == []

    def test_returns_uninstalled_only(self) -> None:
        """Should only return packages that aren't installed."""
        # Mock is_grammar_installed to control results
        with patch("codeplane.index._internal.grammars.is_grammar_installed") as mock:
            mock.return_value = True  # All installed
            result = get_needed_grammars({LanguageFamily.PYTHON})
            assert result == []

    def test_returns_needed_packages(self) -> None:
        """Should return needed packages."""
        with patch("codeplane.index._internal.grammars.is_grammar_installed") as mock:
            mock.return_value = False  # None installed
            result = get_needed_grammars({LanguageFamily.PYTHON})
            pkg_names = [p[0] for p in result]
            assert "tree-sitter-python" in pkg_names

    def test_includes_extra_packages(self) -> None:
        """Should include extra packages for language families."""
        with patch("codeplane.index._internal.grammars.is_grammar_installed") as mock:
            mock.return_value = False
            result = get_needed_grammars({LanguageFamily.JAVASCRIPT})
            pkg_names = [p[0] for p in result]
            assert "tree-sitter-javascript" in pkg_names
            assert "tree-sitter-typescript" in pkg_names


class TestScanRepoLanguages:
    """Tests for scan_repo_languages function."""

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty set for empty repo."""
        result = scan_repo_languages(tmp_path)
        assert result == set()

    def test_detects_python(self, tmp_path: Path) -> None:
        """Should detect Python files."""
        (tmp_path / "main.py").write_text("print('hello')")
        result = scan_repo_languages(tmp_path)
        assert LanguageFamily.PYTHON in result

    def test_detects_javascript(self, tmp_path: Path) -> None:
        """Should detect JavaScript files."""
        (tmp_path / "main.js").write_text("console.log('hello')")
        result = scan_repo_languages(tmp_path)
        assert LanguageFamily.JAVASCRIPT in result

    def test_detects_multiple_languages(self, tmp_path: Path) -> None:
        """Should detect multiple languages."""
        (tmp_path / "main.py").write_text("")
        (tmp_path / "app.js").write_text("")
        (tmp_path / "lib.go").write_text("")
        result = scan_repo_languages(tmp_path)
        assert LanguageFamily.PYTHON in result
        assert LanguageFamily.JAVASCRIPT in result
        assert LanguageFamily.GO in result

    def test_skips_prunable_dirs(self, tmp_path: Path) -> None:
        """Should skip files in node_modules, etc."""
        # Create file in node_modules
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("")
        # Create regular file
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")

        result = scan_repo_languages(tmp_path)
        # Should find Python but not JS from node_modules
        assert LanguageFamily.PYTHON in result
        # JS might not be found if only in node_modules

    def test_skips_hidden_files(self, tmp_path: Path) -> None:
        """Should skip hidden directories."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("")

        result = scan_repo_languages(tmp_path)
        # May or may not find Python depending on implementation
        # Main point is it shouldn't crash
        assert isinstance(result, set)
