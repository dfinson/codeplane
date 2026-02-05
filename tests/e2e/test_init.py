"""E2E tests for server initialization.

Validates that CodePlane can initialize against various real-world repositories.
"""

from __future__ import annotations

import pytest

from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_server_starts(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify the server starts and returns a valid port."""
    _url, port = codeplane_server
    assert port > 0, "Server should bind to a valid port"


@pytest.mark.e2e
def test_health_check(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify the health endpoint responds."""
    import httpx

    url, _port = codeplane_server
    response = httpx.get(f"{url}/health", timeout=5.0)
    assert response.status_code == 200
