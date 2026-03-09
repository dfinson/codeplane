#!/usr/bin/env python3
"""Merge per-task JSON files into a single ground_truth.jsonl per repo.

Usage:
    # Single repo:
    python merge_ground_truth.py ranking/data/python-fastapi

    # All repos:
    python merge_ground_truth.py ranking/data/*
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TASK_PATTERN = re.compile(r"^(N|M|W)\d+\.json$")
TASK_ORDER = {"N": 0, "M": 1, "W": 2}


def _sort_key(p: Path) -> tuple[int, int]:
    """Sort N before M before W, numerically within each tier."""
    m = TASK_PATTERN.match(p.name)
    assert m  # caller already filtered
    prefix = m.group(1)
    num = int(p.stem.lstrip("NMW"))
    return (TASK_ORDER[prefix], num)


def merge_repo(repo_dir: Path) -> Path:
    """Merge ground_truth/*.json into ground_truth.jsonl.

    Returns path to the written JSONL file.
    Raises FileNotFoundError if ground_truth/ is missing or empty.
    """
    gt_dir = repo_dir / "ground_truth"
    if not gt_dir.is_dir():
        raise FileNotFoundError(f"No ground_truth/ directory in {repo_dir}")

    task_files = sorted(
        [f for f in gt_dir.iterdir() if TASK_PATTERN.match(f.name)],
        key=_sort_key,
    )
    if not task_files:
        raise FileNotFoundError(f"No task JSON files in {gt_dir}")

    out_path = repo_dir / "ground_truth.jsonl"
    lines: list[str] = []

    for f in task_files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=False))

    # Append non_ok_queries.json as the last line if present
    # Check both inside ground_truth/ and alongside it
    non_ok = gt_dir / "non_ok_queries.json"
    if not non_ok.exists():
        non_ok = repo_dir / "non_ok_queries.json"
    if non_ok.exists():
        obj = json.loads(non_ok.read_text(encoding="utf-8"))
        lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=False))

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(f"Usage: {sys.argv[0]} <repo_dir> [repo_dir ...]", file=sys.stderr)
        return 1

    ok = 0
    fail = 0
    for arg in args:
        repo_dir = Path(arg)
        try:
            out = merge_repo(repo_dir)
            count = sum(1 for _ in out.read_text().strip().splitlines())
            print(f"  {repo_dir.name}: {count} records -> {out}")
            ok += 1
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"  SKIP {repo_dir.name}: {e}", file=sys.stderr)
            fail += 1

    print(f"\nDone: {ok} merged, {fail} skipped")
    return 1 if fail and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
