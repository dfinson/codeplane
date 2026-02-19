#!/usr/bin/env python3
"""
extract_chatreplay_trace.py — Extract structured traces from VS Code Copilot Chat
chatreplay JSON exports.

This parser handles the VS Code "Export All Prompts" JSON format, which provides
complete structured data including:
  - Full tool arguments (not just results)
  - Exact token usage per LLM request (prompt_tokens, completion_tokens)
  - Exact model per request
  - Timing (duration, timeToFirstToken) per request
  - Full user prompt text
  - MCP server configuration

USAGE:
  # Extract the most recent/largest prompt by default:
  python extract_chatreplay_trace.py export.chatreplay.json

  # Target a specific prompt by text substring:
  python extract_chatreplay_trace.py export.chatreplay.json --prompt-match "MLflow"

  # Target by prompt index (0-based):
  python extract_chatreplay_trace.py export.chatreplay.json --prompt-index 7

  # Custom output path:
  python extract_chatreplay_trace.py export.chatreplay.json -o my_trace.json

OUTPUT:
  Produces a trace JSON compatible with the benchmark-design.md analysis format.
  The trace includes tiered MCP comparison metrics identical to the legacy
  extract_vscode_agent_trace.py output.
"""

import argparse
import json
import logging
import re
import statistics as stats
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
PARSER_VERSION = "1.0.0"
DEFAULT_CODEPLANE_PREFIX = "codeplane_"
PSEUDO_TURN_GAP_MS = 5000

log = logging.getLogger("chatreplay_extractor")
log.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
log.addHandler(_handler)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_underscore_case(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _parse_iso(ts: str | None) -> float | None:
    """Parse ISO timestamp to epoch ms. Returns None on failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Tool classification (shared with legacy extractor)
# ---------------------------------------------------------------------------
_SUBKIND_BY_TOOL_NAME: dict[str, str] = {
    "run_in_terminal": "terminal",
    "get_terminal_output": "terminal",
    "read_source": "read_file",
    "read_file_full": "read_file",
    "read_scaffold": "read_file",
    "read_file": "read_file",
    "search": "search",
    "list_files": "read_file",
    "map_repo": "read_file",
    "describe": "read_file",
    "write_source": "edit",
    "write_files": "edit",
    "semantic_diff": "git",
    "lint_check": "lint",
    "lint_tools": "lint",
    "run_test_targets": "tests",
    "discover_test_targets": "tests",
    "inspect_affected_tests": "tests",
    "get_test_run_status": "tests",
    "git_status": "git",
    "git_log": "git",
    "git_diff": "git",
    "git_commit": "git",
    "git_stage": "git",
    "git_stage_and_commit": "git",
    "git_push": "git",
    "git_pull": "git",
    "git_branch": "git",
    "git_checkout": "git",
    "git_stash": "git",
    "git_reset": "git",
    "git_merge": "git",
    "git_rebase": "git",
    "git_remote": "git",
    "git_inspect": "git",
    "git_history": "git",
    "git_submodule": "git",
    "git_worktree": "git",
    "refactor_rename": "edit",
    "refactor_move": "edit",
    "refactor_delete": "edit",
    "refactor_apply": "edit",
    "refactor_cancel": "edit",
    "refactor_inspect": "read_file",
    "refactor_impact": "read_file",
    "github_api": "git",
    "manage_todo_list": "planning",
    "ask_questions": "planning",
    "tool_search_tool_regex": "planning",
}


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix: 'mcp_codeplane-eve_git_checkout' -> 'git_checkout'."""
    if tool_name.startswith("mcp_"):
        # Pattern: mcp_<server-label>_<actual_tool>
        # Server label may contain dashes: mcp_codeplane-eve_git_checkout
        parts = tool_name.split("_", 2)  # ['mcp', 'codeplane-eve', 'git_checkout']
        if len(parts) >= 3:
            return parts[2]
    return tool_name


def _infer_call_subkind(tool_name: str, tool_kind: str) -> str:
    short = _strip_mcp_prefix(tool_name)
    if short in _SUBKIND_BY_TOOL_NAME:
        return _SUBKIND_BY_TOOL_NAME[short]
    if tool_name in _SUBKIND_BY_TOOL_NAME:
        return _SUBKIND_BY_TOOL_NAME[tool_name]
    for prefix, kind in [
        ("git_", "git"),
        ("refactor_", "edit"),
        ("mcp_codeplane", "mcp_codeplane"),
        ("mcp_github", "git"),
    ]:
        if tool_name.startswith(prefix) or short.startswith(prefix):
            return kind
    if tool_kind == "native":
        return "terminal"
    return "unknown"


def _classify_tool_kind(tool_name: str) -> str:
    """Classify tool into kind: mcp, native, builtin."""
    if tool_name.startswith("mcp_"):
        return "mcp"
    if tool_name in (
        "run_in_terminal",
        "get_terminal_output",
        "manage_todo_list",
        "ask_questions",
        "tool_search_tool_regex",
    ):
        return "native"
    # Tool names with " [server]" suffix are VS Code internal
    if "[server]" in tool_name:
        return "native"
    return "builtin"


def _derive_tool_namespace(
    tool_name: str,
    tool_kind: str,
    codeplane_prefix: str,
) -> str:
    if tool_kind == "mcp":
        if "codeplane" in tool_name or tool_name.startswith(codeplane_prefix):
            return "codeplane"
        return "other_mcp"
    if tool_kind == "native":
        return "native"
    if tool_kind == "builtin":
        return "builtin"
    return "unknown"


# ---------------------------------------------------------------------------
# Extraction from chatreplay format
# ---------------------------------------------------------------------------
def load_chatreplay(path: Path) -> dict[str, Any]:
    """Load and validate a chatreplay JSON export."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict) or "prompts" not in data:
        raise ValueError("Not a valid chatreplay JSON: missing 'prompts' key")
    return data


def select_prompt(
    data: dict[str, Any],
    prompt_index: int | None = None,
    prompt_match: str | None = None,
) -> tuple[dict[str, Any], int, str]:
    """Select a prompt from the chatreplay data.

    Returns (prompt_dict, index, selection_reason).
    """
    prompts = data["prompts"]
    if not prompts:
        raise ValueError("No prompts in chatreplay export.")

    if prompt_index is not None:
        if prompt_index < 0 or prompt_index >= len(prompts):
            raise ValueError(f"Prompt index {prompt_index} out of range (0-{len(prompts) - 1})")
        return prompts[prompt_index], prompt_index, f"explicit_index:{prompt_index}"

    if prompt_match:
        needle = prompt_match.lower()
        matches = [
            (i, p) for i, p in enumerate(prompts) if needle in (p.get("prompt") or "").lower()
        ]
        if not matches:
            raise ValueError(
                f"No prompts matching '{prompt_match}'. "
                f"Available prompts ({len(prompts)}):\n"
                + "\n".join(
                    f"  [{i}] {(p.get('prompt') or '')[:80]!r}  (logs={p.get('logCount', 0)})"
                    for i, p in enumerate(prompts)
                )
            )
        # Pick the match with the most logs (most complete session)
        matches.sort(key=lambda x: x[1].get("logCount", 0), reverse=True)
        idx, prompt = matches[0]
        reason = f"prompt_match:{prompt_match}"
        if len(matches) > 1:
            reason += f" (1_of_{len(matches)}_matches,picked_most_logs)"
        return prompt, idx, reason

    # Default: pick the prompt with the most logs
    best_idx = max(range(len(prompts)), key=lambda i: prompts[i].get("logCount", 0))
    return prompts[best_idx], best_idx, f"auto_most_logs:index_{best_idx}"


def extract_tool_calls_from_prompt(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool call descriptors from a chatreplay prompt's logs."""
    calls: list[dict[str, Any]] = []
    logs = prompt.get("logs", [])

    for lg in logs:
        if lg.get("kind") != "toolCall":
            continue

        tool_name = lg.get("tool", "unknown")
        tool_call_id = lg.get("id", "")
        time_iso = lg.get("time")
        epoch_ms = _parse_iso(time_iso)

        # Parse args
        args_raw = lg.get("args")
        args_bytes: int | None = None
        args_parsed: Any = None
        if isinstance(args_raw, str):
            args_bytes = len(args_raw.encode("utf-8"))
            try:
                args_parsed = json.loads(args_raw)
            except (json.JSONDecodeError, ValueError):
                args_parsed = args_raw
        elif isinstance(args_raw, dict):
            args_bytes = len(json.dumps(args_raw).encode("utf-8"))
            args_parsed = args_raw

        # Parse response
        response_raw = lg.get("response", [])
        result_text = ""
        if isinstance(response_raw, list):
            result_text = "\n".join(
                r if isinstance(r, str) else json.dumps(r) for r in response_raw
            )
        elif isinstance(response_raw, str):
            result_text = response_raw
        result_bytes = len(result_text.encode("utf-8"))

        # Classify
        tool_kind = _classify_tool_kind(tool_name)
        call_subkind = _infer_call_subkind(tool_name, tool_kind)

        # Detect errors in response
        status = "success"
        if isinstance(response_raw, list) and response_raw:
            first = response_raw[0]
            if isinstance(first, str):
                try:
                    parsed = json.loads(first)
                    if isinstance(parsed, dict) and parsed.get("isError"):
                        status = "error"
                except (json.JSONDecodeError, ValueError):
                    pass

        # Warnings from toolMetadata
        warnings = (lg.get("toolMetadata") or {}).get("warnings", [])

        calls.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "raw_tool_name": tool_name,
                "raw_record_type": "chatreplay_export",
                "tool_kind": tool_kind,
                "call_subkind": call_subkind,
                "timestamp_epoch_ms": epoch_ms or 0,
                "timestamp_iso": time_iso,
                "result_bytes": result_bytes,
                "args_bytes": args_bytes,
                "args_shape_hint": _args_shape_hint(args_parsed),
                "status": status,
                "content_type": "chatreplay",
                "resource_kind": None,
                "source_file": None,
                "source_dir": None,
                "turn_index": None,
                "warnings": warnings if warnings else None,
            }
        )

    calls.sort(key=lambda c: c["timestamp_epoch_ms"])
    return calls


def _args_shape_hint(args: Any) -> dict[str, Any] | None:
    """Derive a shape hint from parsed args."""
    if not isinstance(args, dict):
        return None
    hint: dict[str, Any] = {}
    # File paths
    for key in ("path", "paths", "file", "targets"):
        if key in args:
            val = args[key]
            if isinstance(val, str):
                hint["target_paths"] = [val]
            elif isinstance(val, list):
                hint["target_paths"] = [
                    v
                    if isinstance(v, str)
                    else v.get("path", str(v))
                    if isinstance(v, dict)
                    else str(v)
                    for v in val[:5]
                ]
    # Query
    if "query" in args:
        hint["query"] = args["query"]
    # Command
    if "command" in args:
        cmd = args["command"]
        if isinstance(cmd, str):
            hint["command_preview"] = cmd[:120]
    return hint if hint else None


def extract_requests_from_prompt(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract LLM request metadata from a chatreplay prompt's logs."""
    requests: list[dict[str, Any]] = []
    logs = prompt.get("logs", [])

    for lg in logs:
        if lg.get("kind") != "request":
            continue

        meta = lg.get("metadata", {})
        usage = meta.get("usage", {})

        requests.append(
            {
                "request_id": lg.get("id"),
                "name": lg.get("name"),
                "type": lg.get("type"),
                "model": meta.get("model"),
                "request_type": meta.get("requestType"),
                "start_time": meta.get("startTime"),
                "end_time": meta.get("endTime"),
                "duration_ms": meta.get("duration"),
                "time_to_first_token_ms": meta.get("timeToFirstToken"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": (
                    (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
                )
                or None,
            }
        )

    return requests


# ---------------------------------------------------------------------------
# Timeline & pseudo-turn segmentation
# ---------------------------------------------------------------------------
def build_events_timeline(
    tool_calls: list[dict[str, Any]],
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a merged + sorted timeline of tool invocations and LLM requests."""
    events: list[dict[str, Any]] = []

    for tc in tool_calls:
        events.append(
            {
                "type": "tool_invocation",
                "timestamp_epoch_ms": tc["timestamp_epoch_ms"],
                "timestamp_iso": tc["timestamp_iso"],
                "tool_name": tc["tool_name"],
                "tool_kind": tc["tool_kind"],
                "call_subkind": tc["call_subkind"],
                "result_bytes": tc["result_bytes"],
                "args_bytes": tc["args_bytes"],
                "status": tc["status"],
            }
        )

    for req in requests:
        start_ms = _parse_iso(req["start_time"])
        events.append(
            {
                "type": "llm_request",
                "timestamp_epoch_ms": start_ms or 0,
                "timestamp_iso": req["start_time"],
                "model": req["model"],
                "name": req["name"],
                "duration_ms": req["duration_ms"],
                "prompt_tokens": req["prompt_tokens"],
                "completion_tokens": req["completion_tokens"],
            }
        )

    events.sort(key=lambda e: e["timestamp_epoch_ms"])
    return events


def segment_pseudo_turns(
    events: list[dict[str, Any]],
    gap_threshold_ms: int = PSEUDO_TURN_GAP_MS,
) -> list[dict[str, Any]]:
    """Heuristic segmentation of events into pseudo-turns."""
    if not events:
        return []

    turns: list[dict[str, Any]] = []
    current_events: list[dict[str, Any]] = []

    for evt in events:
        if current_events:
            gap = evt["timestamp_epoch_ms"] - current_events[-1]["timestamp_epoch_ms"]
            if gap > gap_threshold_ms:
                turns.append(_flush_turn(current_events))
                current_events = []
        current_events.append(evt)

    if current_events:
        turns.append(_flush_turn(current_events))

    return turns


def _flush_turn(evts: list[dict[str, Any]]) -> dict[str, Any]:
    tool_evts = [e for e in evts if e["type"] == "tool_invocation"]
    llm_evts = [e for e in evts if e["type"] == "llm_request"]
    return {
        "turn_index": None,
        "event_count": len(evts),
        "tool_call_count": len(tool_evts),
        "llm_request_count": len(llm_evts),
        "start_epoch_ms": evts[0]["timestamp_epoch_ms"],
        "end_epoch_ms": evts[-1]["timestamp_epoch_ms"],
        "duration_ms": evts[-1]["timestamp_epoch_ms"] - evts[0]["timestamp_epoch_ms"],
        "tool_names": [e["tool_name"] for e in tool_evts],
        "models": list({e.get("model", "?") for e in llm_evts}),
        "total_prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in llm_evts),
        "total_completion_tokens": sum(e.get("completion_tokens") or 0 for e in llm_evts),
    }


# ---------------------------------------------------------------------------
# MCP comparison metrics (tiered)
# ---------------------------------------------------------------------------
def compute_mcp_comparison_metrics(
    tool_calls: list[dict[str, Any]],
    events: list[dict[str, Any]],  # noqa: ARG001
    pseudo_turns: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    session_start_ms: float | None,
    session_end_ms: float | None,
) -> dict[str, Any]:
    """Compute tiered metrics for MCP vs baseline comparison."""
    # --- Tier 1: Core ---
    by_kind: dict[str, int] = Counter()
    for tc in tool_calls:
        by_kind[tc["tool_kind"]] += 1

    native_count = by_kind.get("native", 0)
    mcp_count = by_kind.get("mcp", 0)
    total_tc = len(tool_calls)

    duration_s: float | None = None
    if session_start_ms and session_end_ms and session_end_ms > session_start_ms:
        duration_s = round((session_end_ms - session_start_ms) / 1000, 2)

    calls_per_second = round(total_tc / duration_s, 4) if duration_s else None
    mcp_calls_per_second = round(mcp_count / duration_s, 4) if duration_s else None

    native_mcp_ratio: float | None = None
    if mcp_count > 0:
        native_mcp_ratio = round(native_count / mcp_count, 4)

    # Thrash shape
    thrash = _compute_thrash_shape(tool_calls)

    native_terminal_calls = sum(
        1 for tc in tool_calls if tc["tool_kind"] == "native" and tc["call_subkind"] == "terminal"
    )

    # --- Tier 2: Convergence ---
    tool_calls_per_turn: float | None = None
    if pseudo_turns:
        tool_calls_per_turn = round(total_tc / len(pseudo_turns), 2)

    calls_before_first_mcp = 0
    for tc in tool_calls:
        if tc["tool_kind"] == "mcp":
            break
        calls_before_first_mcp += 1

    longest_native_only = _longest_streak(tool_calls, lambda tc: tc["tool_kind"] == "native")

    # --- Tier 3: Cost proxies (with real token data!) ---
    total_result_bytes = sum(tc["result_bytes"] or 0 for tc in tool_calls)
    total_args_bytes = sum(tc["args_bytes"] or 0 for tc in tool_calls)
    avg_result_bytes = round(total_result_bytes / total_tc, 1) if total_tc else 0

    # Real token counts from requests
    total_prompt_tokens = sum(r.get("prompt_tokens") or 0 for r in requests)
    total_completion_tokens = sum(r.get("completion_tokens") or 0 for r in requests)
    total_tokens = total_prompt_tokens + total_completion_tokens
    total_llm_duration_ms = sum(r.get("duration_ms") or 0 for r in requests)
    llm_request_count = len(requests)

    # --- Tier 4: Stability ---
    error_calls = sum(1 for tc in tool_calls if tc["status"] == "error")
    error_rate = round(error_calls / total_tc, 4) if total_tc else 0

    return {
        "tier1_core": {
            "by_kind": dict(by_kind),
            "native_mcp_ratio": native_mcp_ratio,
            "session_duration_s": duration_s,
            "tool_calls_per_second": calls_per_second,
            "mcp_calls_per_second": mcp_calls_per_second,
            "thrash_shape": thrash,
            "native_terminal_calls": native_terminal_calls,
        },
        "tier2_convergence": {
            "tool_calls_per_pseudo_turn": tool_calls_per_turn,
            "calls_before_first_mcp": calls_before_first_mcp,
            "longest_native_only_streak": longest_native_only,
        },
        "tier3_cost_proxies": {
            "total_result_bytes": total_result_bytes,
            "total_args_bytes": total_args_bytes,
            "avg_result_bytes_per_call": avg_result_bytes,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_llm_duration_ms": total_llm_duration_ms,
            "llm_request_count": llm_request_count,
            "avg_llm_duration_ms": (
                round(total_llm_duration_ms / llm_request_count, 1) if llm_request_count else None
            ),
        },
        "tier4_stability": {
            "error_calls": error_calls,
            "error_rate": error_rate,
        },
    }


def _compute_thrash_shape(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    if not tool_calls:
        return {
            "max_burst_1s": 0,
            "longest_uninterrupted_streak": 0,
            "calls_per_second_mean": 0,
            "calls_per_second_max": 0,
        }

    timestamps = [tc["timestamp_epoch_ms"] for tc in tool_calls if tc["timestamp_epoch_ms"]]
    if not timestamps:
        return {
            "max_burst_1s": 0,
            "longest_uninterrupted_streak": len(tool_calls),
            "calls_per_second_mean": 0,
            "calls_per_second_max": 0,
        }

    # Max burst in 1-second window
    max_burst = 1
    for i, t in enumerate(timestamps):
        count = sum(1 for t2 in timestamps[i:] if t2 - t <= 1000)
        if count > max_burst:
            max_burst = count

    # CPS per second bucket
    if len(timestamps) >= 2:
        min_t = min(timestamps)
        max_t = max(timestamps)
        span_s = (max_t - min_t) / 1000
        if span_s > 0:
            buckets: dict[int, int] = {}
            for t in timestamps:
                bucket = int((t - min_t) / 1000)
                buckets[bucket] = buckets.get(bucket, 0) + 1
            cps_values = list(buckets.values())
            cps_mean = round(stats.mean(cps_values), 2) if cps_values else 0
            cps_max = max(cps_values) if cps_values else 0
        else:
            cps_mean = len(timestamps)
            cps_max = len(timestamps)
    else:
        cps_mean = 1
        cps_max = 1

    # Longest uninterrupted streak (no gap > 2s between calls)
    longest_streak = 1
    current_streak = 1
    for i in range(1, len(timestamps)):
        if timestamps[i] - timestamps[i - 1] <= 2000:
            current_streak += 1
        else:
            longest_streak = max(longest_streak, current_streak)
            current_streak = 1
    longest_streak = max(longest_streak, current_streak)

    return {
        "max_burst_1s": max_burst,
        "longest_uninterrupted_streak": longest_streak,
        "calls_per_second_mean": cps_mean,
        "calls_per_second_max": cps_max,
    }


def _longest_streak(items: list[dict[str, Any]], predicate: Any) -> int:
    longest = 0
    current = 0
    for item in items:
        if predicate(item):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


# ---------------------------------------------------------------------------
# Trace assembly
# ---------------------------------------------------------------------------
def build_trace(
    data: dict[str, Any],
    prompt: dict[str, Any],
    prompt_index: int,
    tool_calls: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    events: list[dict[str, Any]],
    pseudo_turns: list[dict[str, Any]],
    selection_reason: str,
    codeplane_prefix: str = DEFAULT_CODEPLANE_PREFIX,
) -> dict[str, Any]:
    """Assemble the final trace JSON."""

    # --- Time bounds ---
    all_timestamps = [tc["timestamp_epoch_ms"] for tc in tool_calls if tc["timestamp_epoch_ms"]] + [
        _parse_iso(r["start_time"]) for r in requests if r.get("start_time")
    ]
    all_timestamps = [t for t in all_timestamps if t]

    session_start_ms = min(all_timestamps) if all_timestamps else None
    session_end_ms = max(all_timestamps) if all_timestamps else None
    session_start_iso = (
        datetime.fromtimestamp(session_start_ms / 1000, tz=UTC).isoformat()
        if session_start_ms
        else None
    )
    session_end_iso = (
        datetime.fromtimestamp(session_end_ms / 1000, tz=UTC).isoformat()
        if session_end_ms
        else None
    )

    # --- Token totals (real data!) ---
    total_prompt_tokens = sum(r.get("prompt_tokens") or 0 for r in requests)
    total_completion_tokens = sum(r.get("completion_tokens") or 0 for r in requests)
    total_tokens = total_prompt_tokens + total_completion_tokens

    # --- Models used ---
    models_used = list({r["model"] for r in requests if r.get("model")})

    # --- Run metadata ---
    run_metadata = {
        "extraction_timestamp": datetime.now(UTC).isoformat(),
        "parser_version": PARSER_VERSION,
        "source_format": "chatreplay_json",
        "exported_at": data.get("exportedAt"),
        "prompt_id": prompt.get("promptId"),
        "prompt_index": prompt_index,
        "prompt_text_preview": (prompt.get("prompt") or "")[:200],
        "session_start_iso": session_start_iso,
        "session_end_iso": session_end_iso,
        "models_used": models_used,
        "mcp_servers": data.get("mcpServers"),
    }

    # --- Selection criteria ---
    selection_criteria = {
        "selected_session_reason": selection_reason,
        "total_prompts_in_export": data.get("totalPrompts", len(data.get("prompts", []))),
        "total_log_entries_in_export": data.get("totalLogEntries"),
    }

    # --- Tool invocations ---
    tool_invocations = []
    for i, tc in enumerate(tool_calls):
        tool_namespace = _derive_tool_namespace(
            tc["tool_name"],
            tc["tool_kind"],
            codeplane_prefix,
        )
        tool_invocations.append(
            {
                "order_index": i,
                "turn_index": tc.get("turn_index"),
                "tool_call_id": tc["tool_call_id"],
                "tool_name": tc["tool_name"],
                "tool_name_short": _strip_mcp_prefix(tc["tool_name"]),
                "raw_tool_name": tc["raw_tool_name"],
                "raw_record_type": tc["raw_record_type"],
                "tool_kind": tc["tool_kind"],
                "call_subkind": tc["call_subkind"],
                "tool_namespace": tool_namespace,
                "timestamp_iso": tc["timestamp_iso"],
                "timestamp_epoch_ms": tc["timestamp_epoch_ms"],
                "result_bytes": tc["result_bytes"],
                "args_bytes": tc["args_bytes"],
                "args_shape_hint": tc.get("args_shape_hint"),
                "status": tc["status"],
                "content_type": tc["content_type"],
                "resource_kind": tc["resource_kind"],
            }
        )

    # --- Summaries ---
    tool_calls_by_name: dict[str, int] = Counter()
    tool_calls_by_kind: dict[str, int] = Counter()
    tool_calls_by_subkind: dict[str, int] = Counter()
    tool_calls_by_namespace: dict[str, int] = Counter()
    result_bytes_by_namespace: dict[str, int] = Counter()

    for ti in tool_invocations:
        tool_calls_by_name[ti["tool_name_short"]] += 1
        tool_calls_by_kind[ti["tool_kind"]] += 1
        tool_calls_by_subkind[ti["call_subkind"]] += 1
        ns = ti["tool_namespace"]
        tool_calls_by_namespace[ns] += 1
        result_bytes_by_namespace[ns] += ti.get("result_bytes") or 0

    total_result_bytes = sum(tc["result_bytes"] or 0 for tc in tool_calls)
    total_args_bytes = sum(tc["args_bytes"] or 0 for tc in tool_calls)

    codeplane_tool_calls_total = tool_calls_by_namespace.get("codeplane", 0)
    codeplane_result_bytes_total = result_bytes_by_namespace.get("codeplane", 0)
    total_tc = len(tool_calls) or 1
    codeplane_share_of_all_tool_calls = round(codeplane_tool_calls_total / total_tc, 4)
    codeplane_share_of_all_result_bytes = (
        round(codeplane_result_bytes_total / total_result_bytes, 4) if total_result_bytes else 0.0
    )

    # LLM timing summary
    llm_durations = [r["duration_ms"] for r in requests if r.get("duration_ms")]
    ttft_values = [r["time_to_first_token_ms"] for r in requests if r.get("time_to_first_token_ms")]

    summaries = {
        "total_tool_calls": len(tool_calls),
        "total_llm_requests": len(requests),
        "tool_calls_by_name": dict(tool_calls_by_name),
        "tool_calls_by_kind": dict(tool_calls_by_kind),
        "tool_calls_by_subkind": dict(tool_calls_by_subkind),
        "tool_calls_by_namespace": dict(tool_calls_by_namespace),
        "result_bytes_by_namespace": dict(result_bytes_by_namespace),
        "codeplane_tool_calls_total": codeplane_tool_calls_total,
        "codeplane_result_bytes_total": codeplane_result_bytes_total,
        "codeplane_share_of_all_tool_calls": codeplane_share_of_all_tool_calls,
        "codeplane_share_of_all_result_bytes": codeplane_share_of_all_result_bytes,
        "tool_result_bytes_total": total_result_bytes,
        "tool_args_bytes_total": total_args_bytes,
        "pseudo_turn_count": len(pseudo_turns),
        "tokens": {
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "source": "exact_from_api",
        },
        "llm_timing": {
            "total_duration_ms": sum(llm_durations) if llm_durations else 0,
            "mean_duration_ms": (round(stats.mean(llm_durations), 1) if llm_durations else None),
            "median_duration_ms": (
                round(stats.median(llm_durations), 1) if llm_durations else None
            ),
            "min_duration_ms": min(llm_durations) if llm_durations else None,
            "max_duration_ms": max(llm_durations) if llm_durations else None,
            "mean_ttft_ms": (round(stats.mean(ttft_values), 1) if ttft_values else None),
        },
        "models_used": models_used,
    }

    # --- MCP comparison metrics ---
    mcp_metrics = compute_mcp_comparison_metrics(
        tool_calls=tool_calls,
        events=events,
        pseudo_turns=pseudo_turns,
        requests=requests,
        session_start_ms=session_start_ms,
        session_end_ms=session_end_ms,
    )

    # --- Assemble ---
    return {
        "schema_version": "1.0.0",
        "run_metadata": run_metadata,
        "selection_criteria": selection_criteria,
        "pseudo_turns": pseudo_turns,
        "tool_invocations": tool_invocations,
        "llm_requests": requests,
        "events": events,
        "summaries": summaries,
        "mcp_comparison_metrics": mcp_metrics,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract traces from VS Code Copilot chatreplay JSON exports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        help="Path to .chatreplay.json file.",
    )
    parser.add_argument(
        "--out",
        "-o",
        default=None,
        help="Output JSON file path. Auto-derived from prompt text if omitted.",
    )
    parser.add_argument(
        "--prompt-index",
        type=int,
        default=None,
        help="0-based index of the prompt to extract.",
    )
    parser.add_argument(
        "--prompt-match",
        default=None,
        help="Substring match against prompt text (case-insensitive).",
    )
    parser.add_argument(
        "--codeplane-prefix",
        default=DEFAULT_CODEPLANE_PREFIX,
        help=f"Prefix for CodePlane MCP tool names (default: {DEFAULT_CODEPLANE_PREFIX!r}).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    args = parser.parse_args()

    if not args.verbose:
        log.setLevel(logging.INFO)

    # 1. Load
    input_path = Path(args.input)
    if not input_path.is_file():
        log.error("FATAL: File not found: %s", input_path)
        sys.exit(1)

    log.info("Loading chatreplay export: %s", input_path)
    data = load_chatreplay(input_path)
    log.info(
        "Loaded: %d prompts, %d total log entries, exported at %s",
        data.get("totalPrompts", 0),
        data.get("totalLogEntries", 0),
        data.get("exportedAt", "?"),
    )

    # 2. Select prompt
    prompt, prompt_index, selection_reason = select_prompt(
        data,
        prompt_index=args.prompt_index,
        prompt_match=args.prompt_match,
    )
    log.info(
        "Selected prompt [%d]: %d logs, reason=%s",
        prompt_index,
        prompt.get("logCount", 0),
        selection_reason,
    )
    log.info("  Prompt: %.100s...", prompt.get("prompt", "")[:100])

    # 3. Extract tool calls and LLM requests
    tool_calls = extract_tool_calls_from_prompt(prompt)
    requests = extract_requests_from_prompt(prompt)
    log.info(
        "Extracted %d tool calls and %d LLM requests.",
        len(tool_calls),
        len(requests),
    )

    # 4. Build timeline and pseudo-turns
    events = build_events_timeline(tool_calls, requests)
    pseudo_turns = segment_pseudo_turns(events)
    log.info("Built %d events, %d pseudo-turns.", len(events), len(pseudo_turns))

    # 5. Build trace
    trace = build_trace(
        data=data,
        prompt=prompt,
        prompt_index=prompt_index,
        tool_calls=tool_calls,
        requests=requests,
        events=events,
        pseudo_turns=pseudo_turns,
        selection_reason=selection_reason,
        codeplane_prefix=args.codeplane_prefix,
    )

    # 6. Output
    if args.out:
        out_path = Path(args.out)
    else:
        preview = (prompt.get("prompt") or "trace")[:60]
        out_path = Path(f"{_to_underscore_case(preview)}_trace.json")

    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2, default=str)
    log.info("Trace written to: %s", out_path.resolve())

    # 7. Summary to stderr
    s = trace["summaries"]
    tok = s["tokens"]
    timing = s["llm_timing"]
    m = trace["mcp_comparison_metrics"]
    t1 = m["tier1_core"]
    t2 = m["tier2_convergence"]
    t3 = m["tier3_cost_proxies"]
    t4 = m["tier4_stability"]

    sep = "=" * 60
    thin = "\u2500" * 60
    print(f"\n{sep}", file=sys.stderr)
    print(
        f"Prompt:       [{prompt_index}] {(prompt.get('prompt') or '')[:80]}",
        file=sys.stderr,
    )
    print(f"Tool calls:   {s['total_tool_calls']}", file=sys.stderr)
    print(f"LLM requests: {s['total_llm_requests']}", file=sys.stderr)
    print(f"Models:       {', '.join(s['models_used'])}", file=sys.stderr)
    print(
        f"Tokens:       {tok['total_tokens']} "
        f"(prompt={tok['total_prompt_tokens']} "
        f"completion={tok['total_completion_tokens']}) [exact]",
        file=sys.stderr,
    )
    print(
        f"LLM timing:   {timing['total_duration_ms']}ms total, "
        f"{timing['mean_duration_ms']}ms avg, "
        f"TTFT={timing['mean_ttft_ms']}ms avg",
        file=sys.stderr,
    )
    print(f"Pseudo turns: {s['pseudo_turn_count']}", file=sys.stderr)
    print(
        f"By name:      {json.dumps(s['tool_calls_by_name'], indent=None)}",
        file=sys.stderr,
    )
    print(
        f"By namespace: {json.dumps(s['tool_calls_by_namespace'], indent=None)}",
        file=sys.stderr,
    )
    print(thin, file=sys.stderr)
    print("MCP COMPARISON METRICS", file=sys.stderr)
    print(thin, file=sys.stderr)
    print(f"  T1 \u2502 By kind:     {dict(t1['by_kind'])}", file=sys.stderr)
    print(
        f"  T1 \u2502 native/MCP:  {t1['native_mcp_ratio']}",
        file=sys.stderr,
    )
    print(
        f"  T1 \u2502 Duration:    {t1['session_duration_s']}s",
        file=sys.stderr,
    )
    print(
        f"  T1 \u2502 Calls/sec:   {t1['tool_calls_per_second']}  "
        f"(MCP: {t1['mcp_calls_per_second']})",
        file=sys.stderr,
    )
    ts = t1["thrash_shape"]
    print(
        f"  T1 \u2502 Thrash:      "
        f"burst\u2081\u209b={ts['max_burst_1s']}  "
        f"streak={ts['longest_uninterrupted_streak']}  "
        f"cps_mean={ts['calls_per_second_mean']}  "
        f"cps_max={ts['calls_per_second_max']}",
        file=sys.stderr,
    )
    print(
        f"  T1 \u2502 Terminal:    {t1['native_terminal_calls']} native terminal calls",
        file=sys.stderr,
    )
    print(
        f"  T2 \u2502 Calls/turn:  {t2['tool_calls_per_pseudo_turn']}",
        file=sys.stderr,
    )
    print(
        f"  T2 \u2502 Before MCP:  {t2['calls_before_first_mcp']} calls before first MCP call",
        file=sys.stderr,
    )
    print(
        f"  T2 \u2502 Nat streak:  {t2['longest_native_only_streak']} longest native-only streak",
        file=sys.stderr,
    )
    print(
        f"  T3 \u2502 Tokens:      {t3['total_tokens']} total "
        f"({t3['total_prompt_tokens']} prompt + "
        f"{t3['total_completion_tokens']} completion)",
        file=sys.stderr,
    )
    print(
        f"  T3 \u2502 LLM calls:   {t3['llm_request_count']} requests, "
        f"{t3['total_llm_duration_ms']}ms total, "
        f"{t3['avg_llm_duration_ms']}ms avg",
        file=sys.stderr,
    )
    print(
        f"  T3 \u2502 Result:      {t3['total_result_bytes']} bytes total, "
        f"{t3['avg_result_bytes_per_call']} avg/call",
        file=sys.stderr,
    )
    print(
        f"  T4 \u2502 Errors:      {t4['error_calls']} ({t4['error_rate']:.1%})",
        file=sys.stderr,
    )
    print(
        f"  Selection:  {trace['selection_criteria']['selected_session_reason']}",
        file=sys.stderr,
    )
    print(
        f"  Parser:     {trace['run_metadata']['parser_version']}",
        file=sys.stderr,
    )
    print(f"{sep}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
