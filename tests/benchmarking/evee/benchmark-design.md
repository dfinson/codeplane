# CodePlane A/B Benchmark Design — Evee

> **Purpose:** Run the same issue-derived task with CodePlane enabled vs disabled,
> extract pseudo-traces with `extract_vscode_agent_trace.py`, and compare key metrics.
>
> **Target repo:** [microsoft/evee](https://github.com/microsoft/evee)
>
> **Ground rule — DO NOT push to evee remote or modify issue state.**

---

## Method

| Step | With CodePlane | Without CodePlane |
|------|---------------|-------------------|
| 1. Ensure `.vscode/mcp.json` | present (CodePlane entry) | renamed to `mcp.json.bak` |
| 2. Open a **new** Copilot Agent chat | same prompt verbatim | same prompt verbatim |
| 3. Let the agent run to completion | — | — |
| 4. Undo mutations | `git checkout HEAD -- .` | `git checkout HEAD -- .` |
| 5. Extract trace | `python ~/extract_vscode_agent_trace.py --chat-name "<fragment>" --out ~/bench_Nw.json` | `…--out ~/bench_Nwo.json` |

### Key comparison metrics (from trace JSON)

| Metric | Where in trace | What it shows |
|--------|---------------|---------------|
| `codeplane_share_of_all_tool_calls` | `summaries` | % of agent work routed through CodePlane |
| `native_mcp_ratio` | `mcp_comparison_metrics.tier_1_structure` | native (terminal) vs MCP balance |
| `tool_calls_per_pseudo_turn` | `mcp_comparison_metrics.tier_2_behavioral` | tool efficiency per reasoning step |
| `longest_native_only_streak` | `mcp_comparison_metrics.tier_3_adoption` | longest run without MCP (lower = better adoption) |
| `burst_1s` | `mcp_comparison_metrics.tier_2_behavioral.thrash_shape` | rapid-fire calls (potential flailing) |
| `total_result_bytes` | `summaries` | data volume pulled into context |
| `tool_calls_by_kind` | `summaries` | native / mcp / builtin breakdown |
| `inference_counts` | `run_metadata` | how many LLM inference rounds needed |

---

## Benchmark 1 — Issue #226: Set default MLflow tracking URI to sqlite

**Issue:** [microsoft/evee#226](https://github.com/microsoft/evee/issues/226)
**Complexity:** Low (2-4 files, config change + docs)

### Issue content (verbatim)

> **What would you like to be added?**
>
> Set the default MLflow tracking URI to `sqlite:///mlflow.db` instead of the
> filesystem backend (`./mlruns`), as the filesystem tracking backend will be
> deprecated in February 2026.
>
> **Why is this needed?**
>
> The filesystem backend (e.g., `./mlruns`) is widely used for local experiments
> but is being deprecated. Migrating to SQLite ensures continued compatibility,
> supports MLflow's tracing features, and prepares users for the upcoming change.

### What the agent needs to do

The evee project has an MLflow tracking backend plugin at `packages/evee-mlflow/`.
The current default tracking URI behavior is:

1. `packages/evee-mlflow/src/evee_mlflow/config.py` defines `MLflowTrackingConfig`
   with `tracking_uri: str | None = None` and `artifact_location: str | None = "./mlruns"`.
   When `tracking_uri` is `None`, MLflow falls back to its built-in default (`./mlruns` directory).

2. `packages/evee-mlflow/src/evee_mlflow/tracking.py` uses `config.tracking_uri` —
   if set, calls `mlflow.set_tracking_uri()`; if `None`, logs
   `"Using MLflow default tracking (./mlruns directory)"`.

The agent must:

- **Change the default** so that when no `tracking_uri` is explicitly configured,
  evee uses `sqlite:///mlflow.db` instead of letting MLflow fall back to `./mlruns`.
  This could be done by setting a new default value in `MLflowTrackingConfig` or
  by adding logic in the tracking backend's `__init__` to apply the sqlite default.
- **Update `packages/evee-mlflow/src/evee_mlflow/tracking.py`** to reflect the new
  default in its log messages and docstrings (references to `./mlruns` → `sqlite:///mlflow.db`).
- **Update documentation** — `docs/backends/mlflow.md` likely references the old
  default. The agent should find and update those references.
- **Update or add tests** in `packages/evee-mlflow/tests/` to verify the new default
  is applied correctly (e.g., when no `tracking_uri` is provided, the backend sets
  `sqlite:///mlflow.db`).

### Prompt to paste (verbatim)

```
First, create and checkout a new local branch: bench/226-mlflow-sqlite-default

Then implement issue #226 for this repository (microsoft/evee).

The issue asks:
> Set the default MLflow tracking URI to 'sqlite:///mlflow.db' instead of the
> filesystem backend ('./mlruns'), as the filesystem tracking backend will be
> deprecated in February 2026.

The MLflow backend plugin lives in packages/evee-mlflow/. The current behavior:
- packages/evee-mlflow/src/evee_mlflow/config.py has MLflowTrackingConfig with
  tracking_uri defaulting to None (which causes MLflow to use ./mlruns).
- packages/evee-mlflow/src/evee_mlflow/tracking.py uses that value and logs
  "Using MLflow default tracking (./mlruns directory)" when None.

You need to:
1. Change the default so that when no tracking_uri is configured, evee uses
   sqlite:///mlflow.db instead of ./mlruns.
2. Update the tracking backend code, docstrings, and log messages accordingly.
3. Update docs/backends/mlflow.md if it references the old default.
4. Add or update tests in packages/evee-mlflow/tests/ to verify the new default.
5. Run lint and tests to make sure everything passes.

Definition of Done:
- [ ] MLflowTrackingConfig uses sqlite:///mlflow.db as the default when no tracking_uri is provided
- [ ] tracking.py no longer references ./mlruns as the default in code, docstrings, or log messages
- [ ] docs/backends/mlflow.md is updated to reference the new default
- [ ] At least one test asserts that the default tracking URI is sqlite:///mlflow.db
- [ ] All existing tests in packages/evee-mlflow/tests/ still pass
- [ ] Linter passes with no new warnings

Do not push or create a PR. Just implement locally.
```

---

## Benchmark 2 — Issue #233: Early stop for evaluation on error threshold

**Issue:** [microsoft/evee#233](https://github.com/microsoft/evee/issues/233)
**Complexity:** Medium (feature implementation across 3-5 files)

### Issue content (verbatim)

> We can apply optimizations for evaluation process both for inferencing phase
> and evaluation.
>
> **Inferencing phase:**
> Early stop if we count too many errors, there's no point to run over the whole
> dataset and alert the user. I've noticed that many times, it could be due to a
> bug in the code or simply due to lack of permission to the underlying service
> being called.
>
> **Evaluation phase:**
> We can mark one or more metrics in order as target for optimization until reach
> a point where there is no significant metric improve for a set of hyper
> parameters in a specific model. Perhaps apply grid search algorithm or something
> else. For example, for RAG like use case, we may test a range of K documents
> for retrieval. This is just a suggestion to explore; it might be too complex
> to generalize.

### What the agent needs to do

Focus only on the **inferencing phase** early-stop (the evaluation-phase optimization
is explicitly marked as exploratory/complex in the issue). The core inference loop
is in `src/evee/evaluation/model_evaluator.py` (853 lines). Key areas:

- The `_run_inference_sync` and `_run_inference_async` methods iterate over dataset
  records and call models. When inference fails, they catch exceptions and log them,
  incrementing a `failed_count` (around line 684).
- `src/evee/evaluation/progress_tracker.py` (121 lines) tracks progress during runs.
- `src/evee/config/models.py` (406 lines) defines the configuration schema.
- `src/evee/execution/runner.py` (762 lines) orchestrates the full experiment run.

The agent must:

- **Add a config option** (e.g., `max_error_count` or `early_stop_error_threshold`)
  to the relevant config model so users can set the threshold.
- **Implement counting logic** in the inference loop: track consecutive (or total)
  failed inferences, and when the threshold is exceeded, abort the run early.
- **Surface a clear warning/error** to the user explaining why the run stopped early
  and how many errors occurred.
- **Write unit tests** covering: (a) early stop triggers at threshold, (b) runs
  complete normally below threshold, (c) behavior when threshold is not configured
  (disabled by default).

### Prompt to paste (verbatim)

```
First, create and checkout a new local branch: bench/233-early-stop-on-errors

Implement the inferencing-phase early stop from issue #233 for this repository
(microsoft/evee).

The issue says:
> Early stop if we count too many errors, there's no point to run over the whole
> dataset and alert the user. I've noticed that many times, it could be due to a
> bug in the code or simply due to lack of permission to the underlying service
> being called.

Focus ONLY on the inferencing phase (not the evaluation-phase optimization which
the issue marks as exploratory).

The inference loop is in src/evee/evaluation/model_evaluator.py. When inference
fails, exceptions are caught and a failed_count is incremented. Configuration
models live in src/evee/config/models.py.

You need to:
1. Add a configurable threshold (e.g. max_error_count) to the config schema so
   users can set when to stop early. It should be disabled (None) by default.
2. Implement error counting in the inference loop. When errors exceed the
   threshold, stop the run early.
3. Surface a clear warning to the user explaining why the run was stopped and
   how many errors occurred out of how many total records.
4. Write unit tests covering: threshold triggers early stop, normal completion
   below threshold, and disabled-by-default behavior.
5. Run lint and tests to confirm everything passes.

Definition of Done:
- [ ] A new config field (e.g. max_error_count) exists in the config schema, defaulting to None (disabled)
- [ ] The inference loop in model_evaluator.py tracks error count and stops early when the threshold is exceeded
- [ ] Both sync and async inference paths implement the early-stop logic
- [ ] A clear warning message is logged when early stop triggers, stating error count and total records processed
- [ ] The evaluation output/result reflects that the run was stopped early (not silently truncated)
- [ ] Unit test: inference stops after exactly N errors when threshold is set to N
- [ ] Unit test: inference completes normally when errors < threshold
- [ ] Unit test: inference runs without limit when threshold is None/not configured
- [ ] All existing tests still pass
- [ ] Linter passes with no new warnings

Do not push or create a PR. Just implement locally.
```

---

## Benchmark 3 — Issue #108: Implement integration tests with mocked services

**Issue:** [microsoft/evee#108](https://github.com/microsoft/evee/issues/108)
**Complexity:** Medium (read-heavy comprehension, then writing new tests)

### Issue content (verbatim)

> **What would you like to be added?**
>
> An integration test flow that runs Evee end to end without calling external
> services. The tests should load a real config, run a full evaluation, and
> validate outputs using deterministic mocked LLM responses.
>
> **Why is this needed?**
>
> This provides a fast, deterministic, and cost-effective way to confirm that
> Evee's end to end orchestration still works. It removes external dependencies,
> avoids Azure quota usage, and makes PR gating simpler and more reliable.
>
> **Acceptance Criteria:**
>
> - A new mocked integration test suite exists that runs the full Evee evaluation
>   flow end to end with no external network calls.
> - Mocked LLM responses are deterministic and stable across runs.
> - Tests cover the complete orchestration path: config loading, evaluation kickoff,
>   runner logic, pipeline flow, metric execution, and output artifact generation.
> - The mocked integration suite runs automatically on every PR as part of gating.
> - Any regression in Evee's end to end orchestration causes these tests to fail.
> - Documentation is updated to explain how to run the mocked tests.

### What the agent needs to do

The agent must understand Evee's full evaluation pipeline before writing tests.
The key source files to comprehend are:

- `src/evee/execution/runner.py` (762 lines) — top-level experiment orchestration
- `src/evee/evaluation/model_evaluator.py` (853 lines) — per-model inference + metrics
- `src/evee/config/models.py` (406 lines) — config schema
- `src/evee/datasets/` — dataset loading (CSV, JSONL)
- `src/evee/core/` — base classes for models, metrics, datasets

Existing integration test patterns live in `tests/evee/integration/`:
- `helpers.py` (163 lines) — shared test utilities
- `test_example_evaluate_locally_core.py` (30 lines) — minimal example
- `test_e2e_new_project_workflow.py` (181 lines) — CLI-based e2e test

The agent must:

- **Read and understand** the orchestration pipeline (config → runner → evaluator →
  model → metrics → output)
- **Create a new integration test file** (e.g., `tests/evee/integration/test_mocked_e2e.py`)
  that sets up a mock model with deterministic responses, a small dataset, and real metrics
- **Wire it through the full pipeline**: load config → create runner → run evaluation →
  validate output artifacts exist and contain expected values
- **Ensure no external calls** — all LLM/service interactions must be mocked
- **Run the tests** to verify they pass

### Prompt to paste (verbatim)

```
First, create and checkout a new local branch: bench/108-mocked-integration-tests

Implement issue #108 for this repository (microsoft/evee).

The issue asks for:
> An integration test flow that runs Evee end to end without calling external
> services. The tests should load a real config, run a full evaluation, and
> validate outputs using deterministic mocked LLM responses.

Acceptance criteria:
- A new mocked integration test suite that runs the full evaluation flow e2e
  with no external network calls
- Mocked LLM responses are deterministic and stable across runs
- Tests cover: config loading, evaluation kickoff, runner logic, pipeline flow,
  metric execution, output artifact generation
- Any regression in orchestration causes these tests to fail

The evaluation pipeline flows: config → runner (src/evee/execution/runner.py) →
model_evaluator (src/evee/evaluation/model_evaluator.py) → model inference →
metrics → output. Config models are in src/evee/config/models.py. Base classes
in src/evee/core/. Existing integration patterns in tests/evee/integration/.

You need to:
1. Understand the full evaluation pipeline by reading the source files above.
2. Create a new test file (e.g., tests/evee/integration/test_mocked_e2e.py).
3. Implement a mock model with deterministic responses and a small inline dataset.
4. Wire it through the real pipeline: config loading → runner → evaluator → metrics.
5. Assert that output artifacts are generated and contain the expected values.
6. Ensure zero external network calls — mock everything.
7. Run the tests to verify they pass.

Definition of Done:
- [ ] A new test file exists at tests/evee/integration/test_mocked_e2e.py (or similar)
- [ ] The test uses a mock model that returns deterministic, hardcoded responses
- [ ] The test uses a small inline or fixture-based dataset (not fetched from a remote)
- [ ] The full pipeline executes: config loading → runner → model_evaluator → inference → metrics → output
- [ ] At least one metric is computed and its value is asserted (not just "no exception")
- [ ] Output artifacts (results CSV/JSON) are generated and their contents are validated
- [ ] Zero external network calls — no LLM API, no Azure, no MLflow remote server
- [ ] The test passes when run with: pytest tests/evee/integration/test_mocked_e2e.py -v
- [ ] All existing tests still pass
- [ ] Linter passes with no new warnings

Do not push or create a PR. Just implement locally.
```

---

## Benchmark 4 — Issue #4: Cache model inference

**Issue:** [microsoft/evee#4](https://github.com/microsoft/evee/issues/4)
**Complexity:** High (design-heavy, touches core abstractions)

### Issue content (verbatim)

> As a researcher I would like to configure evee to enable caching my
> deterministic models results, so I'll be able to save costs and time when
> rerunning model evaluation when adding more models, metrics or simply
> developing those.

### What the agent needs to do

This is the most open-ended benchmark. The agent must first explore the codebase
to understand how models work before deciding on an approach. Key files:

- `src/evee/core/base_model.py` (172 lines) — the `BaseModel` abstract class that
  all user models extend. Defines the `run()` method interface.
- `src/evee/evaluation/model_evaluator.py` (853 lines) — calls model inference in
  `_run_inference_sync` / `_run_inference_async`, wraps results in `InferenceOutput`.
- `src/evee/config/models.py` (406 lines) — config schema where a caching option
  would need to live.
- `src/evee/core/models/inference_output.py` — the output dataclass.

The agent must decide:
- **Where to add caching**: as a decorator on `BaseModel.run()`? As a wrapper in
  the evaluator? As a standalone cache module?
- **Cache key design**: likely `(model_name, hash(input_data))` — must be deterministic.
- **Config integration**: add an `enable_cache: bool` (or `cache` section) to config
  so users can opt in.
- **Invalidation**: provide a way to clear the cache (CLI command, config flag, or
  just file deletion if file-backed).
- **Tests**: unit tests for cache hit/miss, invalidation, and opt-in behavior.

### Prompt to paste (verbatim)

```
First, create and checkout a new local branch: bench/4-cache-model-inference

Implement issue #4 for this repository (microsoft/evee).

The issue asks:
> As a researcher I would like to configure evee to enable caching my
> deterministic models results, so I'll be able to save costs and time when
> rerunning model evaluation when adding more models, metrics or simply
> developing those.

The model abstraction is in src/evee/core/base_model.py (BaseModel with a run()
method). Model inference is called from src/evee/evaluation/model_evaluator.py.
Configuration lives in src/evee/config/models.py.

You need to:
1. Explore the codebase to understand how models are invoked and how results
   flow through the pipeline.
2. Design a caching layer for deterministic model inference results. Consider:
   - Where it fits (decorator, evaluator wrapper, standalone module)
   - Cache key design (model name + input hash)
   - Storage (file-backed, in-memory, or configurable)
3. Add a config option so users can opt in to caching (disabled by default).
4. Implement the caching logic with support for cache invalidation.
5. Write unit tests for: cache hit, cache miss, invalidation, and opt-in behavior.
6. Run lint and tests to confirm everything passes.

Definition of Done:
- [ ] A cache module or class exists (e.g., src/evee/core/cache.py or similar)
- [ ] Caching is opt-in via a config field (disabled by default, no behavior change for existing users)
- [ ] Cache keys are deterministic: same model + same input always produces the same key
- [ ] On cache hit, the model's run() method is NOT called (verified by test)
- [ ] On cache miss, the model runs normally and the result is stored for next time
- [ ] A cache invalidation mechanism exists (e.g., clear_cache() method, config flag, or file deletion)
- [ ] Unit test: cache hit returns stored result without calling the model
- [ ] Unit test: cache miss calls the model and stores the result
- [ ] Unit test: cache invalidation causes a subsequent call to re-run the model
- [ ] Unit test: caching is not active when the config option is not set
- [ ] All existing tests still pass
- [ ] Linter passes with no new warnings

Do not push or create a PR. Just implement locally.
```

---

## Recommended Run Order

| Priority | Benchmark | Complexity | Est. time/run | Best signal |
|----------|-----------|-----------|---------------|-------------|
| 1 | **#226** (MLflow URI) | Low | 3-5 min | search/read efficiency |
| 2 | **#233** (early stop) | Medium | 5-10 min | call-graph navigation, write efficiency |
| 3 | **#108** (mocked tests) | Medium | 5-10 min | read-heavy comprehension |
| 4 | **#4** (caching) | High | 10-15 min | exploration depth, design reasoning |

Start with #226 for a quick sanity check, then #233 for a meatier comparison.

---

## Checklist per run

- [ ] Confirm branch is clean: `git status`
- [ ] CodePlane on/off: check `.vscode/mcp.json` presence
- [ ] Open **new** Agent chat, paste prompt verbatim
- [ ] Let agent complete (don't interrupt)
- [ ] Extract trace: `python ~/extract_vscode_agent_trace.py --chat-name "..." --out ~/bench_<N>_<with|without>.json`
- [ ] Reset: `git checkout HEAD -- .`
- [ ] Repeat with CodePlane toggled

---

## Analysis

After collecting trace pairs, compare with:
```bash
python3 -c "
import json, sys
w = json.load(open(sys.argv[1]))
wo = json.load(open(sys.argv[2]))
print(f\"{'Metric':<45} {'With CP':>10} {'Without':>10}\")
print('-'*67)
for k in ['codeplane_tool_calls_total','codeplane_share_of_all_tool_calls']:
    print(f'{k:<45} {w[\"summaries\"][k]:>10} {wo[\"summaries\"][k]:>10}')
for k in ['native_mcp_ratio','tool_calls_per_pseudo_turn','longest_native_only_streak']:
    wv = w['mcp_comparison_metrics'].get('tier_1_structure',{}).get(k) or w['mcp_comparison_metrics'].get('tier_2_behavioral',{}).get(k) or w['mcp_comparison_metrics'].get('tier_3_adoption',{}).get(k)
    wov = wo['mcp_comparison_metrics'].get('tier_1_structure',{}).get(k) or wo['mcp_comparison_metrics'].get('tier_2_behavioral',{}).get(k) or wo['mcp_comparison_metrics'].get('tier_3_adoption',{}).get(k)
    print(f'{k:<45} {str(wv):>10} {str(wov):>10}')
" bench_1_with.json bench_1_without.json
```
