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

## Workspace layout

The pipeline separates **versioned source** (in the git repo) from **mutable
workspace data** (outside the repo). All mutable data lives under a single
configurable root controlled by the `CPL_RANKING_WORKSPACE` environment
variable (default: `~/.codeplane/ranking`).

### In-repo (versioned pipeline source)

```
ranking/
├── pyproject.toml
├── clone_repos.sh             # Clone all 98 repos to workspace
├── repos/                     # 98 task definitions (30 ranker-gate + 48 cutoff + 20 eval)
│   ├── ranker-gate/           #   30 repos — training set for ranker + gate
│   ├── cutoff/                #   48 repos — training set for cutoff
│   └── eval/                  #   20 repos — held-out evaluation set
├── roles/                     # Agent role files for ground truth generation
│   ├── auditor.md             #   Pre-flight auditor (verifies task grounding)
│   ├── executor.md            #   Task executor (solves tasks, writes ground truth)
│   └── reviewer.md            #   Output reviewer (validates executor output)
├── infra/                     # Pipeline infrastructure
│   ├── gt_orchestrator.py     #   Copilot SDK orchestrator (runs all GT generation)
│   ├── merge_ground_truth.py  #   Merge per-task JSONs → single JSONL per repo
│   ├── index_all.sh           #   Local cpl init for all clones
│   └── parse_traces.py        #   Benchmarking trace parser
└── src/cpl_ranking/           # Training code
    ├── schema.py              # §7 dataset table schemas
    ├── collector.py           # Ground truth collection (stable, run once)
    ├── collect_signals.py     # Retrieval signal collection (re-runnable)
    ├── train_ranker.py        # §8.1 LambdaMART training
    ├── train_cutoff.py        # §8.2 no-leakage K-fold cutoff training
    ├── train_gate.py          # §8.3 multiclass gate training
    └── train_all.py           # Orchestrates all 3 training stages
```

### External workspace (mutable data, outside repo)

```
$CPL_RANKING_WORKSPACE/          (default: ~/.codeplane/ranking)
├── clones/                      # Cloned + codeplane-indexed repos
│   ├── ranker-gate/
│   ├── cutoff/
│   └── eval/
├── data/
│   ├── gt_state.json            # Pipeline state tracker
│   ├── {repo_id}/
│   │   ├── ground_truth/        # Per-task JSONs (intermediate)
│   │   ├── ground_truth.jsonl   # 34-line JSONL (33 tasks + non_ok_queries)
│   │   └── signals/             # Retrieval signals per query
│   ├── merged/                  # Training parquets
│   └── logs/
│       ├── sessions/            # Per-session agent transcripts
│       └── errors/              # Archived failed attempt logs
└── .venv/                       # Optional pipeline virtualenv
```

### Setup

```bash
# Initialize the workspace (one-time)
bash ranking/setup_workspace.sh

# Or with a custom location:
export CPL_RANKING_WORKSPACE=/mnt/data/ranking
bash ranking/setup_workspace.sh

# Add to .bashrc to persist:
echo 'export CPL_RANKING_WORKSPACE=~/.codeplane/ranking' >> ~/.bashrc
```

## Ground truth generation

Ground truth is generated via the **Copilot SDK orchestrator** (`gt_orchestrator.py`),
which runs local agentic sessions against the cloned repos using GitHub Copilot CLI.

### Pipeline stages (sequential, gated)

| # | Stage | Model | Sessions | What it does |
|---|-------|-------|----------|-------------|
| 1 | **Audit** | Sonnet 4.6 | 98 | Verify all 33 tasks are grounded in code, fix incorrect ones, run baseline coverage |
| 2 | **Exec N** | Sonnet 4.6 | 98 | Solve N1-N11 (narrow tasks), write ground truth JSONs |
| 3 | **Exec M** | Sonnet 4.6 | 98 | Solve M1-M11 (medium tasks), write ground truth JSONs |
| 4 | **Exec W** | Sonnet 4.6 | 98 | Solve W1-W11 (wide tasks) + non-OK queries, write JSONs |
| 5 | **Review** | Opus 4.6 | 98 | Verify all executor output, fix schema/content issues |

Each stage must complete for **all 98 repos** before the next stage begins.

### Commands

```bash
cd ranking && source .venv/bin/activate

# Run the full pipeline (auto-advances through stages)
python3 infra/gt_orchestrator.py run

# Run a specific stage only
python3 infra/gt_orchestrator.py run --stage audit

# Run one repo only
python3 infra/gt_orchestrator.py run --repo cpp-abseil --stage exec_n

# Check progress
python3 infra/gt_orchestrator.py status

# Reset failed repos for retry
python3 infra/gt_orchestrator.py retry
python3 infra/gt_orchestrator.py retry cpp-abseil

# View session logs
python3 infra/gt_orchestrator.py logs cpp-abseil audit

# Merge completed repos into JSONL (after review stage)
python3 infra/gt_orchestrator.py collect
```

### Output

Each repo produces one file: `$CPL_RANKING_WORKSPACE/data/{repo_id}/ground_truth.jsonl` — 34 lines:
- Lines 1-33: task ground truth (N1-N11, M1-M11, W1-W11)
- Line 34: non-OK queries (UNSAT, BROAD, AMBIG)

## Training workflow

1. **Ground truth** — `gt_orchestrator.py run` (once, takes ~24h for all 98 repos)
2. **Signals** — `collect_signals.py` calls `recon_raw_signals()` per query
3. **Train** — `train_all.py --data-dir $CPL_RANKING_WORKSPACE/data` runs K-fold ranker → cutoff → gate
4. **Deploy** — copy `*.lgbm` into `src/codeplane/ranking/data/`
