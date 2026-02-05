"""Tests for cpl status command."""

from __future__ import annotations

import json
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


@pytest.fixture
def running_repo(initialized_repo: Path) -> Path:
    """Create an initialized repo with running daemon files."""
    codeplane_dir = initialized_repo / ".codeplane"
    (codeplane_dir / "daemon.pid").write_text("12345")
    (codeplane_dir / "daemon.port").write_text("8765")
    return initialized_repo


class TestStatusCommand:
    """cpl status command tests."""

    def test_given_non_git_dir_when_status_then_fails(self, temp_non_git: Path) -> None:
        """Status fails with error when run outside git repository."""
        result = runner.invoke(cli, ["status", str(temp_non_git)])
        assert result.exit_code != 0
        assert "Not inside a git repository" in result.output

    def test_given_uninitialized_repo_when_status_then_reports_not_initialized(
        self, temp_git_repo: Path
    ) -> None:
        """Status reports repo is not initialized."""
        result = runner.invoke(cli, ["status", str(temp_git_repo)])
        assert result.exit_code == 0
        assert "not initialized" in result.output.lower()

    def test_given_uninitialized_repo_when_status_json_then_returns_initialized_false(
        self, temp_git_repo: Path
    ) -> None:
        """Status --json returns initialized: false for uninitialized repo."""
        result = runner.invoke(cli, ["status", "--json", str(temp_git_repo)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["initialized"] is False

    @patch("codeplane.cli.status.is_server_running")
    def test_given_initialized_repo_not_running_when_status_then_reports_not_running(
        self, mock_is_running: MagicMock, initialized_repo: Path
    ) -> None:
        """Status reports daemon not running for initialized but stopped repo."""
        mock_is_running.return_value = False

        result = runner.invoke(cli, ["status", str(initialized_repo)])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    @patch("codeplane.cli.status.is_server_running")
    def test_given_initialized_repo_not_running_when_status_json_then_returns_running_false(
        self, mock_is_running: MagicMock, initialized_repo: Path
    ) -> None:
        """Status --json returns running: false for stopped daemon."""
        mock_is_running.return_value = False

        result = runner.invoke(cli, ["status", "--json", str(initialized_repo)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["initialized"] is True
        assert data["running"] is False

    @patch("codeplane.cli.status.httpx.get")
    @patch("codeplane.cli.status.read_server_info")
    @patch("codeplane.cli.status.is_server_running")
    def test_given_running_daemon_when_status_then_reports_running(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_httpx_get: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Status reports daemon running with PID and port."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)
        mock_httpx_get.return_value = MagicMock(
            json=lambda: {"indexer": {"state": "idle"}, "watcher": {"running": True}}
        )

        result = runner.invoke(cli, ["status", str(initialized_repo)])
        assert result.exit_code == 0
        assert "running" in result.output.lower()
        assert "12345" in result.output
        assert "8765" in result.output

    @patch("codeplane.cli.status.httpx.get")
    @patch("codeplane.cli.status.read_server_info")
    @patch("codeplane.cli.status.is_server_running")
    def test_given_running_daemon_when_status_json_then_returns_full_status(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_httpx_get: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Status --json returns full daemon status."""
        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)
        mock_httpx_get.return_value = MagicMock(
            json=lambda: {"indexer": {"state": "idle"}, "watcher": {"running": True}}
        )

        result = runner.invoke(cli, ["status", "--json", str(initialized_repo)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["initialized"] is True
        assert data["running"] is True
        assert data["pid"] == 12345
        assert data["port"] == 8765
        assert "indexer" in data
        assert "watcher" in data

    @patch("codeplane.cli.status.read_server_info")
    @patch("codeplane.cli.status.is_server_running")
    def test_given_stale_pid_file_when_status_then_reports_stale(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Status reports stale PID file when daemon info cannot be read."""
        mock_is_running.return_value = True
        mock_read_info.return_value = None

        result = runner.invoke(cli, ["status", str(initialized_repo)])
        assert result.exit_code == 0
        assert "stale" in result.output.lower() or "not running" in result.output.lower()

    @patch("codeplane.cli.status.httpx.get")
    @patch("codeplane.cli.status.read_server_info")
    @patch("codeplane.cli.status.is_server_running")
    def test_given_daemon_http_error_when_status_then_reports_unavailable(
        self,
        mock_is_running: MagicMock,
        mock_read_info: MagicMock,
        mock_httpx_get: MagicMock,
        initialized_repo: Path,
    ) -> None:
        """Status reports unavailable when daemon HTTP request fails."""
        import httpx

        mock_is_running.return_value = True
        mock_read_info.return_value = (12345, 8765)
        mock_httpx_get.side_effect = httpx.RequestError("Connection refused")

        result = runner.invoke(cli, ["status", str(initialized_repo)])
        assert result.exit_code == 0
        assert "unavailable" in result.output.lower() or "error" in result.output.lower()
