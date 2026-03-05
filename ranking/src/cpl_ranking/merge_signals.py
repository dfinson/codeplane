"""Merge per-repo signal data into a single Parquet file.

Run after each signal collection pass (when harvesters change).
Joins with pre-merged ground truth for relevance labels.

Reads: ``data/{repo_id}/signals/candidates_rank.jsonl``
       ``data/merged/touched_objects.parquet`` (from merge_ground_truth)
Writes: ``data/merged/candidates_rank.parquet``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def merge_signals(data_dir: Path) -> dict[str, Any]:
    """Merge all per-repo signal JSONL into Parquet with labels.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs
            and ``merged/touched_objects.parquet``.

    Returns:
        Summary dict with row counts.
    """
    merged_dir = data_dir / "merged"

    # Load pre-merged ground truth for labeling
    touched_path = merged_dir / "touched_objects.parquet"
    if not touched_path.exists():
        raise FileNotFoundError(
            f"No {touched_path} — run merge_ground_truth first"
        )

    touched_df = pd.read_parquet(touched_path)
    # Build set of (run_id, def_uid) for fast lookup
    relevant_keys = set(
        zip(touched_df["run_id"], touched_df["def_uid"])
    )

    # Collect all candidates
    rows: list[dict] = []
    for repo_dir in sorted(data_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name == "merged":
            continue
        src = repo_dir / "signals" / "candidates_rank.jsonl"
        if not src.exists():
            continue
        for ln in src.read_text().splitlines():
            if ln.strip():
                rows.append(json.loads(ln))

    if not rows:
        raise ValueError("No signal data found")

    df = pd.DataFrame(rows)

    # Ensure label_relevant is set from ground truth
    # (collect_signals.py may have set it, but re-derive from source of truth)
    df["label_relevant"] = [
        (row["run_id"], row["def_uid"]) in relevant_keys
        for _, row in df.iterrows()
    ]

    out_path = merged_dir / "candidates_rank.parquet"
    df.to_parquet(out_path, index=False)

    summary = {
        "merged_dir": str(merged_dir),
        "total_candidates": len(df),
        "positive_rate": float(df["label_relevant"].mean()),
    }
    (merged_dir / "signals_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
