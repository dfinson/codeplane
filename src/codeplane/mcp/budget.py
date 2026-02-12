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

    def try_add(self, item: dict[str, Any]) -> bool:
        """Attempt to add *item* to the accumulator.

        Returns ``True`` if the item fit within the remaining budget,
        ``False`` if the budget is exhausted.  The first item is always
        accepted regardless of size so that a single oversized result
        still produces output rather than an empty page.
        """
        if self._exhausted:
            return False

        size = measure_bytes(item)

        # Always accept the first item even if it exceeds the budget,
        # so the caller never gets an empty page.
        if self._items and self._used + size > self._budget:
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


def measure_bytes(item: dict[str, Any]) -> int:
    """Return the UTF-8 byte size of *item* serialised as JSON.

    Uses compact separators (',', ':') to approximate the tightest
    reasonable on-wire encoding.  This is intentionally a slight
    undercount relative to pretty-printed JSON, which adds safety
    margin in the budget direction we want (we stop sooner).
    """
    return len(json.dumps(item, separators=(",", ":")).encode("utf-8"))


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
