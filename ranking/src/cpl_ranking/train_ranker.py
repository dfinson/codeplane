"""LambdaMART ranker training (§8.1).

Trains a LightGBM LambdaMART model on ``candidates_rank`` data,
grouped by (run_id, query_id), optimizing NDCG with graded relevance.
Only OK-labeled queries participate.
"""

from __future__ import annotations


def train_ranker() -> None:
    """Train the LambdaMART object ranker."""
    raise NotImplementedError
