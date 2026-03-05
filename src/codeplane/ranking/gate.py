"""Gate classifier — LightGBM multiclass (Model 3).

Classifies (query, repo) as OK / UNSAT / BROAD / AMBIG before
committing to the ranker + cutoff pipeline.
See §2.3 of ranking-design.md.
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

from codeplane.ranking.models import GateLabel

log = structlog.get_logger(__name__)

_LABEL_MAP = {0: GateLabel.OK, 1: GateLabel.UNSAT, 2: GateLabel.BROAD, 3: GateLabel.AMBIG}


class Gate:
    """LightGBM multiclass classifier for query gating."""

    def __init__(self, model_path: Path) -> None:
        self._model = None
        if model_path.exists():
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=str(model_path))
            log.info("ranking.gate.loaded", path=str(model_path))
        else:
            log.warning("ranking.gate.no_model", path=str(model_path))

    @property
    def is_available(self) -> bool:
        return self._model is not None

    def classify(self, features: dict[str, Any]) -> GateLabel:
        """Classify a (query, repo) pair."""
        if self._model is None:
            return GateLabel.OK  # fallback: assume OK

        import numpy as np

        feature_names = self._model.feature_name()
        X = np.array([[features.get(name, 0) for name in feature_names]])
        probs = self._model.predict(X)[0]
        class_idx = int(np.argmax(probs))
        return _LABEL_MAP.get(class_idx, GateLabel.OK)


def load_gate(model_path: Path | None = None) -> Gate:
    """Load the gate model from package data or an explicit path."""
    if model_path is None:
        model_path = Path(__file__).parent / "data" / "gate.lgbm"
    return Gate(model_path)
