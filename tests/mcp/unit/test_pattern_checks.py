"""Unit tests for pattern-check functions in gate.py.

Each function is pure: takes a deque[CallRecord], returns PatternMatch | None.
This makes them trivially testable in isolation.
"""

from __future__ import annotations

from collections import deque

import pytest

from codeplane.mcp.gate import (
    _PATTERN_CHECKS,
    CallRecord,
    PatternMatch,
    _check_pure_search_chain,
    _check_zero_result_searches,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(
    category: str = "meta",
    tool_name: str = "describe",
    files: list[str] | None = None,
    hit_count: int = 0,
) -> CallRecord:
    return CallRecord(
        category=category,
        tool_name=tool_name,
        files=files or [],
        timestamp=0.0,
        hit_count=hit_count,
    )


def _window(*records: CallRecord) -> deque[CallRecord]:
    return deque(records, maxlen=20)


# =========================================================================
# _check_pure_search_chain
# =========================================================================


class TestPureSearchChain:
    """5+ of last 7 calls being searches triggers break."""

    def test_no_match_below_threshold(self) -> None:
        """4 searches out of 7 should NOT trigger."""
        records = [_rec("search", "search") for _ in range(4)]
        records += [_rec("read", "read_source") for _ in range(3)]
        assert _check_pure_search_chain(_window(*records)) is None

    def test_match_at_threshold(self) -> None:
        """Exactly 5 searches out of 7 triggers."""
        records = [_rec("search", "search") for _ in range(5)]
        records += [_rec("meta", "describe") for _ in range(2)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.pattern_name == "pure_search_chain"
        assert match.severity == "break"

    def test_match_all_searches(self) -> None:
        """7/7 searches should trigger."""
        records = [_rec("search", "search") for _ in range(7)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.severity == "break"

    def test_window_too_small(self) -> None:
        """If window has fewer than 5 calls total, not enough to trigger."""
        records = [_rec("search", "search") for _ in range(4)]
        assert _check_pure_search_chain(_window(*records)) is None

    def test_overlap_cause(self) -> None:
        """Searches with overlapping files get 'over_gathering' cause."""
        records = [
            _rec("search", "search", files=["a.py", "b.py"]),
            _rec("search", "search", files=["b.py", "c.py"]),
            _rec("search", "search", files=["c.py", "d.py"]),
            _rec("search", "search", files=["d.py", "e.py"]),
            _rec("search", "search", files=["e.py", "f.py"]),
        ]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.cause == "over_gathering"

    def test_no_overlap_cause(self) -> None:
        """Searches with no overlapping files get 'inefficient' cause."""
        records = [_rec("search", "search", files=[f"file{i}.py"]) for i in range(5)]
        records += [_rec("meta", "describe") for _ in range(2)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.cause == "inefficient"

    def test_only_considers_last_7(self) -> None:
        """Pattern only checks the last 7 calls."""
        # 12 total: first 7 are reads, last 5 are searches -> triggers
        records = [_rec("read", "read_source") for _ in range(7)]
        records += [_rec("search", "search") for _ in range(5)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None


# =========================================================================
# _check_zero_result_searches
# =========================================================================


class TestZeroResultSearches:
    """3+ searches with 0 results triggers warn."""

    def test_three_zero_results(self) -> None:
        """3 searches with hit_count=0 triggers."""
        records = [
            _rec("search", "search", hit_count=0),
            _rec("search", "search", hit_count=5),
            _rec("search", "search", hit_count=0),
            _rec("search", "search", hit_count=0),
            _rec("meta", "describe"),
        ]
        match = _check_zero_result_searches(_window(*records))
        assert match is not None
        assert match.pattern_name == "zero_result_searches"
        assert match.severity == "warn"
        assert match.cause == "inefficient"

    def test_two_zero_results_no_trigger(self) -> None:
        """Only 2 zero-result searches does not trigger."""
        records = [
            _rec("search", "search", hit_count=0),
            _rec("search", "search", hit_count=3),
            _rec("search", "search", hit_count=0),
            _rec("meta", "describe"),
            _rec("meta", "describe"),
        ]
        assert _check_zero_result_searches(_window(*records)) is None

    def test_non_zero_results_ignored(self) -> None:
        """Searches with results don't count."""
        records = [
            _rec("search", "search", hit_count=5),
            _rec("search", "search", hit_count=3),
            _rec("search", "search", hit_count=1),
            _rec("search", "search", hit_count=10),
            _rec("search", "search", hit_count=2),
        ]
        assert _check_zero_result_searches(_window(*records)) is None

    def test_non_search_zero_hits_ignored(self) -> None:
        """Non-search calls with hit_count=0 don't count."""
        records = [
            _rec("read", "read_source", hit_count=0),
            _rec("read", "read_source", hit_count=0),
            _rec("read", "read_source", hit_count=0),
            _rec("meta", "describe", hit_count=0),
            _rec("meta", "list_files", hit_count=0),
        ]
        assert _check_zero_result_searches(_window(*records)) is None


# =========================================================================
# PatternMatch structure tests
# =========================================================================


class TestPatternMatchStructure:
    """All pattern matches contain required fields."""

    @pytest.mark.parametrize(
        "build_window",
        [
            # pure_search_chain
            lambda: _window(*[_rec("search", "search") for _ in range(7)]),
            # zero_result_searches
            lambda: _window(
                _rec("search", "search", hit_count=0),
                _rec("search", "search", hit_count=0),
                _rec("search", "search", hit_count=0),
                _rec("meta", "describe"),
                _rec("meta", "describe"),
            ),
        ],
        ids=[
            "pure_search_chain",
            "zero_result_searches",
        ],
    )
    def test_match_has_required_fields(self, build_window: object) -> None:
        """Every PatternMatch has all required fields."""
        window = build_window()  # type: ignore[operator]
        for check in _PATTERN_CHECKS:
            match = check(window)
            if match is not None:
                assert isinstance(match, PatternMatch)
                assert match.pattern_name
                assert match.severity in ("warn", "break")
                assert match.cause
                assert match.message
                assert match.reason_prompt
                assert isinstance(match.suggested_workflow, dict)
                return
        pytest.fail("Expected at least one pattern to match")
