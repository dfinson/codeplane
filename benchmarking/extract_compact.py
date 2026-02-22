#!/usr/bin/env python3
"""Extract tool call sequences from benchmark traces - compact version for simulation.

Keeps full args for read/search/scaffold calls.
Truncates write content to first 3 + last 3 lines.
"""

import copy
import json
from pathlib import Path
from typing import Any


def truncate_content(content: str, max_lines: int = 6) -> str:
    """Truncate content to first 3 + last 3 lines if long."""
    if not content:
        return content
    lines = content.split("\n")
    if len(lines) <= max_lines:
        return content
    return (
        "\n".join(lines[:3])
        + f"\n... [{len(lines) - 6} lines omitted] ...\n"
        + "\n".join(lines[-3:])
    )


def compact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return args with large content fields truncated."""
    result = copy.deepcopy(args)

    # Truncate write_source edits content
    if "edits" in result:
        for edit in result["edits"]:
            if "content" in edit:
                edit["content"] = truncate_content(edit["content"])
            if "new_content" in edit:
                edit["new_content"] = truncate_content(edit["new_content"])
            if "expected_content" in edit:
                edit["expected_content"] = truncate_content(edit["expected_content"])

    # Truncate todoList (just keep titles/status)
    if "todoList" in result:
        result["todoList"] = [
            {"id": t.get("id"), "status": t.get("status"), "title": t.get("title")}
            for t in result["todoList"]
        ]

    return result


def extract_trace(trace_path: str) -> dict[str, Any]:
    with open(trace_path) as f:
        data = json.load(f)

    calls = []
    idx = 0
    for event in data.get("events", []):
        if event.get("type") != "tool_call":
            continue

        tool_name = event.get("tool", "unknown")
        args = event.get("args", {})
        response = event.get("response")

        error = None
        if response is not None:
            if isinstance(response, str) and "error" in response.lower()[:200]:
                error = response[:300]
            elif isinstance(response, list):
                for item in response:
                    if isinstance(item, str) and "error" in item.lower()[:300]:
                        error = item[:300]
                        break
            elif isinstance(response, dict) and "error" in response:
                error = str(response["error"])[:300]

        call = {
            "idx": idx,
            "tool": tool_name,
            "args": compact_args(args),
            "time": event.get("time", ""),
        }
        if error:
            call["error"] = error

        calls.append(call)
        idx += 1

    return {
        "file": Path(trace_path).name,
        "session": data.get("session_name", ""),
        "issue": data.get("issue", ""),
        "codeplane": data.get("codeplane", False),
        "total_tool_calls": len(calls),
        "calls": calls,
    }


def main() -> None:
    codeplane = [
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_4_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_108_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_233_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_260_claude-opus-4-6-fast_codeplane_trace.json",
        "benchmarking/evee/results/0.1.dev120-8cdf362e/evee_262_claude-opus-4-6-fast_codeplane_trace.json",
    ]
    native = [
        "benchmarking/evee/results/baseline/evee_4_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_108_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_233_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_260_claude-opus-4-6-fast_native_trace.json",
        "benchmarking/evee/results/baseline/evee_262_claude-opus-4-6-fast_native_trace.json",
    ]

    result = {
        "codeplane": [extract_trace(t) for t in codeplane],
        "native": [extract_trace(t) for t in native],
    }

    output_path = "/tmp/benchmark_tool_sequences.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    import os

    print(f"Written to {output_path}: {os.path.getsize(output_path) / 1024:.1f} KB")

    # Also print a compact summary per trace
    for label, traces in [("CODEPLANE", result["codeplane"]), ("NATIVE", result["native"])]:
        print(f"\n{'=' * 80}")
        print(f"{label} TRACES")
        print(f"{'=' * 80}")
        for trace in traces:
            print(
                f"\n--- {trace['file']} (issue #{trace['issue']}, {trace['total_tool_calls']} calls) ---"
            )
            for c in trace["calls"]:
                err_marker = " [ERROR]" if "error" in c else ""
                print(f"  [{c['idx']:3d}] {c['tool']}{err_marker}")


if __name__ == "__main__":
    main()
