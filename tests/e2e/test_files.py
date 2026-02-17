"""E2E tests for file listing and reading.

Validates list_files and read_files tools against real repositories.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import TOOL_TIMEOUTS
from tests.e2e.expectations.schema import RepoExpectation


@pytest.mark.e2e
def test_list_files(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify list_files returns expected files."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("list_files", 30.0)

    # Call list_files MCP tool
    response = httpx.post(
        f"{url}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"path": ".", "recursive": True},
            },
            "id": 1,
        },
        timeout=timeout,
    )
    assert response.status_code == 200
    result = response.json()

    # Validate response structure
    assert "result" in result, f"Expected result in response: {result}"
    content = result["result"].get("content", [])
    assert len(content) > 0, "list_files should return content"

    # Extract file list from response
    text_content = next(
        (c["text"] for c in content if c.get("type") == "text"),
        "",
    )

    # Check file count expectations
    files_section = expectation.files
    if files_section:
        # Count lines as proxy for file count
        file_lines = [line for line in text_content.split("\n") if line.strip()]
        file_count = len(file_lines)

        if files_section.indexed_min is not None:
            assert (
                file_count >= files_section.indexed_min
            ), f"Expected at least {files_section.indexed_min} files, got {file_count}"
        if files_section.indexed_max is not None:
            assert (
                file_count <= files_section.indexed_max
            ), f"Expected at most {files_section.indexed_max} files, got {file_count}"


@pytest.mark.e2e
def test_read_files(
    codeplane_server: tuple[str, int],
    expectation: RepoExpectation,
) -> None:
    """Verify read_files can read expected files."""
    url, _port = codeplane_server
    timeout = TOOL_TIMEOUTS.get("read_files", 30.0)

    files_section = expectation.files
    if not files_section or not files_section.must_include:
        pytest.skip("No must_include files specified")

    # Read each expected file
    for expected_file in files_section.must_include:
        response = httpx.post(
            f"{url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "read_files",
                    "arguments": {"targets": [{"path": expected_file}]},
                },
                "id": 1,
            },
            timeout=timeout,
        )
        assert response.status_code == 200, f"Failed to read {expected_file}"
        result = response.json()
        assert "result" in result, f"Expected result for {expected_file}: {result}"
