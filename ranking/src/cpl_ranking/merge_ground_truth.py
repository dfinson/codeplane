"""Merge per-repo ground truth into a single Parquet file.

Run ONCE after all repos are collected. Output is permanent —
ground truth doesn't change when harvesters are updated.

Reads: ``data/{repo_id}/ground_truth/{runs,touched_objects,queries}.jsonl``
Writes: ``data/merged/runs.parquet``
        ``data/merged/touched_objects.parquet``
        ``data/merged/queries.parquet``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def merge_ground_truth(data_dir: Path) -> dict[str, Any]:
    """Merge all per-repo ground truth JSONL into Parquet.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs.

    Returns:
        Summary dict with row counts.
    """
    merged_dir = data_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)

    tables = {
        "runs": "ground_truth/runs.jsonl",
        "touched_objects": "ground_truth/touched_objects.jsonl",
        "queries": "ground_truth/queries.jsonl",
    }

    counts: dict[str, int] = {}

    for table_name, rel_path in tables.items():
        rows: list[dict] = []
        for repo_dir in sorted(data_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name == "merged":
                continue
            src = repo_dir / rel_path
            if not src.exists():
                continue
            for ln in src.read_text().splitlines():
                if ln.strip():
                    rows.append(json.loads(ln))

        df = pd.DataFrame(rows)
        out_path = merged_dir / f"{table_name}.parquet"
        df.to_parquet(out_path, index=False)
        counts[table_name] = len(df)

    summary = {"merged_dir": str(merged_dir), "counts": counts}
    (merged_dir / "ground_truth_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
