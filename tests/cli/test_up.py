"""Tests for cpl up command."""

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
def temp_non_git(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary non-git directory."""
    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    yield non_git


@pytest.fixture
def initialized_repo(temp_git_repo: Path) -> Path:
    """Create an initialized repo."""
    codeplane_dir = temp_git_repo / ".codeplane"
    codeplane_dir.mkdir()
    (codeplane_dir / "config.yaml").write_text(
        "logging:\n  level: INFO\ndaemon:\n  host: 127.0.0.1\n  port: 0\n"
    )
    (codeplane_dir / ".cplignore").write_text("# Test\n")
    return temp_git_repo


class TestUpCommand:
    """cpl up command tests."""

    def test_given_non_git_dir_when_up_then_fails(self, temp_non_git: Path) -> None:
        """Up fails with error when run outside git repository."""
        result = runner.invoke(cli, ["up", str(temp_non_git)])
        assert result.exit_code != 0
        assert "not a git repository" in result.output

    @patch("codeplane.cli.up.read_daemon_info")
    @patch("codeplane.cli.up.is_daemon_running")
    def test_given_already_running_when_up_then_reports_running(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Up reports daemon already running if it is."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)

        result = runner.invoke(cli, ["up", str(initialized_repo)])
        assert result.exit_code == 0
        assert "already running" in result.output.lower()
        assert "12345" in result.output
        assert "8765" in result.output

    @patch("codeplane.cli.up.os.fork")
    @patch("codeplane.cli.up.IndexCoordinator")
    @patch("codeplane.cli.up.is_daemon_running")
    def test_given_not_running_when_up_then_forks_daemon(
        self,
        mock_is_running: MagicMock,
        mock_coordinator_class: MagicMock,
        mock_fork: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Up forks a daemon process when not running."""
        mock_is_running.return_value = False

        # Mock the coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.initialize = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        # Simulate parent process (fork returns child PID)
        mock_fork.return_value = 54321

        result = runner.invoke(cli, ["up", str(initialized_repo)])
        # Parent exits with 0 after fork
        # The CLI runner captures SystemExit
        assert "started" in result.output.lower() or result.exit_code == 0

    @patch("codeplane.cli.up.initialize_repo")
    @patch("codeplane.cli.up.is_daemon_running")
    def test_given_uninitialized_repo_when_up_then_auto_inits(
        self,
        mock_is_running: MagicMock,
        mock_init: MagicMock,
        temp_git_repo: Path,
    ) -> None:
        """Up auto-initializes the repo if not initialized."""
        mock_is_running.return_value = False
        mock_init.return_value = False  # Simulate init failure to avoid full daemon start

        runner.invoke(cli, ["up", str(temp_git_repo)])
        # Should call initialize_repo
        mock_init.assert_called_once_with(temp_git_repo.resolve())

    @patch("codeplane.cli.up.run_daemon")
    @patch("codeplane.cli.up.IndexCoordinator")
    @patch("codeplane.cli.up.is_daemon_running")
    def test_given_foreground_flag_when_up_then_runs_in_foreground(
        self,
        mock_is_running: MagicMock,
        mock_coordinator_class: MagicMock,
        mock_run_daemon: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Up --foreground runs daemon in foreground without forking."""
        mock_is_running.return_value = False

        mock_coordinator = MagicMock()
        mock_coordinator.initialize = AsyncMock()
        mock_coordinator.close = MagicMock()
        mock_coordinator_class.return_value = mock_coordinator

        # Make run_daemon raise KeyboardInterrupt to simulate Ctrl+C
        mock_run_daemon.side_effect = KeyboardInterrupt()

        result = runner.invoke(cli, ["up", "--foreground", str(initialized_repo)])
        # Should complete (KeyboardInterrupt is caught)
        assert "stopped" in result.output.lower() or result.exit_code == 0
        mock_coordinator.close.assert_called()

    @patch("codeplane.cli.up.os.fork")
    @patch("codeplane.cli.up.IndexCoordinator")
    @patch("codeplane.cli.up.load_config")
    @patch("codeplane.cli.up.is_daemon_running")
    def test_given_port_option_when_up_then_uses_specified_port(
        self,
        mock_is_running: MagicMock,
        mock_load_config: MagicMock,
        mock_coordinator_class: MagicMock,
        mock_fork: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Up --port overrides configured port."""
        mock_is_running.return_value = False

        # Create a mock config
        mock_config = MagicMock()
        mock_config.daemon.host = "127.0.0.1"
        mock_config.daemon.port = 8000
        mock_load_config.return_value = mock_config

        mock_coordinator = MagicMock()
        mock_coordinator.initialize = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        mock_fork.return_value = 54321  # Parent process

        runner.invoke(cli, ["up", "--port", "9999", str(initialized_repo)])

        # Config port should be overridden
        assert mock_config.daemon.port == 9999
