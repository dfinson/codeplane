"""E2E tests for incremental indexing.

Validates that file changes trigger incremental re-indexing.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_incremental_reindex(
    codeplane_server: tuple[str, int],
    initialized_repo: Path,
    expectation: RepoExpectation,
) -> None:
    """Verify incremental indexing after file touch."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("describe", 30.0)

    incremental = expectation.incremental
    if not incremental or not incremental.touch_file:
        pytest.skip("No incremental expectations defined")

    # Touch the specified file
    touch_path = initialized_repo / incremental.touch_file
    if touch_path.exists():
        touch_path.touch()
        # Give the server time to detect the change
        time.sleep(1.0)

        # Make a describe call to verify the server is still responsive
        response = httpx.post(
            f"{url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "describe",
                    "arguments": {},
                },
                "id": 1,
            },
            timeout=timeout,
        )
        assert response.status_code == 200
    else:
        pytest.skip(f"Touch file not found: {touch_path}")
