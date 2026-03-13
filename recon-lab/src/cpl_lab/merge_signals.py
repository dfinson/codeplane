"""Merge per-repo signal data into a single denormalized Parquet file.

Run after each signal collection pass (when harvesters change).
Joins with pre-merged ground truth for graded relevance labels,
query metadata (query_type, label_gate), repo_set, and repo features
(object_count, file_count).  The resulting ``candidates_rank.parquet``
is the single input for all three trainers.

Streams in two dimensions: one repo at a time, and within each repo
reads Parquet row groups individually.  Peak RAM ≈ one row group
(one query's candidates, typically <100K rows) rather than the full
repo or dataset.

Reads: ``data/{repo_id}/signals/candidates_rank.parquet``
       ``data/merged/touched_objects.parquet``
       ``data/merged/queries.parquet``
       ``data/merged/repo_features.parquet``  (optional)
Writes: ``data/merged/candidates_rank.parquet``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from cpl_lab.clone import REPO_MANIFEST


def merge_signals(data_dir: Path) -> dict[str, Any]:
    """Merge all per-repo signal Parquet into one denormalized Parquet.

    Every row gets: ``repo_id``, ``repo_set``, ``query_type``,
    ``label_gate``, ``object_count``, ``file_count``, and graded
    ``label_relevant`` (2 = minimum, 1 = thrash_preventing, 0 = irrelevant).

    Streams row groups from each repo's Parquet, enriches, and writes
    immediately.  Peak memory ≈ one row group (~50-100K rows) rather
    than the full repo.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs
            and ``merged/`` with GT Parquet tables.

    Returns:
        Summary dict with row counts.
    """
    merged_dir = data_dir / "merged"

    # ── load lookups from merged GT ──────────────────────────────

    touched_path = merged_dir / "touched_objects.parquet"
    queries_path = merged_dir / "queries.parquet"
    for p in (touched_path, queries_path):
        if not p.exists():
            raise FileNotFoundError(f"No {p} — run merge_ground_truth first")

    # Graded relevance: (run_id, candidate_key) → 2 or 1
    touched_df = pd.read_parquet(touched_path)
    tier_map: dict[tuple[str, str], int] = {}
    for _, row in touched_df.iterrows():
        grade = 2 if row.get("tier", "minimum") == "minimum" else 1
        tier_map[(row["run_id"], row["candidate_key"])] = grade

    # Query metadata: query_id → {query_type, label_gate, repo_id}
    queries_df = pd.read_parquet(queries_path)
    query_meta: dict[str, dict[str, str]] = {}
    for _, qr in queries_df.iterrows():
        query_meta[qr["query_id"]] = {
            "query_type": qr["query_type"],
            "label_gate": qr.get("label_gate", "OK"),
            "repo_id": qr.get("repo_id", ""),
        }

    # Repo features: repo_id → {object_count, file_count}
    rf_path = merged_dir / "repo_features.parquet"
    repo_feat_map: dict[str, dict[str, int]] = {}
    if rf_path.exists():
        for _, rf in pd.read_parquet(rf_path).iterrows():
            repo_feat_map[rf["repo_id"]] = {
                "object_count": int(rf["object_count"]),
                "file_count": int(rf["file_count"]),
            }

    # ── stream per-repo signals row-group by row-group ───────────

    out_path = merged_dir / "candidates_rank.parquet"
    writer: pq.ParquetWriter | None = None
    writer_schema: pa.Schema | None = None
    total_candidates = 0
    total_positive = 0

    for repo_dir in sorted(data_dir.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name in ("merged", "logs", "index_logs"):
            continue
        repo_id = repo_dir.name

        pq_src = repo_dir / "signals" / "candidates_rank.parquet"
        if not pq_src.exists():
            continue

        repo_set = REPO_MANIFEST.get(repo_id, {}).get("set", "unknown")
        rf = repo_feat_map.get(repo_id, {"object_count": 0, "file_count": 0})

        pf = pq.ParquetFile(pq_src)
        for rg_idx in range(pf.metadata.num_row_groups):
            table = pf.read_row_group(rg_idx)
            df = table.to_pandas()
            del table

            df["repo_id"] = repo_id
            df["repo_set"] = repo_set
            df["object_count"] = rf["object_count"]
            df["file_count"] = rf["file_count"]
            df["query_type"] = df["query_id"].map(
                lambda qid: query_meta.get(qid, {}).get("query_type", "")
            )
            df["label_gate"] = df["query_id"].map(
                lambda qid: query_meta.get(qid, {}).get("label_gate", "OK")
            )

            # Re-derive graded relevance from source of truth
            task_col = df["task_id"] if "task_id" in df.columns else df.get("run_id", "")
            cand_col = df.get("candidate_key", "")
            df["label_relevant"] = [
                tier_map.get((t, c), 0) for t, c in zip(task_col, cand_col)
            ]

            # Alias task_id → run_id (training expects run_id for group keys)
            df["run_id"] = task_col

            total_candidates += len(df)
            total_positive += int((df["label_relevant"] > 0).sum())

            out_table = pa.Table.from_pandas(df, preserve_index=False)
            del df

            if writer is None:
                writer_schema = out_table.schema
                writer = pq.ParquetWriter(out_path, writer_schema)
            else:
                out_table = _align_schema(out_table, writer_schema)

            writer.write_table(out_table)
            del out_table

    if writer is not None:
        writer.close()

    if total_candidates == 0:
        raise ValueError("No signal data found")

    positive_rate = total_positive / total_candidates
    summary = {
        "merged_dir": str(merged_dir),
        "total_candidates": total_candidates,
        "positive_rate": float(positive_rate),
    }
    (merged_dir / "signals_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _align_schema(table: pa.Table, target: pa.Schema) -> pa.Table:
    """Align *table* to *target* schema (add missing cols, reorder, cast)."""
    for field in target:
        if field.name not in table.column_names:
            table = table.append_column(field, pa.nulls(len(table), type=field.type))
    # Drop extra columns not in target, reorder to match
    table = table.select([f.name for f in target])
    return table.cast(target)
