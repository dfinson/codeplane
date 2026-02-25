"""Tier precision metrics — how pure is each returned tier?

Registered as ``@metric("cpl-tier-precision")`` for EVEE evaluation.

While tier-alignment measures *recall* per GT category ("of GT-E files,
how many landed in full_file?"), this metric measures *precision* per
**returned** tier ("of files we returned as full_file, how many are
actually GT-E?").

Key outputs:
    E_precision:        GT-E files at full_file / total full_file
    C_precision:        GT-C files at min_scaffold / total min_scaffold
    S_precision:        GT-S files at summary_only / total summary_only
    full_file_waste:    non-GT-E files at full_file / total full_file
    full_file_noise:    non-GT files at full_file / total full_file

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

# Inverse: tier → expected GT category
_TIER_TO_CAT: dict[str, str] = {v: k for k, v in _EXPECTED_TIER.items()}


@metric("cpl-tier-precision")
class TierPrecisionMetric:
    """Measures precision of each returned tier against GT categories."""

    def compute(
        self,
        returned_tiers: dict[str, str],
        gt_categories: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Compute tier precision for a single query.

        For each returned tier, computes:
        - precision: fraction of files in that tier that match the expected GT category
        - noise: fraction of files in that tier that are not in GT at all
        - waste: fraction of files in that tier that are GT but wrong category
        """
        gt_by_path: dict[str, str] = {g["path"]: g["category"] for g in gt_categories}

        results: dict[str, Any] = {}

        for tier, expected_cat in _TIER_TO_CAT.items():
            files_at_tier = [p for p, t in returned_tiers.items() if t == tier]
            count = len(files_at_tier)
            results[f"{expected_cat}_returned_count"] = count

            if not count:
                results[f"{expected_cat}_precision"] = None
                results[f"{expected_cat}_noise"] = None
                results[f"{expected_cat}_waste"] = None
                continue

            correct = sum(1 for p in files_at_tier if gt_by_path.get(p) == expected_cat)
            not_in_gt = sum(1 for p in files_at_tier if p not in gt_by_path)
            wrong_cat = count - correct - not_in_gt  # in GT but wrong category

            results[f"{expected_cat}_precision"] = round(correct / count, 4)
            results[f"{expected_cat}_noise"] = round(not_in_gt / count, 4)
            results[f"{expected_cat}_waste"] = round(wrong_cat / count, 4)

        # Full-file slot efficiency — the most expensive tier
        full_file_paths = [p for p, t in returned_tiers.items() if t == "full_file"]
        ff_count = len(full_file_paths)
        gt_e_count = sum(1 for g in gt_categories if g["category"] == "E")

        if ff_count and gt_e_count:
            correct_e = sum(1 for p in full_file_paths if gt_by_path.get(p) == "E")
            results["full_file_efficiency"] = round(correct_e / gt_e_count, 4)
            results["full_file_utilization"] = round(correct_e / ff_count, 4)
        else:
            results["full_file_efficiency"] = None
            results["full_file_utilization"] = None

        return results

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate tier precision across all queries."""
        if not scores:
            return {}

        result: dict[str, Number] = {}
        for key in (
            "E_precision",
            "E_noise",
            "E_waste",
            "E_returned_count",
            "C_precision",
            "C_noise",
            "C_waste",
            "C_returned_count",
            "S_precision",
            "S_noise",
            "S_waste",
            "S_returned_count",
            "full_file_efficiency",
            "full_file_utilization",
        ):
            values = [s[key] for s in scores if s.get(key) is not None]
            if values:
                result[f"avg_{key}"] = round(statistics.mean(values), 4)

        result["total_queries"] = len(scores)
        return result
