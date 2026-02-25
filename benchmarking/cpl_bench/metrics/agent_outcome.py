"""Agent outcome metrics — quality scores from code review.

Registered as ``@metric("cpl-outcome")`` for EVEE evaluation.

The ``@metric`` wrapper handles field mapping.  ``compute()`` receives:
    outcome: dict   (from model.outcome — pre-scored quality dimensions)
    variant: str    (from model.variant — "codeplane" or "native")
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from numbers import Number
from typing import Any

from evee import metric

_OUTCOME_DIMENSIONS = [
    "correctness",  # 0-3
    "completeness",  # 0-3
    "code_quality",  # 0-3
    "test_quality",  # 0-3
    "documentation",  # 0-3
    "lint_clean",  # 0-1
    "tests_pass",  # 0-1
]
_MAX_SCORE = 17


@metric("cpl-outcome")
class AgentOutcomeMetric:
    """Measures agent outcome quality from pre-scored code reviews."""

    def compute(self, outcome: dict, variant: str) -> dict[str, Any]:
        """Extract outcome scores from the pre-scored trace."""
        if not outcome:
            return {"variant": variant, "scored": False}

        result: dict[str, Any] = {"variant": variant, "scored": True}
        total = 0
        for dim in _OUTCOME_DIMENSIONS:
            val = outcome.get(dim)
            if val is not None:
                result[dim] = val
                total += val

        result["score"] = outcome.get("score", total)
        result["max_score"] = _MAX_SCORE
        result["score_pct"] = round(result["score"] / _MAX_SCORE * 100, 1)

        return result

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate outcome scores, grouped by variant."""
        scored = [s for s in scores if s.get("scored")]
        if not scored:
            return {"scored_sessions": 0, "total_sessions": len(scores)}

        by_variant: dict[str, list[dict]] = defaultdict(list)
        for s in scored:
            by_variant[s.get("variant", "unknown")].append(s)

        result: dict[str, Number] = {
            "scored_sessions": len(scored),
            "total_sessions": len(scores),
        }

        for variant, vs in sorted(by_variant.items()):
            prefix = f"{variant}_"
            result[f"{prefix}n"] = len(vs)
            result[f"{prefix}avg_score"] = round(statistics.mean(s["score"] for s in vs), 2)
            result[f"{prefix}avg_score_pct"] = round(statistics.mean(s["score_pct"] for s in vs), 1)
            for dim in _OUTCOME_DIMENSIONS:
                values = [s[dim] for s in vs if dim in s]
                if values:
                    result[f"{prefix}avg_{dim}"] = round(statistics.mean(values), 2)

        # Head-to-head
        if "codeplane" in by_variant and "native" in by_variant:
            cp_mean = statistics.mean(s["score"] for s in by_variant["codeplane"])
            nat_mean = statistics.mean(s["score"] for s in by_variant["native"])
            result["delta_score"] = round(cp_mean - nat_mean, 2)

        return result
