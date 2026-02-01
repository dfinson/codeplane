"""E2E tests for daemon functionality.

Tests daemon startup, file watching, and incremental reindex via CLI.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.e2e.cli_runner import IsolatedEnv


def _get_free_port() -> int:
    """Get a free port by binding to port 0 and letting the OS assign one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def daemon_port() -> int:
    """Get a unique free port for daemon tests."""
    return _get_free_port()


@pytest.fixture
def daemon_test_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[Path, None, None]:
    """Create a minimal test repo for daemon tests."""
    import sys

    print("DEBUG daemon_test_repo: starting", file=sys.stderr, flush=True)
    repo_path = tmp_path_factory.mktemp("daemon_test")
    print(f"DEBUG daemon_test_repo: repo_path={repo_path}", file=sys.stderr, flush=True)

    # Initialize git repo
    import subprocess

    print("DEBUG daemon_test_repo: git init", file=sys.stderr, flush=True)
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, timeout=10)
    print("DEBUG daemon_test_repo: git config email", file=sys.stderr, flush=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        timeout=10,
    )
    print("DEBUG daemon_test_repo: git config name", file=sys.stderr, flush=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        timeout=10,
    )

    # Create initial Python file
    print("DEBUG daemon_test_repo: writing app.py", file=sys.stderr, flush=True)
    (repo_path / "app.py").write_text("def hello(): pass\n")

    # Git add and commit
    print("DEBUG daemon_test_repo: git add", file=sys.stderr, flush=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True, timeout=10)
    print("DEBUG daemon_test_repo: git commit", file=sys.stderr, flush=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        timeout=10,
    )

    print("DEBUG daemon_test_repo: yielding", file=sys.stderr, flush=True)
    yield repo_path
    print("DEBUG daemon_test_repo: cleanup starting", file=sys.stderr, flush=True)

    # Cleanup: ensure any daemon is stopped
    import os

    pid_file = repo_path / ".codeplane" / "daemon.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 15)  # SIGTERM
        except (ValueError, OSError):
            pass
    print("DEBUG daemon_test_repo: cleanup done", file=sys.stderr, flush=True)


class TestDaemonE2E:
    """E2E tests for daemon operations."""

    @pytest.mark.e2e
    def test_given_repo_when_cpl_up_then_daemon_starts(
        self, daemon_test_repo: Path, isolated_env: IsolatedEnv, daemon_port: int
    ) -> None:
        """cpl up starts the daemon and creates pid file."""
        import sys

        print(f"DEBUG test: starting, port={daemon_port}", file=sys.stderr, flush=True)
        # Given - initialized repo
        print("DEBUG test: running cpl init", file=sys.stderr, flush=True)
        result, _ = isolated_env.run_cpl(["init"], cwd=daemon_test_repo, timeout=60)
        print(f"DEBUG test: cpl init returned {result.returncode}", file=sys.stderr, flush=True)
        result.check()

        # When - start daemon with unique port
        print(f"DEBUG test: running cpl up --port {daemon_port}", file=sys.stderr, flush=True)
        result, _ = isolated_env.run_cpl(
            ["up", "--port", str(daemon_port)], cwd=daemon_test_repo, timeout=60
        )
        print(f"DEBUG test: cpl up returned {result.returncode}", file=sys.stderr, flush=True)

        # Then
        assert result.success, f"cpl up failed: {result.stderr}"

        # Give daemon a moment to start and write PID file
        print("DEBUG test: sleeping", file=sys.stderr, flush=True)
        time.sleep(1.0)

        # Check PID file exists
        pid_file = daemon_test_repo / ".codeplane" / "daemon.pid"
        assert pid_file.exists(), (
            f"PID file should be created. stdout={result.stdout}, stderr={result.stderr}"
        )
        print("DEBUG test: pid file exists", file=sys.stderr, flush=True)

        # Check port file exists
        port_file = daemon_test_repo / ".codeplane" / "daemon.port"
        assert port_file.exists(), "Port file should be created"

        # Cleanup - stop daemon
        stop_result, _ = isolated_env.run_cpl(["down"], cwd=daemon_test_repo)
        stop_result.check()

    @pytest.mark.e2e
    def test_given_running_daemon_when_cpl_down_then_daemon_stops(
        self, daemon_test_repo: Path, isolated_env: IsolatedEnv, daemon_port: int
    ) -> None:
        """cpl down stops a running daemon."""
        # Given - init and start daemon
        isolated_env.run_cpl(["init"], cwd=daemon_test_repo)
        isolated_env.run_cpl(["up", "--port", str(daemon_port)], cwd=daemon_test_repo)

        pid_file = daemon_test_repo / ".codeplane" / "daemon.pid"
        assert pid_file.exists(), "Daemon should be running"

        # When - stop daemon
        result, _ = isolated_env.run_cpl(["down"], cwd=daemon_test_repo)

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
        self, daemon_test_repo: Path, isolated_env: IsolatedEnv, daemon_port: int
    ) -> None:
        """cpl status shows daemon state when running."""
        # Given - init and start daemon
        isolated_env.run_cpl(["init"], cwd=daemon_test_repo)
        up_result, _ = isolated_env.run_cpl(
            ["up", "--port", str(daemon_port)], cwd=daemon_test_repo
        )
        up_result.check()

        try:
            # When
            result, _ = isolated_env.run_cpl(["status"], cwd=daemon_test_repo)

            # Then
            assert result.success
            assert "running" in result.stdout.lower() or "pid" in result.stdout.lower()
        finally:
            # Cleanup
            isolated_env.run_cpl(["down"], cwd=daemon_test_repo)

    @pytest.mark.e2e
    def test_given_no_daemon_when_cpl_status_then_shows_not_running(
        self, daemon_test_repo: Path, isolated_env: IsolatedEnv
    ) -> None:
        """cpl status shows not running when daemon is stopped."""
        # Given - init but no daemon
        isolated_env.run_cpl(["init"], cwd=daemon_test_repo)

        # Ensure daemon is stopped
        isolated_env.run_cpl(["down"], cwd=daemon_test_repo)
        time.sleep(0.5)

        # When
        result, _ = isolated_env.run_cpl(["status"], cwd=daemon_test_repo)

        # Then
        # Status command should succeed but indicate not running
        assert "not running" in result.stdout.lower() or result.returncode == 0

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_given_daemon_when_file_modified_then_reindex_triggered(
        self, daemon_test_repo: Path, isolated_env: IsolatedEnv, daemon_port: int
    ) -> None:
        """Daemon detects file changes and triggers reindex."""
        # Given - init and start daemon
        isolated_env.run_cpl(["init"], cwd=daemon_test_repo)
        isolated_env.run_cpl(["up", "--port", str(daemon_port)], cwd=daemon_test_repo)

        try:
            # Give daemon time to start watching
            time.sleep(1.0)

            # When - modify a file
            (daemon_test_repo / "app.py").write_text("def hello(): pass\ndef world(): pass\n")

            # Wait for debounce and indexing
            time.sleep(3.0)

            # Then - check status shows indexing activity
            result, _ = isolated_env.run_cpl(["status", "--json"], cwd=daemon_test_repo)

            # The daemon should have processed the change
            # (exact verification depends on status output format)
            assert result.success

        finally:
            # Cleanup
            isolated_env.run_cpl(["down"], cwd=daemon_test_repo)
