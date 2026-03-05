"""Cutoff predictor — LightGBM regressor (Model 2).

Predicts N(q): how many top-ranked objects to return.
See §2.2 of ranking-design.md.
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

log = structlog.get_logger(__name__)

_DEFAULT_N = 20


class Cutoff:
    """LightGBM regressor that predicts the optimal cutoff N."""

    def __init__(self, model_path: Path) -> None:
        self._model = None
        if model_path.exists():
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=str(model_path))
            log.info("ranking.cutoff.loaded", path=str(model_path))
        else:
            log.warning("ranking.cutoff.no_model", path=str(model_path))

    @property
    def is_available(self) -> bool:
        return self._model is not None

    def predict(self, features: dict[str, Any]) -> int:
        """Predict cutoff N for a single query."""
        if self._model is None:
            return _DEFAULT_N

        import numpy as np

        feature_names = self._model.feature_name()
        X = np.array([[features.get(name, 0) for name in feature_names]])
        pred = self._model.predict(X)[0]
        return max(1, int(round(pred)))


def load_cutoff(model_path: Path | None = None) -> Cutoff:
    """Load the cutoff model from package data or an explicit path."""
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "cutoff.lgbm"
    return Cutoff(model_path)
