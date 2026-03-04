"""Gate classifier — LightGBM multiclass (Model 3).

Classifies (query, repo) as OK / UNSAT / BROAD / AMBIG before
committing to the ranker + cutoff pipeline.
See §3.3 of ranking-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codeplane.ranking.models import GateLabel


class Gate:
    """LightGBM multiclass classifier for query gating."""

    def __init__(self, model_path: Path) -> None:
        raise NotImplementedError

    def classify(self, features: dict[str, Any]) -> GateLabel:
        """Classify a (query, repo) pair.

        Parameters
        ----------
        features
            Gate features from ``features.extract_gate_features()``.

        Returns
        -------
        GateLabel
            OK, UNSAT, BROAD, or AMBIG.
        """
        raise NotImplementedError


def load_gate(model_path: Path | None = None) -> Gate:
    """Load the gate model from package data or an explicit path."""
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "gate.lgbm"
    return Gate(model_path)
