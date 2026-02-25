"""Tier confusion metrics — confusion matrix + promotion/demotion analysis.

Registered as ``@metric("cpl-tier-confusion")`` for EVEE evaluation.

For every GT file, tracks where it actually ended up:
    E → full_file (correct), min_scaffold (demoted), summary_only (demoted), miss
    C → full_file (promoted), min_scaffold (correct), summary_only (demoted), miss
    S → full_file (promoted), min_scaffold (promoted), summary_only (correct), miss

Aggregate outputs include:
    promotion_rate:  GT files placed at a *higher* tier than expected (wasteful)
    demotion_rate:   GT files placed at a *lower* tier than expected (harmful)
    miss_rate:       GT files not returned at all
    non_gt_count:    returned files not in GT (pure noise)

The ``@metric`` wrapper handles field mapping.  ``compute()`` receives:
    returned_tiers:  dict[path, tier]            (from model.returned_tiers)
    gt_categories:   list[{path, category}]      (from dataset.gt_categories)
"""

from __future__ import annotations

import statistics
from numbers import Number
from typing import Any

from evee import metric

# GT category → expected recon tier (ordered high→low fidelity)
_EXPECTED_TIER: dict[str, str] = {
    "E": "full_file",
    "C": "min_scaffold",
    "S": "summary_only",
}

# Tier ordinal (higher = more content returned = more expensive)
_TIER_RANK: dict[str, int] = {
    "summary_only": 0,
    "min_scaffold": 1,
    "full_file": 2,
}


@metric("cpl-tier-confusion")
class TierConfusionMetric:
    """Full confusion matrix for tier assignment + promotion/demotion rates."""

    def compute(
        self,
        returned_tiers: dict[str, str],
        gt_categories: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Compute confusion matrix for a single query."""
        gt_by_path: dict[str, str] = {g["path"]: g["category"] for g in gt_categories}
        all_gt_paths = set(gt_by_path.keys())
        all_returned_paths = set(returned_tiers.keys())

        # --- Confusion matrix cells ---
        matrix: dict[str, int] = {}
        for cat in ("E", "C", "S"):
            for tier in ("full_file", "min_scaffold", "summary_only", "miss"):
                matrix[f"{cat}_to_{tier}"] = 0

        for g in gt_categories:
            path, cat = g["path"], g["category"]
            if cat not in _EXPECTED_TIER:
                continue
            actual_tier = returned_tiers.get(path)
            if actual_tier is None:
                matrix[f"{cat}_to_miss"] += 1
            else:
                matrix[f"{cat}_to_{actual_tier}"] += 1

        # Non-GT files returned (pure noise, by tier)
        non_gt_paths = all_returned_paths - all_gt_paths
        non_gt_full = sum(1 for p in non_gt_paths if returned_tiers[p] == "full_file")
        non_gt_scaffold = sum(1 for p in non_gt_paths if returned_tiers[p] == "min_scaffold")
        non_gt_summary = sum(1 for p in non_gt_paths if returned_tiers[p] == "summary_only")

        matrix["non_gt_full_file"] = non_gt_full
        matrix["non_gt_min_scaffold"] = non_gt_scaffold
        matrix["non_gt_summary_only"] = non_gt_summary

        # --- Promotion / demotion / miss rates ---
        total_gt = len([g for g in gt_categories if g["category"] in _EXPECTED_TIER])
        promoted = 0
        demoted = 0
        correct = 0
        missed = 0

        for g in gt_categories:
            path, cat = g["path"], g["category"]
            if cat not in _EXPECTED_TIER:
                continue
            expected_tier = _EXPECTED_TIER[cat]
            actual_tier = returned_tiers.get(path)

            if actual_tier is None:
                missed += 1
            elif actual_tier == expected_tier:
                correct += 1
            elif _TIER_RANK.get(actual_tier, -1) > _TIER_RANK[expected_tier]:
                promoted += 1
            else:
                demoted += 1

        results: dict[str, Any] = {**matrix}

        if total_gt > 0:
            results["promotion_rate"] = round(promoted / total_gt, 4)
            results["demotion_rate"] = round(demoted / total_gt, 4)
            results["correct_rate"] = round(correct / total_gt, 4)
            results["miss_rate"] = round(missed / total_gt, 4)
        else:
            results["promotion_rate"] = None
            results["demotion_rate"] = None
            results["correct_rate"] = None
            results["miss_rate"] = None

        results["non_gt_count"] = len(non_gt_paths)
        results["total_gt"] = total_gt
        results["total_returned"] = len(returned_tiers)

        return results

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate confusion metrics across all queries."""
        if not scores:
            return {}

        result: dict[str, Number] = {}

        # Rate metrics — average across queries
        for key in (
            "promotion_rate",
            "demotion_rate",
            "correct_rate",
            "miss_rate",
        ):
            values = [s[key] for s in scores if s.get(key) is not None]
            if values:
                result[f"avg_{key}"] = round(statistics.mean(values), 4)

        # Count metrics — sum across queries for total confusion matrix
        count_keys = [
            f"{cat}_to_{tier}"
            for cat in ("E", "C", "S")
            for tier in ("full_file", "min_scaffold", "summary_only", "miss")
        ] + ["non_gt_full_file", "non_gt_min_scaffold", "non_gt_summary_only"]

        for key in count_keys:
            result[f"total_{key}"] = sum(s.get(key, 0) for s in scores)

        # Totals
        result["total_non_gt"] = sum(s.get("non_gt_count", 0) for s in scores)
        result["total_gt_files"] = sum(s.get("total_gt", 0) for s in scores)
        result["total_returned_files"] = sum(s.get("total_returned", 0) for s in scores)
        result["total_queries"] = len(scores)

        return result
