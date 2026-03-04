# cpl-ranking

Training pipeline for CodePlane's recon ranking models.

Produces three LightGBM models that ship as package data in
`src/codeplane/ranking/data/`:

| Model | File | Purpose |
|-------|------|---------|
| Ranker | `ranker.lgbm` | LambdaMART object scorer — P(touched \| query, object) |
| Cutoff | `cutoff.lgbm` | Regressor — predict how many top objects to return |
| Gate | `gate.lgbm` | Multiclass classifier — OK / UNSAT / BROAD / AMBIG |

See `docs/ranking-design.md` in the repo root for the full design.

## Project layout

```
ranking/
├── pyproject.toml
├── repos/                 # 30 repo selection docs + task definitions
├── clones/                # Cloned repos (gitignored)
├── src/cpl_ranking/
│   ├── schema.py          # §7 dataset table schemas
│   ├── collector.py       # Ground truth collection (stable, run once)
│   ├── collect_signals.py # Retrieval signal collection (re-runnable)
│   ├── train_ranker.py    # §8.1 LambdaMART training
│   ├── train_cutoff.py    # §8.2 no-leakage K-fold cutoff training
│   ├── train_gate.py      # §8.3 multiclass gate training
│   └── train_all.py       # Orchestrates all 3 training stages
└── data/                  # Generated training data (gitignored)
    └── {repo_id}/
        ├── ground_truth/  # Stable: runs, touched_objects, queries
        └── signals/       # Re-collectable: candidates_rank
```

## Workflow

1. **Collect ground truth** (once) — `collector.py` drives a coding agent
   per repo/task: solve, reflect, classify touched objects, author queries.
   Output is stable and never needs re-collection.
2. **Collect signals** (re-runnable) — `collect_signals.py` calls
   `recon_raw_signals()` for each query. Re-run whenever harvesters,
   embeddings, or scoring change.
3. **Train models** — `train_all.py` runs K-fold ranker → cutoff → gate.
4. **Deploy** — copy `*.lgbm` into `src/codeplane/ranking/data/`.
