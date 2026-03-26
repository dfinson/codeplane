"""Deterministic retry detection for tool calls.

A tool call is a retry if and only if a prior call with the same
(tool_name, tool_target) exists in this job and that prior call failed.
No windows, no thresholds — just a factual relationship.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryResult:
    """Result of checking whether a tool call is a retry."""

    is_retry: bool
    prior_failure_span_id: int | None


class RetryTracker:
    """Tracks tool call outcomes per (tool_name, tool_target) pair.

    A tool call is a retry if and only if a prior call with the
    same (tool_name, tool_target) exists in this job and that prior
    call failed (success = False). No windows, no thresholds.
    """

    def __init__(self) -> None:
        # Maps (tool_name, tool_target) → list of (span_id, success)
        self._history: dict[tuple[str, str], list[tuple[int, bool]]] = defaultdict(list)

    def record(
        self,
        tool_name: str,
        tool_target: str,
        span_id: int,
        success: bool,
    ) -> RetryResult:
        """Record a tool call and check if it retries a prior failure.

        Returns a RetryResult indicating whether this is a retry and,
        if so, which prior span it retries.
        """
        key = (tool_name, tool_target)
        prior_calls = self._history[key]

        # Find the most recent failed call for this exact (name, target)
        prior_failure_id: int | None = None
        for past_span_id, past_success in reversed(prior_calls):
            if not past_success:
                prior_failure_id = past_span_id
                break

        self._history[key].append((span_id, success))

        return RetryResult(
            is_retry=prior_failure_id is not None,
            prior_failure_span_id=prior_failure_id,
        )

    def reset(self) -> None:
        """Clear all tracked history (e.g. at start of a new job)."""
        self._history.clear()
