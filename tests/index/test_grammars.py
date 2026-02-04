"""Tests for grammar installation logic."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from codeplane.index._internal.grammars import (
    GRAMMAR_PACKAGES,
    get_needed_grammars,
    install_grammars,
    is_grammar_installed,
    scan_repo_languages,
)
from codeplane.index.models import LanguageFamily


class TestIsGrammarInstalled:
    """Tests for is_grammar_installed."""

    def test_installed_module(self) -> None:
        """Returns True for installed modules."""
        # os is always installed
        assert is_grammar_installed("os") is True

    def test_missing_module(self) -> None:
        """Returns False for missing modules."""
        assert is_grammar_installed("nonexistent_module_xyz") is False


class TestGetNeededGrammars:
    """Tests for get_needed_grammars."""

    def test_empty_languages(self) -> None:
        """Returns empty list for no languages."""
        assert get_needed_grammars(set()) == []

    def test_unknown_language(self) -> None:
        """Skips languages not in GRAMMAR_PACKAGES."""
        # Create a fake language family value that's not mapped
        result = get_needed_grammars({LanguageFamily.MATLAB})
        # MATLAB has no grammar in GRAMMAR_PACKAGES, should be skipped
        assert result == []

    @patch("codeplane.index._internal.grammars.is_grammar_installed")
    def test_already_installed(self, mock_installed: MagicMock) -> None:
        """Returns empty if grammars already installed."""
        mock_installed.return_value = True
        result = get_needed_grammars({LanguageFamily.PYTHON})
        assert result == []

    @patch("codeplane.index._internal.grammars.is_grammar_installed")
    def test_needs_installation(self, mock_installed: MagicMock) -> None:
        """Returns packages that need installation."""
        mock_installed.return_value = False
        result = get_needed_grammars({LanguageFamily.PYTHON})
        pkg, version, _ = GRAMMAR_PACKAGES[LanguageFamily.PYTHON]
        assert (pkg, version) in result

    @patch("codeplane.index._internal.grammars.is_grammar_installed")
    def test_includes_extra_packages(self, mock_installed: MagicMock) -> None:
        """Returns extra packages for language families that need them."""
        mock_installed.return_value = False
        result = get_needed_grammars({LanguageFamily.JAVASCRIPT})
        # JavaScript has typescript as extra
        pkg_names = [p for p, _ in result]
        assert "tree-sitter-javascript" in pkg_names
        assert "tree-sitter-typescript" in pkg_names


class TestInstallGrammars:
    """Tests for install_grammars."""

    def test_empty_packages(self) -> None:
        """Returns True for empty package list."""
        assert install_grammars([]) is True

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_successful_install(self, mock_run: MagicMock) -> None:
        """Returns True on successful pip install."""
        mock_run.return_value = MagicMock(returncode=0)
        result = install_grammars([("tree-sitter-python", "0.23.0")])
        assert result is True
        mock_run.assert_called_once()

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_failed_install(self, mock_run: MagicMock) -> None:
        """Returns False on pip install failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        result = install_grammars([("fake-package", "1.0.0")])
        assert result is False

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        """Returns False on timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=300)
        result = install_grammars([("tree-sitter-python", "0.23.0")])
        assert result is False

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_status_callback(self, mock_run: MagicMock) -> None:
        """Calls status_fn with progress messages."""
        mock_run.return_value = MagicMock(returncode=0)
        status_calls: list[str] = []

        def status_fn(msg: str, **_: object) -> None:
            status_calls.append(msg)

        install_grammars([("tree-sitter-python", "0.23.0")], status_fn=status_fn)
        assert any("Installing" in call for call in status_calls)


class TestScanRepoLanguages:
    """Tests for scan_repo_languages."""

    def test_scan_python_files(self, tmp_path: Path) -> None:
        """Detects Python from .py files."""
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / ".git").mkdir()

        with patch("codeplane.index._internal.grammars.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main.py\n")
            languages = scan_repo_languages(tmp_path)

        assert LanguageFamily.PYTHON in languages

    def test_scan_multiple_languages(self, tmp_path: Path) -> None:
        """Detects multiple languages."""
        (tmp_path / "main.py").write_text("")
        (tmp_path / "app.js").write_text("")
        (tmp_path / ".git").mkdir()

        with patch("codeplane.index._internal.grammars.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main.py\napp.js\n")
            languages = scan_repo_languages(tmp_path)

        assert LanguageFamily.PYTHON in languages
        assert LanguageFamily.JAVASCRIPT in languages

    def test_fallback_to_walk(self, tmp_path: Path) -> None:
        """Falls back to filesystem walk if git fails."""
        (tmp_path / "main.py").write_text("")

        with patch("codeplane.index._internal.grammars.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            languages = scan_repo_languages(tmp_path)

        assert LanguageFamily.PYTHON in languages

    def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        """Skips hidden directories when walking filesystem."""
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "secret.py").write_text("")

        with patch("codeplane.index._internal.grammars.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            languages = scan_repo_languages(tmp_path)

        # Should not detect Python from hidden dir
        assert LanguageFamily.PYTHON not in languages
