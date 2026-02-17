"""E2E tests for test target discovery.

Validates discover_test_targets and run_test_targets tools.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_discover_test_targets(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify test target discovery works."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("discover_test_targets", 30.0)

    test_spec = expectation.test_targets
    if not test_spec:
        pytest.skip("No test_targets expectations defined")

    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "discover_test_targets",
                "arguments": {},
            },
            "id": 1,
        },
        timeout=timeout,
    )
    assert response.status_code == 200
    result = response.json()
    assert "result" in result, f"Expected result in response: {result}"

    # Validate minimum test targets if specified
    content = result["result"].get("content", [])
    text_content = next(
        (c["text"] for c in content if c.get("type") == "text"),
        "",
    )

    if test_spec.targets_min is not None and test_spec.targets_min > 0:
        # Count non-empty lines as proxy for target count
        target_lines = [line for line in text_content.split("\n") if line.strip()]
        assert (
            len(target_lines) >= test_spec.targets_min
        ), f"Expected at least {test_spec.targets_min} test targets, got {len(target_lines)}"
