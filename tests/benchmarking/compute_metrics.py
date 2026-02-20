"""Compute benchmark metrics from an extracted trace.

Usage:
    python -m tests.benchmarking.compute_metrics <trace.json> [--output-dir DIR]

Reads a *_trace.json produced by extract_trace and computes per-session
metrics.  Output is saved alongside the trace as
  {repo}_{issue}_{model}_{codeplane|native}_result_metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    """Derive aggregate metrics from a trace's event list."""
    events = trace.get("events", [])

    # Partition events
    tool_events = [e for e in events if e.get("type") == "tool_call"]
    llm_events = [e for e in events if e.get("type") == "llm_request"]

    # -- Tool call metrics ---------------------------------------------------
    tool_names = [e.get("tool", "") for e in tool_events]
    tool_counts = Counter(tool_names)

    codeplane_calls = [e for e in tool_events if "codeplane" in (e.get("tool") or "").lower()]
    tool_search_calls = [e for e in tool_events if "tool_search" in (e.get("tool") or "").lower()]
    terminal_calls = [e for e in tool_events if "run_in_terminal" in (e.get("tool") or "")]
    other_tool_calls = [
        e
        for e in tool_events
        if e not in codeplane_calls and e not in tool_search_calls and e not in terminal_calls
    ]

    # Error tool calls: response contains "ERROR" or "error"
    error_tool_calls = []
    for e in tool_events:
        resp = e.get("response")
        resp_str = json.dumps(resp) if not isinstance(resp, str) else resp
        if "ERROR" in resp_str or '"error"' in resp_str.lower():
            error_tool_calls.append(e)

    # -- LLM request metrics -------------------------------------------------
    # Filter out routing models for primary metrics
    routing_models = {"gpt-4o-mini", "gpt-3.5-turbo"}
    agent_llm_events = [e for e in llm_events if e.get("model") not in routing_models]
    routing_llm_events = [e for e in llm_events if e.get("model") in routing_models]

    total_prompt_tokens = sum(e.get("prompt_tokens", 0) or 0 for e in agent_llm_events)
    total_completion_tokens = sum(e.get("completion_tokens", 0) or 0 for e in agent_llm_events)
    total_cached_tokens = sum(e.get("cached_tokens", 0) or 0 for e in agent_llm_events)
    total_llm_duration_ms = sum(e.get("duration_ms", 0) or 0 for e in agent_llm_events)

    # -- Timing --------------------------------------------------------------
    timestamps = []
    for e in events:
        t = e.get("time") or e.get("start_time")
        if t:
            timestamps.append(t)
    timestamps.sort()
    wall_clock_start = timestamps[0] if timestamps else None
    wall_clock_end = timestamps[-1] if timestamps else None

    # -- Turn count ----------------------------------------------------------
    # A "turn" = one LLM request that (likely) generates tool calls.
    # Approximate: count agent LLM requests.
    turn_count = len(agent_llm_events)

    # -- Assemble metrics dict -----------------------------------------------
    return {
        "session_name": trace.get("session_name"),
        "repo": trace.get("repo"),
        "issue": trace.get("issue"),
        "model": trace.get("model"),
        "codeplane": trace.get("codeplane"),
        "wall_clock": {
            "start": wall_clock_start,
            "end": wall_clock_end,
        },
        "turns": turn_count,
        "tool_calls": {
            "total": len(tool_events),
            "codeplane": len(codeplane_calls),
            "terminal": len(terminal_calls),
            "tool_search": len(tool_search_calls),
            "other": len(other_tool_calls),
            "errors": len(error_tool_calls),
            "by_tool": dict(tool_counts.most_common()),
        },
        "llm_requests": {
            "agent": len(agent_llm_events),
            "routing": len(routing_llm_events),
            "total": len(llm_events),
        },
        "tokens": {
            "prompt": total_prompt_tokens,
            "completion": total_completion_tokens,
            "cached": total_cached_tokens,
            "total": total_prompt_tokens + total_completion_tokens,
        },
        "llm_duration_ms": total_llm_duration_ms,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute benchmark metrics from an extracted trace.",
    )
    parser.add_argument(
        "trace",
        type=Path,
        help="Path to a *_trace.json file produced by extract_trace.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output file (default: same directory as trace).",
    )
    args = parser.parse_args(argv)

    trace_path: Path = args.trace
    if not trace_path.exists():
        print(f"ERROR: File not found: {trace_path}", file=sys.stderr)
        return 1

    with open(trace_path) as f:
        trace: dict[str, Any] = json.load(f)

    session_name = trace.get("session_name", trace_path.stem.replace("_trace", ""))
    metrics = compute_metrics(trace)

    output_dir: Path = args.output_dir or trace_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = output_dir / f"{session_name}_result_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"Metrics: {metrics_path}")
    print(f"  Turns: {metrics['turns']}")
    print(
        f"  Tool calls: {metrics['tool_calls']['total']} "
        f"(codeplane={metrics['tool_calls']['codeplane']}, "
        f"terminal={metrics['tool_calls']['terminal']}, "
        f"errors={metrics['tool_calls']['errors']})"
    )
    print(
        f"  Tokens: {metrics['tokens']['total']} "
        f"(prompt={metrics['tokens']['prompt']}, "
        f"completion={metrics['tokens']['completion']}, "
        f"cached={metrics['tokens']['cached']})"
    )
    print(f"  LLM duration: {metrics['llm_duration_ms']}ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
