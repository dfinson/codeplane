"""No-leakage K-fold cutoff training (§6.2).

Trains a LightGBM regressor to predict N(q) — how many top-ranked
objects to return.  Uses out-of-fold ranker scores to avoid label
leakage.

Pipeline:
  1. K-fold split across repos.
  2. Per fold: train ranker on K-1, score held-out fold.
  3. Per held-out query: compute N* = argmax_N F1(top-N, ground truth).
  4. Train cutoff regressor on aggregated out-of-fold data.
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from cpl_ranking.train_ranker import (
    RANKER_FEATURES,
)


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
    n_folds: int = 5,
    params: dict | None = None,
) -> dict:
    """Train the cutoff regressor with no-leakage K-fold.

    Args:
        merged_dir: Path to ``data/merged/`` with Parquet files.
        output_path: Where to save ``cutoff.lgbm``.
        n_folds: Number of folds for K-fold split.
        params: LightGBM parameters override.

    Returns:
        Training summary dict.
    """
    from cpl_ranking.train_ranker import _load_candidates_from_parquet, _prepare_features

    df = _load_candidates_from_parquet(merged_dir)
    if df.empty:
        raise ValueError("No candidate data found")

    df = _prepare_features(df)

    # Extract repo_id from run_id for fold splitting
    df["repo_id"] = df["run_id"].str.rsplit("_", n=1).str[0]

    # Group K-fold by repo
    repos = df["repo_id"].unique()
    repo_to_fold: dict[str, int] = {}
    kf = GroupKFold(n_splits=min(n_folds, len(repos)))
    for fold_idx, (_, test_idx) in enumerate(kf.split(repos, groups=repos)):
        for ri in test_idx:
            repo_to_fold[repos[ri]] = fold_idx

    df["fold"] = df["repo_id"].map(repo_to_fold)

    # Collect cutoff training rows from out-of-fold scoring
    cutoff_rows: list[dict] = []

    for fold in range(n_folds):
        train_mask = df["fold"] != fold
        test_mask = df["fold"] == fold

        if test_mask.sum() == 0:
            continue

        train_df = df[train_mask]
        test_df = df[test_mask]

        # Train fold ranker
        group_col = train_df["run_id"] + "__" + train_df["query_id"]
        train_sorted = train_df.sort_values(by=["run_id", "query_id"]).reset_index(drop=True)
        group_sizes = train_sorted.groupby(
            train_sorted["run_id"] + "__" + train_sorted["query_id"], sort=True
        ).size().values

        X_train = train_sorted[RANKER_FEATURES].values
        y_train = train_sorted["label_relevant"].astype(int).values

        train_data = lgb.Dataset(X_train, label=y_train, group=group_sizes)
        fold_ranker = lgb.train(
            {"objective": "lambdarank", "metric": "ndcg", "verbose": -1,
             "learning_rate": 0.05, "num_leaves": 63},
            train_data,
            num_boost_round=300,
        )

        # Score held-out queries
        for (run_id, query_id), qdf in test_df.groupby(["run_id", "query_id"]):
            X_q = qdf[RANKER_FEATURES].values
            scores = fold_ranker.predict(X_q)
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
