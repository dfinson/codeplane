# CodePlane A/B Benchmark Design — Evee

> **Purpose:** Run the same issue-derived task with CodePlane enabled vs disabled,
> then compare the agent debug logs to measure efficiency.
>
> **Target repo:** [microsoft/evee](https://github.com/microsoft/evee)
>
> **Ground rule — DO NOT push to evee remote or modify issue state.**

---

## Method

| Step | With CodePlane | Without CodePlane |
|------|---------------|-------------------|
| 1. Ensure `.vscode/mcp.json` | present (CodePlane entry) | renamed to `mcp.json.bak` |
| 2. Reload Window (`Ctrl+Shift+P` → Reload) | clean session | clean session |
| 3. Checkout `main`, ensure clean | `git checkout main && git clean -fd` | same |
| 4. Open a **new** Copilot Agent chat | paste prompt verbatim (single convo only) | same |
| 5. Let the agent run to completion | look for `END_BENCHMARKING_RUN` in output | same (best effort if agent doesn't emit it) |
| 6. Export debug logs | Copilot output channel → save to `results/` | same |
| 7. Reset repo | `git checkout main && git clean -fd && git branch -D bench/<N>-*` | same |

### Session isolation

Before each run: **Reload Window** so there is only one conversation in the session.
This keeps the debug logs scoped to a single benchmarking run.

### Markers

Every prompt begins with `START_BENCHMARKING_RUN` and instructs the agent to emit
`END_BENCHMARKING_RUN` when finished. If the agent doesn't emit the end marker,
treat the end of the debug log as the boundary (best effort).

---

## Benchmark 1 — Issue #233: Early stop for evaluation on error threshold

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
START_BENCHMARKING_RUN

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
6. Self-review all changes you made — check for correctness, edge cases, and
   style consistency.
7. Write a detailed inline PR description summarizing what was changed and why.

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
- [ ] Self-review completed — no obvious bugs, edge cases handled, code style consistent
- [ ] Detailed inline PR description written summarizing the change

Do not push or create a PR. Just implement locally.
When you are completely done, say: END_BENCHMARKING_RUN
```

---

## Benchmark 2 — Issue #108: Implement integration tests with mocked services

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
START_BENCHMARKING_RUN

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
8. Self-review all changes you made — check for correctness, edge cases, and
   style consistency.
9. Write a detailed inline PR description summarizing what was changed and why.

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
- [ ] Self-review completed — no obvious bugs, edge cases handled, code style consistent
- [ ] Detailed inline PR description written summarizing the change

Do not push or create a PR. Just implement locally.
When you are completely done, say: END_BENCHMARKING_RUN
```

---

## Benchmark 3 — Issue #4: Cache model inference

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
START_BENCHMARKING_RUN

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
7. Self-review all changes you made — check for correctness, edge cases, and
   style consistency.
8. Write a detailed inline PR description summarizing what was changed and why.

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
- [ ] Self-review completed — no obvious bugs, edge cases handled, code style consistent
- [ ] Detailed inline PR description written summarizing the change

Do not push or create a PR. Just implement locally.
When you are completely done, say: END_BENCHMARKING_RUN
```

---

## Recommended Run Order

| Priority | Benchmark | Complexity | Est. time/run | Best signal |
|----------|-----------|-----------|---------------|-------------|
| 1 | **#233** (early stop) | Medium | 5-10 min | call-graph navigation, write efficiency |
| 2 | **#108** (mocked tests) | Medium | 5-10 min | read-heavy comprehension |
| 3 | **#4** (caching) | High | 10-15 min | exploration depth, design reasoning |

Start with #233 for a focused feature task, then #108 for read-heavy comprehension.

---

## Checklist per run

- [ ] **Reload Window** — clean session, single conversation only
- [ ] On `main`, confirm clean: `git status && git clean -fd`
- [ ] CodePlane on/off: `.vscode/mcp.json` present vs renamed to `mcp.json.bak`
- [ ] Open **new** Agent chat, paste prompt verbatim
- [ ] Let agent complete (look for `END_BENCHMARKING_RUN`)
- [ ] Save debug logs from Copilot output channel to `results/`
- [ ] Reset: `git checkout main && git clean -fd && git branch -D bench/<N>-*`
- [ ] Repeat with CodePlane toggled

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
| 2. Checkout `main`, ensure clean | `git checkout main && git clean -fd` | same |
| 3. Open a **new** Copilot Agent chat | same prompt verbatim | same prompt verbatim |
| 4. Let the agent run to completion | (prompt tells agent to create feature branch) | same |
| 5. Extract trace | `python extract_vscode_agent_trace.py --chat-name "<fragment>"` | same (output auto-derived from chat name) |
| 6. Reset repo | `git checkout main && git clean -fd && git branch -D bench/<N>-*` | same |

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

## Benchmark 1 — Issue #233: Early stop for evaluation on error threshold

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

- [ ] On `main`, confirm clean: `git status && git clean -fd`
- [ ] CodePlane on/off: check `.vscode/mcp.json` presence
- [ ] Open **new** Agent chat, paste prompt verbatim
- [ ] Let agent complete (don't interrupt)
- [ ] Extract trace: `python tests/benchmarking/evee/extract_vscode_agent_trace.py --chat-name "..."`
- [ ] Move trace JSON to `tests/benchmarking/evee/results/`
- [ ] Reset: `git checkout main && git clean -fd && git branch -D bench/<N>-*`
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
mc_w = w.get('mcp_comparison_metrics', {})
mc_wo = wo.get('mcp_comparison_metrics', {})
for tier, keys in [
    ('tier1_core', ['native_mcp_ratio', 'session_duration_s', 'tool_calls_per_second']),
    ('tier2_convergence', ['tool_calls_per_pseudo_turn', 'calls_before_first_mcp', 'longest_native_only_streak']),
    ('tier3_cost_proxies', ['total_result_bytes', 'avg_result_bytes_per_call']),
    ('tier4_stability', ['error_calls', 'error_rate']),
]:
    for k in keys:
        wv = mc_w.get(tier, {}).get(k, 'N/A')
        wov = mc_wo.get(tier, {}).get(k, 'N/A')
        print(f'{tier}.{k:<35} {str(wv):>10} {str(wov):>10}')
" results/with_trace.json results/without_trace.json
```
