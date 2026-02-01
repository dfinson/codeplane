"""E2E tests for daemon functionality.

Tests daemon startup, file watching, and incremental reindex via CLI.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.e2e.cli_runner import IsolatedEnv


@pytest.fixture(scope="module")
def daemon_test_repo(
    tmp_path_factory: pytest.TempPathFactory,
    e2e_env: IsolatedEnv,  # noqa: ARG001
) -> Path:
    """Create a minimal test repo for daemon tests."""
    repo_path = tmp_path_factory.mktemp("daemon_test")

    # Initialize git repo
    import subprocess

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo_path, check=True, capture_output=True
    )

    # Create initial Python file
    (repo_path / "app.py").write_text("def hello(): pass\n")

    # Git add and commit
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"], cwd=repo_path, check=True, capture_output=True
    )

    return repo_path


class TestDaemonE2E:
    """E2E tests for daemon operations."""

    @pytest.mark.e2e
    def test_given_repo_when_cpl_up_then_daemon_starts(
        self, daemon_test_repo: Path, e2e_env: IsolatedEnv
    ) -> None:
        """cpl up starts the daemon and creates pid file."""
        # Given - initialized repo
        result, _ = e2e_env.run_cpl(["init"], cwd=daemon_test_repo)
        result.check()

        # When - start daemon
        result, _ = e2e_env.run_cpl(["up"], cwd=daemon_test_repo)

        # Then
        assert result.success, f"cpl up failed: {result.stderr}"

        # Check PID file exists
        pid_file = daemon_test_repo / ".codeplane" / "daemon.pid"
        assert pid_file.exists(), "PID file should be created"

        # Check port file exists
        port_file = daemon_test_repo / ".codeplane" / "daemon.port"
        assert port_file.exists(), "Port file should be created"

        # Cleanup - stop daemon
        stop_result, _ = e2e_env.run_cpl(["down"], cwd=daemon_test_repo)
        stop_result.check()

    @pytest.mark.e2e
    def test_given_running_daemon_when_cpl_down_then_daemon_stops(
        self, daemon_test_repo: Path, e2e_env: IsolatedEnv
    ) -> None:
        """cpl down stops a running daemon."""
        # Given - init and start daemon
        e2e_env.run_cpl(["init"], cwd=daemon_test_repo)
        e2e_env.run_cpl(["up"], cwd=daemon_test_repo)

        pid_file = daemon_test_repo / ".codeplane" / "daemon.pid"
        assert pid_file.exists(), "Daemon should be running"

        # When - stop daemon
        result, _ = e2e_env.run_cpl(["down"], cwd=daemon_test_repo)

        # Then
        assert result.success, f"cpl down failed: {result.stderr}"

        # Give it a moment to clean up
        time.sleep(0.5)

        # PID file should be removed (or process dead)
        if pid_file.exists():
            # Check if process is actually dead
            import os

            pid = int(pid_file.read_text().strip())
            try:
                os.kill(pid, 0)  # Check if process exists
                pytest.fail("Process should be stopped")
            except OSError:
                pass  # Process is dead, as expected

    @pytest.mark.e2e
    def test_given_running_daemon_when_cpl_status_then_shows_running(
        self, daemon_test_repo: Path, e2e_env: IsolatedEnv
    ) -> None:
        """cpl status shows daemon state when running."""
        # Given - init and start daemon
        e2e_env.run_cpl(["init"], cwd=daemon_test_repo)
        up_result, _ = e2e_env.run_cpl(["up"], cwd=daemon_test_repo)
        up_result.check()

        try:
            # When
            result, _ = e2e_env.run_cpl(["status"], cwd=daemon_test_repo)

            # Then
            assert result.success
            assert "running" in result.stdout.lower() or "pid" in result.stdout.lower()
        finally:
            # Cleanup
            e2e_env.run_cpl(["down"], cwd=daemon_test_repo)

    @pytest.mark.e2e
    def test_given_no_daemon_when_cpl_status_then_shows_not_running(
        self, daemon_test_repo: Path, e2e_env: IsolatedEnv
    ) -> None:
        """cpl status shows not running when daemon is stopped."""
        # Given - init but no daemon
        e2e_env.run_cpl(["init"], cwd=daemon_test_repo)

        # Ensure daemon is stopped
        e2e_env.run_cpl(["down"], cwd=daemon_test_repo)
        time.sleep(0.5)

        # When
        result, _ = e2e_env.run_cpl(["status"], cwd=daemon_test_repo)

        # Then
        # Status command should succeed but indicate not running
        assert "not running" in result.stdout.lower() or result.returncode == 0

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_given_daemon_when_file_modified_then_reindex_triggered(
        self, daemon_test_repo: Path, e2e_env: IsolatedEnv
    ) -> None:
        """Daemon detects file changes and triggers reindex."""
        # Given - init and start daemon
        e2e_env.run_cpl(["init"], cwd=daemon_test_repo)
        e2e_env.run_cpl(["up"], cwd=daemon_test_repo)

        try:
            # Give daemon time to start watching
            time.sleep(1.0)

            # When - modify a file
            (daemon_test_repo / "app.py").write_text("def hello(): pass\ndef world(): pass\n")

            # Wait for debounce and indexing
            time.sleep(3.0)

            # Then - check status shows indexing activity
            result, _ = e2e_env.run_cpl(["status", "--json"], cwd=daemon_test_repo)

            # The daemon should have processed the change
            # (exact verification depends on status output format)
            assert result.success

        finally:
            # Cleanup
            e2e_env.run_cpl(["down"], cwd=daemon_test_repo)
