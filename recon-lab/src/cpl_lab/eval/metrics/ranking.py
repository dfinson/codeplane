"""Ranking metrics — NDCG, Hit@K, Cutoff F1/Precision/Recall.

Registered as ``@metric("cpl-ranking")`` for EVEE evaluation.

Evaluates the ranker's ranked list quality and the cutoff model's
set-selection quality against def-level ground truth.
"""

from __future__ import annotations

import math
import statistics
from numbers import Number
from typing import Any

from evee import metric


def _dcg(relevances: list[float], k: int | None = None) -> float:
    """Discounted cumulative gain."""
    if k is not None:
        relevances = relevances[:k]
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))


def _ndcg(
    predicted_relevances: list[float], ideal_relevances: list[float], k: int | None = None
) -> float:
    """Normalized DCG."""
    ideal = _dcg(sorted(ideal_relevances, reverse=True), k)
    if ideal == 0:
        return 0.0
    return _dcg(predicted_relevances, k) / ideal


def _prf(returned: set[str], gt: set[str]) -> dict[str, float]:
    """Precision, recall, F1."""
    tp = len(returned & gt)
    p = tp / len(returned) if returned else 0.0
    r = tp / len(gt) if gt else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


@metric("cpl-ranking")
class RankingMetric:
    """NDCG, Hit@K, and cutoff F1/P/R for def-level ranking evaluation."""

    def compute(
        self,
        ranked_def_uids: list[str],
        predicted_relevances: list[float],
        predicted_n: int,
        gt_edited: list[str],
        gt_read_necessary: list[str],
        query_type: str = "UNKNOWN",
    ) -> dict[str, Any]:
        """Compute ranking metrics for a single query."""
        edited_set = set(gt_edited)
        read_set = set(gt_read_necessary)
        all_gt = edited_set | read_set

        ideal_relevances = [2.0] * len(edited_set) + [1.0] * len(read_set)

        actual_relevances = []
        for uid in ranked_def_uids:
            if uid in edited_set:
                actual_relevances.append(2.0)
            elif uid in read_set:
                actual_relevances.append(1.0)
            else:
                actual_relevances.append(0.0)

        ndcg_5 = _ndcg(actual_relevances, ideal_relevances, k=5)
        ndcg_10 = _ndcg(actual_relevances, ideal_relevances, k=10)
        ndcg_20 = _ndcg(actual_relevances, ideal_relevances, k=20)
        ndcg_full = _ndcg(actual_relevances, ideal_relevances)

        top_5 = set(ranked_def_uids[:5])
        top_10 = set(ranked_def_uids[:10])
        hit_5 = 1.0 if top_5 & edited_set else 0.0
        hit_10 = 1.0 if top_10 & edited_set else 0.0

        returned = set(ranked_def_uids[:predicted_n])
        cutoff_prf = _prf(returned, all_gt)

        return {
            "ndcg_5": round(ndcg_5, 4),
            "ndcg_10": round(ndcg_10, 4),
            "ndcg_20": round(ndcg_20, 4),
            "ndcg_full": round(ndcg_full, 4),
            "hit_5": hit_5,
            "hit_10": hit_10,
            "cutoff_precision": cutoff_prf["precision"],
            "cutoff_recall": cutoff_prf["recall"],
            "cutoff_f1": cutoff_prf["f1"],
            "predicted_n": predicted_n,
            "gt_edited_count": len(edited_set),
            "gt_read_count": len(read_set),
            "query_type": query_type,
        }

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate ranking metrics across all queries."""
        if not scores:
            return {}

        metric_keys = (
            "ndcg_5",
            "ndcg_10",
            "ndcg_20",
            "ndcg_full",
            "hit_5",
            "hit_10",
            "cutoff_precision",
            "cutoff_recall",
            "cutoff_f1",
        )

        result: dict[str, Number] = {}
        for key in metric_keys:
            values = [s[key] for s in scores]
            result[f"avg_{key}"] = round(statistics.mean(values), 4)

        result["avg_predicted_n"] = round(statistics.mean(s["predicted_n"] for s in scores), 1)
        result["total_queries"] = len(scores)

        # Per-query-type breakdown
        by_type: dict[str, list[dict[str, Any]]] = {}
        for s in scores:
            qt = s.get("query_type", "UNKNOWN")
            by_type.setdefault(qt, []).append(s)

        for qt, type_scores in sorted(by_type.items()):
            prefix = f"qt_{qt.lower()}"
            for key in metric_keys:
                vals = [s[key] for s in type_scores]
                result[f"{prefix}/avg_{key}"] = round(statistics.mean(vals), 4)
            result[f"{prefix}/count"] = len(type_scores)

        return result
