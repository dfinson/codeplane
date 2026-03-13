"""Eval-set ground truth dataset — loads GT from held-out eval repos.

Registered as ``@dataset("cpl-eval-gt")`` for EVEE evaluation.

Each record is a (repo_id, task_id, query) triple with ground-truth
touched defs and gate label.  Loaded from per-repo ground_truth.jsonl
files produced by the annotation pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from evee import dataset

from cpl_lab.clone import REPO_MANIFEST


def _def_key(d: dict) -> str:
    """Canonical candidate key: ``path:kind:name:start_line``."""
    return f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"


@dataset("cpl-eval-gt")
class EvalGroundTruthDataset:
    """Loads ground truth for the held-out eval repo set.

    Args:
        data_dir: Root data directory (default ``~/.cpl-lab/data``).
    """

    def __init__(self, data_dir: str = "~/.cpl-lab/data", **kwargs: object) -> None:
        data_root = Path(data_dir).expanduser()
        eval_repos = [
            rid for rid, info in REPO_MANIFEST.items() if info["set"] == "eval"
        ]

        self.records: list[dict] = []
        for repo_id in sorted(eval_repos):
            gt_file = data_root / repo_id / "ground_truth.jsonl"
            if not gt_file.exists():
                continue
            for line in gt_file.read_text().splitlines():
                if not line.strip():
                    continue
                task = json.loads(line)
                tid = task.get("task_id", "")
                gt_edited = [_def_key(d) for d in task.get("minimum_sufficient_defs", [])]
                gt_read = [_def_key(d) for d in task.get("thrash_preventing_defs", [])]

                # Derive gate label: OK if there are relevant defs, else the
                # task can optionally declare a gate_label override.
                label_gate = task.get("gate_label", "OK" if gt_edited else "UNSAT")

                for qi, q in enumerate(task.get("queries", [])):
                    self.records.append({
                        "repo_id": repo_id,
                        "task_id": tid,
                        "query_id": f"{tid}/Q{qi}",
                        "query_text": q["query_text"],
                        "query_type": q.get("query_type", "UNKNOWN"),
                        "seeds": q.get("seeds", []),
                        "pins": q.get("pins", []),
                        "gt_edited": gt_edited,
                        "gt_read_necessary": gt_read,
                        "label_gate": label_gate,
                    })

        if not self.records:
            msg = (
                f"No ground truth found for eval repos in {data_root}. "
                "Run ground-truth annotation first."
            )
            raise FileNotFoundError(msg)

    def __iter__(self):
        yield from self.records

    def __len__(self) -> int:
        return len(self.records)
