"""Gate metrics — accuracy and confusion matrix.

Registered as ``@metric("cpl-gate")`` for EVEE evaluation.

Evaluates the gate classifier's ability to correctly route queries
as OK / UNSAT / BROAD / AMBIG.
"""

from __future__ import annotations

from collections import Counter
from numbers import Number
from typing import Any

from evee import metric

_GATE_LABELS = ("OK", "UNSAT", "BROAD", "AMBIG")


@metric("cpl-gate")
class GateMetric:
    """Gate classification accuracy and confusion matrix."""

    def compute(
        self,
        predicted_gate: str,
        gt_gate: str,
        query_type: str = "UNKNOWN",
    ) -> dict[str, Any]:
        """Compute gate metric for a single query."""
        return {
            "correct": 1.0 if predicted_gate == gt_gate else 0.0,
            "predicted": predicted_gate,
            "actual": gt_gate,
            "query_type": query_type,
        }

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate gate metrics — accuracy + confusion matrix."""
        if not scores:
            return {}

        correct = sum(s["correct"] for s in scores)
        total = len(scores)

        result: dict[str, Number] = {
            "accuracy": round(correct / total, 4) if total else 0.0,
            "total_queries": total,
        }

        by_class: dict[str, list[float]] = {}
        for s in scores:
            actual = s["actual"]
            by_class.setdefault(actual, []).append(s["correct"])

        for label in _GATE_LABELS:
            vals = by_class.get(label, [])
            result[f"{label.lower()}_accuracy"] = round(sum(vals) / len(vals), 4) if vals else 0.0
            result[f"{label.lower()}_count"] = len(vals)

        confusion: Counter[tuple[str, str]] = Counter()
        for s in scores:
            confusion[(s["actual"], s["predicted"])] += 1
        for actual in _GATE_LABELS:
            for predicted in _GATE_LABELS:
                result[f"confusion_{actual}_{predicted}"] = confusion.get((actual, predicted), 0)

        # Per-query-type accuracy breakdown
        by_type: dict[str, list[dict[str, Any]]] = {}
        for s in scores:
            qt = s.get("query_type", "UNKNOWN")
            by_type.setdefault(qt, []).append(s)

        for qt, type_scores in sorted(by_type.items()):
            prefix = f"qt_{qt.lower()}"
            qt_correct = sum(s["correct"] for s in type_scores)
            qt_total = len(type_scores)
            result[f"{prefix}/accuracy"] = round(qt_correct / qt_total, 4) if qt_total else 0.0
            result[f"{prefix}/count"] = qt_total

        return result
