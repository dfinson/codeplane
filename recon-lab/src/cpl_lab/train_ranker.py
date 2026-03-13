"""LambdaMART ranker training (§6.1).

Trains a LightGBM LambdaMART model on ``candidates_rank`` data,
grouped by (run_id, query_id), optimizing NDCG with graded relevance.
Only OK-labeled queries from ranker-gate repos participate.

Reads one table: ``data/merged/candidates_rank.parquet``.
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cpl_lab.schema import OK_QUERY_TYPES

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

# Columns needed from the merged parquet to produce RANKER_FEATURES + labels.
# Categorical sources are encoded into binary cols by _prepare_features.
_RANKER_LOAD_COLS = [
    "run_id", "query_id", "query_type", "repo_set", "label_relevant",
    # Numeric features (direct)
    "emb_score", "emb_rank",
    "term_match_count", "term_total_matches",
    "graph_seed_rank", "retriever_hits",
    "object_size_lines", "path_depth", "nesting_depth",
    "hub_score", "is_test",
    "has_docstring", "has_decorators", "has_return_type", "has_parent_scope",
    "query_len", "has_identifier", "has_path",
    # Categorical sources → binary encoding
    "graph_edge_type", "symbol_source", "import_direction",
    "signature_text",
]


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


def _subsample_negatives(
    df: pd.DataFrame,
    max_neg: int = 500,
    rng: np.random.RandomState | None = None,
) -> pd.DataFrame:
    """Keep all positives, sample at most *max_neg* negatives per query group."""
    if rng is None:
        rng = np.random.RandomState(42)
    group_col = df["run_id"].astype(str) + "__" + df["query_id"].astype(str)
    parts: list[pd.DataFrame] = []
    for _, grp in df.groupby(group_col, sort=False):
        pos = grp[grp["label_relevant"] > 0]
        neg = grp[grp["label_relevant"] == 0]
        if len(neg) > max_neg:
            neg = neg.sample(n=max_neg, random_state=rng)
        parts.append(pd.concat([pos, neg]))
    if not parts:
        return df.iloc[:0]
    return pd.concat(parts, ignore_index=True)


def _stream_and_subsample(
    merged_dir: Path,
    repo_sets: set[str],
    max_neg: int = 500,
) -> pd.DataFrame:
    """Stream row groups, filter + subsample, return accumulated result.

    Reads one Parquet row group at a time, keeping only columns in
    ``_RANKER_LOAD_COLS``.  Filters to *repo_sets* and OK query types,
    subsamples negatives per query group, and accumulates the small
    subsampled chunks.  Peak RAM ≈ one row group + accumulated result.
    """
    pq_path = merged_dir / "candidates_rank.parquet"
    pf = pq.ParquetFile(pq_path)

    rng = np.random.RandomState(42)
    parts: list[pd.DataFrame] = []

    for rg_idx in range(pf.metadata.num_row_groups):
        table = pf.read_row_group(rg_idx, columns=_RANKER_LOAD_COLS)
        chunk = table.to_pandas()
        del table

        # Filter to target repo set
        chunk = chunk[chunk["repo_set"].isin(repo_sets)]
        if chunk.empty:
            del chunk
            continue

        # Filter to OK query types
        chunk = chunk[chunk["query_type"].isin(OK_QUERY_TYPES)]
        if chunk.empty:
            del chunk
            continue

        # Prepare features + subsample within this chunk
        chunk = _prepare_features(chunk)
        chunk = _subsample_negatives(chunk, max_neg=max_neg, rng=rng)
        parts.append(chunk)
        del chunk

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


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
    df = _stream_and_subsample(merged_dir, repo_sets={"ranker-gate"})
    if df.empty:
        raise ValueError("No candidate data found (ranker-gate set)")

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
