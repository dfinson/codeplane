"""No-leakage K-fold cutoff training (§8.2).

Trains a LightGBM regressor to predict N(q) — how many top-ranked
objects to return.  Uses out-of-fold ranker scores to avoid label
leakage.

Pipeline:
  1. K-fold split across tasks/runs.
  2. Per fold: train ranker on K-1, score held-out fold.
  3. Per held-out query: compute N* = argmax_N F1(top-N, ground truth).
  4. Train cutoff regressor on aggregated out-of-fold data.
"""

from __future__ import annotations


def train_cutoff() -> None:
    """Train the cutoff regressor with no-leakage K-fold."""
    raise NotImplementedError
