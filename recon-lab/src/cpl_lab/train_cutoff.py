"""No-leakage cutoff training (§6.2).

Trains a LightGBM regressor to predict N(q) — how many top-ranked
objects to return.  The ranker (trained on ranker-gate repos) scores
cutoff-set candidates.  N* is computed from those scores.  Zero leakage.

Uses streaming row-group iteration to avoid loading the full table.

Reads one table: ``data/merged/candidates_rank.parquet``.
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cpl_lab.train_ranker import (
    RANKER_FEATURES,
    _RANKER_LOAD_COLS,
    _prepare_features,
)
from cpl_lab.schema import OK_QUERY_TYPES


def _compute_n_star(
    scores: np.ndarray,
    labels: np.ndarray,
    max_n: int = 100,
) -> int:
    """Find N that maximizes F1 between top-N predictions and ground truth."""
    n_relevant = labels.sum()
    if n_relevant == 0:
        return 0

    best_f1 = 0.0
    best_n = 1

    tp_cumsum = np.cumsum(labels)
    for n in range(1, min(len(scores), max_n) + 1):
        tp = tp_cumsum[n - 1]
        precision = tp / n
        recall = tp / n_relevant
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
            if f1 > best_f1:
                best_f1 = f1
                best_n = n

    return best_n


def _extract_cutoff_features(
    scores: np.ndarray,
    retriever_hits: np.ndarray,
    query_len: int,
    has_identifier: bool,
    has_path: bool,
    n_candidates: int,
) -> dict:
    """Compute query-level cutoff features from the ranked score list."""
    n = len(scores)
    f: dict = {}
    f["query_len"] = query_len
    f["has_identifier"] = has_identifier
    f["has_path"] = has_path
    f["total_candidates"] = n_candidates

    if n == 0:
        f["top_score"] = 0.0
        f["score_p25"] = 0.0
        f["score_p50"] = 0.0
        f["score_p75"] = 0.0
        f["max_gap"] = 0.0
        f["max_gap_pos"] = 0.0
        f["score_var"] = 0.0
        f["agreement_mean"] = 0.0
        return f

    f["top_score"] = float(scores[0])
    f["score_p25"] = float(np.percentile(scores, 75))  # top 25% threshold
    f["score_p50"] = float(np.percentile(scores, 50))
    f["score_p75"] = float(np.percentile(scores, 25))  # bottom 25%

    gaps = np.diff(scores) if n > 1 else np.array([0.0])
    f["max_gap"] = float(np.abs(gaps).max()) if len(gaps) > 0 else 0.0
    f["max_gap_pos"] = float((np.abs(gaps).argmax() + 1) / n) if len(gaps) > 0 else 0.0
    f["score_var"] = float(scores.var())
    f["agreement_mean"] = float(retriever_hits.mean())

    return f


CUTOFF_FEATURES = [
    "query_len", "has_identifier", "has_path", "total_candidates",
    "top_score", "score_p25", "score_p50", "score_p75",
    "max_gap", "max_gap_pos", "score_var", "agreement_mean",
]


def train_cutoff(
    merged_dir: Path,
    output_path: Path,
    ranker_model_path: Path | None = None,
    params: dict | None = None,
) -> dict:
    """Train the cutoff regressor using disjoint repo split.

    The ranker (trained on ranker+gate repos) scores cutoff repo
    candidates. N* is computed from those scores. Zero leakage.

    Streams one Parquet row group at a time to avoid loading the full
    dataset into memory.

    Args:
        merged_dir: Path to ``data/merged/`` with Parquet files.
        output_path: Where to save ``cutoff.lgbm``.
        ranker_model_path: Path to trained ``ranker.lgbm``.
            If None, looks for it next to output_path.
        params: LightGBM parameters override.

    Returns:
        Training summary dict.
    """
    # Load ranker for scoring cutoff candidates
    if ranker_model_path is None:
        ranker_model_path = output_path.parent / "ranker.lgbm"
    if not ranker_model_path.exists():
        raise FileNotFoundError(
            f"Ranker model not found at {ranker_model_path}. "
            f"Train the ranker first."
        )

    ranker = lgb.Booster(model_file=str(ranker_model_path))
    feature_names = ranker.feature_name()

    # Stream cutoff-set candidates row-group by row-group
    pq_path = merged_dir / "candidates_rank.parquet"
    pf = pq.ParquetFile(pq_path)

    cutoff_rows: list[dict] = []

    for rg_idx in range(pf.metadata.num_row_groups):
        table = pf.read_row_group(rg_idx, columns=_RANKER_LOAD_COLS)
        chunk = table.to_pandas()
        del table

        # Filter to cutoff set only, OK queries only
        chunk = chunk[chunk["repo_set"] == "cutoff"]
        if "query_type" in chunk.columns:
            chunk = chunk[chunk["query_type"].isin(OK_QUERY_TYPES)]
        if chunk.empty:
            del chunk
            continue

        chunk = _prepare_features(chunk)

        # Score all candidates with the trained ranker
        X_all = chunk[feature_names].fillna(0).values
        chunk["ranker_score"] = ranker.predict(X_all)

        # Compute N* per query within this row group
        for (run_id, query_id), qdf in chunk.groupby(["run_id", "query_id"]):
            scores = qdf["ranker_score"].values
            labels = qdf["label_relevant"].astype(int).values

            # Sort by predicted score
            order = np.argsort(-scores)
            sorted_scores = scores[order]
            sorted_labels = labels[order]
            sorted_hits = qdf["retriever_hits"].values[order]

            n_star = _compute_n_star(sorted_scores, sorted_labels)

            feat = _extract_cutoff_features(
                sorted_scores, sorted_hits,
                query_len=int(qdf["query_len"].iloc[0]),
                has_identifier=bool(qdf["has_identifier"].iloc[0]),
                has_path=bool(qdf["has_path"].iloc[0]),
                n_candidates=len(qdf),
            )
            feat["n_star"] = n_star
            cutoff_rows.append(feat)

        del chunk

    if not cutoff_rows:
        raise ValueError("No cutoff training rows generated")

    cutoff_df = pd.DataFrame(cutoff_rows)
    X = cutoff_df[CUTOFF_FEATURES].values
    y = cutoff_df["n_star"].values

    default_params = {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "verbose": -1,
    }
    if params:
        default_params.update(params)

    train_data = lgb.Dataset(X, label=y, feature_name=CUTOFF_FEATURES)
    booster = lgb.train(default_params, train_data, num_boost_round=300)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(output_path))

    summary = {
        "cutoff_rows": len(cutoff_df),
        "n_star_mean": float(y.mean()),
        "n_star_std": float(y.std()),
        "model_path": str(output_path),
    }
    return summary
