"""Tier alignment metrics — measures how well recon tiers match GT categories.

Registered as ``@metric("cpl-tier-align")`` for EVEE evaluation.

Ground-truth categories map to expected tiers:
    E (Edit)         → full_file
    C (Context/Test) → min_scaffold
    S (Supp/Docs)    → summary_only

The ``@metric`` wrapper handles field mapping.  ``compute()`` receives:
    returned_tiers:  dict[path, tier]            (from model.returned_tiers)
    gt_categories:   list[{path, category}]      (from dataset.gt_categories)
"""

from __future__ import annotations

import statistics
from numbers import Number
from typing import Any

from evee import metric

# GT category → expected recon tier
_EXPECTED_TIER: dict[str, str] = {
    "E": "full_file",
    "C": "min_scaffold",
    "S": "summary_only",
}


@metric("cpl-tier-align")
class TierAlignmentMetric:
    """Measures how well recon's output tiers align with ground-truth categories."""

    def compute(
        self,
        returned_tiers: dict[str, str],
        gt_categories: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Compute tier alignment for a single query.

        For each GT category (E/C/S), computes:
        - exact_match: fraction of GT files in the expected tier
        - found_any:   fraction of GT files found in any tier
        """
        results: dict[str, Any] = {}
        for cat, expected_tier in _EXPECTED_TIER.items():
            gt_paths = [g["path"] for g in gt_categories if g["category"] == cat]
            if not gt_paths:
                results[f"{cat}_exact"] = None
                results[f"{cat}_found_any"] = None
                continue

            exact = sum(1 for p in gt_paths if returned_tiers.get(p) == expected_tier)
            found = sum(1 for p in gt_paths if p in returned_tiers)

            results[f"{cat}_exact"] = round(exact / len(gt_paths), 4)
            results[f"{cat}_found_any"] = round(found / len(gt_paths), 4)

        # Overall alignment (weighted across all GT files)
        all_gt = [g for g in gt_categories if g["category"] in _EXPECTED_TIER]
        if all_gt:
            exact_total = sum(
                1 for g in all_gt if returned_tiers.get(g["path"]) == _EXPECTED_TIER[g["category"]]
            )
            results["overall_exact"] = round(exact_total / len(all_gt), 4)
        else:
            results["overall_exact"] = None

        return results

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate tier alignment across all queries."""
        if not scores:
            return {}

        result: dict[str, Number] = {}
        for key in (
            "E_exact",
            "E_found_any",
            "C_exact",
            "C_found_any",
            "S_exact",
            "S_found_any",
            "overall_exact",
        ):
            values = [s[key] for s in scores if s.get(key) is not None]
            if values:
                result[f"avg_{key}"] = round(statistics.mean(values), 4)

        result["total_queries"] = len(scores)
        return result
