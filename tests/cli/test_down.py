"""Tests for cpl down command."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pygit2
import pytest
from click.testing import CliRunner

from codeplane.cli.main import cli

runner = CliRunner()


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository with initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Test"
    repo.config["user.email"] = "test@test.com"

    (repo_path / "README.md").write_text("# Test repo")
    repo.index.add("README.md")
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Test", "test@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

    yield repo_path


@pytest.fixture
def temp_non_git(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary non-git directory."""
    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    yield non_git


@pytest.fixture
def initialized_repo(temp_git_repo: Path) -> Path:
    """Create an initialized but not running repo."""
    codeplane_dir = temp_git_repo / ".codeplane"
    codeplane_dir.mkdir()
    (codeplane_dir / "config.yaml").write_text("logging:\n  level: INFO\n")
    return temp_git_repo


class TestDownCommand:
    """cpl down command tests."""

    def test_given_non_git_dir_when_down_then_fails(self, temp_non_git: Path) -> None:
        """Down fails with error when run outside git repository."""
        result = runner.invoke(cli, ["down", str(temp_non_git)])
        assert result.exit_code != 0
        assert "not a git repository" in result.output

    def test_given_uninitialized_repo_when_down_then_reports_nothing_to_stop(
        self, temp_git_repo: Path
    ) -> None:
        """Down reports nothing to stop for uninitialized repo."""
        result = runner.invoke(cli, ["down", str(temp_git_repo)])
        assert result.exit_code == 0
        assert "not initialized" in result.output.lower()

    @patch("codeplane.cli.down.is_daemon_running")
    def test_given_initialized_repo_not_running_when_down_then_reports_not_running(
        self, mock_is_running: MagicMock, initialized_repo: Path
    ) -> None:
        """Down reports daemon not running for stopped daemon."""
        mock_is_running.return_value = False

        result = runner.invoke(cli, ["down", str(initialized_repo)])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    @patch("codeplane.cli.down.stop_daemon")
    @patch("codeplane.cli.down.read_daemon_info")
    @patch("codeplane.cli.down.is_daemon_running")
    def test_given_running_daemon_when_down_then_stops_daemon(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop_daemon: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Down stops a running daemon."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)
        mock_stop_daemon.return_value = True

        result = runner.invoke(cli, ["down", str(initialized_repo)])
        assert result.exit_code == 0
        assert "stopping" in result.output.lower() or "stopped" in result.output.lower()
        mock_stop_daemon.assert_called_once()

    @patch("codeplane.cli.down.stop_daemon")
    @patch("codeplane.cli.down.read_daemon_info")
    @patch("codeplane.cli.down.is_daemon_running")
    def test_given_running_daemon_when_stop_fails_then_reports_failure(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop_daemon: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Down reports failure when stop_daemon returns False."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)
        mock_stop_daemon.return_value = False

        result = runner.invoke(cli, ["down", str(initialized_repo)])
        assert result.exit_code == 0
        assert "failed" in result.output.lower() or "already exited" in result.output.lower()

    @patch("codeplane.cli.down.stop_daemon")
    @patch("codeplane.cli.down.read_daemon_info")
    @patch("codeplane.cli.down.is_daemon_running")
    def test_given_running_daemon_no_info_when_down_then_still_stops(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop_daemon: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Down still attempts to stop even if read_daemon_info returns None."""
        mock_is_running.return_value = True
        mock_read_info.return_value = None
        mock_stop_daemon.return_value = True

        result = runner.invoke(cli, ["down", str(initialized_repo)])
        assert result.exit_code == 0
        mock_stop_daemon.assert_called_once()
