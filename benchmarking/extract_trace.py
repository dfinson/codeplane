"""Extract a benchmark trace from a VS Code Copilot chatreplay export.

Usage:
    python -m benchmarking.extract_trace <chatreplay.json> --repo REPO [--output-dir DIR]

Steps:
  1. Load the chatreplay JSON (each export = one benchmark run).
  2. Auto-detect: issue number, model, codeplane vs native.
  3. Save raw JSON as  <output-dir>/<name>_raw.json
  4. Extract trace events and save as <output-dir>/<name>_trace.json

Naming:  {repo}_{issue}_{model}_{codeplane|native}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

START_MARKER = "START_BENCHMARKING_RUN"

# Models used for internal routing (not the primary agent model)
_ROUTING_MODELS = frozenset({"gpt-4o-mini", "gpt-3.5-turbo"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_issue(prompts: list[dict[str, Any]]) -> str:
    """Extract issue number from the first prompt.

    Only looks at prompts[0] (the initial user message containing the
    benchmark task).  Searches for patterns like:
      - bench/NNN-  (branch name)
      - Issue #NNN / issue #NNN
      - #NNN (standalone)
    """
    if not prompts:
        return "unknown"
    text = prompts[0].get("prompt", "")
    # branch pattern:  bench/233-early-stop
    m = re.search(r"bench/(\d+)-", text)
    if m:
        return m.group(1)
    # Issue #NNN
    m = re.search(r"[Ii]ssue\s*#(\d+)", text)
    if m:
        return m.group(1)
    # standalone #NNN
    m = re.search(r"#(\d+)", text)
    if m:
        return m.group(1)
    return "unknown"


def _detect_model(prompts: list[dict[str, Any]]) -> str:
    """Return the primary agent model from request metadata.

    Ignores routing models (gpt-4o-mini etc.) and returns the most
    frequently used non-routing model.
    """
    counts: dict[str, int] = {}
    for p in prompts:
        for log in p.get("logs", []):
            if not isinstance(log, dict) or log.get("kind") != "request":
                continue
            model = log.get("metadata", {}).get("model", "")
            if model and model not in _ROUTING_MODELS:
                counts[model] = counts.get(model, 0) + 1
    if not counts:
        return "unknown"
    return max(counts, key=lambda m: counts[m])


def _has_codeplane(prompts: list[dict[str, Any]]) -> bool:
    """Return True if any tool call targets a codeplane MCP tool."""
    for p in prompts:
        for log in p.get("logs", []):
            if not isinstance(log, dict) or log.get("kind") != "toolCall":
                continue
            tool = log.get("tool", "")
            if "codeplane" in tool.lower():
                return True
    return False


def _build_session_name(repo: str, issue: str, model: str, codeplane: bool) -> str:
    """Build the canonical file-name prefix.

    Format: {repo}_{issue}_{model}_{codeplane|native}
    Model names are sanitised (dots/slashes replaced with dashes).
    """
    safe_model = re.sub(r"[./]", "-", model)
    variant = "codeplane" if codeplane else "native"
    return f"{repo}_{issue}_{safe_model}_{variant}"


# ---------------------------------------------------------------------------
# Trace extraction
# ---------------------------------------------------------------------------


def _extract_tool_event(log: dict[str, Any]) -> dict[str, Any]:
    """Convert a toolCall log entry into a trace event."""
    args = log.get("args", {})
    # args may be a JSON string (MCP tools) or a dict
    if isinstance(args, str):
        with contextlib.suppress(json.JSONDecodeError):
            args = json.loads(args)

    response = log.get("response")
    thinking = log.get("thinking")

    return {
        "type": "tool_call",
        "id": log.get("id"),
        "tool": log.get("tool"),
        "args": args,
        "time": log.get("time"),
        "response": response,
        "thinking": thinking,
        "thinking_length": len(thinking) if isinstance(thinking, str) else 0,
        "tool_metadata": log.get("toolMetadata"),
    }


def _extract_request_event(log: dict[str, Any]) -> dict[str, Any]:
    """Convert a request log entry into a trace event."""
    meta = log.get("metadata", {})
    usage = meta.get("usage", {})

    # Extract agent text reasoning from the response message
    agent_text: str | None = None
    resp = log.get("response", {})
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, list):
            # list of strings — join non-empty ones
            joined = " ".join(s.strip() for s in msg if isinstance(s, str) and s.strip())
            if joined:
                agent_text = joined
        elif isinstance(msg, str) and msg.strip():
            agent_text = msg

    # Context size: count messages in requestMessages
    request_messages = log.get("requestMessages", {})
    if isinstance(request_messages, dict):
        msgs = request_messages.get("messages", [])
        context_message_count = len(msgs) if isinstance(msgs, list) else 0
    else:
        context_message_count = 0

    # Completion token breakdown
    ctd = usage.get("completion_tokens_details", {})

    return {
        "type": "llm_request",
        "id": log.get("id"),
        "model": meta.get("model"),
        "request_type": meta.get("requestType"),
        "agent_text": agent_text,
        "start_time": meta.get("startTime"),
        "end_time": meta.get("endTime"),
        "duration_ms": meta.get("duration"),
        "time_to_first_token_ms": meta.get("timeToFirstToken"),
        "max_prompt_tokens": meta.get("maxPromptTokens"),
        "max_response_tokens": meta.get("maxResponseTokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens"),
        "reasoning_tokens": ctd.get("reasoning_tokens") if ctd else None,
        "tools_available": len(meta.get("tools", [])),
        "context_message_count": context_message_count,
    }


def extract_trace(prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk trimmed prompts and produce a flat list of trace events."""
    events: list[dict[str, Any]] = []
    for p_idx, p in enumerate(prompts):
        for log in p.get("logs", []):
            if not isinstance(log, dict):
                continue
            kind = log.get("kind")
            if kind == "toolCall":
                ev = _extract_tool_event(log)
            elif kind == "request":
                ev = _extract_request_event(log)
            else:
                continue
            ev["prompt_index"] = p_idx
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract a benchmark trace from a Copilot chatreplay export.",
    )
    parser.add_argument(
        "chatreplay",
        type=Path,
        help="Path to the chatreplay .json file.",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Repository name (e.g. 'evee').",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output files (default: benchmarking/evee/results/).",
    )
    args = parser.parse_args(argv)

    # Load -------------------------------------------------------------------
    replay_path: Path = args.chatreplay
    if not replay_path.exists():
        print(f"ERROR: File not found: {replay_path}", file=sys.stderr)
        return 1

    with open(replay_path) as f:
        data: dict[str, Any] = json.load(f)

    prompts = data.get("prompts", [])
    if not prompts:
        print("ERROR: No prompts found in chatreplay.", file=sys.stderr)
        return 1

    print(f"Loaded {len(prompts)} prompts")

    # Auto-detect metadata ---------------------------------------------------
    repo = args.repo
    issue = _detect_issue(prompts)
    model = _detect_model(prompts)
    codeplane = _has_codeplane(prompts)
    session_name = _build_session_name(repo, issue, model, codeplane)

    print(f"Detected: repo={repo} issue={issue} model={model} codeplane={codeplane}")
    print(f"Session name: {session_name}")

    # Output dir -------------------------------------------------------------
    output_dir: Path = args.output_dir or (Path("benchmarking/evee/results"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save raw ---------------------------------------------------------------
    raw_path = output_dir / f"{session_name}_raw.json"
    with open(raw_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Raw: {raw_path}")

    # Extract and save trace -------------------------------------------------
    events = extract_trace(prompts)
    trace = {
        "session_name": session_name,
        "repo": repo,
        "issue": issue,
        "model": model,
        "codeplane": codeplane,
        "exported_at": data.get("exportedAt"),
        "total_prompts": len(prompts),
        "total_events": len(events),
        "events": events,
    }
    trace_path = output_dir / f"{session_name}_trace.json"
    with open(trace_path, "w") as f:
        json.dump(trace, f, indent=2)
    print(f"Trace: {trace_path}  ({len(events)} events)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
