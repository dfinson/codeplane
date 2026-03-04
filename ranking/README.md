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
├── src/cpl_ranking/
│   ├── schema.py          # §7 dataset table schemas
│   ├── collector.py       # §5.3 data collection orchestrator
│   ├── train_ranker.py    # §8.1 LambdaMART training
│   ├── train_cutoff.py    # §8.2 no-leakage K-fold cutoff training
│   ├── train_gate.py      # §8.3 multiclass gate training
│   └── train_all.py       # Orchestrates all 3 training stages
└── data/                  # Generated training data (gitignored)
    └── {repo_id}/
```

## Workflow

1. **Collect data** — `collector.py` drives a coding agent per repo/task,
   then calls `recon_raw_signals()` to harvest retrieval features.
2. **Train models** — `train_all.py` runs K-fold ranker → cutoff → gate.
3. **Deploy** — copy `*.lgbm` into `src/codeplane/ranking/data/`.
