"""E2E tests for code search functionality.

Validates the search tool against real repositories.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import MCP_ACCEPT_HEADER, TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_search(
    mcp_session: tuple[str, str],
    expectation: RepoExpectation,
) -> None:
    """Verify search returns expected matches."""
    url, session_id = mcp_session
    timeout = TOOL_TIMEOUTS.get("search", 30.0)

    search_specs = expectation.search
    if not search_specs:
        pytest.skip("No search expectations defined")

    for spec in search_specs:
        response = httpx.post(
            f"{url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": spec.query},
                },
                "id": 1,
            },
            headers={
                "Accept": MCP_ACCEPT_HEADER,
                "Mcp-Session-Id": session_id,
            },
            timeout=timeout,
        )
        assert response.status_code == 200
        result = response.json()
        assert "result" in result, f"Expected result for query '{spec.query}': {result}"

        # Validate that expected file is found
        content = result["result"].get("content", [])
        text_content = next(
            (c["text"] for c in content if c.get("type") == "text"),
            "",
        )

        if spec.must_find_file:
            assert (
                spec.must_find_file in text_content
            ), f"Search for '{spec.query}' should find '{spec.must_find_file}'"
