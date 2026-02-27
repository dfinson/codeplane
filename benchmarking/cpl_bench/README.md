# cpl-bench — EVEE Benchmarking for CodePlane

EVEE-native benchmarking suite that evaluates two aspects of CodePlane:

1. **Recon quality** — Does `recon` retrieve the right files at the right tiers?
2. **Agent efficiency & outcome** — Do CodePlane-augmented sessions outperform native ones?

Built on [EVEE](https://github.com/microsoft/evee) (`evee-ms-core`), Microsoft's
evaluation framework for AI coding tools.

## Project Structure

```
cpl_bench/
├── run.py                          # Entry point — registers components, invokes EVEE
├── pyproject.toml                  # Package metadata
├── models/
│   ├── recon.py                    # cpl-recon — calls CodePlane recon via MCP
│   └── agent_replay.py            # cpl-agent-replay — passes pre-collected traces through
├── datasets/
│   ├── recon_gt.py                 # cpl-recon-gt — loads ground-truth JSON
│   └── agent_traces.py            # cpl-agent-traces — loads *_trace.json files
├── metrics/
│   ├── retrieval.py                # cpl-retrieval — P/R/F1/noise
│   ├── tier_alignment.py           # cpl-tier-align — E/C/S tier accuracy
│   ├── agent_efficiency.py         # cpl-efficiency — turns, tokens, tool calls
│   └── agent_outcome.py            # cpl-outcome — quality scores from code review
├── preprocessing/
│   └── chatreplay_to_traces.py     # Convert VS Code chatreplay exports → trace JSON
├── experiments/
│   ├── recon_baseline.yaml         # Recon evaluation config
│   └── agent_ab.yaml               # Agent A/B comparison config
├── data/
│   ├── ground_truth.json           # 72 records (24 issues × 3 query levels)
│   └── traces/                     # Agent trace files (not committed)
└── vendor/
    └── evee_ms_core-*.whl          # Vendored EVEE wheel
```

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (deps are managed at the repo root)
- A running CodePlane daemon (for recon evaluation only)

### Running the Recon Benchmark

Start a CodePlane daemon on the target repo, then:

```bash
cd benchmarking/cpl_bench

# Point at the target repo (defaults to ~/wsl-repos/evees/evee_cpl/evee)
export CPL_BENCH_TARGET_REPO=/path/to/repo

python run.py experiments/recon_baseline.yaml
```

This calls `recon` for each of the 72 ground-truth queries and measures:

| Metric | Description |
|--------|-------------|
| Precision | Fraction of returned files that are in the ground truth |
| Recall | Fraction of ground-truth files that were returned |
| F1 | Harmonic mean of precision and recall |
| Noise ratio | Fraction of returned files not in ground truth |
| Tier alignment | Whether files land in the correct tier (E→full, C→scaffold, S→summary) |

### Running the Agent A/B Benchmark

1. **Collect traces** — Run the same issues with and without CodePlane, export chatreplay:

   ```bash
   python -m benchmarking.cpl_bench.preprocessing.chatreplay_to_traces \
       path/to/chatreplay_exports/*.json \
       --repo evee \
       --output-dir benchmarking/cpl_bench/data/traces
   ```

2. **(Optional) Score outcomes** — Add an `"outcome"` field to each trace JSON with
   manual code review scores (correctness 0-3, completeness 0-3, code_quality 0-3,
   test_quality 0-3, documentation 0-3, lint_clean 0-1, tests_pass 0-1).

3. **Run the benchmark:**

   ```bash
   cd benchmarking/cpl_bench
   python run.py experiments/agent_ab.yaml
   ```

   This compares CodePlane vs native sessions on:

   | Metric | Description |
   |--------|-------------|
   | Turns | Number of LLM round-trips |
   | Token usage | Prompt, completion, cached tokens |
   | Tool calls | CodePlane, terminal, tool_search, other |
   | Cache hit ratio | Fraction of prompt tokens served from cache |
   | Outcome score | Composite quality score (0-17) from code review |

## Components

### Models

- **`cpl-recon`** — Creates a fresh MCP session per query (avoids consecutive-recon
  guards), sends the task to CodePlane's `recon` tool, and parses the response into
  file lists with tier assignments.

- **`cpl-agent-replay`** — Pass-through model for pre-collected traces. Classifies
  tool calls (codeplane/terminal/tool_search/other), filters routing models from
  LLM events, and aggregates token counts.

### Datasets

- **`cpl-recon-gt`** — Loads `data/ground_truth.json` (72 records). Each record has
  an `issue`, `query_level` (Q1/Q2/Q3), `task` text, `gt_files`, `gt_categories`
  (E/C/S), and `difficulty`.

- **`cpl-agent-traces`** — Loads `*_trace.json` files from a directory. Each trace
  contains session metadata, event list, and optional outcome scores.

### Metrics

- **`cpl-retrieval`** — Standard IR metrics. Aggregates to avg/median/min/max F1.

- **`cpl-tier-align`** — Measures whether recon assigns the expected tier per GT
  category: E (Edit) → `scaffold`, C (Context) → `scaffold`,
  S (Supplementary) → `lite`.

- **`cpl-efficiency`** — Aggregates efficiency stats grouped by variant with
  head-to-head deltas (codeplane − native) for turns, tool calls, and tokens.

- **`cpl-outcome`** — Aggregates quality scores grouped by variant. Computes
  `delta_score` (codeplane − native) when both variants are present.

## Ground Truth Format

Each record in `data/ground_truth.json`:

```json
{
  "issue": "42",
  "query_level": "Q1",
  "task": "Fix the broken import in coordinator.py",
  "gt_files": ["src/core/coordinator.py", "src/core/base.py"],
  "gt_categories": [
    {"path": "src/core/coordinator.py", "category": "E"},
    {"path": "src/core/base.py", "category": "C"}
  ],
  "difficulty": "simple"
}
```

Categories: **E** (Edit target), **C** (Context/test), **S** (Supplementary/docs).

Query levels: **Q1** (anchored — mentions specific files/symbols), **Q2** (scoped —
domain-specific but no exact paths), **Q3** (vague — high-level description only).

## Writing Experiment Configs

Experiment YAML files follow the [EVEE config format](https://github.com/microsoft/evee).
The `mapping:` section in each metric connects model output fields and dataset fields
to the metric's `compute()` parameters:

```yaml
metrics:
  - name: cpl-retrieval
    mapping:
      returned_files: model.returned_files    # from model.infer() output
      gt_files: dataset.gt_files              # from dataset record
```

## Output

Results are written to `experiments/output/<experiment_name>/` by EVEE, including
per-record scores and aggregated metrics.
