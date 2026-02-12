"""Tests for mcp.budget module.

Covers:
- BudgetAccumulator: try_add, first-item guarantee, exhaustion, properties
- measure_bytes: deterministic JSON byte measurement
- make_budget_pagination: consistent pagination dict construction
"""

from __future__ import annotations

import json

import pytest

from codeplane.mcp.budget import BudgetAccumulator, make_budget_pagination, measure_bytes

# =============================================================================
# Tests for measure_bytes
# =============================================================================


class TestMeasureBytes:
    """Tests for the measure_bytes helper."""

    def test_empty_dict(self) -> None:
        """Empty dict measures as 2 bytes ('{}')."""
        assert measure_bytes({}) == 2

    def test_simple_dict(self) -> None:
        """Simple dict matches compact JSON encoding."""
        item = {"key": "value"}
        expected = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
        assert measure_bytes(item) == expected

    def test_nested_dict(self) -> None:
        """Nested structures are measured correctly."""
        item = {"outer": {"inner": [1, 2, 3]}}
        expected = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
        assert measure_bytes(item) == expected

    def test_unicode_correct_byte_count(self) -> None:
        """Unicode characters are counted by UTF-8 byte size, not char count."""
        # json.dumps escapes non-ASCII by default (ensure_ascii=True),
        # so the byte measurement reflects the escaped representation.
        item = {"text": "caf\u00e9 \u2603 \U0001f600"}
        result = measure_bytes(item)
        expected = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
        assert result == expected
        # The key property: measurement is deterministic and positive
        assert result > 0

    def test_large_dict(self) -> None:
        """Large dict measures correctly."""
        item = {f"key_{i}": f"value_{i}" for i in range(100)}
        expected = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
        assert measure_bytes(item) == expected

    def test_uses_compact_separators(self) -> None:
        """Measurement uses compact separators, not default pretty ones."""
        item = {"a": 1, "b": 2}
        compact = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
        pretty = len(json.dumps(item).encode("utf-8"))
        assert measure_bytes(item) == compact
        assert compact < pretty  # compact is smaller


# =============================================================================
# Tests for BudgetAccumulator
# =============================================================================


class TestBudgetAccumulator:
    """Tests for the BudgetAccumulator class."""

    # ---- Construction ----

    def test_default_budget(self) -> None:
        """Default budget uses RESPONSE_BUDGET_BYTES."""
        from codeplane.config.constants import RESPONSE_BUDGET_BYTES

        acc = BudgetAccumulator()
        assert acc.remaining_bytes == RESPONSE_BUDGET_BYTES

    def test_custom_budget(self) -> None:
        """Custom budget is respected."""
        acc = BudgetAccumulator(budget=500)
        assert acc.remaining_bytes == 500

    def test_initial_state(self) -> None:
        """Fresh accumulator has correct initial state."""
        acc = BudgetAccumulator(budget=1000)
        assert acc.items == []
        assert acc.count == 0
        assert acc.used_bytes == 0
        assert acc.remaining_bytes == 1000
        assert acc.has_room is True

    # ---- try_add: basic ----

    def test_try_add_single_item(self) -> None:
        """Single item within budget is accepted."""
        acc = BudgetAccumulator(budget=1000)
        assert acc.try_add({"x": 1}) is True
        assert acc.count == 1
        assert acc.items == [{"x": 1}]

    def test_try_add_multiple_items(self) -> None:
        """Multiple items within budget are all accepted."""
        acc = BudgetAccumulator(budget=10_000)
        for i in range(10):
            assert acc.try_add({"i": i}) is True
        assert acc.count == 10

    def test_try_add_tracks_bytes(self) -> None:
        """used_bytes increases with each item."""
        acc = BudgetAccumulator(budget=10_000)
        item = {"data": "hello"}
        size = measure_bytes(item)
        acc.try_add(item)
        assert acc.used_bytes == size
        acc.try_add(item)
        assert acc.used_bytes == size * 2

    # ---- try_add: budget exhaustion ----

    def test_try_add_rejects_when_budget_exceeded(self) -> None:
        """Item is rejected when it would exceed the remaining budget."""
        item = {"data": "x" * 100}
        size = measure_bytes(item)
        # Budget for exactly one item
        acc = BudgetAccumulator(budget=size + 1)
        assert acc.try_add(item) is True
        assert acc.try_add(item) is False
        assert acc.count == 1

    def test_try_add_rejects_all_after_exhaustion(self) -> None:
        """Once exhausted, all subsequent try_add calls return False."""
        item = {"data": "x" * 50}
        size = measure_bytes(item)
        acc = BudgetAccumulator(budget=size + 1)  # room for 1
        acc.try_add(item)
        assert acc.try_add(item) is False
        assert acc.try_add({"tiny": 1}) is False  # even small items rejected
        assert acc.count == 1

    def test_has_room_false_after_exhaustion(self) -> None:
        """has_room is False after budget is exhausted."""
        item = {"data": "x" * 50}
        size = measure_bytes(item)
        acc = BudgetAccumulator(budget=size + 1)
        acc.try_add(item)
        acc.try_add(item)  # rejected
        assert acc.has_room is False

    def test_remaining_bytes_decreases(self) -> None:
        """remaining_bytes decreases correctly with each item."""
        acc = BudgetAccumulator(budget=1000)
        item = {"x": 1}
        size = measure_bytes(item)
        acc.try_add(item)
        assert acc.remaining_bytes == 1000 - size

    def test_remaining_bytes_zero_when_exhausted(self) -> None:
        """remaining_bytes is 0 (not negative) after exhaustion."""
        item = {"data": "x" * 100}
        size = measure_bytes(item)
        # Budget slightly less than item size — first item still accepted
        acc = BudgetAccumulator(budget=size - 5)
        acc.try_add(item)  # accepted (first item guarantee)
        assert acc.remaining_bytes == 0

    # ---- try_add: first-item guarantee ----

    def test_first_item_always_accepted(self) -> None:
        """First item is accepted even if it exceeds the entire budget."""
        big_item = {"data": "x" * 10_000}
        acc = BudgetAccumulator(budget=10)  # very small budget
        assert acc.try_add(big_item) is True
        assert acc.count == 1
        assert acc.items == [big_item]

    def test_first_item_accepted_marks_exhausted(self) -> None:
        """First oversized item is accepted but marks accumulator as exhausted."""
        big_item = {"data": "x" * 10_000}
        acc = BudgetAccumulator(budget=10)
        acc.try_add(big_item)
        assert acc.has_room is False
        assert acc.try_add({"tiny": 1}) is False

    def test_no_empty_pages(self) -> None:
        """Even with budget=1, the first item is accepted."""
        acc = BudgetAccumulator(budget=1)
        assert acc.try_add({"k": "v"}) is True
        assert acc.count == 1

    # ---- try_add: exact budget boundary ----

    def test_exact_budget_match_is_accepted(self) -> None:
        """An item that exactly fills the budget is accepted."""
        item = {"x": 1}
        size = measure_bytes(item)
        acc = BudgetAccumulator(budget=size)
        assert acc.try_add(item) is True
        assert acc.has_room is False  # exactly at budget → exhausted

    def test_exact_budget_two_items(self) -> None:
        """Two items that exactly fill the budget are both accepted."""
        item = {"x": 1}
        size = measure_bytes(item)
        acc = BudgetAccumulator(budget=size * 2)
        assert acc.try_add(item) is True
        assert acc.try_add(item) is True
        assert acc.count == 2
        assert acc.has_room is False

    # ---- items property ----

    def test_items_returns_accumulated(self) -> None:
        """items returns the list of accepted items in order."""
        acc = BudgetAccumulator(budget=10_000)
        items = [{"i": 0}, {"i": 1}, {"i": 2}]
        for item in items:
            acc.try_add(item)
        assert acc.items == items

    def test_items_excludes_rejected(self) -> None:
        """items does not contain rejected items."""
        item = {"data": "x" * 100}
        size = measure_bytes(item)
        acc = BudgetAccumulator(budget=size + 1)
        acc.try_add({"data": "x" * 100})
        acc.try_add({"data": "y" * 100})  # rejected
        assert len(acc.items) == 1
        assert acc.items[0]["data"] == "x" * 100


# =============================================================================
# Tests for make_budget_pagination
# =============================================================================


class TestMakeBudgetPagination:
    """Tests for the make_budget_pagination helper."""

    def test_no_more_results(self) -> None:
        """When has_more is False, returns empty dict."""
        result = make_budget_pagination(has_more=False)
        assert result == {}

    def test_has_more_without_cursor(self) -> None:
        """When has_more but no cursor, sets truncated but no next_cursor."""
        result = make_budget_pagination(has_more=True)
        assert result == {"truncated": True}

    def test_has_more_with_cursor(self) -> None:
        """When has_more with cursor, sets both truncated and next_cursor."""
        result = make_budget_pagination(has_more=True, next_cursor="abc123")
        assert result == {"truncated": True, "next_cursor": "abc123"}

    def test_total_estimate_included(self) -> None:
        """total_estimate is included when provided."""
        result = make_budget_pagination(has_more=True, total_estimate=42)
        assert result == {"truncated": True, "total_estimate": 42}

    def test_total_estimate_without_has_more(self) -> None:
        """total_estimate is included even when has_more is False."""
        result = make_budget_pagination(has_more=False, total_estimate=42)
        assert result == {"total_estimate": 42}

    def test_all_fields(self) -> None:
        """All fields are included when provided."""
        result = make_budget_pagination(
            has_more=True,
            next_cursor="cursor_xyz",
            total_estimate=100,
        )
        assert result == {
            "truncated": True,
            "next_cursor": "cursor_xyz",
            "total_estimate": 100,
        }

    def test_cursor_ignored_when_no_more(self) -> None:
        """next_cursor is not included when has_more is False."""
        result = make_budget_pagination(has_more=False, next_cursor="cursor_xyz")
        assert "next_cursor" not in result
        assert "truncated" not in result

    def test_minimal_call(self) -> None:
        """Minimal call with just has_more returns expected result."""
        result = make_budget_pagination(has_more=False)
        assert result == {}
        assert "budget_used" not in result
        assert "budget_total" not in result


# =============================================================================
# Parametrized edge cases
# =============================================================================


class TestBudgetAccumulatorEdgeCases:
    """Edge cases and stress scenarios."""

    @pytest.mark.parametrize("budget", [0, 1, 2])
    def test_tiny_budgets(self, budget: int) -> None:
        """Extremely small budgets still guarantee first item."""
        acc = BudgetAccumulator(budget=budget)
        assert acc.try_add({"x": 1}) is True
        assert acc.count == 1

    def test_many_small_items(self) -> None:
        """Many small items are accepted until budget runs out."""
        item = {"i": 0}  # small item
        size = measure_bytes(item)
        budget = size * 5  # room for exactly 5
        acc = BudgetAccumulator(budget=budget)

        accepted = 0
        for i in range(100):
            if acc.try_add({"i": i}):
                accepted += 1
            else:
                break
        # The item size is stable ({"i":0}, {"i":1}, ..., {"i":9} all same size)
        # but multi-digit raises size
        assert accepted >= 5
        assert accepted <= 100

    def test_heterogeneous_item_sizes(self) -> None:
        """Mix of small and large items works correctly."""
        acc = BudgetAccumulator(budget=200)
        small = {"x": 1}  # ~7 bytes
        large = {"data": "x" * 100}  # ~115 bytes

        assert acc.try_add(small) is True
        assert acc.try_add(large) is True
        assert acc.try_add(small) is True
        # At this point ~129 bytes used, ~71 remaining
        # Another large should fail
        assert acc.try_add(large) is False
        assert acc.count == 3

    def test_budget_accumulator_is_deterministic(self) -> None:
        """Same items always produce same result."""
        items = [{"i": i, "data": f"payload_{i}"} for i in range(20)]

        acc1 = BudgetAccumulator(budget=500)
        for item in items:
            if not acc1.try_add(item):
                break

        acc2 = BudgetAccumulator(budget=500)
        for item in items:
            if not acc2.try_add(item):
                break

        assert acc1.items == acc2.items
        assert acc1.used_bytes == acc2.used_bytes
        assert acc1.count == acc2.count
