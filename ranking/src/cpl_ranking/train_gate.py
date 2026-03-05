"""Multiclass gate classifier training (§6.3).

Trains a LightGBM multiclass classifier on retrieval distribution
features. All query types participate (OK, UNSAT, BROAD, AMBIG).
Optimizes cross-entropy.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from cpl_ranking.schema import OK_QUERY_TYPES


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
    queries_df = pd.read_parquet(merged_dir / "queries.parquet")
    candidates_df = pd.read_parquet(merged_dir / "candidates_rank.parquet")

    # Group candidates by query_id
    candidates_by_query: dict[str, list[dict]] = {}
    for _, row in candidates_df.iterrows():
        qid = row["query_id"]
        candidates_by_query.setdefault(qid, []).append(row.to_dict())

    repo_features = {"object_count": 0, "file_count": 0}

    rows: list[dict] = []
    for _, q in queries_df.iterrows():
        cands = candidates_by_query.get(q["query_id"], [])
        feat = _compute_gate_features(cands, q.to_dict(), repo_features)
        feat["label_gate"] = q["label_gate"]
        rows.append(feat)

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
