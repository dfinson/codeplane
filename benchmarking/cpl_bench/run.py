#!/usr/bin/env python3
"""Run EVEE benchmark — imports components and invokes evaluator.

Usage:
    cd benchmarking/cpl_bench
    python run.py experiments/recon_baseline.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path for local imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Register EVEE components (decorator registration happens on import)
import datasets  # noqa: F401  # isort: skip
import metrics  # noqa: F401  # isort: skip
import models  # noqa: F401  # isort: skip

from evee.evaluation.evaluate import main

if __name__ == "__main__":
    config = sys.argv[1] if len(sys.argv) > 1 else "experiments/recon_baseline.yaml"
    main(config, tracking_enabled=False)
