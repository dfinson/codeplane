#!/usr/bin/env python3
"""
trace_from_capture.py — Transform mitmproxy capture JSON into benchmark trace.

Reads a capture file produced by ``copilot_logger.py`` and outputs a benchmark
trace JSON in the same schema as ``extract_vscode_agent_trace.py`` (schema
version 0.8.0).  All tiered metrics (T1–T4) are computed from the richer
captured data: exact token counts, full tool arguments, per-turn latency,
finish reasons, and HTTP status codes.

Usage:
    python3 trace_from_capture.py \
        --capture ~/.copilot-logs/<session>.json \
        --repo-dir /path/to/repo \
        --out results/226_mlflow_with_codeplane.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import tool classification from the existing extractor
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent))
from extract_vscode_agent_trace import (  # noqa: E402
    DEFAULT_CODEPLANE_PREFIX,
    _classify_tool_kind,
    _derive_tool_namespace,
    _infer_call_subkind,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.8.0"
PARSER_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _get_git_head(repo_dir: str | None) -> str | None:
    """Return the current HEAD SHA of the repo, or None."""
    if not repo_dir:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ---------------------------------------------------------------------------
# Tool call classification
# ---------------------------------------------------------------------------


def _classify_tool_call(
    tc: dict[str, Any],
    codeplane_prefix: str,
) -> dict[str, Any]:
    """Classify a raw tool call from capture into the trace format."""
    name = tc.get("name", "")
    tool_kind = _classify_tool_kind(name)
    tool_namespace = _derive_tool_namespace(name, tool_kind, codeplane_prefix)
    call_subkind = _infer_call_subkind(name, tool_kind)
    arguments = tc.get("arguments", {})
    arguments_bytes = len(json.dumps(arguments).encode("utf-8"))

    return {
        "id": tc.get("id", ""),
        "name": name,
        "tool_kind": tool_kind,
        "tool_namespace": tool_namespace,
        "call_subkind": call_subkind,
        "arguments": arguments,
        "arguments_bytes": arguments_bytes,
    }


# ---------------------------------------------------------------------------
# Pseudo-turn segmentation
# ---------------------------------------------------------------------------


def _segment_pseudo_turns(
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Segment API turns into pseudo-turns using finish_reason.

    A pseudo-turn boundary occurs when ``finish_reason`` is ``stop``
    (not ``tool_calls``), meaning the agent completed a reasoning step
    and is waiting for user input.
    """
    if not turns:
        return []

    pseudo_turns: list[dict[str, Any]] = []
    current_turns: list[dict[str, Any]] = []
    pt_index = 0

    def _flush(buffer: list[dict[str, Any]]) -> dict[str, Any]:
        all_tool_calls: list[str] = []
        for t in buffer:
            for tc in t.get("classified_tool_calls", []):
                all_tool_calls.append(tc["name"])
        first = buffer[0]
        last = buffer[-1]
        return {
            "pseudo_turn_index": -1,
            "first_api_turn": first["turn_number"],
            "last_api_turn": last["turn_number"],
            "api_turn_count": len(buffer),
            "tool_invocation_count": len(all_tool_calls),
            "tool_names": all_tool_calls,
            "total_prompt_tokens": sum(t.get("usage", {}).get("prompt_tokens", 0) for t in buffer),
            "total_completion_tokens": sum(
                t.get("usage", {}).get("completion_tokens", 0) for t in buffer
            ),
            "total_duration_ms": sum(t.get("duration_ms", 0) for t in buffer),
            "finish_reason": last.get("finish_reason"),
        }

    for turn in turns:
        current_turns.append(turn)
        finish = turn.get("finish_reason")
        # Boundary: stop (not tool_calls) marks end of a pseudo-turn
        if finish and finish != "tool_calls":
            pt = _flush(current_turns)
            pt["pseudo_turn_index"] = pt_index
            pseudo_turns.append(pt)
            pt_index += 1
            current_turns = []

    # Flush remainder (if session ended mid-tool-loop)
    if current_turns:
        pt = _flush(current_turns)
        pt["pseudo_turn_index"] = pt_index
        pseudo_turns.append(pt)

    return pseudo_turns


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _compute_metrics(
    classified_turns: list[dict[str, Any]],
    pseudo_turns: list[dict[str, Any]],
    all_tool_calls: list[dict[str, Any]],
    session_duration_s: float | None,
) -> dict[str, Any]:
    """Compute T1–T4 metrics from captured data."""

    # ---------------------------------------------------------------
    # Tier 1 — Core proof metrics
    # ---------------------------------------------------------------

    total_tool_calls = len(all_tool_calls)
    native_calls = sum(1 for tc in all_tool_calls if tc["tool_kind"] == "native")
    mcp_calls = sum(1 for tc in all_tool_calls if tc["tool_kind"] == "mcp")
    builtin_calls = sum(1 for tc in all_tool_calls if tc["tool_kind"] == "builtin")
    other_calls = total_tool_calls - native_calls - mcp_calls - builtin_calls
    native_mcp_ratio = round(native_calls / mcp_calls, 2) if mcp_calls > 0 else None

    tool_calls_per_second = None
    mcp_calls_per_second = None
    if session_duration_s and session_duration_s > 0:
        tool_calls_per_second = round(
            total_tool_calls / session_duration_s,
            4,
        )
        mcp_calls_per_second = round(mcp_calls / session_duration_s, 4) if mcp_calls else 0.0

    # Thrash / burst analysis using per-turn timestamps
    turn_timestamps_ms: list[float] = []
    for turn in classified_turns:
        ts_iso = turn.get("timestamp_iso")
        if ts_iso:
            try:
                dt = datetime.fromisoformat(ts_iso)
                turn_timestamps_ms.append(dt.timestamp() * 1000)
            except (ValueError, TypeError):
                pass

    max_burst_1s = 0
    longest_streak = 0
    cps_samples: list[float] = []
    if len(turn_timestamps_ms) >= 2:
        sorted_ts = sorted(turn_timestamps_ms)
        for i, ts in enumerate(sorted_ts):
            count = sum(1 for t in sorted_ts[i:] if t <= ts + 1000)
            max_burst_1s = max(max_burst_1s, count)

        bucket_start = sorted_ts[0]
        bucket_t = bucket_start
        while bucket_t <= sorted_ts[-1]:
            count = sum(1 for ts in sorted_ts if bucket_t <= ts < bucket_t + 1000)
            cps_samples.append(count)
            bucket_t += 1000

        current = 1
        for i in range(1, len(sorted_ts)):
            if sorted_ts[i] - sorted_ts[i - 1] < 2000:
                current += 1
            else:
                longest_streak = max(longest_streak, current)
                current = 1
        longest_streak = max(longest_streak, current)

    native_terminal_calls = sum(
        1
        for tc in all_tool_calls
        if tc["tool_kind"] == "native" and tc["call_subkind"] == "terminal"
    )

    tier1: dict[str, Any] = {
        "total_tool_calls": total_tool_calls,
        "by_kind": {
            "native": native_calls,
            "mcp": mcp_calls,
            "builtin": builtin_calls,
            "other": other_calls,
        },
        "native_mcp_ratio": native_mcp_ratio,
        "session_duration_s": session_duration_s,
        "tool_calls_per_second": tool_calls_per_second,
        "mcp_calls_per_second": mcp_calls_per_second,
        "thrash_shape": {
            "max_burst_1s": max_burst_1s,
            "longest_uninterrupted_streak": longest_streak,
            "calls_per_second_mean": (
                round(statistics.mean(cps_samples), 2) if cps_samples else None
            ),
            "calls_per_second_max": (max(cps_samples) if cps_samples else None),
            "calls_per_second_stddev": (
                round(statistics.stdev(cps_samples), 2) if len(cps_samples) > 1 else None
            ),
        },
        "native_terminal_calls": native_terminal_calls,
    }

    # ---------------------------------------------------------------
    # Tier 2 — Convergence efficiency
    # ---------------------------------------------------------------

    pseudo_turn_count = len(pseudo_turns)
    tool_calls_per_pt = (
        round(total_tool_calls / pseudo_turn_count, 2) if pseudo_turn_count > 0 else None
    )

    calls_before_first_mcp: int | None = None
    for i, tc in enumerate(all_tool_calls):
        if tc["tool_kind"] == "mcp":
            calls_before_first_mcp = i
            break
    if calls_before_first_mcp is None and mcp_calls == 0:
        calls_before_first_mcp = total_tool_calls

    longest_native_streak = 0
    current_native = 0
    for tc in all_tool_calls:
        if tc["tool_kind"] == "native":
            current_native += 1
        else:
            longest_native_streak = max(longest_native_streak, current_native)
            current_native = 0
    longest_native_streak = max(longest_native_streak, current_native)

    tier2: dict[str, Any] = {
        "total_pseudo_turns": pseudo_turn_count,
        "tool_calls_per_pseudo_turn": tool_calls_per_pt,
        "calls_before_first_mcp": calls_before_first_mcp,
        "longest_native_only_streak": longest_native_streak,
    }

    # ---------------------------------------------------------------
    # Tier 3 — Cost & payload proxies
    # ---------------------------------------------------------------

    # Tool result bytes from tool-role messages in the context
    total_result_bytes = 0
    result_by_tool: dict[str, list[int]] = {}
    for turn in classified_turns:
        for tr in turn.get("tool_results_in_context", []):
            rb = tr.get("content_bytes", 0)
            total_result_bytes += rb
            # Try to match tool_call_id to a tool name
            tc_id = tr.get("tool_call_id", "")
            matched_name = "unknown"
            for tc in turn.get("classified_tool_calls", []):
                if tc.get("id") == tc_id:
                    matched_name = tc["name"]
                    break
            result_by_tool.setdefault(matched_name, []).append(rb)

    avg_result_by_tool = {
        name: {
            "count": len(sizes),
            "total_bytes": sum(sizes),
            "avg_bytes": round(sum(sizes) / len(sizes)),
        }
        for name, sizes in sorted(result_by_tool.items())
    }

    # Token totals (exact from API)
    total_prompt_tokens = sum(t.get("usage", {}).get("prompt_tokens", 0) for t in classified_turns)
    total_completion_tokens = sum(
        t.get("usage", {}).get("completion_tokens", 0) for t in classified_turns
    )

    # Latency
    durations = [t["duration_ms"] for t in classified_turns if t.get("duration_ms")]
    total_api_latency_ms = sum(durations)
    mean_latency = round(statistics.mean(durations)) if durations else None

    # Cost estimate (approximate; uses public pricing)
    # Claude Opus 4: ~$15/Mtok prompt, ~$75/Mtok completion
    # GPT-4o: ~$5/Mtok prompt, ~$15/Mtok completion
    # We use a rough average; the consumer can refine per-model
    cost_estimate = round(
        (total_prompt_tokens * 15 / 1_000_000) + (total_completion_tokens * 75 / 1_000_000),
        4,
    )

    tier3: dict[str, Any] = {
        "total_result_bytes": total_result_bytes,
        "avg_result_bytes_per_call": (
            round(total_result_bytes / total_tool_calls) if total_tool_calls else 0
        ),
        "avg_result_by_tool": avg_result_by_tool,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "tokens_source": "api_usage",
        "total_api_latency_ms": total_api_latency_ms,
        "mean_latency_per_turn_ms": mean_latency,
        "cost_estimate_usd": cost_estimate,
    }

    # ---------------------------------------------------------------
    # Tier 4 — Stability & reliability
    # ---------------------------------------------------------------

    # Error tool calls (we can detect from HTTP status on responses)
    error_calls = sum(
        1 for t in classified_turns if t.get("http_status") and t["http_status"] >= 400
    )
    error_rate = round(error_calls / len(classified_turns), 4) if classified_turns else 0.0

    rate_limited = sum(1 for t in classified_turns if t.get("http_status") == 429)

    finish_reasons: dict[str, int] = {}
    for t in classified_turns:
        fr = t.get("finish_reason", "unknown") or "unknown"
        finish_reasons[fr] = finish_reasons.get(fr, 0) + 1

    tier4: dict[str, Any] = {
        "error_calls": error_calls,
        "error_rate": error_rate,
        "rate_limited_count": rate_limited,
        "finish_reason_distribution": finish_reasons,
    }

    return {
        "tier1_core": tier1,
        "tier2_convergence": tier2,
        "tier3_cost_proxies": tier3,
        "tier4_stability": tier4,
    }


# ---------------------------------------------------------------------------
# Trace builder
# ---------------------------------------------------------------------------


def build_trace(
    capture: dict[str, Any],
    repo_dir: str | None,
    codeplane_prefix: str,
    label: str | None,
) -> dict[str, Any]:
    """Build benchmark trace JSON from a capture file."""

    session_id = capture.get("session_id", "unknown")
    models_used = capture.get("models_used", [])
    started_at = capture.get("started_at")
    ended_at = capture.get("ended_at")
    raw_turns: list[dict[str, Any]] = capture.get("turns", [])

    # Session duration
    session_duration_s: float | None = None
    if started_at and ended_at:
        try:
            start_dt = datetime.fromisoformat(started_at)
            end_dt = datetime.fromisoformat(ended_at)
            session_duration_s = round(
                (end_dt - start_dt).total_seconds(),
                1,
            )
        except (ValueError, TypeError):
            pass

    # Classify tool calls in each turn
    classified_turns: list[dict[str, Any]] = []
    all_tool_calls: list[dict[str, Any]] = []

    for turn in raw_turns:
        ctcs = [_classify_tool_call(tc, codeplane_prefix) for tc in turn.get("tool_calls", [])]
        all_tool_calls.extend(ctcs)
        classified_turn = {**turn, "classified_tool_calls": ctcs}
        classified_turns.append(classified_turn)

    # Pseudo-turns
    pseudo_turns = _segment_pseudo_turns(classified_turns)

    # Metrics
    metrics = _compute_metrics(
        classified_turns,
        pseudo_turns,
        all_tool_calls,
        session_duration_s,
    )

    # Summaries
    tool_calls_by_name: dict[str, int] = {}
    tool_calls_by_kind: dict[str, int] = {}
    tool_calls_by_namespace: dict[str, int] = {}
    for tc in all_tool_calls:
        tool_calls_by_name[tc["name"]] = tool_calls_by_name.get(tc["name"], 0) + 1
        tool_calls_by_kind[tc["tool_kind"]] = tool_calls_by_kind.get(tc["tool_kind"], 0) + 1
        tool_calls_by_namespace[tc["tool_namespace"]] = (
            tool_calls_by_namespace.get(tc["tool_namespace"], 0) + 1
        )

    codeplane_calls = tool_calls_by_namespace.get("codeplane", 0)
    codeplane_share = round(codeplane_calls / len(all_tool_calls), 4) if all_tool_calls else 0.0

    total_prompt_tokens = capture.get("totals", {}).get("prompt_tokens", 0)
    total_completion_tokens = capture.get("totals", {}).get(
        "completion_tokens",
        0,
    )
    total_tokens = capture.get("totals", {}).get("tokens", 0)

    # Tool result bytes
    tool_result_bytes_total = 0
    for turn in classified_turns:
        for tr in turn.get("tool_results_in_context", []):
            tool_result_bytes_total += tr.get("content_bytes", 0)

    # Build turn records for output
    output_turns: list[dict[str, Any]] = []
    for turn in classified_turns:
        ctcs = turn.get("classified_tool_calls", [])
        # User message preview
        user_preview = ""
        for msg in reversed(turn.get("messages", [])):
            if msg.get("role") == "user":
                c = msg.get("content", "")
                if isinstance(c, str):
                    user_preview = c[:500]
                break

        # Assistant text preview
        assistant_text = turn.get("assistant_text", "")
        assistant_preview = assistant_text[:500] if assistant_text else ""

        output_turns.append(
            {
                "turn_number": turn.get("turn_number"),
                "timestamp_iso": turn.get("timestamp_iso"),
                "role_summary": _build_role_summary(turn),
                "prompt_tokens": turn.get("usage", {}).get("prompt_tokens"),
                "completion_tokens": turn.get("usage", {}).get(
                    "completion_tokens",
                ),
                "duration_ms": turn.get("duration_ms"),
                "http_status": turn.get("http_status"),
                "model": turn.get("model"),
                "request_model": turn.get("request_model"),
                "finish_reason": turn.get("finish_reason"),
                "sse_chunk_count": turn.get("sse_chunk_count"),
                "temperature": turn.get("temperature"),
                "top_p": turn.get("top_p"),
                "max_tokens": turn.get("max_tokens"),
                "tool_calls": ctcs,
                "tool_results_in_context": turn.get(
                    "tool_results_in_context",
                    [],
                ),
                "message_stats": turn.get("message_stats"),
                "incremental": turn.get("incremental"),
                "chat_segment_index": turn.get("chat_segment_index"),
                "user_message_preview": user_preview,
                "assistant_text_preview": assistant_preview,
            }
        )

    # Build events list (flat, for compatibility with legacy format)
    events: list[dict[str, Any]] = []
    for turn in classified_turns:
        for tc in turn.get("classified_tool_calls", []):
            events.append(
                {
                    "event_type": "tool_invocation",
                    "timestamp_iso": turn.get("timestamp_iso"),
                    "tool_name": tc["name"],
                    "tool_kind": tc["tool_kind"],
                    "tool_namespace": tc["tool_namespace"],
                    "call_subkind": tc["call_subkind"],
                    "arguments_bytes": tc["arguments_bytes"],
                    "api_turn_number": turn.get("turn_number"),
                }
            )

    trace: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_metadata": {
            "extraction_timestamp": datetime.now(tz=UTC).isoformat(),
            "parser_version": PARSER_VERSION,
            "capture_source": "mitmproxy",
            "session_id": session_id,
            "models_used": models_used,
            "session_start_iso": started_at,
            "session_end_iso": ended_at,
            "session_duration_s": session_duration_s,
            "git_head_sha": _get_git_head(repo_dir),
            "label": label,
        },
        "summaries": {
            "total_turns": len(raw_turns),
            "total_pseudo_turns": len(pseudo_turns),
            "total_tool_calls": len(all_tool_calls),
            "tool_calls_by_name": dict(
                sorted(tool_calls_by_name.items(), key=lambda x: -x[1]),
            ),
            "tool_calls_by_kind": tool_calls_by_kind,
            "tool_calls_by_namespace": tool_calls_by_namespace,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "tokens_source": "api_usage",
            "codeplane_share_of_all_tool_calls": codeplane_share,
            "tool_result_bytes_total": tool_result_bytes_total,
        },
        "turns": output_turns,
        "tool_invocations": events,
        "pseudo_turns": pseudo_turns,
        "chat_segments": capture.get("chat_segments", []),
        "mcp_comparison_metrics": metrics,
    }

    return trace


def _build_role_summary(turn: dict[str, Any]) -> str:
    """Build a compact role summary string for a turn."""
    parts: list[str] = []
    stats = turn.get("message_stats", {})
    roles = stats.get("role_counts", {})
    for role in ("system", "user", "assistant", "tool"):
        count = roles.get(role, 0)
        if count > 0:
            parts.append(role)
    suffix = ""
    if turn.get("tool_calls"):
        suffix = "(tool_calls)"
    elif turn.get("finish_reason") == "stop":
        suffix = "(stop)"
    elif turn.get("finish_reason") == "length":
        suffix = "(length)"
    return ">".join(parts) + suffix


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform mitmproxy capture to benchmark trace",
    )
    parser.add_argument(
        "--capture",
        required=True,
        help="Capture JSON file from copilot_logger.py",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="Repository directory (for git HEAD)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output trace JSON path",
    )
    parser.add_argument(
        "--codeplane-prefix",
        default=DEFAULT_CODEPLANE_PREFIX,
        help=f"CodePlane MCP tool prefix (default: {DEFAULT_CODEPLANE_PREFIX})",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Human label for the run (e.g., 'with_codeplane')",
    )
    args = parser.parse_args()

    # Read capture
    capture_path = Path(args.capture)
    if not capture_path.exists():
        print(f"ERROR: Capture file not found: {capture_path}", file=sys.stderr)
        sys.exit(1)

    with open(capture_path) as f:
        capture = json.load(f)

    # Build trace
    trace = build_trace(
        capture,
        repo_dir=args.repo_dir,
        codeplane_prefix=args.codeplane_prefix,
        label=args.label,
    )

    # Write output
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2, default=str)

    # Summary
    s = trace["summaries"]
    m = trace["mcp_comparison_metrics"]
    print(
        f"Trace written to {out_path}\n"
        f"  Turns:        {s['total_turns']}\n"
        f"  Pseudo-turns: {s['total_pseudo_turns']}\n"
        f"  Tool calls:   {s['total_tool_calls']}\n"
        f"  Tokens:       {s['total_tokens']:,} "
        f"({s['total_prompt_tokens']:,}p + {s['total_completion_tokens']:,}c)\n"
        f"  CP share:     {s['codeplane_share_of_all_tool_calls']:.1%}\n"
        f"  Cost est:     ${m['tier3_cost_proxies']['cost_estimate_usd']:.4f}\n"
        f"  Duration:     {trace['run_metadata']['session_duration_s']}s",
    )


if __name__ == "__main__":
    main()
