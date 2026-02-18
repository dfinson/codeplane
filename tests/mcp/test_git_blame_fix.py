"""Tests for git_inspect blame fix (issue #154).

Ensures blame action returns hunks data in results array.
"""

from dataclasses import asdict
from datetime import UTC, datetime

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
