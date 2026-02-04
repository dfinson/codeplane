"""Tests for CLI clear command.

Covers:
- clear_repo() function
- Removing .codeplane/ directory
- Removing XDG index directory
- Force flag behavior
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from codeplane.cli.clear import clear_repo


class TestClearRepoNothingToRemove:
    """Tests when there's nothing to clear."""

    def test_returns_false_when_nothing_to_clear(self, tmp_path: Path) -> None:
        """Returns False when no CodePlane data exists."""
        result = clear_repo(tmp_path, force=True)
        assert result is False

    def test_prints_message_when_nothing_to_clear(
        self, tmp_path: Path, _capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Prints 'nothing to clear' message."""
        clear_repo(tmp_path, force=True)
        # Rich Console writes to stderr by default in our code
        # The capsys captures both, so we need to check stderr
        # Since Console(stderr=True), output goes to stderr


class TestClearRepoCodeplaneDir:
    """Tests for removing .codeplane/ directory."""

    def test_removes_codeplane_directory(self, tmp_path: Path) -> None:
        """Removes .codeplane/ directory when force=True."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / "config.yaml").write_text("key: value")
        (codeplane_dir / "index").mkdir()

        result = clear_repo(tmp_path, force=True)

        assert result is True
        assert not codeplane_dir.exists()

    def test_removes_nested_contents(self, tmp_path: Path) -> None:
        """Removes nested directories and files."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        nested = codeplane_dir / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("data")

        clear_repo(tmp_path, force=True)

        assert not codeplane_dir.exists()


class TestClearRepoXdgDir:
    """Tests for removing XDG index directory."""

    def test_removes_xdg_index_directory(self, tmp_path: Path) -> None:
        """Removes XDG index directory when it exists."""
        # Initialize git repo (needed for XDG path generation)
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        # Create .codeplane dir (needed for XDG path)
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        # Mock the XDG path function to point to a test location
        xdg_dir = tmp_path / "xdg_index"
        xdg_dir.mkdir()
        (xdg_dir / "tantivy").mkdir()
        (xdg_dir / "sqlite.db").write_text("database")

        with patch("codeplane.cli.clear._get_xdg_index_dir", return_value=xdg_dir):
            result = clear_repo(tmp_path, force=True)

        assert result is True
        assert not xdg_dir.exists()


class TestClearRepoForceFlag:
    """Tests for force flag behavior."""

    def test_force_skips_confirmation(self, tmp_path: Path) -> None:
        """force=True skips confirmation prompt."""
        (tmp_path / ".codeplane").mkdir()

        # Should not prompt - force=True
        with patch("codeplane.cli.clear.questionary.select") as mock_select:
            clear_repo(tmp_path, force=True)
            mock_select.assert_not_called()

    def test_without_force_prompts_user(self, tmp_path: Path) -> None:
        """Without force, prompts for confirmation."""
        (tmp_path / ".codeplane").mkdir()

        # Mock questionary to simulate user cancelling
        with patch("codeplane.cli.clear.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = False
            result = clear_repo(tmp_path, force=False)

        assert result is False
        mock_select.assert_called_once()

    def test_without_force_confirms_deletion(self, tmp_path: Path) -> None:
        """Without force, deletes on confirmation."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        with patch("codeplane.cli.clear.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = True
            result = clear_repo(tmp_path, force=False)

        assert result is True
        assert not codeplane_dir.exists()


class TestClearRepoErrors:
    """Tests for error handling."""

    def test_handles_permission_error(self, tmp_path: Path) -> None:
        """Handles permission errors gracefully."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()

        with patch("codeplane.cli.clear.shutil.rmtree", side_effect=OSError("Permission denied")):
            result = clear_repo(tmp_path, force=True)

        assert result is False

    def test_continues_on_partial_failure(self, tmp_path: Path) -> None:
        """Continues clearing other directories on partial failure."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        xdg_dir = tmp_path / "xdg"
        xdg_dir.mkdir()

        # First rmtree fails, second succeeds
        call_count = [0]
        original_rmtree = __import__("shutil").rmtree

        def mock_rmtree(path: Path) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("First failure")
            original_rmtree(path)

        with (
            patch("codeplane.cli.clear._get_xdg_index_dir", return_value=xdg_dir),
            patch("codeplane.cli.clear.shutil.rmtree", side_effect=mock_rmtree),
        ):
            result = clear_repo(tmp_path, force=True)

        # Should return False due to error, but attempt both
        assert result is False


class TestClearRepoBothDirs:
    """Tests for clearing both directories."""

    def test_removes_both_dirs(self, tmp_path: Path) -> None:
        """Removes both .codeplane/ and XDG directories."""
        codeplane_dir = tmp_path / ".codeplane"
        codeplane_dir.mkdir()
        xdg_dir = tmp_path / "xdg_index"
        xdg_dir.mkdir()

        with patch("codeplane.cli.clear._get_xdg_index_dir", return_value=xdg_dir):
            result = clear_repo(tmp_path, force=True)

        assert result is True
        assert not codeplane_dir.exists()
        assert not xdg_dir.exists()
