"""Tests for CLI utilities.

Covers:
- find_repo_root() function
- Edge cases for git detection
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
import pytest

from codeplane.cli.utils import find_repo_root


class TestFindRepoRoot:
    """Tests for find_repo_root function."""

    def test_finds_root_from_root(self, tmp_path: Path) -> None:
        """Finds repo root when starting from root."""
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        result = find_repo_root(tmp_path)
        assert result == tmp_path

    def test_finds_root_from_subdirectory(self, tmp_path: Path) -> None:
        """Finds repo root when starting from subdirectory."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        # Create nested directories
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)

        result = find_repo_root(nested)
        assert result == tmp_path

    def test_finds_root_from_deep_nesting(self, tmp_path: Path) -> None:
        """Finds repo root from deeply nested directory."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        deep = tmp_path / "level1" / "level2" / "level3" / "level4" / "level5"
        deep.mkdir(parents=True)

        result = find_repo_root(deep)
        assert result == tmp_path

    def test_raises_when_not_in_repo(self, tmp_path: Path) -> None:
        """Raises ClickException when not in a git repository."""
        # No git init - just an empty directory
        with pytest.raises(click.ClickException) as exc_info:
            find_repo_root(tmp_path)

        assert "Not inside a git repository" in str(exc_info.value)

    def test_error_message_includes_path(self, tmp_path: Path) -> None:
        """Error message includes the searched path."""
        with pytest.raises(click.ClickException) as exc_info:
            find_repo_root(tmp_path)

        assert str(tmp_path) in str(exc_info.value.message)

    def test_uses_cwd_when_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Uses current working directory when start_path is None."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        monkeypatch.chdir(tmp_path)
        result = find_repo_root(None)
        assert result == tmp_path

    def test_uses_cwd_from_subdirectory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uses cwd and walks up when in subdirectory."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        subdir = tmp_path / "sub"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        result = find_repo_root(None)
        assert result == tmp_path

    def test_resolves_symlinks(self, tmp_path: Path) -> None:
        """Resolves symlinks when finding repo root."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        # Create a symlink to a subdirectory
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        result = find_repo_root(link_dir)
        assert result == tmp_path

    def test_handles_relative_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Handles relative paths correctly."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

        subdir = tmp_path / "sub"
        subdir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = find_repo_root(Path("sub"))
        assert result == tmp_path
