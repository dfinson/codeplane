"""Retrieval metrics — Precision, Recall, F1 at two fidelity levels.

Registered as ``@metric("cpl-retrieval")`` for EVEE evaluation.

Computes P/R/F1 for two views of the returned file set:
    all:       every returned file regardless of tier
    scaffold:  scaffold tier only (files the agent gets imports + signatures for)

The ``@metric`` wrapper handles field mapping via the config YAML.
The inner ``compute()`` receives the mapped fields as keyword arguments:
    returned_tiers: dict[path, tier]   (from model.returned_tiers)
    gt_files:       list[str]          (from dataset.gt_files)
"""

from __future__ import annotations

import statistics
from numbers import Number
from typing import Any

from evee import metric

_SCAFFOLD_TIERS = {"scaffold"}


def _prf(returned: set[str], gt: set[str]) -> dict[str, float]:
    """Compute precision, recall, F1 for a returned-vs-GT pair."""
    tp = len(returned & gt)
    p = tp / len(returned) if returned else 0.0
    r = tp / len(gt) if gt else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


@metric("cpl-retrieval")
class RetrievalMetric:
    """P/R/F1 at two fidelity levels: all (every returned file), scaffold (scaffold tier only)."""

    def compute(self, returned_tiers: dict[str, str], gt_files: list[str]) -> dict[str, Any]:
        """Compute P/R/F1 for a single query at both levels."""
        gt = set(gt_files)

        all_returned = set(returned_tiers.keys())
        scaffold_returned = {p for p, t in returned_tiers.items() if t in _SCAFFOLD_TIERS}

        all_prf = _prf(all_returned, gt)
        scaffold_prf = _prf(scaffold_returned, gt)

        return {
            # All tiers
            "all_precision": all_prf["precision"],
            "all_recall": all_prf["recall"],
            "all_f1": all_prf["f1"],
            # Scaffold tier
            "scaffold_precision": scaffold_prf["precision"],
            "scaffold_recall": scaffold_prf["recall"],
            "scaffold_f1": scaffold_prf["f1"],
            # Counts
            "all_count": len(all_returned),
            "scaffold_count": len(scaffold_returned),
            "gt_count": len(gt),
        }

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate retrieval metrics across all queries."""
        if not scores:
            return {}

        result: dict[str, Number] = {}
        for level in ("all", "scaffold"):
            for stat in ("precision", "recall", "f1"):
                key = f"{level}_{stat}"
                values = [s[key] for s in scores]
                result[f"avg_{key}"] = round(statistics.mean(values), 4)
            f1_values = [s[f"{level}_f1"] for s in scores]
            result[f"median_{level}_f1"] = round(statistics.median(f1_values), 4)
            count_values = [s[f"{level}_count"] for s in scores]
            result[f"avg_{level}_count"] = round(statistics.mean(count_values), 1)

        result["avg_gt_count"] = round(statistics.mean(s["gt_count"] for s in scores), 1)
        result["total_queries"] = len(scores)
        return result
