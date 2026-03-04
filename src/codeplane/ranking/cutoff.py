"""Cutoff predictor — LightGBM regressor (Model 2).

Predicts N(q): how many top-ranked objects to return, maximizing F1
against the ground-truth touched set subject to a rendering budget.
See §3.2 of ranking-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class Cutoff:
    """LightGBM regressor that predicts the optimal cutoff N."""

    def __init__(self, model_path: Path) -> None:
        raise NotImplementedError

    def predict(self, features: dict[str, Any]) -> int:
        """Predict cutoff N for a single query.

        Parameters
        ----------
        features
            Query-level cutoff features from
            ``features.extract_cutoff_features()``.

        Returns
        -------
        int
            Predicted number of top candidates to return.
        """
        raise NotImplementedError


def load_cutoff(model_path: Path | None = None) -> Cutoff:
    """Load the cutoff model from package data or an explicit path."""
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "cutoff.lgbm"
    return Cutoff(model_path)
