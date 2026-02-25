"""Convert VS Code Copilot chatreplay exports → EVEE-compatible trace JSON.

Thin wrapper around ``benchmarking.extract_trace`` that writes files in the
format expected by ``cpl-agent-traces`` dataset (``*_trace.json``).

Usage:
    python -m benchmarking.cpl_bench.preprocessing.chatreplay_to_traces \
        benchmarking/evee/results/*.json \
        --repo evee \
        --output-dir benchmarking/cpl_bench/data/traces
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmarking.extract_trace import (
    _build_session_name,
    _detect_issue,
    _detect_model,
    _has_codeplane,
    extract_trace,
)


def convert_chatreplay(chatreplay_path: Path, repo: str, output_dir: Path) -> Path | None:
    """Convert a single chatreplay export to EVEE trace format.

    Returns the output path on success, None if the file cannot be processed.
    """
    with open(chatreplay_path) as f:
        data: dict[str, Any] = json.load(f)

    prompts = data.get("prompts", [])
    if not prompts:
        print(f"  SKIP (no prompts): {chatreplay_path.name}", file=sys.stderr)
        return None

    issue = _detect_issue(prompts)
    model = _detect_model(prompts)
    codeplane = _has_codeplane(prompts)
    session_name = _build_session_name(repo, issue, model, codeplane)

    events = extract_trace(prompts)
    trace = {
        "session_name": session_name,
        "repo": repo,
        "issue": issue,
        "model": model,
        "codeplane": codeplane,
        "variant": "codeplane" if codeplane else "native",
        "exported_at": data.get("exportedAt"),
        "total_prompts": len(prompts),
        "total_events": len(events),
        "events": events,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{session_name}_trace.json"
    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2)

    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert chatreplay exports → EVEE traces")
    parser.add_argument("files", nargs="+", type=Path, help="Chatreplay .json files")
    parser.add_argument("--repo", required=True, help="Repository name")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarking/cpl_bench/data/traces"),
        help="Output directory for trace files",
    )
    args = parser.parse_args(argv)

    converted = 0
    skipped = 0
    for path in args.files:
        if not path.exists():
            print(f"  NOT FOUND: {path}", file=sys.stderr)
            skipped += 1
            continue
        result = convert_chatreplay(path, args.repo, args.output_dir)
        if result:
            print(f"  OK: {result}")
            converted += 1
        else:
            skipped += 1

    print(f"\nConverted: {converted}, Skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
