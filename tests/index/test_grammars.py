"""Tests for grammar installation logic."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from codeplane.index._internal.grammars import (
    GRAMMAR_PACKAGES,
    GrammarInstallResult,
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


class TestGrammarInstallResult:
    """Tests for GrammarInstallResult dataclass."""

    def test_dataclass_fields(self) -> None:
        """GrammarInstallResult has expected fields."""
        result = GrammarInstallResult(
            success=True,
            failed_packages=[],
            installed_packages=["tree-sitter-python"],
        )
        assert result.success is True
        assert result.failed_packages == []
        assert result.installed_packages == ["tree-sitter-python"]

    def test_partial_failure(self) -> None:
        """GrammarInstallResult can represent partial failure."""
        result = GrammarInstallResult(
            success=False,
            failed_packages=["tree-sitter-powershell", "tree-sitter-fsharp"],
            installed_packages=["tree-sitter-python", "tree-sitter-javascript"],
        )
        assert result.success is False
        assert len(result.failed_packages) == 2
        assert len(result.installed_packages) == 2


class TestInstallGrammars:
    """Tests for install_grammars."""

    def test_empty_packages(self) -> None:
        """Returns success GrammarInstallResult for empty package list."""
        result = install_grammars([])
        assert isinstance(result, GrammarInstallResult)
        assert result.success is True
        assert result.failed_packages == []
        assert result.installed_packages == []

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_successful_install(self, mock_run: MagicMock) -> None:
        """Returns success GrammarInstallResult on successful pip install."""
        mock_run.return_value = MagicMock(returncode=0)
        result = install_grammars([("tree-sitter-python", "0.23.0")])
        assert isinstance(result, GrammarInstallResult)
        assert result.success is True
        assert result.failed_packages == []
        assert "tree-sitter-python" in result.installed_packages
        mock_run.assert_called_once()

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_failed_install(self, mock_run: MagicMock) -> None:
        """Returns failure GrammarInstallResult with failed_packages on pip failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        result = install_grammars([("fake-package", "1.0.0")])
        assert isinstance(result, GrammarInstallResult)
        assert result.success is False
        assert "fake-package" in result.failed_packages
        assert result.installed_packages == []

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_partial_install_failure(self, mock_run: MagicMock) -> None:
        """Returns GrammarInstallResult with both installed and failed packages."""
        # First call succeeds, second fails
        mock_run.side_effect = [
            MagicMock(returncode=0),  # python succeeds
            MagicMock(returncode=1),  # fake fails
        ]
        result = install_grammars(
            [
                ("tree-sitter-python", "0.23.0"),
                ("fake-package", "1.0.0"),
            ]
        )
        assert isinstance(result, GrammarInstallResult)
        assert result.success is False  # Overall failure due to partial
        assert "tree-sitter-python" in result.installed_packages
        assert "fake-package" in result.failed_packages

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        """Returns failure GrammarInstallResult on timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=120)
        result = install_grammars([("tree-sitter-python", "0.23.0")])
        assert isinstance(result, GrammarInstallResult)
        assert result.success is False
        assert "tree-sitter-python" in result.failed_packages

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_multiple_packages_all_success(self, mock_run: MagicMock) -> None:
        """Returns success when all packages install successfully."""
        mock_run.return_value = MagicMock(returncode=0)
        result = install_grammars(
            [
                ("tree-sitter-python", "0.23.0"),
                ("tree-sitter-javascript", "0.23.0"),
                ("tree-sitter-go", "0.23.0"),
            ]
        )
        assert result.success is True
        assert len(result.installed_packages) == 3
        assert result.failed_packages == []

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_multiple_packages_all_fail(self, mock_run: MagicMock) -> None:
        """Returns failure with all packages in failed_packages."""
        mock_run.return_value = MagicMock(returncode=1)
        result = install_grammars(
            [
                ("fake-a", "1.0.0"),
                ("fake-b", "1.0.0"),
            ]
        )
        assert result.success is False
        assert len(result.failed_packages) == 2
        assert result.installed_packages == []

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_status_callback_on_success(self, mock_run: MagicMock) -> None:
        """Calls status_fn with progress messages."""
        mock_run.return_value = MagicMock(returncode=0)
        status_calls: list[str] = []

        def status_fn(msg: str, **_: object) -> None:
            status_calls.append(msg)

        install_grammars([("tree-sitter-python", "0.23.0")], status_fn=status_fn)
        assert any("Installing" in call for call in status_calls)

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_status_callback_on_failure(self, mock_run: MagicMock) -> None:
        """Calls status_fn with failure messages."""
        mock_run.return_value = MagicMock(returncode=1)
        status_calls: list[str] = []

        def status_fn(msg: str, **_: object) -> None:
            status_calls.append(msg)

        install_grammars([("fake-package", "1.0.0")], status_fn=status_fn)
        # Should have "Installing" message and "Failed to install" message
        assert any("Installing" in call for call in status_calls)
        assert any("Failed" in call for call in status_calls)

    @patch("codeplane.index._internal.grammars.subprocess.run")
    def test_invalidates_caches(self, mock_run: MagicMock) -> None:
        """Invalidates import caches after installation."""
        mock_run.return_value = MagicMock(returncode=0)

        with patch("importlib.invalidate_caches") as mock_invalidate:
            install_grammars([("tree-sitter-python", "0.23.0")])
            mock_invalidate.assert_called_once()


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
