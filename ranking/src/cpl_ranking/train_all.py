"""Orchestrate all 3 training stages.

Usage::

    python -m cpl_ranking.train_all --data-dir ranking/data --output-dir output/

Pipeline:
  1. Train gate (multiclass, all query types — ships first)
  2. Train ranker (LambdaMART, OK queries only)
  3. Train cutoff (K-fold, out-of-fold scoring — depends on ranker)
  4. Write ranker.lgbm, cutoff.lgbm, gate.lgbm to output dir
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def train_all(data_dir: Path, output_dir: Path) -> None:
    """Run the full training pipeline.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs.
        output_dir: Where to write model artifacts.
    """
    from cpl_ranking.train_cutoff import train_cutoff
    from cpl_ranking.train_gate import train_gate
    from cpl_ranking.train_ranker import train_ranker

    # Find all repo data directories
    repo_dirs = sorted([
        d for d in data_dir.iterdir()
        if d.is_dir() and (d / "ground_truth" / "queries.jsonl").exists()
    ])

    if not repo_dirs:
        print(f"No repo data found in {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(repo_dirs)} repos with ground truth data")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Gate (ships first, no dependency on ranker)
    print("\n=== Training Gate ===")
    gate_summary = train_gate(repo_dirs, output_dir / "gate.lgbm")
    print(f"  Gate: {gate_summary['total_queries']} queries, "
          f"distribution: {gate_summary['label_distribution']}")

    # 2. Ranker
    print("\n=== Training Ranker ===")
    ranker_summary = train_ranker(repo_dirs, output_dir / "ranker.lgbm")
    print(f"  Ranker: {ranker_summary['total_candidates']} candidates, "
          f"{ranker_summary['total_groups']} groups, "
          f"positive rate: {ranker_summary['positive_rate']:.3f}")

    # 3. Cutoff (depends on ranker — uses K-fold out-of-fold scoring)
    print("\n=== Training Cutoff ===")
    cutoff_summary = train_cutoff(repo_dirs, output_dir / "cutoff.lgbm")
    print(f"  Cutoff: {cutoff_summary['cutoff_rows']} rows, "
          f"N* mean: {cutoff_summary['n_star_mean']:.1f} ± {cutoff_summary['n_star_std']:.1f}")

    # Write combined summary
    summary = {
        "gate": gate_summary,
        "ranker": ranker_summary,
        "cutoff": cutoff_summary,
    }
    (output_dir / "training_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nAll models saved to {output_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train all ranking models")
    parser.add_argument("--data-dir", type=Path, required=True, help="Root data directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory for models")
    args = parser.parse_args()
    train_all(args.data_dir, args.output_dir)
