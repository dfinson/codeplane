"""Shared fixtures for E2E tests.

Provides fixtures for:
- Cloning real repositories
- Initializing CodePlane
- Starting/stopping the daemon server
- Making MCP tool calls via HTTP
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from tests.e2e.expectations.schema import (
    RepoExpectation,
    TimeoutConfig,
    load_all_expectations,
)

if TYPE_CHECKING:
    from subprocess import Popen


# Operation-specific HTTP timeouts for MCP tool calls
TOOL_TIMEOUTS: dict[str, float] = {
    "describe": 30.0,
    "search": 30.0,
    "list_files": 30.0,
    "read_files": 30.0,
    "map_repo": 60.0,
    "checkpoint": 60.0,
}

# MCP protocol headers
MCP_ACCEPT_HEADER = "application/json, text/event-stream"


@dataclass
class CodePlaneServer:
    """Manages a CodePlane daemon process for E2E testing.

    Handles:
    - Starting the daemon as a foreground subprocess
    - Waiting for the server to become ready
    - MCP session initialization
    - Reading the port from .codeplane/daemon.port
    - Graceful shutdown with SIGTERM
    """

    repo_path: Path
    timeout_config: TimeoutConfig
    process: Popen[bytes] | None = field(default=None, init=False)
    port: int | None = field(default=None, init=False)
    url: str | None = field(default=None, init=False)
    session_id: str | None = field(default=None, init=False)

    def start(self) -> tuple[str, int]:
        """Start the daemon and wait for it to be ready.

        Returns:
            Tuple of (base_url, port)

        Raises:
            TimeoutError: If server doesn't become ready in time
            RuntimeError: If server fails to start
        """
        # Start cpl up (runs in foreground by default) in its own process group
        # This allows us to kill the entire tree on cleanup
        self.process = subprocess.Popen(
            ["cpl", "up", "--port", "17654"],
            cwd=self.repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "CODEPLANE_LOG_LEVEL": "DEBUG"},
            start_new_session=True,
        )

        # Wait for server to write port file and respond to health checks
        self._wait_for_server_ready()

        if self.port is None:
            raise RuntimeError("Server started but port not discovered")

        self.url = f"http://127.0.0.1:{self.port}"

        # Initialize MCP session
        self._initialize_session()

        return self.url, self.port

    def _wait_for_server_ready(self) -> None:
        """Wait for server to be ready with exponential backoff."""
        port_file = self.repo_path / ".codeplane" / "daemon.port"
        timeout = self.timeout_config.server_ready_sec
        health_timeout = self.timeout_config.health_check_sec

        start_time = time.monotonic()
        backoff = 0.1
        max_backoff = 2.0

        while time.monotonic() - start_time < timeout:
            # Check if process died
            if self.process and self.process.poll() is not None:
                stdout = self.process.stdout.read() if self.process.stdout else b""
                stderr = self.process.stderr.read() if self.process.stderr else b""
                raise RuntimeError(
                    f"Server process exited with code {self.process.returncode}\n"
                    f"stdout: {stdout.decode()}\n"
                    f"stderr: {stderr.decode()}"
                )

            # Try to read port file
            if port_file.exists():
                with contextlib.suppress(ValueError, OSError):
                    self.port = int(port_file.read_text().strip())

            # If we have a port, try health check
            if self.port:
                try:
                    response = httpx.get(
                        f"http://127.0.0.1:{self.port}/health",
                        timeout=health_timeout,
                    )
                    if response.status_code == 200:
                        return
                except httpx.RequestError:
                    pass

            time.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

        raise TimeoutError(
            f"Server did not become ready within {timeout}s. "
            f"Port file exists: {port_file.exists()}, Port: {self.port}"
        )

    def _initialize_session(self) -> None:
        """Initialize MCP session to get session ID."""
        if self.url is None:
            raise RuntimeError("Server not started")

        response = httpx.post(
            f"{self.url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "e2e-test", "version": "1.0.0"},
                },
                "id": 1,
            },
            headers={"Accept": MCP_ACCEPT_HEADER},
            timeout=10.0,
        )
        response.raise_for_status()

        self.session_id = response.headers.get("mcp-session-id")
        if not self.session_id:
            raise RuntimeError("MCP session initialization did not return session ID")

    def stop(self) -> None:
        """Stop the daemon gracefully with SIGTERM.

        Kills the entire process group to ensure child processes are cleaned up,
        even if pytest is interrupted.
        """
        if self.process is None:
            return

        try:
            # Kill the entire process group, not just the main process
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=self.timeout_config.shutdown_sec)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5.0)
        except ProcessLookupError:
            # Process already dead
            pass
        finally:
            self.process = None

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP tool and return the result.

        Args:
            tool_name: Name of the MCP tool to call
            arguments: Tool arguments

        Returns:
            The result dict from the MCP response
        """
        if self.url is None:
            raise RuntimeError("Server not started")
        if self.session_id is None:
            raise RuntimeError("MCP session not initialized")

        timeout = TOOL_TIMEOUTS.get(tool_name, self.timeout_config.tool_call_sec)

        response = httpx.post(
            f"{self.url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
                "id": 1,
            },
            headers={
                "Accept": MCP_ACCEPT_HEADER,
                "Mcp-Session-Id": self.session_id,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate test cases from expectation YAML files."""
    if "expectation" in metafunc.fixturenames or "_expectation" in metafunc.fixturenames:
        expectations = load_all_expectations()
        metafunc.parametrize(
            "expectation" if "expectation" in metafunc.fixturenames else "_expectation",
            expectations,
            ids=[exp.test_id for exp in expectations],
        )


@pytest.fixture
def timeout_config(expectation: RepoExpectation) -> TimeoutConfig:
    """Get the timeout configuration for the current test."""
    return expectation.timeout_config


@pytest.fixture
def cloned_repo(
    expectation: RepoExpectation,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[Path, None, None]:
    """Clone the repository for testing.

    Uses shallow clone with specified depth for efficiency.
    Cleans up after the test.
    """
    timeout = expectation.timeout_config
    repo_dir = tmp_path_factory.mktemp(expectation.test_id)

    # Build clone command
    clone_url = f"https://github.com/{expectation.repo}.git"
    clone_cmd = ["git", "clone", "--depth", str(expectation.clone_depth)]

    if expectation.commit:
        # For tags, we can use --branch
        clone_cmd.extend(["--branch", expectation.commit])

    clone_cmd.extend([clone_url, str(repo_dir)])

    # Execute clone with proper cleanup on interrupt
    result = _run_with_cleanup(
        clone_cmd,
        cwd=Path.cwd(),
        timeout=timeout.clone_sec,
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to clone {expectation.repo}: {result.stderr.decode()}")

    yield repo_dir

    # Cleanup
    shutil.rmtree(repo_dir, ignore_errors=True)


def _run_with_cleanup(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command with proper process group cleanup on interrupt.

    Uses start_new_session to create a process group, allowing us to kill
    the entire tree if pytest is interrupted (e.g., Ctrl+C, SIGKILL).
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired:
        # Kill the entire process group on timeout
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5.0)
        raise
    except BaseException:
        # On any interrupt (KeyboardInterrupt, etc.), kill the process group
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5.0)
        raise


@pytest.fixture
def initialized_repo(
    cloned_repo: Path,
    expectation: RepoExpectation,
) -> Path:
    """Initialize CodePlane in the cloned repository."""
    timeout = expectation.timeout_config

    result = _run_with_cleanup(
        ["cpl", "init"],
        cwd=cloned_repo,
        timeout=timeout.init_sec,
    )

    if result.returncode != 0:
        pytest.fail(
            f"Failed to initialize CodePlane in {expectation.repo}: {result.stderr.decode()}"
        )

    return cloned_repo


@pytest.fixture
def codeplane_server(
    initialized_repo: Path,
    expectation: RepoExpectation,
) -> Generator[tuple[str, int], None, None]:
    """Start CodePlane server and yield (url, port).

    The server is stopped after the test completes.
    """
    server = CodePlaneServer(
        repo_path=initialized_repo,
        timeout_config=expectation.timeout_config,
    )

    try:
        url, port = server.start()
        yield url, port
    finally:
        server.stop()


@pytest.fixture
def mcp_session(
    codeplane_server: tuple[str, int],
    initialized_repo: Path,
    expectation: RepoExpectation,
) -> Generator[tuple[str, str], None, None]:
    """Get MCP session info (url, session_id) for direct httpx calls.

    This fixture is useful for tests that need to make raw MCP calls
    instead of using the call_tool helper.
    """
    url, _port = codeplane_server

    # Initialize a new session for tests that need direct access
    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-test-direct", "version": "1.0.0"},
            },
            "id": 1,
        },
        headers={"Accept": MCP_ACCEPT_HEADER},
        timeout=10.0,
    )
    response.raise_for_status()

    session_id = response.headers.get("mcp-session-id")
    if not session_id:
        raise RuntimeError("MCP session initialization did not return session ID")

    yield url, session_id
