"""Multiclass gate classifier training (§8.3).

Trains a LightGBM multiclass classifier on ``queries_gate`` data.
All query types participate (OK, UNSAT, BROAD, AMBIG).
Optimizes cross-entropy.
"""

from __future__ import annotations


def train_gate() -> None:
    """Train the gate classifier."""
    raise NotImplementedError
