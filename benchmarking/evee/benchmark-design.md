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
| 6. Export debug logs | **Export Chat with Prompts** → save `.chatreplay.json` | same |
| 7. Process logs | see [Post-run processing](#post-run-processing) below | same |
| 8. Reset repo | `git checkout main && git clean -fd && git branch -D bench/<N>-*` | same |

### Session isolation

Before each run: **Reload Window** so there is only one conversation in the session.
This keeps the debug logs scoped to a single benchmarking run.

### Markers

Every prompt begins with `START_BENCHMARKING_RUN` and instructs the agent to emit
`END_BENCHMARKING_RUN` when finished. If the agent doesn't emit the end marker,
treat the end of the debug log as the boundary (best effort).

### Post-run processing

Two scripts in `benchmarking/` process the exported chatreplay:

**Step 1 — Extract trace** (`extract_trace.py`)

```bash
python -m benchmarking.extract_trace <chatreplay.json> [--output-dir DIR]
```

- Checks for `START_BENCHMARKING_RUN` / `END_BENCHMARKING_RUN` markers
- Trims the export to only the marker window
- Auto-detects: repo (from MCP server label), issue number (from prompt text),
  model (from LLM request metadata), codeplane vs native (from tool calls)
- Saves `{name}_raw.json` (trimmed chatreplay) and `{name}_trace.json` (events)

**Step 2 — Compute metrics** (`compute_metrics.py`)

```bash
python -m benchmarking.compute_metrics <trace.json> [--output-dir DIR]
```

- Reads a `*_trace.json` and computes aggregate metrics
- Saves `{name}_result_metrics.json`

**Output naming convention:**

```
{repo}_{issue}_{model}_{codeplane|native}_{suffix}.json
```

Example: `evee_260_claude-opus-4-6-fast_codeplane_trace.json`

All outputs default to `benchmarking/evee/results/`.

---

## Benchmark 1 — Issue #260: Config flag to disable rich progress bars

**Issue:** [microsoft/evee#260](https://github.com/microsoft/evee/issues/260)
**Complexity:** Low (config field + conditional rendering, 2-4 files)

### Issue content (verbatim)

> **What would you like to be added?**
>
> Expose a configuration flag in config.yaml to disable rich-based progress bars.
> We already have the logic to suppress progress bars for MCP and AzureML runs.
> This change would make that behavior configurable via a dedicated flag.
> When disabled, the framework should avoid rendering rich progress output and
> fall back to minimal or plain logging.
>
> **Why is this needed?**
>
> rich progress bars clutter CI logs and reduce readability. In automated
> environments, structured and minimal logs are preferred. Making this
> configurable ensures consistent behavior across CI systems while preserving
> rich output for local development.

### What the agent needs to do

The progress bar logic lives in `src/evee/evaluation/progress_tracker.py` (121
lines) and is used from `src/evee/evaluation/model_evaluator.py` (853 lines).
The issue says suppression logic already exists for MCP and AzureML contexts —
the agent needs to find that pattern and generalize it to a config flag.

The agent must:

- **Add a config field** (e.g., `disable_progress_bars: bool = False`) to the
  experiment/run configuration in `src/evee/config/models.py`.
- **Wire the flag** into the progress tracker so that when enabled, rich progress
  bars are suppressed and plain logging is used instead.
- **Verify existing suppression** for MCP/AzureML still works (the new flag
  should not break those paths).
- **Write unit tests** covering: progress bars shown by default, suppressed when
  flag is set, existing MCP/AzureML suppression still works.
- **Update documentation** if config.yaml examples exist.

### Prompt to paste (verbatim)

```
START_BENCHMARKING_RUN

First, create and checkout a new local branch: bench/260-disable-progress-bars

Implement issue #260 for this repository (microsoft/evee).

The issue asks:
> Expose a configuration flag in config.yaml to disable rich-based progress bars.
> We already have the logic to suppress progress bars for MCP and AzureML runs.
> This change would make that behavior configurable via a dedicated flag.

The progress bar logic is in src/evee/evaluation/progress_tracker.py and is used
from src/evee/evaluation/model_evaluator.py. Configuration models live in
src/evee/config/models.py.

You need to:
1. Add a config field (e.g. disable_progress_bars: bool = False) to the
   configuration schema.
2. Wire the flag into the progress tracker so that when enabled, rich progress
   bars are suppressed and plain/minimal logging is used instead.
3. Ensure existing MCP/AzureML progress bar suppression still works.
4. Write unit tests: bars shown by default, suppressed when flag is set,
   existing suppression paths unaffected.
5. Update any config.yaml examples or documentation if they exist.
6. Run lint and tests to confirm everything passes.
7. Self-review all changes you made — check for correctness, edge cases, and
   style consistency.
8. Write a detailed inline PR description summarizing what was changed and why.

Definition of Done:
- [ ] A new config field exists for disabling progress bars, defaulting to False (bars shown)
- [ ] When the flag is set, rich progress output is suppressed
- [ ] Plain/minimal logging replaces rich output when suppressed
- [ ] Existing MCP and AzureML progress bar suppression still works
- [ ] Unit test: progress bars are shown by default
- [ ] Unit test: progress bars are suppressed when the flag is set
- [ ] All existing tests still pass
- [ ] Linter passes with no new warnings
- [ ] Self-review completed — no obvious bugs, edge cases handled, code style consistent
- [ ] Detailed inline PR description written summarizing the change

Do not push or create a PR. Just implement locally.
When you are completely done, say: END_BENCHMARKING_RUN
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

## Benchmark 5 — Issue #262: Support for configurable REST-based models

**Issue:** [microsoft/evee#262](https://github.com/microsoft/evee/issues/262)
**Complexity:** Medium-High (design + plumbing across config, core, and evaluator)

### Issue content (verbatim)

> **What would you like to be added?**
>
> Add support for defining REST-based models through configuration instead of
> creating custom models that just wrap REST calls. The goal is to enable easier
> integration of REST endpoints as models by specifying their configuration,
> endpoints, and expected behavior without the need for boilerplate model code.
>
> **Why is this needed?**
>
> Currently, implementing models that rely on REST APIs requires writing custom
> model classes, which mostly just forward requests and responses. Introducing a
> configuration-driven approach for REST-based models will reduce repetitive code,
> simplify onboarding of new REST-based models, and improve maintainability. This
> change will make it significantly easier to add, update, or remove such
> integrations by simply editing configuration files.

### What the agent needs to do

The agent must understand the model abstraction and design a generic REST model
that can be configured via YAML. Key files:

- `src/evee/core/base_model.py` (172 lines) — the `BaseModel` abstract class with
  the `run()` method interface that all models implement.
- `src/evee/config/models.py` (406 lines) — configuration schema where model
  definitions live.
- `src/evee/evaluation/model_evaluator.py` (853 lines) — how models are instantiated
  and invoked during evaluation.

The agent must:

- **Design a REST model config schema** that allows users to specify endpoint URL,
  HTTP method, headers, request/response mapping, and authentication.
- **Implement a `RestModel` class** extending `BaseModel` that reads from config
  and makes HTTP calls to the specified endpoint.
- **Wire it into model resolution** so that when a user configures a REST model
  in config.yaml, the framework instantiates `RestModel` automatically.
- **Handle request/response mapping** — the user needs to specify how input data
  maps to the request body and how the response maps to model output.
- **Write unit tests** with mocked HTTP calls covering: successful call, error
  handling, config validation, and request/response mapping.

### Prompt to paste (verbatim)

```
START_BENCHMARKING_RUN

First, create and checkout a new local branch: bench/262-rest-based-models

Implement issue #262 for this repository (microsoft/evee).

The issue asks:
> Add support for defining REST-based models through configuration instead of
> creating custom models that just wrap REST calls. The goal is to enable easier
> integration of REST endpoints as models by specifying their configuration,
> endpoints, and expected behavior without the need for boilerplate model code.

The model abstraction is in src/evee/core/base_model.py (BaseModel with a run()
method). Configuration lives in src/evee/config/models.py. Model evaluation is
in src/evee/evaluation/model_evaluator.py.

You need to:
1. Explore how models are defined, configured, and invoked in the codebase.
2. Design a config schema for REST-based models (endpoint URL, HTTP method,
   headers, auth, request/response mapping).
3. Implement a RestModel class extending BaseModel that reads from config and
   makes HTTP calls to the specified endpoint.
4. Wire RestModel into model resolution so the framework instantiates it
   automatically when a REST model is configured in config.yaml.
5. Handle request/response mapping — users specify how input data maps to the
   request body and how the response maps to model output.
6. Write unit tests with mocked HTTP calls: successful call, error handling,
   config validation, request/response mapping.
7. Run lint and tests to confirm everything passes.
8. Self-review all changes you made — check for correctness, edge cases, and
   style consistency.
9. Write a detailed inline PR description summarizing what was changed and why.

Definition of Done:
- [ ] A RestModel class exists extending BaseModel
- [ ] REST model config schema supports: endpoint URL, HTTP method, headers, auth, request/response mapping
- [ ] RestModel is automatically instantiated when configured in config.yaml (no custom model code needed)
- [ ] Request mapping: input data is correctly transformed into the HTTP request body
- [ ] Response mapping: HTTP response is correctly transformed into model output
- [ ] Error handling: HTTP errors, timeouts, and malformed responses are handled gracefully
- [ ] Unit test: successful REST call returns expected output
- [ ] Unit test: HTTP error is handled and reported clearly
- [ ] Unit test: config validation rejects invalid REST model configs
- [ ] Unit test: request/response mapping works correctly
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
| 1 | **#260** (progress bars) | Low | 3-5 min | config wiring, search efficiency |
| 2 | **#233** (early stop) | Medium | 5-10 min | call-graph navigation, write efficiency |
| 3 | **#108** (mocked tests) | Medium | 5-10 min | read-heavy comprehension |
| 4 | **#262** (REST models) | Medium-High | 8-12 min | design reasoning, config + abstraction |
| 5 | **#4** (caching) | High | 10-15 min | exploration depth, design reasoning |

Start with #260 for a quick sanity check, then #233 for focused feature work.

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
