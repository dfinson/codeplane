"""Ranking ground-truth dataset — def-level ground truth for ranking evaluation.

Registered as ``@dataset("cpl-ranking-gt")`` for EVEE evaluation.

Each record is a ``(run_id, query_id)`` pair with ground-truth
touched objects and gate label.  Loaded from the training pipeline's
output tables (§7 of ranking-design.md).
"""

from __future__ import annotations

import json
from pathlib import Path

from evee import dataset


@dataset("cpl-ranking-gt")
class RankingGroundTruthDataset:
    """Loads def-level ranking ground truth for EVEE evaluation.

    Config args:
        data_dir: Path to ranking ground truth directory
    """

    def __init__(self, data_dir: str = "data/ranking_ground_truth", **kwargs: object) -> None:
        path = Path(data_dir)
        if not path.exists():
            msg = f"Ranking ground truth directory not found: {path.resolve()}"
            raise FileNotFoundError(msg)

        self.records: list[dict] = []
        for gt_file in sorted(path.glob("**/*.json")):
            with open(gt_file) as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.records.extend(data)
                else:
                    self.records.append(data)

    def __iter__(self):
        yield from self.records

    def __len__(self) -> int:
        return len(self.records)
