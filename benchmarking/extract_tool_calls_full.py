#!/usr/bin/env python3
"""Extract all tool calls from benchmark trace files and write to a comprehensive output."""

import json
from pathlib import Path
from typing import Any


def extract_tool_calls(trace_path: str) -> dict[str, Any]:
    """Extract all tool calls from a trace file."""
    with open(trace_path) as f:
        data = json.load(f)

    result = {
        "file": Path(trace_path).name,
        "session": data.get("session_name", ""),
        "repo": data.get("repo", ""),
        "issue": data.get("issue", ""),
        "model": data.get("model", ""),
        "codeplane": data.get("codeplane", False),
        "total_events": data.get("total_events", 0),
        "tool_calls": [],
    }

    idx = 0
    for event in data.get("events", []):
        if event.get("type") != "tool_call":
            continue

        tool_name = event.get("tool", "unknown")
        args = event.get("args", {})
        response = event.get("response")

        # Check for error
        error = None
        if response is not None:
            if isinstance(response, str) and "error" in response.lower()[:200]:
                error = response[:500]
            elif isinstance(response, list):
                for item in response:
                    if isinstance(item, str) and "error" in item.lower()[:300]:
                        error = item[:500]
                        break
            elif isinstance(response, dict) and "error" in response:
                error = str(response["error"])[:500]

        call_info = {
            "index": idx,
            "tool": tool_name,
            "args": args,
            "time": event.get("time", ""),
        }

        if error:
            call_info["error"] = error

        result["tool_calls"].append(call_info)
        idx += 1

    return result


def format_trace(trace: dict[str, Any]) -> str:
    """Format a trace into readable text."""
    lines = []
    lines.append(f"{'=' * 80}")
    lines.append(f"TRACE: {trace['file']}")
    lines.append(f"  Session: {trace['session']}")
    lines.append(f"  Repo: {trace['repo']}, Issue: #{trace['issue']}")
    lines.append(f"  Model: {trace['model']}, CodePlane: {trace['codeplane']}")
    lines.append(f"  Total events: {trace['total_events']}, Tool calls: {len(trace['tool_calls'])}")
    lines.append(f"{'=' * 80}")
    lines.append("")

    for tc in trace["tool_calls"]:
        lines.append(f"  [{tc['index']:3d}] {tc['tool']}")
        lines.append(f"       Time: {tc['time']}")

        # Format args compactly but completely
        args = tc["args"]
        args_str = json.dumps(args, indent=6, ensure_ascii=False)
        lines.append(f"       Args: {args_str}")

        if "error" in tc:
            lines.append(f"       ERROR: {tc['error'][:200]}")

        lines.append("")

    return "\n".join(lines)


def main() -> None:
    codeplane_traces = [
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_4_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_108_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_233_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_260_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_262_claude-opus-4-6-fast_codeplane_trace.json",
    ]

    native_traces = [
        "benchmarking/evee/results/baseline/evee_4_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_108_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_233_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_260_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_262_claude-opus-4-6-fast_native_trace.json",
    ]

    # Write JSON with full details
    all_results: dict[str, list[dict[str, Any]]] = {"codeplane_traces": [], "native_traces": []}

    for trace in codeplane_traces:
        result = extract_tool_calls(trace)
        all_results["codeplane_traces"].append(result)

    for trace in native_traces:
        result = extract_tool_calls(trace)
        all_results["native_traces"].append(result)

    with open("/tmp/all_tool_calls_full.json", "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    # Write readable text version
    with open("/tmp/all_tool_calls_full.txt", "w") as f:
        f.write("CODEPLANE TRACES\n")
        f.write("=" * 80 + "\n\n")
        for trace in codeplane_traces:
            result = extract_tool_calls(trace)
            f.write(format_trace(result))
            f.write("\n\n")

        f.write("\n\nNATIVE (BASELINE) TRACES\n")
        f.write("=" * 80 + "\n\n")
        for trace in native_traces:
            result = extract_tool_calls(trace)
            f.write(format_trace(result))
            f.write("\n\n")

    print("Done. Files written:")
    print("  /tmp/all_tool_calls_full.json")
    print("  /tmp/all_tool_calls_full.txt")

    import os

    for p in ["/tmp/all_tool_calls_full.json", "/tmp/all_tool_calls_full.txt"]:
        size = os.path.getsize(p)
        print(f"  {p}: {size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
