"""Unit tests for the eight pattern-check functions in gate.py.

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
    _check_full_file_creep,
    _check_phantom_read,
    _check_pure_search_chain,
    _check_read_spiral,
    _check_scatter_read,
    _check_search_read_loop,
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
    """8+ of last 10 calls being searches triggers break."""

    def test_no_match_below_threshold(self) -> None:
        """7 searches out of 10 should NOT trigger."""
        records = [_rec("search", "search") for _ in range(7)]
        records += [_rec("read", "read_source") for _ in range(3)]
        assert _check_pure_search_chain(_window(*records)) is None

    def test_match_at_threshold(self) -> None:
        """Exactly 8 searches out of 10 triggers."""
        records = [_rec("search", "search") for _ in range(8)]
        records += [_rec("meta", "describe") for _ in range(2)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.pattern_name == "pure_search_chain"
        assert match.severity == "break"

    def test_match_all_searches(self) -> None:
        """10/10 searches should trigger."""
        records = [_rec("search", "search") for _ in range(10)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.severity == "break"

    def test_window_too_small(self) -> None:
        """If window has fewer than 8 calls total, not enough to trigger."""
        records = [_rec("search", "search") for _ in range(5)]
        assert _check_pure_search_chain(_window(*records)) is None

    def test_overlap_cause(self) -> None:
        """Searches with overlapping files get 'over_gathering' cause."""
        records = [
            _rec("search", "search", files=["a.py", "b.py"]),
            _rec("search", "search", files=["b.py", "c.py"]),
            _rec("search", "search", files=["c.py", "d.py"]),
            _rec("search", "search", files=["d.py", "e.py"]),
            _rec("search", "search", files=["e.py", "f.py"]),
            _rec("search", "search", files=["f.py", "g.py"]),
            _rec("search", "search", files=["g.py", "h.py"]),
            _rec("search", "search", files=["h.py", "i.py"]),
        ]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.cause == "over_gathering"

    def test_no_overlap_cause(self) -> None:
        """Searches with no overlapping files get 'inefficient' cause."""
        records = [_rec("search", "search", files=[f"file{i}.py"]) for i in range(8)]
        records += [_rec("meta", "describe") for _ in range(2)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None
        assert match.cause == "inefficient"

    def test_only_considers_last_10(self) -> None:
        """Pattern only checks the last 10 calls."""
        # 15 total: first 7 are reads, last 8 are searches -> triggers
        records = [_rec("read", "read_source") for _ in range(7)]
        records += [_rec("search", "search") for _ in range(8)]
        match = _check_pure_search_chain(_window(*records))
        assert match is not None


# =========================================================================
# _check_read_spiral
# =========================================================================


class TestReadSpiral:
    """8+ reads touching <= 1 unique file triggers break."""

    def test_spiral_same_file(self) -> None:
        """8 reads of the same file triggers."""
        records = [_rec("read", "read_source", files=["same.py"]) for _ in range(8)]
        match = _check_read_spiral(_window(*records))
        assert match is not None
        assert match.pattern_name == "read_spiral"
        assert match.severity == "break"

    def test_spiral_no_files(self) -> None:
        """8 reads with no files (0 unique files) triggers."""
        records = [_rec("read", "read_source") for _ in range(8)]
        match = _check_read_spiral(_window(*records))
        assert match is not None

    def test_no_match_multiple_files(self) -> None:
        """8 reads across 2+ unique files does not trigger."""
        records = [
            _rec("read", "read_source", files=["a.py" if i % 2 == 0 else "b.py"]) for i in range(8)
        ]
        assert _check_read_spiral(_window(*records)) is None

    def test_no_match_below_threshold(self) -> None:
        """7 reads of the same file does not trigger."""
        records = [_rec("read", "read_source", files=["same.py"]) for _ in range(7)]
        assert _check_read_spiral(_window(*records)) is None

    def test_mixed_read_and_read_full(self) -> None:
        """Both 'read' and 'read_full' categories count."""
        records = [_rec("read", "read_source", files=["f.py"]) for _ in range(4)]
        records += [_rec("read_full", "read_file_full", files=["f.py"]) for _ in range(4)]
        match = _check_read_spiral(_window(*records))
        assert match is not None

    def test_non_read_calls_ignored(self) -> None:
        """Non-read calls in window don't count toward threshold."""
        records = [_rec("read", "read_source", files=["f.py"]) for _ in range(6)]
        records += [_rec("search", "search") for _ in range(4)]
        assert _check_read_spiral(_window(*records)) is None


# =========================================================================
# _check_phantom_read
# =========================================================================


class TestPhantomRead:
    """Search -> write with no read in between triggers warn."""

    def test_search_then_write_triggers(self) -> None:
        """search -> write_source with no read_source triggers."""
        records = [
            _rec("search", "search"),
            _rec("meta", "describe"),
            _rec("meta", "list_files"),
            _rec("meta", "describe"),
            _rec("write", "write_source"),
        ]
        match = _check_phantom_read(_window(*records))
        assert match is not None
        assert match.pattern_name == "phantom_read"
        assert match.severity == "warn"
        assert match.cause == "tool_bypass"

    def test_search_read_write_no_trigger(self) -> None:
        """search -> read_source -> write_source does not trigger."""
        records = [
            _rec("search", "search"),
            _rec("read", "read_source"),
            _rec("write", "write_source"),
        ]
        assert _check_phantom_read(_window(*records)) is None

    def test_search_read_full_write_no_trigger(self) -> None:
        """search -> read_file_full -> write_source does not trigger."""
        records = [
            _rec("search", "search"),
            _rec("read_full", "read_file_full"),
            _rec("write", "write_source"),
        ]
        assert _check_phantom_read(_window(*records)) is None

    def test_no_write_no_trigger(self) -> None:
        """No write_source means no trigger."""
        records = [
            _rec("search", "search"),
            _rec("search", "search"),
            _rec("meta", "describe"),
        ]
        assert _check_phantom_read(_window(*records)) is None

    def test_write_without_search_no_trigger(self) -> None:
        """write_source without any search does not trigger."""
        records = [
            _rec("read", "read_source"),
            _rec("read", "read_source"),
            _rec("read", "read_source"),
            _rec("meta", "describe"),
            _rec("write", "write_source"),
        ]
        assert _check_phantom_read(_window(*records)) is None

    def test_refactor_apply_also_triggers(self) -> None:
        """search -> refactor_apply with no read also triggers."""
        records = [
            _rec("search", "search"),
            _rec("meta", "describe"),
            _rec("meta", "list_files"),
            _rec("meta", "describe"),
            _rec("refactor", "refactor_apply"),
        ]
        match = _check_phantom_read(_window(*records))
        assert match is not None


# =========================================================================
# _check_scatter_read
# =========================================================================


class TestScatterRead:
    """8+ reads across 8+ different files triggers warn."""

    def test_scatter_many_files(self) -> None:
        """8 reads across 8 different files triggers."""
        records = [_rec("read", "read_source", files=[f"file{i}.py"]) for i in range(8)]
        match = _check_scatter_read(_window(*records))
        assert match is not None
        assert match.pattern_name == "scatter_read"
        assert match.severity == "warn"

    def test_no_match_few_files(self) -> None:
        """8 reads across only 3 files does not trigger."""
        records = [_rec("read", "read_source", files=[f"file{i % 3}.py"]) for i in range(8)]
        assert _check_scatter_read(_window(*records)) is None

    def test_no_match_few_reads(self) -> None:
        """7 reads across 7 files does not trigger."""
        records = [_rec("read", "read_source", files=[f"file{i}.py"]) for i in range(7)]
        assert _check_scatter_read(_window(*records)) is None

    def test_single_target_inefficient(self) -> None:
        """Single-target reads get 'inefficient' cause (bad batching)."""
        records = [_rec("read", "read_source", files=[f"f{i}.py"]) for i in range(8)]
        match = _check_scatter_read(_window(*records))
        assert match is not None
        assert match.cause == "inefficient"

    def test_multi_target_over_gathering(self) -> None:
        """Multi-target reads across many files get 'over_gathering' cause."""
        records = [_rec("read", "read_source", files=[f"a{i}.py", f"b{i}.py"]) for i in range(8)]
        match = _check_scatter_read(_window(*records))
        assert match is not None
        assert match.cause == "over_gathering"


# =========================================================================
# _check_search_read_loop
# =========================================================================


class TestSearchReadLoop:
    """8+ alternating search/read transitions triggers warn."""

    def test_alternating_loop(self) -> None:
        """Perfect alternation: s,r,s,r,s,r,s,r,s,r -> 9 transitions."""
        records = []
        for _ in range(5):
            records.append(_rec("search", "search"))
            records.append(_rec("read", "read_source"))
        match = _check_search_read_loop(_window(*records))
        assert match is not None
        assert match.pattern_name == "search_read_loop"
        assert match.severity == "warn"

    def test_too_few_transitions(self) -> None:
        """3 cycles (s,r,s,r,s,r) = 5 transitions -- not enough."""
        records = []
        for _ in range(3):
            records.append(_rec("search", "search"))
            records.append(_rec("read", "read_source"))
        assert _check_search_read_loop(_window(*records)) is None

    def test_consecutive_same_category_collapsed(self) -> None:
        """Consecutive same-category calls collapse into one."""
        records = [
            _rec("search", "search"),
            _rec("search", "search"),  # collapsed with above
            _rec("read", "read_source"),
            _rec("read", "read_source"),  # collapsed with above
            _rec("search", "search"),
            _rec("read", "read_source"),
            _rec("read_full", "read_file_full"),  # collapsed into 'read'
            _rec("search", "search"),
            _rec("read", "read_source"),
            _rec("search", "search"),
            _rec("read", "read_source"),
            _rec("search", "search"),
            _rec("read", "read_source"),
        ]
        match = _check_search_read_loop(_window(*records))
        assert match is not None

    def test_meta_calls_ignored(self) -> None:
        """Non-search/read categories don't count toward transitions."""
        records = [
            _rec("search", "search"),
            _rec("meta", "describe"),
            _rec("meta", "list_files"),
            _rec("read", "read_source"),
            _rec("meta", "describe"),
        ]
        assert _check_search_read_loop(_window(*records)) is None


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
# _check_full_file_creep
# =========================================================================


class TestFullFileCreep:
    """3+ read_file_full calls in window triggers warn."""

    def test_three_full_reads(self) -> None:
        """3 read_file_full calls triggers."""
        records = [
            _rec("read_full", "read_file_full"),
            _rec("read_full", "read_file_full"),
            _rec("read_full", "read_file_full"),
        ]
        match = _check_full_file_creep(_window(*records))
        assert match is not None
        assert match.pattern_name == "full_file_creep"
        assert match.severity == "warn"
        assert match.cause == "inefficient"

    def test_two_full_reads_no_trigger(self) -> None:
        """Only 2 read_file_full calls does not trigger."""
        records = [
            _rec("read_full", "read_file_full"),
            _rec("read_full", "read_file_full"),
            _rec("read", "read_source"),
        ]
        assert _check_full_file_creep(_window(*records)) is None

    def test_regular_reads_not_counted(self) -> None:
        """read_source calls don't count toward the full-file threshold."""
        records = [
            _rec("read", "read_source"),
            _rec("read", "read_source"),
            _rec("read", "read_source"),
            _rec("read", "read_source"),
            _rec("read", "read_source"),
        ]
        assert _check_full_file_creep(_window(*records)) is None

    def test_mixed_with_other_calls(self) -> None:
        """3 full reads mixed with other calls still triggers."""
        records = [
            _rec("search", "search"),
            _rec("read_full", "read_file_full"),
            _rec("read", "read_source"),
            _rec("read_full", "read_file_full"),
            _rec("meta", "describe"),
            _rec("read_full", "read_file_full"),
        ]
        match = _check_full_file_creep(_window(*records))
        assert match is not None


# =========================================================================
# PatternMatch structure tests
# =========================================================================


class TestPatternMatchStructure:
    """All pattern matches contain required fields."""

    @pytest.mark.parametrize(
        "build_window",
        [
            # pure_search_chain
            lambda: _window(*[_rec("search", "search") for _ in range(10)]),
            # read_spiral
            lambda: _window(*[_rec("read", "read_source", files=["f.py"]) for _ in range(8)]),
            # phantom_read
            lambda: _window(
                _rec("search", "search"),
                _rec("meta", "describe"),
                _rec("meta", "list_files"),
                _rec("meta", "describe"),
                _rec("write", "write_source"),
            ),
            # scatter_read
            lambda: _window(*[_rec("read", "read_source", files=[f"f{i}.py"]) for i in range(8)]),
            # zero_result_searches
            lambda: _window(
                _rec("search", "search", hit_count=0),
                _rec("search", "search", hit_count=0),
                _rec("search", "search", hit_count=0),
                _rec("meta", "describe"),
                _rec("meta", "describe"),
            ),
            # full_file_creep
            lambda: _window(*[_rec("read_full", "read_file_full") for _ in range(3)]),
        ],
        ids=[
            "pure_search_chain",
            "read_spiral",
            "phantom_read",
            "scatter_read",
            "zero_result_searches",
            "full_file_creep",
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
