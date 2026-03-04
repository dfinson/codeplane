"""Object ranker — LightGBM LambdaMART model (Model 1).

Scores P(touched | query, object) for each candidate DefFact.
See §3.1 of ranking-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class Ranker:
    """LambdaMART object ranker backed by a serialized LightGBM model."""

    def __init__(self, model_path: Path) -> None:
        raise NotImplementedError

    def score(self, candidate_features: list[dict[str, Any]]) -> list[float]:
        """Score each candidate.  Returns scores in input order."""
        raise NotImplementedError


def load_ranker(model_path: Path | None = None) -> Ranker:
    """Load the ranker from package data or an explicit path.

    Parameters
    ----------
    model_path
        Path to ``ranker.lgbm``.  Defaults to the bundled package-data
        artifact at ``src/codeplane/ranking/data/ranker.lgbm``.
    """
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "ranker.lgbm"
    return Ranker(model_path)
