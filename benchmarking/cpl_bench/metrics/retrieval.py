"""Retrieval metrics — Precision, Recall, F1, Noise Ratio.

Registered as ``@metric("cpl-retrieval")`` for EVEE evaluation.

The ``@metric`` wrapper handles field mapping via the config YAML.
The inner ``compute()`` receives the mapped fields as keyword arguments:
    returned_files: list[str]   (from model.returned_files)
    gt_files:       list[str]   (from dataset.gt_files)
"""

from __future__ import annotations

import statistics
from numbers import Number
from typing import Any

from evee import metric


@metric("cpl-retrieval")
class RetrievalMetric:
    """Standard information retrieval metrics for recon evaluation."""

    def compute(self, returned_files: list[str], gt_files: list[str]) -> dict[str, Any]:
        """Compute P/R/F1/noise for a single query."""
        returned = set(returned_files)
        gt = set(gt_files)

        tp = len(returned & gt)
        precision = tp / len(returned) if returned else 0.0
        recall = tp / len(gt) if gt else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        noise_ratio = len(returned - gt) / len(returned) if returned else 0.0

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "noise_ratio": round(noise_ratio, 4),
            "tp": tp,
            "fp": len(returned - gt),
            "fn": len(gt - returned),
            "returned_count": len(returned),
            "gt_count": len(gt),
        }

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate retrieval metrics across all queries."""
        if not scores:
            return {"avg_precision": 0.0, "avg_recall": 0.0, "avg_f1": 0.0, "avg_noise_ratio": 0.0}

        return {
            "avg_precision": round(statistics.mean(s["precision"] for s in scores), 4),
            "avg_recall": round(statistics.mean(s["recall"] for s in scores), 4),
            "avg_f1": round(statistics.mean(s["f1"] for s in scores), 4),
            "median_f1": round(statistics.median(s["f1"] for s in scores), 4),
            "min_f1": round(min(s["f1"] for s in scores), 4),
            "max_f1": round(max(s["f1"] for s in scores), 4),
            "avg_noise_ratio": round(statistics.mean(s["noise_ratio"] for s in scores), 4),
            "total_queries": len(scores),
        }
