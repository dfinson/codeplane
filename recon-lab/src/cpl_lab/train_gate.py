"""Multiclass gate classifier training (§6.3).

Trains a LightGBM multiclass classifier on retrieval distribution
features. All query types from non-eval repos participate
(OK, UNSAT, BROAD, AMBIG).  Optimizes cross-entropy.

Reads one table: ``data/merged/candidates_rank.parquet``.
Uses streaming row-group iteration to avoid loading the full table.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


# Columns needed from the merged parquet for gate feature extraction.
_GATE_LOAD_COLS = [
    "query_id", "query_type", "repo_set", "label_gate",
    "emb_score", "parent_dir", "path", "retriever_hits",
    "object_count", "file_count",
    "query_len", "identifier_density", "has_path",
    "has_numbers", "has_quoted_strings",
]


GATE_FEATURES = [
    "query_len", "identifier_density", "has_path",
    "has_numbers", "has_quoted_strings",
    "has_agent_seeds", "agent_seed_count",
    "object_count", "file_count",
    "total_candidates",
    "top_score", "score_p25", "score_p50", "score_p75",
    "path_entropy", "cluster_count",
    "agreement_mean", "agreement_std",
]

GATE_LABELS = {"OK": 0, "UNSAT": 1, "BROAD": 2, "AMBIG": 3}


def _compute_gate_features(
    candidates: list[dict],
    query: dict,
    repo_features: dict,
) -> dict:
    """Compute gate features from a candidate pool and query metadata."""
    f: dict = {}

    # Query features
    f["query_len"] = query.get("query_len", len(query.get("query_text", "")))
    f["identifier_density"] = query.get("identifier_density", 0.0)
    f["has_path"] = query.get("has_path", False)
    f["has_numbers"] = query.get("has_numbers", False)
    f["has_quoted_strings"] = query.get("has_quoted_strings", False)

    # Seed presence
    seeds = query.get("seeds", [])
    f["has_agent_seeds"] = len(seeds) > 0
    f["agent_seed_count"] = len(seeds)

    # Repo features
    f["object_count"] = repo_features.get("object_count", 0)
    f["file_count"] = repo_features.get("file_count", 0)

    # Candidate pool features
    f["total_candidates"] = len(candidates)

    emb_scores = sorted(
        [c.get("emb_score", 0.0) or 0.0 for c in candidates], reverse=True
    )
    n = len(emb_scores)

    if n == 0:
        f["top_score"] = 0.0
        f["score_p25"] = 0.0
        f["score_p50"] = 0.0
        f["score_p75"] = 0.0
        f["path_entropy"] = 0.0
        f["cluster_count"] = 0
        f["agreement_mean"] = 0.0
        f["agreement_std"] = 0.0
        return f

    f["top_score"] = emb_scores[0]
    f["score_p25"] = emb_scores[min(int(n * 0.25), n - 1)]
    f["score_p50"] = emb_scores[min(int(n * 0.5), n - 1)]
    f["score_p75"] = emb_scores[min(int(n * 0.75), n - 1)]

    # Path entropy
    dirs = [c.get("parent_dir", "") for c in candidates if c.get("parent_dir")]
    dir_counts = Counter(dirs)
    total_dirs = sum(dir_counts.values())
    if total_dirs > 0:
        probs = [count / total_dirs for count in dir_counts.values()]
        f["path_entropy"] = -sum(p * math.log(p) for p in probs if p > 0)
    else:
        f["path_entropy"] = 0.0

    # Cluster count (distinct depth-2 directories)
    depth2 = set()
    for c in candidates:
        parts = c.get("path", "").split("/")
        if len(parts) >= 2:
            depth2.add("/".join(parts[:2]))
    f["cluster_count"] = len(depth2)

    # Retriever agreement
    hits = [c.get("retriever_hits", 0) for c in candidates]
    mean_h = sum(hits) / max(len(hits), 1)
    f["agreement_mean"] = mean_h
    f["agreement_std"] = (sum((h - mean_h) ** 2 for h in hits) / max(len(hits), 1)) ** 0.5

    return f


def _stream_gate_features(merged_dir: Path) -> list[dict]:
    """Build per-query gate feature rows by streaming row groups.

    Reads one Parquet row group at a time (one per repo), keeping only
    the columns needed for gate features.  Peak RAM ≈ largest single
    repo rather than the full dataset.
    """
    pq_path = merged_dir / "candidates_rank.parquet"
    pf = pq.ParquetFile(pq_path)

    allowed_sets = {"ranker-gate", "cutoff"}

    # Accumulators: query_id → per-query candidate dicts + metadata
    # Process one row group → aggregate → discard.
    all_rows: list[dict] = []

    for rg_idx in range(pf.metadata.num_row_groups):
        table = pf.read_row_group(rg_idx, columns=_GATE_LOAD_COLS)
        chunk = table.to_pandas()
        del table

        # Filter to non-eval repo sets
        chunk = chunk[chunk["repo_set"].isin(allowed_sets)]
        if chunk.empty:
            del chunk
            continue

        # Group by query_id within this row group
        for qid, grp in chunk.groupby("query_id", sort=False):
            first = grp.iloc[0]
            cands = grp.to_dict("records")
            repo_features = {
                "object_count": first.get("object_count", 0),
                "file_count": first.get("file_count", 0),
            }
            feat = _compute_gate_features(cands, first, repo_features)
            feat["label_gate"] = first.get("label_gate", "OK")
            all_rows.append(feat)
        del chunk

    return all_rows


def train_gate(
    merged_dir: Path,
    output_path: Path,
    params: dict | None = None,
) -> dict:
    """Train the gate classifier.

    Args:
        merged_dir: Path to ``data/merged/`` with Parquet files.
        output_path: Where to save ``gate.lgbm``.
        params: LightGBM parameters override.

    Returns:
        Training summary dict.
    """
    rows = _stream_gate_features(merged_dir)
    if not rows:
        raise ValueError("No gate training data found")

    df = pd.DataFrame(rows)

    X = df[GATE_FEATURES].fillna(0).values
    y = df["label_gate"].map(GATE_LABELS).values

    default_params = {
        "objective": "multiclass",
        "num_class": 4,
        "metric": "multi_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "verbose": -1,
    }
    if params:
        default_params.update(params)

    train_data = lgb.Dataset(X, label=y, feature_name=GATE_FEATURES)
    booster = lgb.train(default_params, train_data, num_boost_round=300)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(output_path))

    label_counts = Counter(df["label_gate"].values)
    summary = {
        "total_queries": len(df),
        "label_distribution": dict(label_counts),
        "model_path": str(output_path),
    }
    return summary
