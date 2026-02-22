"""Aggregate benchmark metrics across multiple runs.

Usage:
    python -m benchmarking.aggregate_metrics <results_dir> [<results_dir2> ...]

Reads all *_result_metrics.json files from the given directories, groups
them by variant (codeplane vs native), and produces:

  1. Per-group summary statistics (mean, median, stdev, min, max)
  2. Head-to-head comparison (deltas + percentages)
  3. Per-issue paired breakdown

Output is saved as aggregate_report.json in the first results directory,
and a human-readable summary is printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


def _stats(values: list[float | int]) -> dict[str, float | int | None]:
    """Compute summary stats for a list of numeric values."""
    if not values:
        return {"mean": None, "median": None, "stdev": None, "min": None, "max": None, "n": 0}
    n = len(values)
    mean = sum(values) / n
    sorted_v = sorted(values)
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        stdev = math.sqrt(variance)
    else:
        stdev = 0.0
    return {
        "mean": round(mean, 2),
        "median": round(median, 2),
        "stdev": round(stdev, 2),
        "min": min(values),
        "max": max(values),
        "n": n,
    }


def _safe_get(d: dict, *keys: str, default: float | int = 0) -> float | int:
    """Nested dict get with a default."""
    val = d
    for k in keys:
        if not isinstance(val, dict):
            return default
        val = val.get(k, default)
    return val if val is not None else default


# ---------------------------------------------------------------------------
# Metric extraction from a single result_metrics.json
# ---------------------------------------------------------------------------

# Each entry: (display_name, extractor_function)
_METRIC_EXTRACTORS: list[tuple[str, Any]] = [
    ("turns", lambda m: m.get("turns", 0)),
    ("tool_calls_total", lambda m: _safe_get(m, "tool_calls", "total")),
    ("tool_calls_codeplane", lambda m: _safe_get(m, "tool_calls", "codeplane")),
    ("tool_calls_terminal", lambda m: _safe_get(m, "tool_calls", "terminal")),
    ("tool_calls_tool_search", lambda m: _safe_get(m, "tool_calls", "tool_search")),
    ("tool_calls_other", lambda m: _safe_get(m, "tool_calls", "other")),
    ("tool_calls_errors", lambda m: _safe_get(m, "tool_calls", "errors")),
    ("tool_thinking_chars", lambda m: _safe_get(m, "tool_calls", "total_thinking_chars")),
    ("tokens_total", lambda m: _safe_get(m, "tokens", "total")),
    ("tokens_prompt", lambda m: _safe_get(m, "tokens", "prompt")),
    ("tokens_completion", lambda m: _safe_get(m, "tokens", "completion")),
    ("tokens_cached", lambda m: _safe_get(m, "tokens", "cached")),
    ("tokens_reasoning", lambda m: _safe_get(m, "tokens", "reasoning")),
    ("cache_hit_ratio", lambda m: _safe_get(m, "tokens", "cache_hit_ratio")),
    ("llm_duration_ms", lambda m: m.get("llm_duration_ms", 0)),
    ("avg_ttft_ms", lambda m: m.get("avg_ttft_ms")),
    ("context_first", lambda m: _safe_get(m, "context_growth", "first")),
    ("context_last", lambda m: _safe_get(m, "context_growth", "last")),
    ("context_max", lambda m: _safe_get(m, "context_growth", "max")),
    ("context_mean", lambda m: _safe_get(m, "context_growth", "mean")),
    # Outcome metrics (may not be present — added manually)
    ("outcome_correctness", lambda m: _safe_get(m, "outcome", "correctness") if "outcome" in m else None),
    ("outcome_completeness", lambda m: _safe_get(m, "outcome", "completeness") if "outcome" in m else None),
    ("outcome_code_quality", lambda m: _safe_get(m, "outcome", "code_quality") if "outcome" in m else None),
    ("outcome_test_quality", lambda m: _safe_get(m, "outcome", "test_quality") if "outcome" in m else None),
    ("outcome_documentation", lambda m: _safe_get(m, "outcome", "documentation") if "outcome" in m else None),
    ("outcome_lint_clean", lambda m: _safe_get(m, "outcome", "lint_clean") if "outcome" in m else None),
    ("outcome_tests_pass", lambda m: _safe_get(m, "outcome", "tests_pass") if "outcome" in m else None),
    ("outcome_score", lambda m: _safe_get(m, "outcome", "score") if "outcome" in m else None),
]


def _extract_values(
    metrics_list: list[dict[str, Any]],
) -> dict[str, list[float | int]]:
    """Extract metric values from a list of result_metrics dicts."""
    result: dict[str, list[float | int]] = {}
    for name, extractor in _METRIC_EXTRACTORS:
        values = []
        for m in metrics_list:
            v = extractor(m)
            if v is not None:
                values.append(v)
        result[name] = values
    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _group_summary(metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary stats for a group of metric results."""
    extracted = _extract_values(metrics_list)
    return {name: _stats(values) for name, values in extracted.items()}


def _comparison(
    baseline_metrics: list[dict[str, Any]],
    codeplane_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute deltas between baseline and codeplane groups."""
    baseline_vals = _extract_values(baseline_metrics)
    codeplane_vals = _extract_values(codeplane_metrics)

    comparison: dict[str, Any] = {}
    for name in baseline_vals:
        b_vals = baseline_vals[name]
        c_vals = codeplane_vals.get(name, [])
        if not b_vals or not c_vals:
            continue
        b_mean = sum(b_vals) / len(b_vals)
        c_mean = sum(c_vals) / len(c_vals)
        delta = c_mean - b_mean
        delta_pct = (delta / b_mean * 100) if b_mean != 0 else None
        comparison[name] = {
            "baseline_mean": round(b_mean, 2),
            "codeplane_mean": round(c_mean, 2),
            "delta": round(delta, 2),
            "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
        }
    return comparison


def _per_issue_breakdown(
    baseline_metrics: list[dict[str, Any]],
    codeplane_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair runs by issue and compute per-issue deltas."""
    # Index by issue
    baseline_by_issue = {m["issue"]: m for m in baseline_metrics}
    codeplane_by_issue = {m["issue"]: m for m in codeplane_metrics}

    all_issues = sorted(set(baseline_by_issue) | set(codeplane_by_issue))
    breakdown = []

    key_metrics = [
        ("turns", lambda m: m.get("turns", 0)),
        ("tool_calls", lambda m: _safe_get(m, "tool_calls", "total")),
        ("tool_errors", lambda m: _safe_get(m, "tool_calls", "errors")),
        ("tokens_total", lambda m: _safe_get(m, "tokens", "total")),
        ("tokens_completion", lambda m: _safe_get(m, "tokens", "completion")),
        ("cache_hit_ratio", lambda m: _safe_get(m, "tokens", "cache_hit_ratio")),
        ("llm_duration_ms", lambda m: m.get("llm_duration_ms", 0)),
        ("avg_ttft_ms", lambda m: m.get("avg_ttft_ms")),
        ("outcome_total", lambda m: _safe_get(m, "outcome", "total")),
    ]

    for issue in all_issues:
        b = baseline_by_issue.get(issue)
        c = codeplane_by_issue.get(issue)
        row: dict[str, Any] = {"issue": issue}

        for name, extractor in key_metrics:
            b_val = extractor(b) if b else None
            c_val = extractor(c) if c else None
            entry: dict[str, Any] = {"baseline": b_val, "codeplane": c_val}
            if b_val is not None and c_val is not None:
                delta = c_val - b_val
                entry["delta"] = round(delta, 2)
                entry["delta_pct"] = round(delta / b_val * 100, 2) if b_val != 0 else None
            row[name] = entry

        breakdown.append(row)

    return breakdown


def aggregate(results_dirs: list[Path]) -> dict[str, Any]:
    """Load all result_metrics.json and produce the aggregate report."""
    # Collect all metrics files
    all_metrics: list[dict[str, Any]] = []
    for d in results_dirs:
        for f in sorted(d.glob("*_result_metrics.json")):
            with open(f) as fp:
                all_metrics.append(json.load(fp))

    if not all_metrics:
        return {"error": "No result_metrics.json files found"}

    # Group by variant
    baseline = [m for m in all_metrics if not m.get("codeplane")]
    codeplane = [m for m in all_metrics if m.get("codeplane")]

    report: dict[str, Any] = {
        "total_runs": len(all_metrics),
        "baseline_runs": len(baseline),
        "codeplane_runs": len(codeplane),
    }

    if baseline:
        report["baseline_summary"] = _group_summary(baseline)
    if codeplane:
        report["codeplane_summary"] = _group_summary(codeplane)
    if baseline and codeplane:
        report["comparison"] = _comparison(baseline, codeplane)
        report["per_issue"] = _per_issue_breakdown(baseline, codeplane)

    return report


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------

_LOWER_IS_BETTER = {
    "turns",
    "tool_calls_total",
    "tool_calls_errors",
    "tokens_total",
    "tokens_prompt",
    "tokens_completion",
    "tokens_reasoning",
    "llm_duration_ms",
    "avg_ttft_ms",
    "context_max",
    "context_last",
}

_HIGHER_IS_BETTER = {
    "cache_hit_ratio",
    "outcome_correctness",
    "outcome_completeness",
    "outcome_code_quality",
    "outcome_test_quality",
    "outcome_documentation",
    "outcome_lint_clean",
    "outcome_tests_pass",
    "outcome_total",
}


def _direction_indicator(name: str, delta_pct: float | None) -> str:
    """Return a human-readable indicator for whether delta is good/bad."""
    if delta_pct is None or delta_pct == 0:
        return ""
    if name in _LOWER_IS_BETTER:
        return " (better)" if delta_pct < 0 else " (worse)"
    if name in _HIGHER_IS_BETTER:
        return " (better)" if delta_pct > 0 else " (worse)"
    return ""


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable summary of the aggregate report."""
    print(f"\n{'=' * 60}")
    print(f"BENCHMARK AGGREGATE REPORT")
    print(f"{'=' * 60}")
    print(f"Total runs: {report['total_runs']} "
          f"(baseline={report['baseline_runs']}, codeplane={report['codeplane_runs']})")

    # Summary stats per group
    for group in ("baseline", "codeplane"):
        key = f"{group}_summary"
        if key not in report:
            continue
        summary = report[key]
        print(f"\n--- {group.upper()} summary (n={summary.get('turns', {}).get('n', '?')}) ---")
        for name, stats in summary.items():
            if stats.get("n", 0) == 0:
                continue
            print(f"  {name:30s}  mean={stats['mean']:>12.2f}  "
                  f"median={stats['median']:>12.2f}  stdev={stats['stdev']:>10.2f}  "
                  f"[{stats['min']}, {stats['max']}]")

    # Comparison
    if "comparison" in report:
        comp = report["comparison"]
        print(f"\n--- COMPARISON (CodePlane vs Baseline) ---")
        for name, c in comp.items():
            if c["baseline_mean"] == 0 and c["codeplane_mean"] == 0:
                continue
            pct_str = f"{c['delta_pct']:+.1f}%" if c["delta_pct"] is not None else "N/A"
            indicator = _direction_indicator(name, c.get("delta_pct"))
            print(f"  {name:30s}  {c['baseline_mean']:>12.2f} → {c['codeplane_mean']:>12.2f}  "
                  f"Δ={c['delta']:>+12.2f}  ({pct_str}){indicator}")

    # Per-issue breakdown
    if "per_issue" in report:
        issues = report["per_issue"]
        print(f"\n--- PER-ISSUE BREAKDOWN ---")
        for row in issues:
            print(f"\n  Issue #{row['issue']}:")
            for key, vals in row.items():
                if key == "issue" or not isinstance(vals, dict):
                    continue
                b = vals.get("baseline")
                c = vals.get("codeplane")
                b_str = f"{b}" if b is not None else "—"
                c_str = f"{c}" if c is not None else "—"
                delta_str = ""
                if vals.get("delta_pct") is not None:
                    delta_str = f"  ({vals['delta_pct']:+.1f}%)"
                print(f"    {key:25s}  B={b_str:>12s}  C={c_str:>12s}{delta_str}")

    print(f"\n{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate benchmark metrics across multiple runs.",
    )
    parser.add_argument(
        "results_dirs",
        nargs="+",
        type=Path,
        help="One or more directories containing *_result_metrics.json files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for aggregate report (default: first results dir).",
    )
    args = parser.parse_args(argv)

    dirs: list[Path] = args.results_dirs
    for d in dirs:
        if not d.is_dir():
            print(f"ERROR: Not a directory: {d}", file=sys.stderr)
            return 1

    report = aggregate(dirs)

    if "error" in report:
        print(f"ERROR: {report['error']}", file=sys.stderr)
        return 1

    # Save report
    output_dir: Path = args.output_dir or dirs[0].parent
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "aggregate_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report saved: {report_path}")

    # Print human-readable
    print_report(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
