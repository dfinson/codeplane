#!/usr/bin/env python3
"""Merge per-task JSON files + non_ok_queries into a single ground_truth.jsonl.

Each repo produces one JSONL file with exactly 34 lines:
  - Lines 1-33: task ground truth (N1-N11, M1-M11, W1-W11)
  - Line 34: non_ok_queries

The merge validates completeness (all 33 tasks + non_ok present) before
writing the JSONL. On success with --clean, intermediate files are removed,
leaving only the JSONL.

Usage:
    python merge_ground_truth.py ranking/data/python-fastapi
    python merge_ground_truth.py --clean ranking/data/python-fastapi
    python merge_ground_truth.py ranking/data/*
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

TASK_PATTERN = re.compile(r"^(N|M|W)\d+\.json$")
TASK_ORDER = {"N": 0, "M": 1, "W": 2}
EXPECTED_TASKS = [f"{t}{n}" for t in ("N", "M", "W") for n in range(1, 12)]


def _sort_key(p: Path) -> tuple[int, int]:
    """Sort N before M before W, numerically within each tier."""
    m = TASK_PATTERN.match(p.name)
    assert m  # caller already filtered
    prefix = m.group(1)
    num = int(p.stem.lstrip("NMW"))
    return (TASK_ORDER[prefix], num)


def merge_repo(repo_dir: Path, *, clean: bool = False) -> tuple[Path, list[str]]:
    """Merge ground_truth/*.json + non_ok_queries.json into ground_truth.jsonl.

    Returns (output_path, warnings).
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

    warnings: list[str] = []

    # Validate all 33 tasks present
    found_tasks = {f.stem for f in task_files}
    missing_tasks = set(EXPECTED_TASKS) - found_tasks
    extra_tasks = found_tasks - set(EXPECTED_TASKS)
    if missing_tasks:
        warnings.append(f"Missing tasks: {sorted(missing_tasks)}")
    if extra_tasks:
        warnings.append(f"Unexpected files: {sorted(extra_tasks)}")

    # Build JSONL lines
    lines: list[str] = []
    for f in task_files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=False))

    # Find non_ok_queries.json — canonical location is ground_truth/non_ok_queries.json
    non_ok: Path | None = None
    for candidate in [gt_dir / "non_ok_queries.json", repo_dir / "non_ok_queries.json"]:
        if candidate.exists():
            non_ok = candidate
            break

    if non_ok:
        obj = json.loads(non_ok.read_text(encoding="utf-8"))
        lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=False))
    else:
        warnings.append("No non_ok_queries.json found")

    # Only write + clean if no missing tasks AND non_ok present
    out_path = repo_dir / "ground_truth.jsonl"
    if missing_tasks or not non_ok:
        # Write partial JSONL but do NOT clean
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        warnings.append("INCOMPLETE — intermediates preserved")
        return out_path, warnings

    # Complete — write JSONL
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Clean up intermediates if requested
    if clean:
        shutil.rmtree(gt_dir)
        # Also remove standalone non_ok if it's outside ground_truth/
        standalone_non_ok = repo_dir / "non_ok_queries.json"
        if standalone_non_ok.exists():
            standalone_non_ok.unlink()

    return out_path, warnings


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    clean = "--clean" in args
    args = [a for a in args if a != "--clean"]

    if not args:
        print(f"Usage: {sys.argv[0]} [--clean] <repo_dir> [repo_dir ...]", file=sys.stderr)
        return 1

    ok = 0
    fail = 0
    for arg in args:
        repo_dir = Path(arg)
        try:
            out, warnings = merge_repo(repo_dir, clean=clean)
            count = sum(1 for _ in out.read_text().strip().splitlines())
            status = "✓" if not warnings else "⚠"
            print(f"  {status} {repo_dir.name}: {count} lines -> {out}")
            for w in warnings:
                print(f"      {w}")
            if clean and not warnings:
                print(f"      cleaned intermediates")
            ok += 1
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"  ✗ {repo_dir.name}: {e}", file=sys.stderr)
            fail += 1

    print(f"\nDone: {ok} merged, {fail} skipped")
    return 1 if fail and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
