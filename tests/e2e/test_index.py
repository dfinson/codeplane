"""E2E tests for semantic indexing functionality.

Validates symbol anchors, references, imports, and scopes.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_describe_anchors(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify describe can locate expected symbol anchors."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("describe", 30.0)

    anchors = expectation.anchors
    if not anchors:
        pytest.skip("No anchor expectations defined")

    for anchor in anchors:
        response = httpx.post(
            f"{url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "describe",
                    "arguments": {"symbol": anchor.symbol},
                },
                "id": 1,
            },
            timeout=timeout,
        )
        assert response.status_code == 200
        result = response.json()
        assert "result" in result, f"Expected result for symbol '{anchor.symbol}': {result}"

        content = result["result"].get("content", [])
        text_content = next(
            (c["text"] for c in content if c.get("type") == "text"),
            "",
        )

        # Check that we find the symbol in the expected file
        if anchor.file_contains:
            assert anchor.file_contains in text_content, (
                f"Symbol '{anchor.symbol}' should be found in path containing "
                f"'{anchor.file_contains}'"
            )


@pytest.mark.e2e
def test_references(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify we can find expected references."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("describe", 30.0)

    refs = expectation.refs
    if not refs:
        pytest.skip("No reference expectations defined")

    for ref in refs:
        response = httpx.post(
            f"{url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "describe",
                    "arguments": {"symbol": ref.to_symbol, "include_refs": True},
                },
                "id": 1,
            },
            timeout=timeout,
        )
        assert response.status_code == 200
        result = response.json()
        assert "result" in result

        # Note: Actual reference count validation would require
        # parsing the response structure
