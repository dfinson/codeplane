"""E2E tests for git operations.

Validates git_status, git_log, git_diff, and related tools.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import TOOL_TIMEOUTS
from .expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_git_status(
    codeplane_server: tuple[str, int],
    _expectation: RepoExpectation,
) -> None:
    """Verify git_status returns repository state."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("git_status", 10.0)

    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "git_status",
                "arguments": {},
            },
            "id": 1,
        },
        timeout=timeout,
    )
    assert response.status_code == 200
    result = response.json()
    assert "result" in result, f"Expected result in response: {result}"


@pytest.mark.e2e
def test_git_log(
    codeplane_server: tuple[str, int],
    _expectation: RepoExpectation,
) -> None:
    """Verify git_log returns commit history."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("git_log", 10.0)

    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "git_log",
                "arguments": {"max_count": 5},
            },
            "id": 1,
        },
        timeout=timeout,
    )
    assert response.status_code == 200
    result = response.json()
    assert "result" in result, f"Expected result in response: {result}"
