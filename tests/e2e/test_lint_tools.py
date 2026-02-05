"""E2E tests for lint tool discovery.

Validates lint_tools and lint_check functionality.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_lint_tools(
    codeplane_server: tuple[str, int],
    _expectation: RepoExpectation,
) -> None:
    """Verify lint tool discovery."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("lint_tools", 30.0)

    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "lint_tools",
                "arguments": {},
            },
            "id": 1,
        },
        timeout=timeout,
    )
    assert response.status_code == 200
    result = response.json()
    assert "result" in result, f"Expected result in response: {result}"
