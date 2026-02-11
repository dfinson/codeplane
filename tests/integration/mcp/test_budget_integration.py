"""Integration tests for response-budget pagination.

Tests budget enforcement patterns as used by MCP tool handlers:
- BudgetAccumulator with realistic handler data patterns
- Diff truncation logic (git_diff pattern)
- Budget enforcement with map_repo-style measurement
- Pagination cursor construction under budget pressure

These tests exercise the budget module in the same patterns as the
actual tool handlers, but without requiring the full MCP server stack.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from codeplane.config.constants import RESPONSE_BUDGET_BYTES
from codeplane.mcp.budget import BudgetAccumulator, make_budget_pagination, measure_bytes

pytestmark = pytest.mark.integration


# =============================================================================
# Helpers
# =============================================================================


def _make_search_result(path: str, line: int, snippet_size: int = 200) -> dict[str, Any]:
    """Build a search result dict resembling actual search handler output."""
    return {
        "path": path,
        "line": line,
        "column": 0,
        "score": 12.5,
        "match_type": "exact",
        "snippet": "x" * snippet_size,
        "context_resolved": "standard",
    }


def _make_file_result(path: str, lines: int = 50) -> dict[str, Any]:
    """Build a read_files result dict resembling actual handler output."""
    return {
        "path": path,
        "content": "# line\n" * lines,
        "language": "python",
        "line_count": lines,
        "range": None,
        "metadata": None,
    }


def _make_commit(sha: str, message: str, files: int = 3) -> dict[str, Any]:
    """Build a git_log commit dict resembling actual handler output."""
    return {
        "sha": sha,
        "message": message,
        "author": "Test User <test@test.com>",
        "time": 1700000000,
        "parents": ["0" * 40],
        "files_changed": files,
    }


def _make_blame_line(line_no: int) -> dict[str, Any]:
    """Build a blame line dict resembling actual handler output."""
    return {
        "line_no": line_no,
        "content": f"    code_line_{line_no} = True  # some comment\n",
        "commit_sha": "a" * 40,
        "author": "Test User",
        "date": "2024-01-15T10:30:00",
    }


# =============================================================================
# Search handler pattern
# =============================================================================


class TestSearchBudgetPattern:
    """Tests budget accumulation in the search handler pattern."""

    def test_small_result_set_fits(self) -> None:
        """A small number of search results fits within budget."""
        acc = BudgetAccumulator()
        results = [_make_search_result(f"src/file_{i}.py", i * 10) for i in range(5)]
        for r in results:
            assert acc.try_add(r) is True
        assert acc.count == 5
        assert acc.has_room is True

    def test_large_result_set_truncated(self) -> None:
        """Many search results are truncated at the budget boundary."""
        acc = BudgetAccumulator()
        # Each result is ~250 bytes. 40KB / 250 = ~160 results max
        results = [
            _make_search_result(f"src/module_{i}/handler.py", i, snippet_size=200)
            for i in range(500)
        ]
        accepted = 0
        for r in results:
            if not acc.try_add(r):
                break
            accepted += 1

        assert accepted < 500
        assert accepted > 0
        assert acc.has_room is False
        assert acc.used_bytes <= RESPONSE_BUDGET_BYTES + measure_bytes(results[0])

    def test_budget_pagination_emitted_correctly(self) -> None:
        """Search handler emits correct pagination when budget exceeded."""
        acc = BudgetAccumulator(budget=500)
        results = [_make_search_result(f"f{i}.py", i) for i in range(100)]
        for r in results:
            if not acc.try_add(r):
                break

        budget_more = not acc.has_room
        pagination = make_budget_pagination(
            has_more=budget_more,
            total_estimate=100,
        )
        assert pagination["truncated"] is True
        assert pagination["total_estimate"] == 100

    def test_search_with_large_snippets(self) -> None:
        """Large code snippets in results consume budget faster."""
        acc_small = BudgetAccumulator(budget=5000)
        acc_large = BudgetAccumulator(budget=5000)

        for i in range(100):
            acc_small.try_add(_make_search_result(f"f{i}.py", i, snippet_size=20))
            acc_large.try_add(_make_search_result(f"f{i}.py", i, snippet_size=500))

        assert acc_small.count > acc_large.count


# =============================================================================
# read_files handler pattern
# =============================================================================


class TestReadFilesBudgetPattern:
    """Tests budget accumulation in the read_files handler pattern."""

    def test_small_files_fit(self) -> None:
        """A few small files fit within budget."""
        acc = BudgetAccumulator()
        for i in range(3):
            assert acc.try_add(_make_file_result(f"src/f{i}.py", lines=20)) is True
        assert acc.count == 3

    def test_large_file_exceeds_on_second(self) -> None:
        """A very large file is accepted as first, blocks second."""
        acc = BudgetAccumulator()
        big = _make_file_result("src/huge.py", lines=5000)
        small = _make_file_result("src/tiny.py", lines=5)
        assert acc.try_add(big) is True  # first item guarantee
        assert acc.try_add(small) is False  # budget exceeded
        assert acc.count == 1

    def test_pagination_with_remaining_files(self) -> None:
        """Pagination correctly signals remaining files."""
        files = [_make_file_result(f"f{i}.py", lines=500) for i in range(20)]
        acc = BudgetAccumulator()
        for f in files:
            if not acc.try_add(f):
                break

        budget_more = not acc.has_room and len(files) > acc.count
        pagination = make_budget_pagination(
            has_more=budget_more,
            total_estimate=len(files),
        )
        assert pagination.get("truncated") is True
        assert pagination["total_estimate"] == 20


# =============================================================================
# git_log handler pattern
# =============================================================================


class TestGitLogBudgetPattern:
    """Tests budget accumulation in the git_log handler pattern."""

    def test_log_entries_accumulated(self) -> None:
        """Commit entries are accumulated correctly."""
        acc = BudgetAccumulator()
        commits = [_make_commit(f"{i:040d}", f"commit {i}") for i in range(50)]
        for c in commits:
            if not acc.try_add(c):
                break
        assert acc.count == 50  # all fit easily

    def test_log_budget_with_large_messages(self) -> None:
        """Commits with very large messages consume budget faster."""
        acc = BudgetAccumulator(budget=5000)
        commits = [_make_commit(f"{i:040d}", "x" * 1000, files=10) for i in range(100)]
        accepted = 0
        for c in commits:
            if not acc.try_add(c):
                break
            accepted += 1

        assert accepted < 100
        assert accepted > 0

    def test_log_cursor_from_last_accepted(self) -> None:
        """Cursor is built from the last accepted commit's SHA."""
        acc = BudgetAccumulator(budget=2000)
        commits = [_make_commit(f"{i:040d}", f"msg {i}") for i in range(100)]
        for c in commits:
            if not acc.try_add(c):
                break

        last_sha = acc.items[-1]["sha"]
        pagination = make_budget_pagination(
            has_more=True,
            next_cursor=last_sha,
        )
        assert pagination["next_cursor"] == last_sha
        assert pagination["truncated"] is True


# =============================================================================
# git_diff truncation pattern
# =============================================================================


class TestGitDiffBudgetPattern:
    """Tests the git_diff patch truncation pattern."""

    def _build_diff_result(self, patch_size: int) -> dict[str, Any]:
        """Build a diff result dict matching the git_diff handler output."""
        return {
            "files_changed": 5,
            "total_additions": 100,
            "total_deletions": 50,
            "patch": "+" * patch_size,
            "summary": "5 files changed, +100 -50",
        }

    def test_small_diff_not_truncated(self) -> None:
        """Small diffs pass through without truncation."""
        result = self._build_diff_result(100)
        size = measure_bytes(result)
        assert size < RESPONSE_BUDGET_BYTES
        # No truncation needed
        pagination = make_budget_pagination(has_more=False)
        assert pagination == {}

    def test_large_diff_truncation(self) -> None:
        """Large diffs are truncated following the handler pattern."""
        result = self._build_diff_result(100_000)  # 100KB patch
        size = measure_bytes(result)
        assert size > RESPONSE_BUDGET_BYTES

        # Apply the same truncation logic as the git_diff handler
        if size > RESPONSE_BUDGET_BYTES and result.get("patch"):
            metadata_size = size - len(result["patch"].encode("utf-8"))
            available = max(0, RESPONSE_BUDGET_BYTES - metadata_size - 200)
            truncated_patch = (
                result["patch"].encode("utf-8")[:available].decode("utf-8", errors="ignore")
            )
            result["patch"] = (
                truncated_patch + "\n\n[... DIFF TRUNCATED \u2014 response budget exceeded ...]\n"
            )
            pagination = make_budget_pagination(has_more=True)
        else:
            pagination = make_budget_pagination(has_more=False)

        # Verify truncation worked
        assert pagination == {"truncated": True}
        assert "DIFF TRUNCATED" in result["patch"]
        # Result should now be approximately within budget
        new_size = measure_bytes(result)
        assert new_size < RESPONSE_BUDGET_BYTES + 500  # +500B for truncation msg

    def test_diff_truncation_preserves_metadata(self) -> None:
        """Truncation preserves all non-patch fields."""
        result = self._build_diff_result(100_000)
        original_fields = {
            "files_changed": result["files_changed"],
            "total_additions": result["total_additions"],
            "total_deletions": result["total_deletions"],
            "summary": result["summary"],
        }

        # Truncate
        size = measure_bytes(result)
        metadata_size = size - len(result["patch"].encode("utf-8"))
        available = max(0, RESPONSE_BUDGET_BYTES - metadata_size - 200)
        result["patch"] = (
            result["patch"][:available]
            + "\n\n[... DIFF TRUNCATED \u2014 response budget exceeded ...]\n"
        )

        # All non-patch fields preserved
        for key, val in original_fields.items():
            assert result[key] == val

    def test_diff_without_patch_not_truncated(self) -> None:
        """Diff result without patch field is not truncated."""
        result = {
            "files_changed": 5,
            "total_additions": 100,
            "total_deletions": 50,
            "summary": "5 files changed",
        }
        size = measure_bytes(result)
        # Small result, no patch — should not trigger truncation
        assert size < RESPONSE_BUDGET_BYTES
        pagination = make_budget_pagination(has_more=False)
        assert pagination == {}


# =============================================================================
# git_inspect (blame) handler pattern
# =============================================================================


class TestGitBlameBudgetPattern:
    """Tests budget accumulation in the blame handler pattern."""

    def test_blame_lines_accumulated(self) -> None:
        """Blame lines are accumulated within budget."""
        acc = BudgetAccumulator()
        lines = [_make_blame_line(i) for i in range(100)]
        for line in lines:
            if not acc.try_add(line):
                break
        assert acc.count == 100  # all fit easily

    def test_blame_large_file_truncated(self) -> None:
        """Blame with many lines is truncated at budget boundary."""
        acc = BudgetAccumulator()
        lines = [_make_blame_line(i) for i in range(5000)]
        for line in lines:
            if not acc.try_add(line):
                break

        assert acc.count < 5000
        assert acc.count > 0
        assert acc.has_room is False

    def test_blame_cursor_calculation(self) -> None:
        """Cursor offset matches the number of accepted blame lines."""
        start_idx = 50
        acc = BudgetAccumulator(budget=3000)
        lines = [_make_blame_line(start_idx + i) for i in range(200)]
        for line in lines:
            if not acc.try_add(line):
                break

        cursor = str(start_idx + acc.count)
        pagination = make_budget_pagination(
            has_more=True,
            next_cursor=cursor,
        )
        assert int(pagination["next_cursor"]) == start_idx + acc.count


# =============================================================================
# map_repo handler pattern
# =============================================================================


class TestMapRepoBudgetPattern:
    """Tests budget measurement in the map_repo handler pattern."""

    def _build_map_output(self, file_count: int = 50) -> dict[str, Any]:
        """Build a map_repo output dict resembling handler output."""
        return {
            "structure": {
                "tree": [
                    {"path": f"src/module_{i}/handler.py", "type": "file", "size": 1024}
                    for i in range(file_count)
                ],
                "file_count": file_count,
                "dir_count": file_count // 5,
            },
            "languages": {
                "Python": {"files": file_count, "percentage": 100.0},
            },
            "summary": f"Mapped {file_count} files",
        }

    def test_small_map_within_budget(self) -> None:
        """Small repo map is within budget."""
        output = self._build_map_output(file_count=10)
        size = measure_bytes(output)
        assert size < RESPONSE_BUDGET_BYTES

    def test_large_map_exceeds_budget(self) -> None:
        """Large repo map exceeds budget and is flagged."""
        output = self._build_map_output(file_count=1000)
        size = measure_bytes(output)
        budget_exceeded = size > RESPONSE_BUDGET_BYTES
        assert budget_exceeded is True

        pagination = make_budget_pagination(
            has_more=budget_exceeded,
            next_cursor="cursor_abc",
            total_estimate=500,
        )
        assert pagination["truncated"] is True

    def test_budget_check_is_post_hoc(self) -> None:
        """map_repo measures the complete output, not per-item."""
        # This verifies the pattern: build output, then measure
        output = self._build_map_output(file_count=100)
        size = measure_bytes(output)
        # Size should be deterministic
        assert measure_bytes(output) == size


# =============================================================================
# Cross-cutting budget enforcement
# =============================================================================


class TestBudgetConstantIntegration:
    """Tests that RESPONSE_BUDGET_BYTES is used consistently."""

    def test_constant_value(self) -> None:
        """RESPONSE_BUDGET_BYTES is 40KB."""
        assert RESPONSE_BUDGET_BYTES == 40_000

    def test_default_accumulator_uses_constant(self) -> None:
        """Default BudgetAccumulator uses RESPONSE_BUDGET_BYTES."""
        acc = BudgetAccumulator()
        # Fill to capacity
        item = {"data": "x" * 1000}
        while acc.try_add(item):
            pass
        # Total used should be close to but not exceed budget
        # (first-item guarantee may cause slight overshoot on the first)
        assert acc.used_bytes > 0
        # With items of ~1008 bytes each, we expect ~39 items
        expected_approx = RESPONSE_BUDGET_BYTES // measure_bytes(item)
        assert abs(acc.count - expected_approx) <= 1

    def test_budget_headroom_below_60kb(self) -> None:
        """Budget provides headroom below VS Code's 60KB truncation ceiling."""
        assert RESPONSE_BUDGET_BYTES < 60_000
        # At least 33% headroom
        assert RESPONSE_BUDGET_BYTES <= 60_000 * 0.67

    def test_pagination_dict_is_json_serializable(self) -> None:
        """Pagination dicts can be JSON-serialized."""
        pagination = make_budget_pagination(
            has_more=True,
            next_cursor="test_cursor",
            total_estimate=42,
        )
        serialized = json.dumps(pagination)
        roundtripped = json.loads(serialized)
        assert roundtripped == pagination

    def test_measure_bytes_matches_actual_serialization(self) -> None:
        """measure_bytes output matches actual JSON serialization size."""
        items: list[dict[str, Any]] = [
            {"simple": "value"},
            {"nested": {"a": [1, 2, 3], "b": {"c": True}}},
            {"unicode": "caf\u00e9 \u2603"},
            {"numbers": 42, "float": 3.14, "null": None},
            {"path": "src/very/long/path/to/some/file.py", "line": 9999},
        ]
        for item in items:
            expected = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
            assert measure_bytes(item) == expected
