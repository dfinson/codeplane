"""Object ranker — LightGBM LambdaMART model (Model 1).

Scores P(relevant | query, object) for each candidate DefFact.
See §2.1 of ranking-design.md.
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

log = structlog.get_logger(__name__)


class Ranker:
    """LambdaMART object ranker backed by a serialized LightGBM model."""

    def __init__(self, model_path: Path) -> None:
        self._model = None
        if model_path.exists():
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=str(model_path))
            log.info("ranking.ranker.loaded", path=str(model_path))
        else:
            log.warning("ranking.ranker.no_model", path=str(model_path))

    @property
    def is_available(self) -> bool:
        return self._model is not None

    def score(self, candidate_features: list[dict[str, Any]]) -> list[float]:
        """Score each candidate. Returns scores in input order."""
        if self._model is None:
            # Fallback: return embedding score as proxy
            return [f.get("emb_score", 0.0) or 0.0 for f in candidate_features]

        import numpy as np

        feature_names = self._model.feature_name()
        X = np.array([
            [f.get(name, 0) for name in feature_names]
            for f in candidate_features
        ])
        return self._model.predict(X).tolist()


def load_ranker(model_path: Path | None = None) -> Ranker:
    """Load the ranker from package data or an explicit path."""
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "ranker.lgbm"
    return Ranker(model_path)
