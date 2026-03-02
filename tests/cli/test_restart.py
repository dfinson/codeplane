"""Tests for cpl restart command."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
def initialized_repo(temp_git_repo: Path) -> Path:
    """Create an initialized repo."""
    codeplane_dir = temp_git_repo / ".codeplane"
    codeplane_dir.mkdir()
    (codeplane_dir / "config.yaml").write_text(
        "logging:\n  level: INFO\nserver:\n  host: 127.0.0.1\n  port: 0\n"
    )
    (codeplane_dir / ".cplignore").write_text("# Test\n")
    return temp_git_repo


class TestRestartCommand:
    """cpl restart command tests."""

    def test_given_non_git_dir_when_restart_then_fails(self, tmp_path: Path) -> None:
        """Restart fails with error when run outside git repository."""
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()
        result = runner.invoke(cli, ["restart", str(non_git)])
        assert result.exit_code != 0
        assert "Not inside a git repository" in result.output

    @patch("codeplane.daemon.lifecycle.run_server")
    @patch("codeplane.cli.up.IndexCoordinator")
    @patch("codeplane.cli.restart.is_server_running")
    def test_given_not_running_when_restart_then_starts_fresh(
        self,
        mock_is_running: MagicMock,
        mock_coordinator_class: MagicMock,
        mock_run_server: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Restart starts fresh when no daemon is running."""
        mock_is_running.return_value = False

        mock_coordinator = MagicMock()
        mock_coordinator.load_existing = AsyncMock(return_value=True)
        mock_coordinator.close = MagicMock()
        mock_coordinator_class.return_value = mock_coordinator

        mock_run_server.side_effect = KeyboardInterrupt()

        # Also mock is_server_running in up.py (different import)
        with patch("codeplane.daemon.lifecycle.is_server_running", return_value=False):
            result = runner.invoke(cli, ["restart", str(initialized_repo)])

        assert "starting fresh" in result.output.lower()

    @patch("codeplane.daemon.lifecycle.run_server")
    @patch("codeplane.cli.up.IndexCoordinator")
    @patch("codeplane.cli.restart.stop_daemon")
    @patch("codeplane.cli.restart.read_server_info")
    @patch("codeplane.cli.restart.is_server_running")
    def test_given_running_when_restart_then_stops_then_starts(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop: MagicMock,
        mock_coordinator_class: MagicMock,
        mock_run_server: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Restart stops the running daemon, then starts a new one."""
        # First call: running (stop phase), subsequent: not running
        mock_is_running.side_effect = [True, False]
        mock_read_info.return_value = (12345, 7654)
        mock_stop.return_value = True

        mock_coordinator = MagicMock()
        mock_coordinator.load_existing = AsyncMock(return_value=True)
        mock_coordinator.close = MagicMock()
        mock_coordinator_class.return_value = mock_coordinator

        mock_run_server.side_effect = KeyboardInterrupt()

        with patch("codeplane.daemon.lifecycle.is_server_running", return_value=False):
            result = runner.invoke(cli, ["restart", str(initialized_repo)])

        assert "stopping" in result.output.lower()
        assert "12345" in result.output
        mock_stop.assert_called_once()

    @patch("codeplane.cli.restart.stop_daemon")
    @patch("codeplane.cli.restart.read_server_info")
    @patch("codeplane.cli.restart.is_server_running")
    def test_given_running_when_stop_fails_then_exits(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Restart exits with error if stop signal fails."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 7654)
        mock_stop.return_value = False

        result = runner.invoke(cli, ["restart", str(initialized_repo)])
        assert result.exit_code != 0

    @patch("codeplane.cli.restart.stop_daemon")
    @patch("codeplane.cli.restart.read_server_info")
    @patch("codeplane.cli.restart.is_server_running")
    def test_given_running_when_stop_timeout_then_exits(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_stop: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Restart exits with error if daemon doesn't stop within timeout."""
        # Always returns True — daemon never stops
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 7654)
        mock_stop.return_value = True

        with patch("codeplane.cli.restart.time.sleep"):
            result = runner.invoke(cli, ["restart", str(initialized_repo)])

        assert result.exit_code != 0
        assert "did not stop" in result.output.lower()
