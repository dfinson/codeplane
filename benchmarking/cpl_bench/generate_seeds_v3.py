"""Generate v3 enhanced seeds by filtering v2 candidates with embedding similarity.

v2 was mined programmatically from map_repo — high recall but too many candidates
(avg 22 pinned_paths vs v1's 3). This script uses CodePlane's file embedding index
to cosine-rank v2 candidates against each issue's task description, keeping only
the top-K most semantically relevant.

Usage:
    cd benchmarking/cpl_bench
    python generate_seeds_v3.py

Requires:
    - CodePlane index built on the evee repo (`.codeplane/file_embedding/`)
    - ground_truth.json (for task texts)
    - enhanced_seeds_v2.json (candidate pool)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

# ── Config ───────────────────────────────────────────────────────────

MAX_PINS = 4  # v1 avg = 3.1, cap at 4
MAX_SEEDS = 5  # v1 avg = 2.8, cap at 5
EVEE_REPO = os.environ.get(
    "CPL_BENCH_TARGET_REPO",
    os.path.expanduser("~/wsl-repos/evees/evee_cpl/evee"),
)
V2_PATH = Path(__file__).parent / "data" / "enhanced_seeds_v2.json"
GT_PATH = Path(__file__).parent / "data" / "ground_truth.json"
OUT_PATH = Path(__file__).parent / "data" / "enhanced_seeds_v3.json"


def _load_embedding_index(index_path: Path) -> object:
    """Load FileEmbeddingIndex without importing full codeplane (avoids sqlmodel dep)."""
    import importlib.util

    # Direct module load to skip __init__.py import chains
    fe_path = Path(__file__).resolve().parents[2] / "src" / "codeplane" / "index" / "_internal" / "indexing" / "file_embedding.py"
    spec = importlib.util.spec_from_file_location("file_embedding", fe_path)
    if spec is None or spec.loader is None:
        print("ERROR: Could not find file_embedding.py at", fe_path)
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    idx = mod.FileEmbeddingIndex(index_path)
    if not idx.load():
        print("ERROR: Could not load file embedding index from", index_path)
        sys.exit(1)
    return idx


def main() -> None:
    # ── 1. Load file embedding index ──────────────────────────────
    index_path = Path(EVEE_REPO) / ".codeplane"
    idx = _load_embedding_index(index_path)

    file_count = len(idx._path_to_idx)
    print(f"Loaded embedding index: {file_count} files, {len(idx._paths)} chunks")

    # ── 2. Load inputs ────────────────────────────────────────────
    with open(V2_PATH) as f:
        v2 = json.load(f)

    with open(GT_PATH) as f:
        gt_entries = json.load(f)

    # Build issue → task mapping (use Q1 — most detailed task description)
    issue_tasks: dict[str, str] = {}
    for entry in gt_entries:
        issue = str(entry["issue"])
        ql = entry.get("query_level", "")
        # Prefer Q1 (most detailed), but take any if Q1 not seen yet
        if issue not in issue_tasks or ql == "Q1":
            issue_tasks[issue] = entry["task"]

    # ── 3. Rank and filter ────────────────────────────────────────
    v3: dict[str, dict] = {
        "_comment": (
            "V3: Embedding-filtered from v2 candidates. Each issue's task is "
            "embedded and v2's pinned_paths are cosine-ranked against it. "
            f"Top-{MAX_PINS} paths and top-{MAX_SEEDS} seeds kept."
        ),
    }

    # Pre-compute: get full similarity rankings for all issue tasks
    # so we can also use them to filter seeds
    for issue in sorted(v2.keys(), key=lambda k: int(k) if k.isdigit() else 0):
        if issue.startswith("_"):
            continue

        v2_entry = v2[issue]
        v2_seeds: list[str] = v2_entry.get("seeds", [])
        v2_pins: list[str] = v2_entry.get("pinned_paths", [])

        task = issue_tasks.get(issue)
        if not task:
            print(f"  Issue {issue}: no task text found, copying v2 as-is")
            v3[issue] = v2_entry
            continue

        # Query the embedding index with the task text
        all_ranked = idx.query(task, top_k=500)
        path_sim: dict[str, float] = dict(all_ranked)

        # ── Filter pinned_paths ───────────────────────────────────
        # Score each v2 pin by its embedding similarity to the task
        pin_scores: list[tuple[str, float]] = []
        for pin in v2_pins:
            sim = path_sim.get(pin, 0.0)
            pin_scores.append((pin, sim))

        # Sort by similarity descending, take top-K
        pin_scores.sort(key=lambda x: -x[1])
        filtered_pins = [p for p, _s in pin_scores[:MAX_PINS]]

        # ── Filter seeds ──────────────────────────────────────────
        # Strategy: for each seed, check if any file containing that seed
        # name (as a substring) appears in the top-50 ranked files.
        # Seeds appearing in higher-ranked files are more relevant.
        top_paths = [p for p, _s in all_ranked[:50]]
        seed_scores: list[tuple[str, float]] = []
        for seed in v2_seeds:
            seed_lower = seed.lower()
            # Find best-ranked file that plausibly contains this symbol
            best_rank = 999
            for rank, p in enumerate(top_paths):
                # Check if seed name appears in the file path
                # (e.g., "ModelEvaluator" → "model_evaluator.py")
                path_parts = p.lower().replace("_", "").replace("-", "")
                seed_normalized = seed_lower.replace("_", "")
                if seed_normalized in path_parts:
                    best_rank = rank
                    break
            # Also use direct embedding similarity of the seed name
            seed_sim = _embed_seed_sim(idx, seed, task)
            # Combined score: file rank bonus + embedding similarity
            rank_score = max(0.0, 1.0 - best_rank / 50.0) if best_rank < 999 else 0.0
            combined = 0.4 * rank_score + 0.6 * seed_sim
            seed_scores.append((seed, combined))

        seed_scores.sort(key=lambda x: -x[1])
        filtered_seeds = [s for s, _sc in seed_scores[:MAX_SEEDS]]

        v3[issue] = {
            "seeds": filtered_seeds,
            "pinned_paths": filtered_pins,
        }

        print(
            f"  Issue {issue}: "
            f"{len(v2_seeds)}→{len(filtered_seeds)} seeds, "
            f"{len(v2_pins)}→{len(filtered_pins)} pins"
        )

    # ── 4. Write output ───────────────────────────────────────────
    with open(OUT_PATH, "w") as f:
        json.dump(v3, f, indent=4)
    print(f"\nWrote {OUT_PATH} ({len(v3) - 1} issues)")  # -1 for _comment


def _embed_seed_sim(idx: object, seed: str, task: str) -> float:
    """Compute cosine similarity between a seed symbol name and the task.

    Embeds the seed as a short symbol-like query (e.g., "class ModelEvaluator")
    and returns the cosine similarity with the task embedding.
    """
    # Use the index's internal embedding method
    idx._ensure_model()  # type: ignore[attr-defined]

    # Embed seed as a symbol reference
    seed_text = f"{seed}"
    seed_vec = idx._embed_single(seed_text)  # type: ignore[attr-defined]
    task_vec = idx._embed_single(task)  # type: ignore[attr-defined]

    # Cosine similarity (both are L2-normalized)
    sim = float(np.dot(seed_vec, task_vec))
    return max(0.0, sim)


if __name__ == "__main__":
    main()
