#!/usr/bin/env python3
"""Extract all tool calls from benchmark trace files."""

import json
from pathlib import Path
from typing import Any

CONTEXT_TOOLS = {
    "read_source",
    "read_scaffold",
    "read_file_full",
    "search",
    "describe",
    "list_files",
    "map_repo",
    "read_file",
    "list_dir",
    "grep_search",
    "file_search",
    "semantic_search",
    "get_errors",
    # Native tools
    "readFile",
    "listDirectory",
    "searchFiles",
    "grep",
}

MUTATION_TOOLS = {
    "write_source",
    "checkpoint",
    "refactor_rename",
    "refactor_move",
    "refactor_apply",
    "refactor_cancel",
    "refactor_impact",
    "refactor_inspect",
    # Native tools
    "replace_string_in_file",
    "multi_replace_string_in_file",
    "create_file",
    "run_in_terminal",
    "insert_edit",
}


def classify_tool(tool_name: str) -> str:
    """Classify a tool call as context-gathering or mutation."""
    lower = tool_name.lower()
    for t in MUTATION_TOOLS:
        if t.lower() in lower:
            return "MUTATION"
    for t in CONTEXT_TOOLS:
        if t.lower() in lower:
            return "CONTEXT"
    # Check by keywords
    if any(k in lower for k in ["write", "commit", "push", "checkpoint", "refactor"]):
        return "MUTATION"
    if any(k in lower for k in ["read", "search", "list", "describe", "map", "scaffold", "get"]):
        return "CONTEXT"
    return "OTHER"


def extract_response_error(response: object) -> str | None:
    """Check if response contains an error."""
    if response is None:
        return None
    if isinstance(response, str):
        if "error" in response.lower()[:200]:
            return response[:300]
        return None
    if isinstance(response, list):
        for item in response:
            if isinstance(item, str) and "error" in item.lower()[:200]:
                return item[:300]
    if isinstance(response, dict) and "error" in response:
        return str(response["error"])[:300]
    return None


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
        error = extract_response_error(response)
        classification = classify_tool(tool_name)

        call_info = {
            "index": idx,
            "tool": tool_name,
            "classification": classification,
            "args": args,
            "time": event.get("time", ""),
            "has_error": error is not None,
        }

        if error:
            call_info["error_snippet"] = error

        # Annotate specific tool types
        if "read_source" in tool_name.lower():
            targets = args.get("targets", [])
            call_info["read_paths"] = [
                {
                    "path": t.get("path", ""),
                    "start_line": t.get("start_line"),
                    "end_line": t.get("end_line"),
                }
                for t in targets
            ]

        if "search" in tool_name.lower() and "search_tool" not in tool_name.lower():
            call_info["search_details"] = {
                "query": args.get("query", ""),
                "mode": args.get("mode", ""),
                "enrichment": args.get("enrichment", ""),
            }

        if "read_scaffold" in tool_name.lower():
            call_info["scaffold_path"] = args.get("path", "")

        if "checkpoint" in tool_name.lower():
            call_info["checkpoint_details"] = {
                "changed_files": args.get("changed_files", []),
                "commit_message": args.get("commit_message", ""),
                "push": args.get("push", False),
            }

        if "write_source" in tool_name.lower():
            edits = args.get("edits", [])
            call_info["write_details"] = [
                {
                    "path": e.get("path", ""),
                    "start_line": e.get("start_line"),
                    "end_line": e.get("end_line"),
                }
                for e in edits
            ]

        result["tool_calls"].append(call_info)
        idx += 1

    return result


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

    all_results: dict[str, list[dict[str, Any]]] = {"codeplane_traces": [], "native_traces": []}

    for trace in codeplane_traces:
        result = extract_tool_calls(trace)
        all_results["codeplane_traces"].append(result)

    for trace in native_traces:
        result = extract_tool_calls(trace)
        all_results["native_traces"].append(result)

    print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
