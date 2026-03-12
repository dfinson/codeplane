#!/usr/bin/env python3
"""Run EVEE evaluation — imports components and invokes evaluator.

Usage (standalone):
    cd recon-lab/src/cpl_lab/eval
    python run.py experiments/eval_pipeline.yaml

Or via the CLI:
    cpl-lab eval experiments/eval_pipeline.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure eval package root is importable
_pkg_root = str(Path(__file__).resolve().parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

# Register EVEE components (decorator registration happens on import)
from cpl_lab.eval import datasets  # noqa: F401  # isort: skip
from cpl_lab.eval import metrics  # noqa: F401  # isort: skip
from cpl_lab.eval import models  # noqa: F401  # isort: skip

from evee.evaluation.evaluate import main


def run(config: str | None = None, *, tracking_enabled: bool = False) -> None:
    """Entry point callable from cpl-lab CLI."""
    if config is None:
        config = str(Path(__file__).resolve().parent / "experiments" / "eval_pipeline.yaml")
    main(config, tracking_enabled=tracking_enabled)


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else None
    run(cfg)
