"""LambdaMART ranker training (§6.1).

Trains a LightGBM LambdaMART model on ``candidates_rank`` data,
grouped by (run_id, query_id), optimizing NDCG with binary relevance.
Only OK-labeled queries participate.
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from cpl_ranking.schema import OK_QUERY_TYPES

# Features used by the ranker (must match extract_ranker_features output)
RANKER_FEATURES = [
    "emb_score", "emb_rank",
    "term_match_count", "term_total_matches",
    "lex_hit_count",
    "graph_is_callee", "graph_is_caller", "graph_is_sibling", "graph_seed_rank",
    "sym_agent_seed", "sym_auto_seed", "sym_task_extracted", "sym_path_mention",
    "import_forward", "import_reverse", "import_barrel", "import_test_pair",
    "retriever_hits",
    "object_size_lines", "path_depth", "nesting_depth",
    "hub_score", "is_test",
    "has_docstring", "has_decorators", "has_return_type", "has_parent_scope",
    "has_signature",
    "query_len", "has_identifier", "has_path", "term_count",
]


def _load_candidates_from_parquet(merged_dir: Path) -> pd.DataFrame:
    """Load candidates from merged Parquet, filter to OK queries."""
    candidates_df = pd.read_parquet(merged_dir / "candidates_rank.parquet")
    queries_df = pd.read_parquet(merged_dir / "queries.parquet")

    ok_query_ids = set(
        queries_df.loc[
            queries_df["query_type"].isin(OK_QUERY_TYPES), "query_id"
        ]
    )

    return candidates_df[candidates_df["query_id"].isin(ok_query_ids)].copy()


def _prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Encode categorical signals into binary columns for LightGBM."""
    df = df.copy()

    # Graph edge type → binary columns
    df["graph_is_callee"] = df["graph_edge_type"] == "callee"
    df["graph_is_caller"] = df["graph_edge_type"] == "caller"
    df["graph_is_sibling"] = df["graph_edge_type"] == "sibling"

    # Symbol source → binary columns
    df["sym_agent_seed"] = df["symbol_source"] == "agent_seed"
    df["sym_auto_seed"] = df["symbol_source"] == "auto_seed"
    df["sym_task_extracted"] = df["symbol_source"] == "task_extracted"
    df["sym_path_mention"] = df["symbol_source"] == "path_mention"

    # Import direction → binary columns
    df["import_forward"] = df["import_direction"] == "forward"
    df["import_reverse"] = df["import_direction"] == "reverse"
    df["import_barrel"] = df["import_direction"] == "barrel"
    df["import_test_pair"] = df["import_direction"] == "test_pair"

    # Has signature
    df["has_signature"] = df.get("signature_text", pd.Series(dtype=object)).notna()

    # Term count from query features (may not be in candidates directly)
    if "term_count" not in df.columns:
        df["term_count"] = 0

    # Fill NaN for numeric columns
    for col in RANKER_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(0)
        else:
            df[col] = 0

    return df


def train_ranker(
    merged_dir: Path,
    output_path: Path,
    params: dict | None = None,
) -> dict:
    """Train the LambdaMART object ranker.

    Args:
        merged_dir: Path to ``data/merged/`` with Parquet files.
        output_path: Where to save ``ranker.lgbm``.
        params: LightGBM parameters override.

    Returns:
        Training summary dict.
    """
    df = _load_candidates_from_parquet(merged_dir)
    if df.empty:
        raise ValueError("No candidate data found")

    df = _prepare_features(df)

    # Build group ids for LambdaMART
    group_col = df["run_id"] + "__" + df["query_id"]
    groups = group_col.value_counts().sort_index()

    # Sort by group to align with group sizes
    df = df.sort_values(by=["run_id", "query_id"]).reset_index(drop=True)
    group_sizes = df.groupby(group_col, sort=True).size().values

    X = df[RANKER_FEATURES].values
    y = df["label_relevant"].astype(int).values

    default_params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [5, 10, 20],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 10,
        "verbose": -1,
    }
    if params:
        default_params.update(params)

    train_data = lgb.Dataset(X, label=y, group=group_sizes, feature_name=RANKER_FEATURES)

    booster = lgb.train(
        default_params,
        train_data,
        num_boost_round=500,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(output_path))

    summary = {
        "total_candidates": len(df),
        "total_groups": len(group_sizes),
        "positive_rate": float(y.mean()),
        "model_path": str(output_path),
    }
    return summary
