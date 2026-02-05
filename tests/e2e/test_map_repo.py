"""E2E tests for repository mapping functionality.

Validates the map_repo tool against real repositories.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_map_repo(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify map_repo returns a structural overview."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("map_repo", 60.0)

    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "map_repo",
                "arguments": {},
            },
            "id": 1,
        },
        timeout=timeout,
    )
    assert response.status_code == 200
    result = response.json()
    assert "result" in result, f"Expected result in response: {result}"

    content = result["result"].get("content", [])
    assert len(content) > 0, "map_repo should return content"

    # Verify we get some structural information
    text_content = next(
        (c["text"] for c in content if c.get("type") == "text"),
        "",
    )
    assert len(text_content) > 0, "map_repo should return text content"
