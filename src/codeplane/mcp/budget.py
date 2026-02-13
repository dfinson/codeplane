"""Response-size budget accumulator.

Provides a byte-aware accumulator that endpoints use to collect results
until a shared budget (RESPONSE_BUDGET_BYTES) is exhausted.  When the
budget is exceeded, the accumulator signals that no more items should be
added and the endpoint should emit a pagination cursor.

Design rationale (from issue #162 analysis):
  - MCP spec defines NO response size limits (known gap, Discussion #2211).
  - VS Code Copilot truncates terminal tool output at 60 KB.
  - 40 KB gives ~33% headroom for JSON framing overhead.
  - Per-page budget, not max-before-rejection: conservative by design.
"""

from __future__ import annotations

import json
from typing import Any

from codeplane.config.constants import RESPONSE_BUDGET_BYTES


class BudgetAccumulator:
    """Byte-aware result accumulator for size-bounded pagination.

    Usage::

        acc = BudgetAccumulator()
        # Reserve space for fixed overhead (metadata, hints, etc.)
        acc.reserve(overhead_bytes)
        for item in all_results:
            if not acc.try_add(item):
                break  # budget exhausted
        results = acc.items
        has_more = not acc.has_room  # or len(all_results) > len(results)

    The accumulator measures each item's JSON-serialised byte size
    (UTF-8) to approximate the on-wire cost.  This is intentionally
    conservative: the real MCP response includes field names, nesting,
    and framing that are not counted per-item.
    """

    __slots__ = ("_budget", "_items", "_used", "_exhausted")

    def __init__(self, budget: int = RESPONSE_BUDGET_BYTES) -> None:
        self._budget = budget
        self._items: list[dict[str, Any]] = []
        self._used: int = 0
        self._exhausted: bool = False

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def reserve(self, overhead: int) -> None:
        """Reserve *overhead* bytes from the budget for fixed fields.

        Call this before adding items to ensure space is left for
        response metadata (summary, hints, pagination, etc.) that
        will be added after the accumulated items.
        """
        self._used += overhead
        if self._used >= self._budget:
            self._exhausted = True

    def try_add(self, item: dict[str, Any], *, nested: bool = True) -> bool:
        """Attempt to add *item* to the accumulator.

        Returns ``True`` if the item fit within the remaining budget,
        ``False`` if the budget is exhausted.  The first item is accepted
        even if slightly over budget (up to 2x) so a single moderately
        oversized result still produces output rather than an empty page.
        Items over 2x budget are rejected even as the first item.

        Args:
            item: The item to add.
            nested: If True (default), measure the item with nesting_depth=2
                to account for extra indentation when embedded in an array
                inside the response object.
        """
        if self._exhausted:
            return False

        # Items in arrays like structural_changes are at depth 2:
        # response object (0) -> array field (1) -> array item (2)
        size = measure_bytes(item, nesting_depth=2 if nested else 0)

        # First item gets a 2x budget allowance (to avoid empty pages),
        # but items over 2x budget are rejected even as first item
        # to prevent massive blowouts (e.g., 44KB file on 7.5KB budget).
        is_first = not self._items
        over_budget = self._used + size > self._budget
        massively_over = size > self._budget * 2

        if over_budget:
            if is_first and not massively_over:
                # Accept slightly-over first item to avoid empty page
                pass
            else:
                self._exhausted = True
                return False

        self._items.append(item)
        self._used += size

        # Mark exhausted if we just hit/exceeded the limit
        if self._used >= self._budget:
            self._exhausted = True

        return True

    @property
    def items(self) -> list[dict[str, Any]]:
        """Accumulated items that fit within the budget."""
        return self._items

    @property
    def used_bytes(self) -> int:
        """Total bytes consumed so far."""
        return self._used

    @property
    def remaining_bytes(self) -> int:
        """Bytes remaining before budget is exhausted."""
        return max(0, self._budget - self._used)

    @property
    def has_room(self) -> bool:
        """Whether the accumulator can still accept items."""
        return not self._exhausted

    @property
    def count(self) -> int:
        """Number of items accumulated."""
        return len(self._items)


# -----------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------


def measure_bytes(item: dict[str, Any], *, nesting_depth: int = 0) -> int:
    """Return the UTF-8 byte size of *item* serialised as pretty-printed JSON.

    Uses indent=2 to match VS Code's display format. This ensures our
    budget calculations match what users actually see, preventing the
    "Large tool result" warnings from VS Code.

    Args:
        item: The dict to measure.
        nesting_depth: How many levels deep this item will be nested in the
            final response. Each level adds 2 extra spaces per line.
            For items in a top-level array, use nesting_depth=1.

    Example:
        measure_bytes({"x": 1})  # standalone object
        measure_bytes({"x": 1}, nesting_depth=1)  # item in an array
    """
    base = json.dumps(item, indent=2)
    if nesting_depth > 0:
        # Add extra indentation to each line
        extra_indent = "  " * nesting_depth
        lines = base.split("\n")
        indented = "\n".join(extra_indent + line for line in lines)
        # Also add 2 bytes for array item separator (",\n")
        return len(indented.encode("utf-8")) + 2
    return len(base.encode("utf-8"))


# Convenience function for measuring items inside arrays
def measure_nested_item(item: dict[str, Any]) -> int:
    """Measure an item that will be nested inside a top-level array.

    Items in arrays like `structural_changes` get extra indentation
    compared to standalone measurement. This function accounts for that.
    """
    return measure_bytes(item, nesting_depth=1)


# Keep private alias for internal use
_measure = measure_bytes


def make_budget_pagination(
    *,
    has_more: bool,
    next_cursor: str | None = None,
    total_estimate: int | None = None,
) -> dict[str, Any]:
    """Build a standard pagination dict with optional budget metadata.

    Every paginated endpoint should use this to build its ``pagination``
    key so the agentic caller gets a consistent signal.
    """
    result: dict[str, Any] = {}
    if has_more and next_cursor is not None:
        result["next_cursor"] = next_cursor
    if total_estimate is not None:
        result["total_estimate"] = total_estimate
    if has_more:
        result["truncated"] = True
    return result
