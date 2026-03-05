"""Orchestrate all 3 training stages.

Usage::

    python -m cpl_ranking.train_all --data-dir ranking/data --output-dir output/

Pre-requisites:
  - merge_ground_truth.py has been run once (writes merged/*.parquet)
  - merge_signals.py has been run after signal collection

Pipeline:
  0. Merge signals (re-derives labels from ground truth parquet)
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


def train_all(data_dir: Path, output_dir: Path, skip_merge: bool = False) -> None:
    """Run the full training pipeline.

    Args:
        data_dir: Root data directory containing ``{repo_id}/`` subdirs
            and ``merged/`` with pre-merged parquet files.
        output_dir: Where to write model artifacts.
        skip_merge: If True, skip signal merge (use existing parquet).
    """
    from cpl_ranking.train_cutoff import train_cutoff
    from cpl_ranking.train_gate import train_gate
    from cpl_ranking.train_ranker import train_ranker

    merged_dir = data_dir / "merged"

    # Validate pre-merged ground truth exists
    for required in ("queries.parquet", "touched_objects.parquet"):
        if not (merged_dir / required).exists():
            print(
                f"Missing {merged_dir / required} — "
                f"run merge_ground_truth.py first",
                file=sys.stderr,
            )
            sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # 0. Merge signals (re-runnable, re-derives labels)
    if not skip_merge:
        from cpl_ranking.merge_signals import merge_signals

        print("=== Merging Signals ===")
        sig_summary = merge_signals(data_dir)
        print(f"  {sig_summary['total_candidates']} candidates, "
              f"positive rate: {sig_summary['positive_rate']:.3f}")

    # Load merged data
    candidates_path = merged_dir / "candidates_rank.parquet"
    queries_path = merged_dir / "queries.parquet"
    if not candidates_path.exists():
        print(f"Missing {candidates_path}", file=sys.stderr)
        sys.exit(1)

    # 1. Gate (ships first, no dependency on ranker)
    print("\n=== Training Gate ===")
    gate_summary = train_gate(
        merged_dir=merged_dir,
        output_path=output_dir / "gate.lgbm",
    )
    print(f"  Gate: {gate_summary['total_queries']} queries, "
          f"distribution: {gate_summary['label_distribution']}")

    # 2. Ranker
    print("\n=== Training Ranker ===")
    ranker_summary = train_ranker(
        merged_dir=merged_dir,
        output_path=output_dir / "ranker.lgbm",
    )
    print(f"  Ranker: {ranker_summary['total_candidates']} candidates, "
          f"{ranker_summary['total_groups']} groups, "
          f"positive rate: {ranker_summary['positive_rate']:.3f}")

    # 3. Cutoff (depends on ranker — uses K-fold out-of-fold scoring)
    print("\n=== Training Cutoff ===")
    cutoff_summary = train_cutoff(
        merged_dir=merged_dir,
        output_path=output_dir / "cutoff.lgbm",
    )
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
    parser.add_argument("--skip-merge", action="store_true", help="Skip signal merge step")
    args = parser.parse_args()
    train_all(args.data_dir, args.output_dir, skip_merge=args.skip_merge)
