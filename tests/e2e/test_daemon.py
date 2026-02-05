"""E2E tests for daemon lifecycle.

Validates daemon start, status, and stop behavior.
"""

from __future__ import annotations

import pytest

from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_daemon_started(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify daemon started successfully."""
    daemon = expectation.daemon
    if not daemon:
        pytest.skip("No daemon expectations defined")

    if daemon.starts:
        _url, port = codeplane_server
        assert port > 0, "Daemon should have started and bound to a port"


@pytest.mark.e2e
def test_daemon_health(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify daemon responds to health checks."""
    import httpx

    daemon = expectation.daemon
    if not daemon or not daemon.status_shows_running:
        pytest.skip("No daemon status expectations defined")

    url, _port = codeplane_server
    response = httpx.get(f"{url}/health", timeout=5.0)
    assert response.status_code == 200, "Daemon health check should return 200"
