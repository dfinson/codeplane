"""Recon ground-truth dataset — loads curated query+GT pairs from JSON.

Registered as ``@dataset("cpl-recon-gt")`` for EVEE evaluation.

Each record contains:
    issue:          GitHub issue number (str)
    query_level:    Q1 (anchored/precise), Q2 (mixed/scoped), Q3 (unanchored/open)
    task:           The query text sent to recon
    gt_files:       List of ground-truth file paths
    gt_categories:  List of {path, category} dicts (E=Edit, C=Context, S=Supp)
    difficulty:     simple | medium | complex
"""

from __future__ import annotations

import json
from pathlib import Path

from evee import dataset


@dataset("cpl-recon-gt")
class ReconGroundTruthDataset:
    """Loads recon ground-truth queries from a JSON file.

    Ground truth is maintained in ``data/ground_truth.json``
    (originally derived from ``benchmarking/evee/ReconEveeEvaluation.md``).

    Config args:
        data_path: Path to the ground_truth.json file
    """

    def __init__(self, data_path: str = "data/ground_truth.json", **kwargs: object) -> None:
        path = Path(data_path)
        if not path.exists():
            msg = f"Ground truth file not found: {path.resolve()}"
            raise FileNotFoundError(msg)

        with open(path) as f:
            self.records = json.load(f)

    def __iter__(self):
        yield from self.records

    def __len__(self) -> int:
        return len(self.records)
