"""Orchestrate all 3 training stages.

Usage::

    python -m cpl_ranking.train_all --data-dir ranking/data --output-dir output/

Pipeline:
  1. Train ranker (LambdaMART, OK queries only)
  2. Train cutoff (K-fold, out-of-fold scoring)
  3. Train gate (multiclass, all query types)
  4. Write ranker.lgbm, cutoff.lgbm, gate.lgbm to output dir
"""

from __future__ import annotations


def train_all() -> None:
    """Run the full training pipeline."""
    raise NotImplementedError


if __name__ == "__main__":
    train_all()
