"""Tests for git_inspect blame fix (issue #154).

Ensures blame action returns hunks data in results array.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from codeplane.git.models import BlameHunk, BlameInfo, Signature
from codeplane.mcp.tools.git import _serialize_datetimes


class TestBlameHunksInResults:
    """Regression tests for issue #154: blame returns empty results."""

    def test_blame_info_has_hunks_not_lines(self) -> None:
        """BlameInfo model has hunks field, not lines."""
        blame = BlameInfo(
            path="test.py",
            hunks=(
                BlameHunk(
                    commit_sha="abc123",
                    author=Signature(
                        name="Alice",
                        email="alice@example.com",
                        time=datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
                    ),
                    start_line=1,
                    line_count=10,
                    original_start_line=1,
                ),
            ),
        )

        # asdict should produce 'hunks', not 'lines'
        blame_dict = asdict(blame)
        assert "hunks" in blame_dict
        assert "lines" not in blame_dict
        assert len(blame_dict["hunks"]) == 1

    def test_blame_dict_pop_hunks_returns_data(self) -> None:
        """Popping 'hunks' from blame dict returns the actual data."""
        blame = BlameInfo(
            path="test.py",
            hunks=(
                BlameHunk(
                    commit_sha="abc123",
                    author=Signature(
                        name="Alice",
                        email="alice@example.com",
                        time=datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
                    ),
                    start_line=1,
                    line_count=10,
                    original_start_line=1,
                ),
                BlameHunk(
                    commit_sha="def456",
                    author=Signature(
                        name="Bob",
                        email="bob@example.com",
                        time=datetime(2026, 1, 16, 11, 0, 0, tzinfo=UTC),
                    ),
                    start_line=11,
                    line_count=5,
                    original_start_line=11,
                ),
            ),
        )

        blame_dict = _serialize_datetimes(asdict(blame))

        # The fix: pop 'hunks' (not 'lines') to get actual data
        hunks = blame_dict.pop("hunks", [])
        assert len(hunks) == 2
        assert hunks[0]["commit_sha"] == "abc123"
        assert hunks[0]["line_count"] == 10
        assert hunks[1]["commit_sha"] == "def456"
        assert hunks[1]["line_count"] == 5

        # Popping 'lines' (the bug) would return empty list
        lines = blame_dict.pop("lines", [])
        assert lines == []

    def test_total_lines_from_hunks(self) -> None:
        """Total line count is computed from hunk line_counts."""
        hunks: list[dict[str, int | str]] = [
            {"commit_sha": "a", "line_count": 10},
            {"commit_sha": "b", "line_count": 5},
            {"commit_sha": "c", "line_count": 20},
        ]
        total_lines = sum(int(h.get("line_count", 0)) for h in hunks)
        assert total_lines == 35


class TestGitInspectBlameHandler:
    """Integration tests for git_inspect blame handler."""

    @pytest.fixture
    def git_inspect_tool(self, mock_context: MagicMock) -> Any:
        """Register git tools and return the git_inspect function."""
        from fastmcp import FastMCP

        from codeplane.mcp.tools.git import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, mock_context)
        tool = mcp._tool_manager._tools["git_inspect"]
        return tool.fn  # type: ignore[attr-defined]

    @pytest.fixture
    def mock_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.session_id = "test-session"
        return ctx

    @pytest.mark.asyncio
    async def test_blame_returns_hunks_in_results(
        self,
        git_inspect_tool: Any,
        mock_ctx: MagicMock,
        mock_context: MagicMock,
    ) -> None:
        """Test that blame action returns hunks data in results.

        Regression test for issue #154: blame was returning empty results.
        """
        # Create mock blame result with hunks
        mock_blame = BlameInfo(
            path="src/test.py",
            hunks=(
                BlameHunk(
                    commit_sha="abc123def",
                    author=Signature(
                        name="Alice",
                        email="alice@example.com",
                        time=datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
                    ),
                    start_line=1,
                    line_count=10,
                    original_start_line=1,
                ),
                BlameHunk(
                    commit_sha="def456ghi",
                    author=Signature(
                        name="Bob",
                        email="bob@example.com",
                        time=datetime(2026, 1, 16, 11, 0, 0, tzinfo=UTC),
                    ),
                    start_line=11,
                    line_count=5,
                    original_start_line=11,
                ),
            ),
        )
        mock_context.git_ops.blame.return_value = mock_blame

        result = await git_inspect_tool(mock_ctx, action="blame", path="src/test.py", limit=100)
        # Results should contain the hunks, not be empty
        assert len(result["results"]) == 2
        assert result["results"][0]["commit_sha"] == "abc123def"
        assert result["results"][0]["line_count"] == 10
        assert result["results"][1]["commit_sha"] == "def456ghi"
        assert result["results"][1]["line_count"] == 5
        # Summary should reflect actual line count (10 + 5 = 15 lines)
        assert "15 lines" in result["summary"]
        assert "2 hunks" in result["summary"]
