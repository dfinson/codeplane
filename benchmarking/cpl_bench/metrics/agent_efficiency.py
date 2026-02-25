"""Agent efficiency metrics — turns, tokens, tool calls for A/B comparison.

Registered as ``@metric("cpl-efficiency")`` for EVEE evaluation.

The ``@metric`` wrapper handles field mapping.  ``compute()`` receives all
mapped fields as keyword arguments from the agent replay model output.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from numbers import Number
from typing import Any

from evee import metric


@metric("cpl-efficiency")
class AgentEfficiencyMetric:
    """Measures agent efficiency — tool calls, tokens, turns."""

    def compute(
        self,
        variant: str,
        turns: int,
        total_tool_calls: int,
        codeplane_tool_calls: int,
        terminal_tool_calls: int,
        tool_search_calls: int,
        other_tool_calls: int,
        total_tokens: int,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int,
        cache_hit_ratio: float,
    ) -> dict[str, Any]:
        """Pass through pre-computed efficiency fields from the model."""
        return {
            "variant": variant,
            "turns": turns,
            "total_tool_calls": total_tool_calls,
            "codeplane_tool_calls": codeplane_tool_calls,
            "terminal_tool_calls": terminal_tool_calls,
            "tool_search_calls": tool_search_calls,
            "other_tool_calls": other_tool_calls,
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit_ratio": cache_hit_ratio,
        }

    def aggregate(self, scores: list[dict[str, Any]]) -> dict[str, Number]:
        """Aggregate efficiency metrics, grouped by variant for head-to-head comparison."""
        if not scores:
            return {}

        # Group by variant
        by_variant: dict[str, list[dict]] = defaultdict(list)
        for s in scores:
            by_variant[s.get("variant", "unknown")].append(s)

        result: dict[str, Number] = {"total_sessions": len(scores)}

        numeric_keys = [
            "turns",
            "total_tool_calls",
            "codeplane_tool_calls",
            "terminal_tool_calls",
            "tool_search_calls",
            "other_tool_calls",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "cache_hit_ratio",
        ]

        for variant, variant_scores in sorted(by_variant.items()):
            prefix = f"{variant}_"
            result[f"{prefix}n"] = len(variant_scores)
            for key in numeric_keys:
                values = [s[key] for s in variant_scores if s.get(key) is not None]
                if values:
                    result[f"{prefix}avg_{key}"] = round(statistics.mean(values), 2)
                    result[f"{prefix}median_{key}"] = round(statistics.median(values), 2)

        # Head-to-head deltas (codeplane vs native)
        if "codeplane" in by_variant and "native" in by_variant:
            cp = by_variant["codeplane"]
            nat = by_variant["native"]
            for key in ("turns", "total_tool_calls", "total_tokens"):
                cp_mean = statistics.mean(s[key] for s in cp if s.get(key) is not None)
                nat_mean = statistics.mean(s[key] for s in nat if s.get(key) is not None)
                result[f"delta_{key}"] = round(cp_mean - nat_mean, 2)
                if nat_mean:
                    result[f"delta_{key}_pct"] = round((cp_mean - nat_mean) / nat_mean * 100, 1)

        return result
