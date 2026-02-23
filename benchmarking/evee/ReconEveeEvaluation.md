# Recon Evaluation Benchmark — Evee

## Purpose

Validate Recon's retrieval quality against the [microsoft/evee](https://github.com/microsoft/evee) open backlog.
Each issue is a real implementation task with a manually curated ground truth file set
(established via iterative context gathering: grep, semantic search, file reads).

**Scope**: 24 issues × 3 query levels = 72 queries.
**Results**: Versioned JSON files in `benchmarking/evee/results/`.

## Query Rubric

Queries vary on two orthogonal dimensions.

### Anchoring — code specificity

| Level | Definition | Example |
|-------|-----------|---------|
| **Anchored** | Names specific files, functions, classes | "`_infer_record` in `model_evaluator.py`" |
| **Mixed** | References modules or architectural concepts | "the evaluation pipeline", "config models" |
| **Unanchored** | No code references | "I want to add caching" |

### Detail — task specificity

| Level | Definition | Example |
|-------|-----------|---------|
| **Precise** | Step-by-step, fields/methods named | "add `cache_enabled` to `ModelVariantConfig`" |
| **Scoped** | Clear goal with approach hints | "add caching to the evaluation pipeline" |
| **Open** | Intent or question only | "how can I avoid re-running inference?" |

### Standard Levels

Each issue defines three queries along the anchoring × detail diagonal:

| Query | Anchoring | Detail | Simulates |
|-------|-----------|--------|-----------|
| **Q1** | Anchored | Precise | Agent post-exploration |
| **Q2** | Mixed | Scoped | Developer who knows the codebase |
| **Q3** | Unanchored | Open | Cold start |

## Metrics

Retrieval quality only. Latency and token counts are tracked separately during AB testing.

### Retrieval (per query)

| Metric | Formula |
|--------|---------|
| Precision | `\|returned ∩ GT\| / \|returned\|` |
| Recall | `\|returned ∩ GT\| / \|GT\|` |
| F1 | `2·P·R / (P+R)` |
| Edit Recall | `\|returned ∩ GT_edit\| / \|GT_edit\|` |
| Noise Ratio | `\|returned − GT\| / \|returned\|` |

### Bucket Alignment (per query)

Recon assigns files to buckets (`edit_target`, `context`, `supplementary`).
Ground truth categorizes as Edit (E), Context/Test (C), Supp/Docs (S).
Alignment measures whether Recon's bucket matches the GT category.

| Metric | Formula |
|--------|---------|
| Edit → edit_target | `\|edit_target ∩ GT_E\| / \|GT_E\|` |
| Ctx → context | `\|context ∩ GT_C\| / \|GT_C\|` |
| Supp → supplementary | `\|supp ∩ GT_S\| / \|GT_S\|` |

### Alert Thresholds

| Condition | Flag |
|-----------|------|
| Q1 Recall < 0.5 | 🔴 Critical gap |
| Precision < 0.3 | 🟡 Excessive noise |
| Q1 Edit Recall = 0 | 🔴 Missing all edit targets |

## Results Format

Write to `benchmarking/evee/results/recon_{pipeline}_{date}.json`.
See `results/schema.json` for full schema.

```json
{
  "meta": {
    "pipeline_version": "v5",
    "date": "2026-02-23",
    "recon_commit": "<sha>",
    "evee_commit": "<sha>"
  },
  "issues": {
    "4": {
      "Q1": {
        "precision": 0.0, "recall": 0.0, "f1": 0.0,
        "edit_recall": 0.0, "noise_ratio": 0.0,
        "returned_files": [],
        "bucket_alignment": {
          "edit_to_edit_target": 0.0,
          "ctx_to_context": 0.0,
          "supp_to_supplementary": 0.0
        }
      }
    }
  },
  "aggregates": {
    "by_query_level": { "Q1": {}, "Q2": {}, "Q3": {} },
    "overall": { "mean_f1": 0.0, "median_f1": 0.0 },
    "by_difficulty": { "simple": {}, "medium": {}, "complex": {} }
  }
}
```

## Ground Truth Categories

| Code | Category | Meaning |
|------|----------|---------|
| **E** | Edit | Files requiring direct code changes |
| **C** | Context/Test | Pattern context + test files |
| **S** | Supp/Docs | Docs, examples, CI, infra, config |

Category assignments are issue-specific — the same file may be E for one issue and C for another.

---

## Issue Entries

---

### #4 — Cache model inference

**GitHub**: https://github.com/microsoft/evee/issues/4  
**Labels**: enhancement  
**Summary**: Configure Evee to enable caching deterministic model inference results to save costs and time when rerunning evaluations with additional models, metrics, or during development.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/evaluation/model_evaluator.py | Core evaluator with `_infer_record`, `_infer_record_async` — inference points where caching would be injected | Edit |
| 2 | src/evee/core/base_model.py | `ModelWrapper`, `@model` decorator — cache config could be per-model | Edit |
| 3 | src/evee/core/models/inference_output.py | `InferenceOutput` dataclass — the cached result structure | Edit |
| 4 | src/evee/config/models.py | `Config`, `ExperimentConfig`, `ModelVariantConfig` — needs cache config fields | Edit |
| 5 | src/evee/logging/local_metrics_logger.py | Logging for cache hits/misses | Edit |
| 6 | tests/evee/evaluation/test_model_evaluator_evaluation.py | Tests needing cache hit/miss scenarios | Context/Test |
| 7 | tests/evee/evaluation/test_model_evaluator_init.py | Evaluator init — cache config initialization tests | Context/Test |
| 8 | tests/evee/conftest.py | Test fixtures needing cache config fields | Context/Test |
| 9 | docs/user-guide/configuration.md | Config reference — needs cache configuration docs | Supp/Docs |
| 10 | docs/user-guide/models.md | Model documentation — caching behavior | Supp/Docs |
| 11 | example/experiment/config.yaml | Example config — cache configuration example | Supp/Docs |
| 12 | pyproject.toml | Potential new dependency for cache storage | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to implement inference result caching in Evee's evaluation pipeline. The cache should intercept model inference calls in the ModelEvaluator (`_infer_record` and `_infer_record_async`), store InferenceOutput results keyed by input record hash, and skip re-inference on cache hits. I need to add cache configuration fields to Config/ModelVariantConfig in the config models, update the evaluation loop, add cache hit/miss logging, and write tests for the caching behavior.

**Q2** *(mixed, scoped)*:
Add caching support for deterministic model inference results in Evee. When a model's results are deterministic, re-running evaluation should reuse cached inference outputs instead of calling the model again. This involves changes to the evaluation pipeline, configuration schema, and model infrastructure. Need to know where inference happens and how config is structured.

**Q3** *(unanchored, open)*:
How can I add result caching to Evee so that re-running experiments with the same models doesn't repeat inference? I want to save time and costs during iterative development. Where should the caching logic live in the codebase?

---

### #38 — Evee MCP server

**GitHub**: https://github.com/microsoft/evee/issues/38  
**Labels**: —  
**Summary**: Build an MCP server exposing Evee's evaluation framework capabilities to AI coding assistants. Core tools for experiment management, result analysis, configuration assistance, code scaffolding, and Azure ML integration. Python-based using FastMCP.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/mcp/server.py | Main FastMCP server with tool and resource registrations | Edit |
| 2 | src/evee/mcp/__init__.py | MCP package public API | Edit |
| 3 | src/evee/mcp/constants.py | SERVER_NAME, MimeTypes, ResourceURIs, ToolNames | Edit |
| 4 | src/evee/mcp/README.md | MCP server documentation | Edit |
| 5 | src/evee/mcp/tools/__init__.py | Tools package/registry | Edit |
| 6 | src/evee/mcp/tools/base.py | BaseTool, ToolResult base classes | Edit |
| 7 | src/evee/mcp/tools/experiment.py | RunExperimentTool implementation | Edit |
| 8 | src/evee/mcp/tools/validation.py | ValidateConfigTool implementation | Edit |
| 9 | src/evee/mcp/tools/discovery.py | ListComponentsTool implementation | Edit |
| 10 | src/evee/mcp/tools/view_results.py | ViewResultsTool implementation | Edit |
| 11 | src/evee/mcp/resources/__init__.py | Resources package/registry | Edit |
| 12 | src/evee/mcp/resources/base.py | BaseResource, ResourceMetadata | Edit |
| 13 | src/evee/mcp/resources/config.py | ConfigSchemaResource | Edit |
| 14 | src/evee/mcp/resources/connections.py | Connection patterns resource | Edit |
| 15 | src/evee/mcp/resources/model_patterns.py | Model implementation patterns | Edit |
| 16 | src/evee/mcp/resources/metric_patterns.py | Metric patterns resource | Edit |
| 17 | src/evee/mcp/resources/evaluators.py | Azure evaluators resource | Edit |
| 18 | src/evee/mcp/resources/patterns.py | Decorator patterns resource | Edit |
| 19 | src/evee/mcp/resources/app_viewer.py | Results viewer app resource | Edit |
| 20 | src/evee/execution/runner.py | ExecutionRunner used by MCP tools | Edit |
| 21 | src/evee/execution/environment.py | EnvironmentResolver for venv discovery | Edit |
| 22 | src/evee/ui/results-viewer/src/app.tsx | Results viewer HTML app | Edit |
| 23 | tests/mcp/conftest.py | MCP test fixtures | Context/Test |
| 24 | tests/mcp/test_e2e.py | End-to-end MCP tests | Context/Test |
| 25 | tests/mcp/test_tools.py | Tool unit tests | Context/Test |
| 26 | tests/mcp/test_resources.py | Resource unit tests | Context/Test |
| 27 | tests/mcp/__init__.py | MCP test package init | Context/Test |
| 28 | pyproject.toml | MCP dependency, CLI entry point | Supp/Docs |
| 29 | docs/user-guide/mcp-server.md | MCP server user documentation | Supp/Docs |
| 30 | docs/design/mcp-server.md | MCP server design document | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to build an MCP server for the Evee evaluation framework using FastMCP. The server should expose tools for running experiments (`run_experiment`), validating configs (`validate_config`), discovering components (`list_components`), and viewing results (`view_results`). It also needs static resources for config schemas, model/metric patterns, and Azure evaluator metadata. The server uses an execution runner for subprocess-based evaluation and an environment resolver for venv discovery. I need the existing tool implementations, resource definitions, test fixtures, and documentation structure.

**Q2** *(mixed, scoped)*:
I'm working on an MCP server that exposes Evee's evaluation capabilities to AI coding assistants in IDEs. It needs tools for experiment management, configuration validation, component discovery, and result viewing, plus documentation resources. I need to understand the current MCP server architecture, tool/resource patterns, and test structure.

**Q3** *(unanchored, open)*:
I want to add an MCP server to Evee so that AI assistants can interact with the evaluation framework. Where is the MCP code and how are tools and resources structured? What patterns should I follow?

---

### #57 — Add tests for all supported python versions in CI

**GitHub**: https://github.com/microsoft/evee/issues/57  
**Labels**: —  
**Summary**: Run CI tests across all Python LTS versions (3.11, 3.12, 3.13). Add CI badges to README.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | .github/workflows/ci.yml | CI workflow with python-version matrix to expand | Edit |
| 2 | pyproject.toml | `requires-python` constraint, test config | Edit |
| 3 | packages/evee-mlflow/pyproject.toml | MLflow backend `requires-python` | Edit |
| 4 | packages/evee-azureml/pyproject.toml | AzureML backend `requires-python` | Edit |
| 5 | README.md | Needs CI status badges per Python version | Edit |
| 6 | .github/workflows/integration-tests.yml | Integration test workflow — may need multi-version | Edit |
| 7 | example/core/pyproject.toml | Example project `requires-python` | Edit |
| 8 | example/azureml/pyproject.toml | Example project `requires-python` | Edit |
| 9 | example/mlflow/pyproject.toml | Example project `requires-python` | Edit |
| 10 | samples/coding-sample/pyproject.toml | Sample `requires-python` | Edit |
| 11 | samples/agent-sample/pyproject.toml | Sample `requires-python` | Edit |
| 12 | .devcontainer/devcontainer.json | Dev container Python version | Edit |
| 13 | Makefile | Setup/test targets | Context/Test |

**Q1** *(anchored, precise)*:
I need to expand Evee's CI pipeline to test on all Python LTS versions (3.11, 3.12, 3.13). The CI workflow in `.github/workflows/ci.yml` currently has a `matrix.python-version` that needs expanding. I also need to update `requires-python` in the root `pyproject.toml` and all package/example/sample `pyproject.toml` files to support 3.11+, and add CI status badges to the README. Need to verify the integration test workflow and devcontainer configuration as well.

**Q2** *(mixed, scoped)*:
Expand CI tests to run on Python 3.11, 3.12, and 3.13. This means updating the CI matrix, changing the minimum Python version across all `pyproject.toml` files in the monorepo, and adding CI badges to the README. Need to find all Python version constraints and CI configs.

**Q3** *(unanchored, open)*:
How do I add support for testing Evee on all Python LTS versions and show badges in the README? Where are the CI workflows and Python version constraints defined?

---

### #63 — Load test benchmarks

**GitHub**: https://github.com/microsoft/evee/issues/63  
**Labels**: —  
**Summary**: Implement load tests benchmarking Evee with different model types (async/sync), dataset sizes, and model counts. Use mock models as baseline. Determine Evee's limits with large-scale data.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/evaluation/model_evaluator.py | Core evaluator: `evaluate()`, sync/async paths — code being benchmarked | Context/Test |
| 2 | src/evee/core/base_model.py | `@model` decorator, `_is_async` detection — mock model creation | Context/Test |
| 3 | src/evee/core/base_dataset.py | `BaseDataset` — mock datasets of various sizes | Context/Test |
| 4 | src/evee/datasets/jsonl_dataset.py | JSONL dataset loader for large data | Context/Test |
| 5 | src/evee/datasets/dataset_factory.py | DatasetFactory for dataset creation | Context/Test |
| 6 | src/evee/config/models.py | Config models for benchmark test configuration | Context/Test |
| 7 | src/evee/evaluation/metrics_aggregator.py | Results aggregation performance | Context/Test |
| 8 | src/evee/evaluation/progress_tracker.py | Progress tracking under load | Context/Test |
| 9 | src/evee/logging/local_metrics_logger.py | Logging performance | Context/Test |
| 10 | src/evee/core/models/evaluation_output.py | EvaluationOutput data model | Context/Test |
| 11 | src/evee/core/models/inference_output.py | InferenceOutput data model | Context/Test |
| 12 | src/evee/tracking/backends/no_op_fallback_backend.py | NoOp backend for baseline benchmarks | Context/Test |
| 13 | tests/evee/conftest.py | Shared test fixtures | Context/Test |
| 14 | tests/evee/evaluation/test_model_evaluator_evaluation.py | Existing eval tests (async/sync patterns) | Context/Test |
| 15 | tests/evee/core/test_base_model.py | Async model tests — benchmark reference | Context/Test |
| 16 | pyproject.toml | Pytest config, potential benchmark marker | Edit |
| 17 | Makefile | Would need benchmark target | Edit |
| 18 | .github/workflows/ci.yml | CI pipeline for running benchmarks | Edit |

**Q1** *(anchored, precise)*:
I need to implement load test benchmarks for Evee's evaluation pipeline. The tests should create mock models (both sync and async using `@model` decorator) of varying complexity, generate mock datasets of different sizes (100, 1000, 10000+ records), and measure evaluation throughput. I need to understand the `ModelEvaluator` evaluation loop (sync vs async paths), dataset loading via `DatasetFactory`, metrics aggregation, and progress tracking. Tests should use `NoOpTrackingBackend` as baseline and possibly add a `benchmark` pytest marker.

**Q2** *(mixed, scoped)*:
I want to benchmark Evee's evaluation pipeline performance with different model types and dataset sizes. I need to create mock models and datasets, run them through the evaluation loop, and measure throughput and resource usage. Where does the evaluation happen and how do models and datasets work?

**Q3** *(unanchored, open)*:
How can I load test Evee to find its performance limits? I want to test with different sizes of data and types of models. Where is the evaluation logic?

---

### #72 — Azure AI Foundry Integration

**GitHub**: https://github.com/microsoft/evee/issues/72  
**Labels**: enhancement  
**Summary**: Parent issue for integrating Evee with Azure AI Foundry. Covers tracking backend (metrics to Foundry dashboards), compute backend (evaluations on Foundry infrastructure), and SDK-level integration exploration.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/tracking/backend.py | TrackingBackend protocol for Foundry backend implementation | Edit |
| 2 | src/evee/tracking/factory.py | Factory for backend discovery via entry points | Edit |
| 3 | src/evee/tracking/events.py | Tracking event definitions | Edit |
| 4 | src/evee/tracking/__init__.py | Tracking public API | Edit |
| 5 | src/evee/compute/backend.py | ComputeBackend protocol for Foundry compute | Edit |
| 6 | packages/evee-azureml/src/evee_azureml/tracking.py | AzureML tracking — reference pattern | Context/Test |
| 7 | packages/evee-azureml/src/evee_azureml/compute.py | AzureML compute — reference pattern | Context/Test |
| 8 | packages/evee-azureml/src/evee_azureml/config.py | AzureML config models — config pattern | Context/Test |
| 9 | packages/evee-azureml/src/evee_azureml/auth.py | Azure identity auth — auth pattern | Context/Test |
| 10 | packages/evee-azureml/pyproject.toml | Entry points pattern for backends | Supp/Docs |
| 11 | src/evee/config/models.py | ComputeBackendConfig, TrackingBackendConfig | Edit |
| 12 | src/evee/cli/commands/tracking.py | Tracking set command with backend types | Edit |
| 13 | src/evee/cli/commands/compute.py | Compute commands with backend types | Edit |
| 14 | src/evee/cli/commands/metric.py | Foundry evaluator metric scaffolding | Edit |
| 15 | src/evee/cli/constants.py | TEMPLATE_TYPE_FOUNDRY constant | Edit |
| 16 | src/evee/cli/azure_evaluators.json | Azure AI Foundry evaluator metadata | Edit |
| 17 | src/evee/core/telemetry.py | Azure partner telemetry | Edit |
| 18 | infra/terraform/modules/ai-foundry/main.tf | Foundry Hub and Project resources | Supp/Docs |
| 19 | infra/terraform/modules/ai-foundry/variables.tf | Foundry module variables | Supp/Docs |
| 20 | infra/terraform/modules/ai-foundry/outputs.tf | Foundry endpoints | Supp/Docs |
| 21 | infra/terraform/main.tf | Module invocation | Supp/Docs |
| 22 | infra/terraform/variables.tf | Deploy flags | Supp/Docs |
| 23 | infra/terraform/terraform.tfvars | Foundry config values | Supp/Docs |
| 24 | infra/terraform/outputs.tf | Foundry outputs | Supp/Docs |
| 25 | infra/terraform/rbac.tf | RBAC assignments for Foundry | Supp/Docs |
| 26 | infra/terraform/generate-env.sh | AZURE_AI_FOUNDRY_PROJECT_ENDPOINT generation | Supp/Docs |
| 27 | docs/backends/overview.md | Backend overview | Supp/Docs |
| 28 | docs/backends/azureml.md | AzureML docs — reference pattern | Supp/Docs |
| 29 | docs/backends/custom-backends.md | Custom backend implementation guide | Supp/Docs |
| 30 | docs/advanced/infrastructure.md | Terraform infrastructure docs | Supp/Docs |
| 31 | docs/user-guide/configuration.md | Config reference | Supp/Docs |
| 32 | pyproject.toml | Core entry points | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to integrate Azure AI Foundry as both a tracking and compute backend for Evee. The tracking backend should send metrics to Foundry dashboards via OpenTelemetry or native SDK. The compute backend should execute evaluations on Foundry infrastructure. I should follow the `packages/evee-azureml/` pattern — examining the AzureML tracking backend, compute backend, auth, config models, and entry points. I also need the Terraform AI Foundry module for infrastructure provisioning and the CLI commands for tracking/compute backend configuration.

**Q2** *(mixed, scoped)*:
I want to add Azure AI Foundry as a backend for Evee, similar to the existing Azure ML backend. I need to understand the compute and tracking backend plugin architecture, the AzureML reference implementation, Terraform modules for Foundry, and the CLI commands for backend management.

**Q3** *(unanchored, open)*:
How do I add a new backend to Evee for Azure AI Foundry? I need both tracking and compute support. What's the pattern for backend plugins and where is the existing Azure ML code?

---

### #108 — Implement Integration Tests with Mocked Services

**GitHub**: https://github.com/microsoft/evee/issues/108  
**Labels**: —  
**Summary**: Add mocked integration tests that run Evee end-to-end without calling external services. Load real configs, run full evaluations, validate outputs with deterministic mocked LLM responses. Should run on every PR.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | tests/evee/integration/helpers.py | Integration test helpers: `run_evee_evaluation()`, `EvaluationResult` | Context/Test |
| 2 | tests/evee/integration/__init__.py | Integration test package | Context/Test |
| 3 | tests/evee/integration/test_example_evaluate_locally_core.py | Existing local integration test pattern | Context/Test |
| 4 | tests/evee/integration/test_example_evaluate_locally_mlflow.py | Existing MLflow integration test | Context/Test |
| 5 | tests/evee/integration/test_example_evaluate_submission_remote_azureml.py | AzureML remote integration test | Context/Test |
| 6 | tests/evee/integration/test_model_cleanup.py | Existing mocked integration test pattern | Context/Test |
| 7 | tests/evee/conftest.py | Shared fixtures: `mock_config_dict`, `mock_config_yaml`, `evaluator_with_setup` | Context/Test |
| 8 | src/evee/evaluation/model_evaluator.py | Core evaluator pipeline to test e2e | Edit |
| 9 | src/evee/config/models.py | Config models for loading real config | Edit |
| 10 | src/evee/execution/experiment_runner.py | ExperimentRunner — top-level execution flow | Edit |
| 11 | src/evee/core/base_model.py | `@model` decorator for mocked model registration | Edit |
| 12 | src/evee/core/base_metric.py | `@metric` decorator for mocked metrics | Edit |
| 13 | src/evee/core/base_dataset.py | `@dataset` decorator for mock dataset | Edit |
| 14 | src/evee/datasets/jsonl_dataset.py | JSONL dataset loader | Edit |
| 15 | src/evee/datasets/dataset_factory.py | DatasetFactory | Edit |
| 16 | src/evee/tracking/backends/no_op_fallback_backend.py | NoOp backend for no-network tests | Edit |
| 17 | src/evee/tracking/factory.py | Tracking backend factory | Edit |
| 18 | src/evee/evaluation/metrics_aggregator.py | Output validation | Edit |
| 19 | src/evee/logging/local_metrics_logger.py | Output artifact validation | Edit |
| 20 | src/evee/compute/local_compute_backend.py | Local execution path | Edit |
| 21 | src/evee/cli/commands/run.py | CLI run command | Edit |
| 22 | example/experiment/config.yaml | Reference config | Supp/Docs |
| 23 | example/experiment/data/sample_dataset.jsonl | Reference dataset | Supp/Docs |
| 24 | pyproject.toml | Pytest config, integration markers | Supp/Docs |
| 25 | .github/workflows/ci.yml | CI config for PR gating | Supp/Docs |
| 26 | .github/workflows/integration-tests.yml | Integration test workflow | Supp/Docs |
| 27 | Makefile | `test-core`, `test-integration` targets | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to implement integration tests that run Evee's full evaluation pipeline end-to-end with mocked services. The tests should load a real config, create mock `@model` and `@metric` decorated classes with deterministic responses, use `NoOpTrackingBackend`, run through `ExperimentRunner` and `ModelEvaluator`, and validate output artifacts. No external network calls. I need the existing integration test patterns in `tests/evee/integration/`, the evaluation pipeline code, config models, dataset loading, and CI workflow configuration to add these to PR gating.

**Q2** *(mixed, scoped)*:
Add mocked integration tests for Evee that test the full evaluation flow without external services. I need to understand the existing integration test structure, how the evaluation pipeline works end-to-end, how to create mock models/metrics, and how to wire them into the config and execution flow.

**Q3** *(unanchored, open)*:
I want to add end-to-end tests for Evee that don't call any external APIs. They should test the full evaluation flow with mocked models. Where are the existing integration tests and how does the evaluation pipeline work?

---

### #172 — MCP Server - Documentation, Testing & Integration

**GitHub**: https://github.com/microsoft/evee/issues/172  
**Labels**: documentation, testing, mcp-server  
**Summary**: Create comprehensive documentation and E2E testing for the MCP server. Installation guide, client configuration, user guide, security docs, API reference. Testing with IDE clients, backend testing, security/performance testing.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/mcp/server.py | Main MCP server | Context/Test |
| 2 | src/evee/mcp/__init__.py | MCP package exports | Context/Test |
| 3 | src/evee/mcp/constants.py | Constants | Context/Test |
| 4 | src/evee/mcp/README.md | Existing MCP README | Context/Test |
| 5 | src/evee/mcp/tools/__init__.py | Tools registry | Context/Test |
| 6 | src/evee/mcp/tools/base.py | BaseTool classes | Context/Test |
| 7 | src/evee/mcp/tools/experiment.py | RunExperimentTool | Context/Test |
| 8 | src/evee/mcp/tools/validation.py | ValidateConfigTool | Context/Test |
| 9 | src/evee/mcp/tools/discovery.py | ListComponentsTool | Context/Test |
| 10 | src/evee/mcp/tools/view_results.py | ViewResultsTool | Context/Test |
| 11 | src/evee/mcp/resources/__init__.py | Resources registry | Context/Test |
| 12 | src/evee/mcp/resources/base.py | BaseResource | Context/Test |
| 13 | src/evee/mcp/resources/evaluators.py | Evaluators resource | Context/Test |
| 14 | src/evee/mcp/resources/patterns.py | Patterns resource | Context/Test |
| 15 | src/evee/mcp/resources/config.py | Config schema resource | Context/Test |
| 16 | src/evee/mcp/resources/connections.py | Connections resource | Context/Test |
| 17 | src/evee/mcp/resources/model_patterns.py | Model patterns | Context/Test |
| 18 | src/evee/mcp/resources/metric_patterns.py | Metric patterns | Context/Test |
| 19 | src/evee/mcp/resources/app_viewer.py | Results viewer | Context/Test |
| 20 | src/evee/ui/results-viewer/src/app.tsx | Viewer React app | Context/Test |
| 21 | src/evee/ui/results-viewer/package.json | Viewer package config | Context/Test |
| 22 | tests/test_mcp_server.py | Basic MCP server tests | Edit |
| 23 | tests/mcp/__init__.py | MCP test package | Edit |
| 24 | tests/mcp/conftest.py | MCP test fixtures | Edit |
| 25 | tests/mcp/test_e2e.py | E2E tests | Edit |
| 26 | tests/mcp/test_resources.py | Resource tests | Edit |
| 27 | tests/mcp/test_tools.py | Tool tests | Edit |
| 28 | docs/user-guide/mcp-server.md | Existing MCP user docs | Edit |
| 29 | docs/design/mcp-server.md | MCP design document | Edit |
| 30 | docs/user-guide/cli.md | CLI reference (view-results MCP) | Edit |
| 31 | docs/user-guide/configuration.md | Config reference | Edit |
| 32 | docs/troubleshooting.md | Troubleshooting | Edit |
| 33 | pyproject.toml | MCP dependency, CLI entry point | Edit |
| 34 | mkdocs.yml | Docs navigation | Edit |
| 35 | src/evee/execution/runner.py | ExecutionRunner used by tools | Context/Test |
| 36 | src/evee/execution/environment.py | EnvironmentResolver | Context/Test |
| 37 | src/evee/execution/experiment_runner.py | ExperimentRunner used by run_experiment | Context/Test |
| 38 | Makefile | Test targets | Edit |

**Q1** *(anchored, precise)*:
I need to create comprehensive documentation and end-to-end testing for Evee's MCP server. Documentation should cover installation, MCP client configuration (VS Code, Cursor, Claude Desktop), user guide with example workflows, security documentation, and API reference for all 5 tools and resources. Testing should include IDE client testing, project configuration testing (venv, conda), backend testing (local, Azure ML, MLflow), security testing (path traversal, injection), and performance testing. I need the full MCP server implementation, all tools and resources, existing tests/fixtures, current docs, the execution infrastructure, and the results viewer UI.

**Q2** *(mixed, scoped)*:
I'm creating documentation and tests for Evee's MCP server. I need to understand all the MCP tools, resources, how they work, existing test patterns, current documentation, and the execution infrastructure. I also need to test security aspects like path traversal prevention and performance with large datasets.

**Q3** *(unanchored, open)*:
I need to document and test the Evee MCP server thoroughly. Where is all the MCP code, what tools does it expose, and what existing docs and tests are there? What security considerations should I address?

---

### #191 — Azure AI Foundry: Tracking and Compute Backend Support (Phase 1)

**GitHub**: https://github.com/microsoft/evee/issues/191  
**Labels**: enhancement, wontfix  
**Summary**: Implement Azure AI Foundry as a tracking and compute backend. Send metrics to Foundry dashboards via OpenTelemetry/SDK. Execute evaluations on Foundry infrastructure. Add `make run_foundry` in example Makefile.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | packages/evee-azureml/src/evee_azureml/tracking.py | Reference tracking backend | Context/Test |
| 2 | packages/evee-azureml/src/evee_azureml/compute.py | Reference compute backend | Context/Test |
| 3 | packages/evee-azureml/src/evee_azureml/config.py | Reference config models | Context/Test |
| 4 | packages/evee-azureml/src/evee_azureml/auth.py | Azure identity auth | Context/Test |
| 5 | packages/evee-azureml/src/evee_azureml/utils.py | Shared utilities | Context/Test |
| 6 | packages/evee-azureml/src/evee_azureml/__init__.py | Package init | Context/Test |
| 7 | packages/evee-azureml/pyproject.toml | Entry points pattern | Supp/Docs |
| 8 | packages/evee-azureml/tests/test_tracking.py | Tracking tests | Context/Test |
| 9 | packages/evee-azureml/tests/test_azureml_backend_pkg.py | Compute tests | Context/Test |
| 10 | packages/evee-azureml/tests/test_azureml_inference_events.py | Inference event tests | Context/Test |
| 11 | src/evee/tracking/backend.py | TrackingBackend base class | Edit |
| 12 | src/evee/tracking/factory.py | Backend factory | Edit |
| 13 | src/evee/tracking/events.py | Event types | Edit |
| 14 | src/evee/tracking/constants.py | Tracking constants | Edit |
| 15 | src/evee/tracking/__init__.py | Tracking API | Edit |
| 16 | src/evee/tracking/backends/no_op_fallback_backend.py | Fallback reference | Edit |
| 17 | src/evee/compute/backend.py | ComputeBackend ABC | Edit |
| 18 | src/evee/compute/local_compute_backend.py | Local compute reference | Edit |
| 19 | src/evee/config/models.py | Config models | Edit |
| 20 | src/evee/core/telemetry.py | Azure partner telemetry | Edit |
| 21 | src/evee/execution/experiment_runner.py | Backend loading via entry points | Edit |
| 22 | src/evee/execution/runner.py | Execution runner | Edit |
| 23 | src/evee/execution/environment.py | Environment resolver | Edit |
| 24 | src/evee/cli/commands/tracking.py | Tracking CLI commands | Edit |
| 25 | src/evee/cli/commands/compute.py | Compute CLI commands | Edit |
| 26 | src/evee/cli/constants.py | CLI constants | Edit |
| 27 | src/evee/cli/utils/config_manager.py | Config management | Edit |
| 28 | pyproject.toml | Core entry points | Supp/Docs |
| 29 | example/Makefile | Needs `run_foundry` target | Supp/Docs |
| 30 | example/experiment/config.azureml.yaml | Reference AzureML config | Supp/Docs |
| 31 | example/README.md | Example docs | Supp/Docs |
| 32 | example/.env.sample | Env var samples | Supp/Docs |
| 33 | infra/terraform/modules/ai-foundry/main.tf | Foundry infrastructure | Supp/Docs |
| 34 | infra/terraform/modules/ai-foundry/variables.tf | Foundry variables | Supp/Docs |
| 35 | infra/terraform/modules/ai-foundry/outputs.tf | Foundry endpoints | Supp/Docs |
| 36 | infra/terraform/main.tf | Module invocation | Supp/Docs |
| 37 | infra/terraform/outputs.tf | Foundry outputs | Supp/Docs |
| 38 | infra/terraform/variables.tf | Deploy flags | Supp/Docs |
| 39 | infra/terraform/terraform.tfvars | Foundry config | Supp/Docs |
| 40 | infra/terraform/README.md | Terraform readme | Supp/Docs |
| 41 | docs/backends/azureml.md | AzureML backend docs pattern | Supp/Docs |
| 42 | docs/backends/custom-backends.md | Backend implementation guide | Supp/Docs |
| 43 | docs/backends/overview.md | Backend overview | Supp/Docs |
| 44 | docs/user-guide/configuration.md | Config reference | Supp/Docs |
| 45 | docs/user-guide/cli.md | CLI reference | Supp/Docs |
| 46 | docs/advanced/infrastructure.md | Infrastructure docs | Supp/Docs |
| 47 | docs/design/architecture.md | Architecture doc | Supp/Docs |
| 48 | docs/getting-started/glossary.md | Glossary | Supp/Docs |
| 49 | docs/troubleshooting.md | Troubleshooting | Supp/Docs |
| 50 | Makefile | Root Makefile | Supp/Docs |
| 51 | mkdocs.yml | Docs navigation | Supp/Docs |
| 52 | tests/evee/tracking/test_tracking_factory.py | Factory tests | Context/Test |
| 53 | tests/evee/tracking/test_tracking_backend.py | Backend tests | Context/Test |
| 54 | tests/evee/execution/test_experiment_runner.py | Runner tests | Context/Test |

**Q1** *(anchored, precise)*:
I need to implement Azure AI Foundry as both a tracking and compute backend for Evee, following the `packages/evee-azureml/` pattern exactly. The tracking backend should implement the `TrackingBackend` protocol and send metrics to Foundry dashboards. The compute backend should implement `ComputeBackend` ABC and submit evaluations to Foundry infrastructure. I need to register via entry points, add CLI commands for tracking/compute backend selection, add a `make run_foundry` target in the example Makefile, configure Terraform infrastructure, and document region limitations for LLM-based evaluators.

**Q2** *(mixed, scoped)*:
Add Azure AI Foundry as a tracking and compute backend for Evee. I need to follow the existing AzureML backend package pattern, understand the tracking/compute backend plugin architecture, update CLI commands, add Terraform infrastructure, and create documentation. What are the reference implementations and where do entry points get registered?

**Q3** *(unanchored, open)*:
I want to make Evee work with Azure AI Foundry for running evaluations and tracking metrics. There's already an Azure ML backend — I need to do something similar for Foundry. Where should I start and what's the backend plugin structure?

---

### #192 — Azure AI Foundry: SDK Integration Exploration (Phase 2)

**GitHub**: https://github.com/microsoft/evee/issues/192  
**Labels**: enhancement  
**Summary**: Explore embedding Evee capabilities within Azure AI Foundry workflows for deeper SDK-level integration. Spike to validate feasibility, document patterns and tradeoffs. Depends on Phase 1 (#191).

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | packages/evee-azureml/src/evee_azureml/tracking.py | Reference tracking backend to extend | Context/Test |
| 2 | packages/evee-azureml/src/evee_azureml/compute.py | Reference compute backend | Context/Test |
| 3 | packages/evee-azureml/src/evee_azureml/config.py | Config models pattern | Context/Test |
| 4 | packages/evee-azureml/src/evee_azureml/auth.py | Auth pattern | Context/Test |
| 5 | packages/evee-azureml/src/evee_azureml/utils.py | Shared utilities | Context/Test |
| 6 | packages/evee-azureml/src/evee_azureml/__init__.py | Package init | Context/Test |
| 7 | packages/evee-azureml/pyproject.toml | Entry points, azure-ai-projects dependency | Supp/Docs |
| 8 | packages/evee-azureml/tests/test_tracking.py | Test patterns | Context/Test |
| 9 | packages/evee-azureml/tests/test_azureml_backend_pkg.py | Backend test patterns | Context/Test |
| 10 | packages/evee-azureml/tests/test_azureml_inference_events.py | Inference event tests | Context/Test |
| 11 | src/evee/tracking/backend.py | TrackingBackend protocol | Edit |
| 12 | src/evee/tracking/factory.py | Backend factory | Edit |
| 13 | src/evee/tracking/__init__.py | Tracking API | Edit |
| 14 | src/evee/tracking/events.py | Event types | Edit |
| 15 | src/evee/tracking/constants.py | Tracking constants | Edit |
| 16 | src/evee/tracking/backends/no_op_fallback_backend.py | Fallback reference | Edit |
| 17 | src/evee/compute/backend.py | ComputeBackend ABC | Edit |
| 18 | src/evee/compute/local_compute_backend.py | Local compute reference | Edit |
| 19 | src/evee/config/models.py | Config models | Edit |
| 20 | src/evee/core/telemetry.py | Azure partner telemetry | Edit |
| 21 | src/evee/execution/experiment_runner.py | Backend loading | Edit |
| 22 | src/evee/execution/runner.py | Execution runner | Edit |
| 23 | src/evee/execution/environment.py | Environment resolver | Edit |
| 24 | src/evee/cli/commands/tracking.py | Tracking CLI | Edit |
| 25 | src/evee/cli/commands/compute.py | Compute CLI | Edit |
| 26 | src/evee/cli/constants.py | CLI constants | Edit |
| 27 | src/evee/cli/utils/config_manager.py | Config management | Edit |
| 28 | infra/terraform/modules/ai-foundry/main.tf | Foundry infrastructure | Supp/Docs |
| 29 | infra/terraform/modules/ai-foundry/variables.tf | Foundry variables | Supp/Docs |
| 30 | infra/terraform/modules/ai-foundry/outputs.tf | Foundry outputs | Supp/Docs |
| 31 | infra/terraform/main.tf | Module invocation | Supp/Docs |
| 32 | infra/terraform/outputs.tf | Foundry outputs | Supp/Docs |
| 33 | infra/terraform/variables.tf | Deploy flags | Supp/Docs |
| 34 | infra/terraform/terraform.tfvars | Config values | Supp/Docs |
| 35 | docs/backends/azureml.md | AzureML docs pattern | Supp/Docs |
| 36 | docs/backends/custom-backends.md | Backend implementation guide | Supp/Docs |
| 37 | docs/backends/overview.md | Backend overview | Supp/Docs |
| 38 | docs/user-guide/configuration.md | Config reference | Supp/Docs |
| 39 | docs/advanced/infrastructure.md | Infrastructure docs | Supp/Docs |
| 40 | docs/design/architecture.md | Architecture reference | Supp/Docs |
| 41 | docs/getting-started/glossary.md | Glossary | Supp/Docs |
| 42 | example/Makefile | Example Makefile | Supp/Docs |
| 43 | example/experiment/config.azureml.yaml | AzureML config pattern | Supp/Docs |
| 44 | Makefile | Root Makefile | Supp/Docs |
| 45 | mkdocs.yml | Docs navigation | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to explore deeper SDK-level integration between Evee and Azure AI Foundry. This is Phase 2, which assumes Phase 1 (basic tracking/compute backend) is complete. I need to investigate whether Evee evaluators can be called directly from the Foundry SDK, whether Foundry-native configuration and deployment of Evee experiments is feasible, and whether shared model/dataset registries make sense. I need the existing AzureML backend package as reference, the compute/tracking backend protocols, config models, Terraform Foundry modules, and architecture documentation.

**Q2** *(mixed, scoped)*:
Explore making Evee work more deeply within Azure AI Foundry's SDK workflows. I need to understand Evee's backend plugin architecture, the existing AzureML reference implementation, and the Foundry infrastructure setup to evaluate SDK integration feasibility.

**Q3** *(unanchored, open)*:
Can Evee be embedded more deeply into Azure AI Foundry beyond just backends? I need to explore SDK-level integration possibilities. Where is the relevant backend code and infrastructure configuration?

---

### #193 — feat(cli): Support configurable dependency sources in `evee new` scaffolding

**GitHub**: https://github.com/microsoft/evee/issues/193  
**Labels**: kind/feature  
**Summary**: Add interactive source selection flow to `evee new` for different distribution models (Git, wheels, local source). Flags can override interactive flow for automation.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/cli/commands/new.py | Main command: `new_project()`, `_resolve_evee_version()`, `_backend_dep_from_source/git()`, `_GIT_BASE` | Edit |
| 2 | src/evee/cli/utils/new_project_operations.py | `copy_and_render_template()`, validation functions | Edit |
| 3 | src/evee/cli/templates/overlays/core/pyproject.toml | Template with dependency/source placeholders | Edit |
| 4 | src/evee/cli/templates/overlays/mlflow/pyproject.toml | MLflow template with placeholders | Edit |
| 5 | src/evee/cli/templates/overlays/azureml/pyproject.toml | AzureML template with placeholders | Edit |
| 6 | src/evee/cli/main.py | CLI entry point | Edit |
| 7 | tests/evee/cli/test_new_command.py | Tests for new project command | Context/Test |
| 8 | tests/evee/cli/test_template.py | Template rendering tests | Context/Test |
| 9 | tests/evee/cli/test_e2e_project_workflow.py | E2E workflow tests | Context/Test |
| 10 | tests/evee/integration/test_e2e_new_project_workflow.py | Integration test for new project | Context/Test |
| 11 | tests/evee/integration/test_wheels_provisioner.py | Wheel provisioning tests | Context/Test |
| 12 | docs/user-guide/cli.md | CLI reference with `evee new` section | Supp/Docs |
| 13 | docs/getting-started/installation.md | Installation docs | Supp/Docs |
| 14 | docs/getting-started/quickstart.md | Quickstart docs | Supp/Docs |
| 15 | pyproject.toml | Entry point: `evee = "evee.cli.main:main"` | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to add an interactive source selection flow to the `evee new` CLI command. Currently `new.py` hardcodes Git-based dependencies via `_GIT_BASE`. I need to add interactive prompts for choosing between Git, pre-built wheels, and local source, with flags (`--from-git`, `--wheels`, `--from-repo`, `--from-source`) for automation. The pyproject.toml templates in `src/evee/cli/templates/overlays/` use placeholders like `{evee_core_dependency}` and `{evee_core_source}` that need different rendering per source type. Tests in `test_new_command.py` cover version pinning and from-source scenarios.

**Q2** *(mixed, scoped)*:
I want to add dependency source selection to `evee new` so users can choose between Git, wheels, or local source for installing Evee packages. I need to understand the current scaffolding flow in the CLI, the template overlay system, and how pyproject.toml placeholders are rendered.

**Q3** *(unanchored, open)*:
How does `evee new` work for creating new projects? I need to add support for different ways of installing Evee packages — not just from Git. Where is the scaffolding code and template system?

---

### #201 — Move example project into end-to-end testing

**GitHub**: https://github.com/microsoft/evee/issues/201  
**Labels**: kind/feature  
**Summary**: Move example project to tests folder for E2E testing. Leave the agent sample as the reference sample project. The example project currently serves dual purposes (testing + sample), causing unnecessary complexity for users.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | example/__init__.py | Example project root | Supp/Docs |
| 2 | example/README.md | Example documentation | Supp/Docs |
| 3 | example/Makefile | Example build/run targets | Supp/Docs |
| 4 | example/.env.sample | Environment template | Supp/Docs |
| 5 | example/.gitignore | Example gitignore | Supp/Docs |
| 6 | example/.amlignore | AzureML ignore | Supp/Docs |
| 7 | example/.dockerignore | Docker ignore | Supp/Docs |
| 8 | example/Dockerfile.aml | AzureML Dockerfile | Supp/Docs |
| 9 | example/experiment/config.yaml | Core experiment config | Supp/Docs |
| 10 | example/experiment/config.mlflow.yaml | MLflow config | Supp/Docs |
| 11 | example/experiment/config.azureml.yaml | AzureML config | Supp/Docs |
| 12 | example/experiment/data/sample_dataset.jsonl | Test dataset | Supp/Docs |
| 13 | example/experiment/data/full_dataset.jsonl | Full dataset | Supp/Docs |
| 14 | example/models/__init__.py | Models package | Supp/Docs |
| 15 | example/models/baseline/baseline.py | Baseline model | Supp/Docs |
| 16 | example/models/baseline/baseline.j2 | Baseline template | Supp/Docs |
| 17 | example/models/few_shot/few_shot.py | Few-shot model | Supp/Docs |
| 18 | example/models/few_shot/few_shot.j2 | Few-shot template | Supp/Docs |
| 19 | example/models/few_shot/single_shot.j2 | Single-shot template | Supp/Docs |
| 20 | example/metrics/__init__.py | Metrics package | Supp/Docs |
| 21 | example/metrics/f1score_metric.py | F1 metric | Supp/Docs |
| 22 | example/metrics/llm_judge_metric.py | LLM judge metric | Supp/Docs |
| 23 | example/metrics/rouge_metric.py | ROUGE metric | Supp/Docs |
| 24 | example/metrics/token_usage_metric.py | Token usage metric | Supp/Docs |
| 25 | example/auth/auth.py | Auth implementation | Supp/Docs |
| 26 | example/prompt_template/prompt_loader.py | Prompt loader | Supp/Docs |
| 27 | example/core/pyproject.toml | Core pyproject | Supp/Docs |
| 28 | example/mlflow/pyproject.toml | MLflow pyproject | Supp/Docs |
| 29 | example/azureml/pyproject.toml | AzureML pyproject | Supp/Docs |
| 30 | tests/evee/integration/helpers.py | Integration helpers (`get_example_dir()`) | Context/Test |
| 31 | tests/evee/integration/test_example_evaluate_locally_core.py | Core integration test | Context/Test |
| 32 | tests/evee/integration/test_example_evaluate_locally_mlflow.py | MLflow integration test | Context/Test |
| 33 | tests/evee/integration/test_example_evaluate_submission_remote_azureml.py | AzureML integration test | Context/Test |
| 34 | tests/evee/integration/test_cli.py | CLI integration test | Context/Test |
| 35 | tests/evee/integration/test_e2e_new_project_workflow.py | E2E workflow test | Context/Test |
| 36 | tests/evee/integration/test_model_cleanup.py | Model cleanup test | Context/Test |
| 37 | tests/mcp/conftest.py | MCP conftest with example_project fixture | Context/Test |
| 38 | .github/workflows/integration-tests.yml | Integration test workflow | Supp/Docs |
| 39 | .vscode/launch.json | Debug configs referencing example/ | Supp/Docs |
| 40 | pyproject.toml | Coverage omit, testpaths | Supp/Docs |
| 41 | cspell.config.yaml | Spell check paths | Supp/Docs |
| 42 | .gitignore | Ignore patterns | Supp/Docs |
| 43 | CONTRIBUTING.md | References to example/ | Supp/Docs |
| 44 | docs/development/contributing.md | Project structure showing example/ | Supp/Docs |
| 45 | Makefile | `test-integration` target | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to move the `example/` project directory into the `tests/` folder so it becomes a pure testing fixture instead of serving double duty as a user-facing sample. All integration tests in `tests/evee/integration/` reference `example/` via `get_example_dir()` in `helpers.py`. The MCP conftest also references it via `example_project` fixture. I need to update all path references, CI workflows, debug configs, pyproject.toml coverage settings, and documentation that mentions `example/`. The `samples/agent-sample/` should remain as the user-facing reference project.

**Q2** *(mixed, scoped)*:
Move the `example/` directory into `tests/` for use as an E2E testing fixture. I need to find all references to `example/` across the codebase — in tests, CI workflows, documentation, and configuration — and update them. The agent sample should remain as the user-facing sample project.

**Q3** *(unanchored, open)*:
The example project needs to move into the tests folder. It's currently used for both testing and as a sample, which is confusing. Where is the example project referenced throughout the codebase?

---

### #210 — Evaluate OSS Release Strategy: Options, Requirements & Decision Framework

**GitHub**: https://github.com/microsoft/evee/issues/210  
**Labels**: documentation, kind/feature  
**Summary**: Evaluate paths for making Evee publicly available. Covers hosting options (Microsoft vs external org), release strategy (PyPI, GitHub Releases, source-only), security/compliance, long-term support. Deliverables: decision matrix, recommendation document, compliance checklist.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | LICENSE | MIT license — compliance | Supp/Docs |
| 2 | SECURITY.md | Security/compliance checklist | Supp/Docs |
| 3 | README.md | Public-facing identity, install instructions | Supp/Docs |
| 4 | CONTRIBUTING.md | Contributor model, package descriptions | Supp/Docs |
| 5 | pyproject.toml | Package metadata, build system, entry points | Supp/Docs |
| 6 | packages/evee-mlflow/pyproject.toml | Package metadata, repository URL | Supp/Docs |
| 7 | packages/evee-azureml/pyproject.toml | Package metadata, repository URL | Supp/Docs |
| 8 | Makefile | Build targets | Supp/Docs |
| 9 | tools/build/build_wheels.sh | Wheel build script | Context/Test |
| 10 | .github/workflows/release.yml | Release workflow | Context/Test |
| 11 | .github/tools/calculate_version.py | Version calculation | Context/Test |
| 12 | .github/workflows/ci.yml | CI pipeline | Context/Test |
| 13 | .github/workflows/codeql.yml | Security scanning | Context/Test |
| 14 | .github/workflows/docs.yml | Docs publishing | Context/Test |
| 15 | mkdocs.yml | Documentation site config | Supp/Docs |
| 16 | docs/index.md | Documentation landing page | Supp/Docs |
| 17 | docs/getting-started/installation.md | Install instructions (GitHub-only distribution) | Supp/Docs |
| 18 | docs/user-guide/cli.md | CLI reference | Supp/Docs |
| 19 | docs/development/contributing.md | Contributor guide | Supp/Docs |
| 20 | example/README.md | References wheels, GitHub Releases | Supp/Docs |
| 21 | src/evee/cli/commands/new.py | `_GIT_BASE` URL hardcoded to github.com/microsoft/evee | Context/Test |
| 22 | src/evee/cli/main.py | Package name for version lookup | Context/Test |

**Q1** *(anchored, precise)*:
I need to evaluate Evee's OSS release strategy. I need to understand the current distribution model: build infrastructure (`tools/build/build_wheels.sh`), release workflow (`.github/workflows/release.yml`), version calculation, CI/CD pipelines including CodeQL scanning, and the docs publishing workflow. I also need the package metadata in all `pyproject.toml` files (root, MLflow, AzureML), the hardcoded GitHub URLs in CLI commands, installation documentation, security/compliance files (LICENSE, SECURITY.md, CONTRIBUTING.md), and the docs site configuration. This is an investigation task to produce a decision matrix for Microsoft vs external hosting and PyPI vs GitHub Releases.

**Q2** *(mixed, scoped)*:
I'm evaluating how to release Evee publicly. I need to review the current build and release infrastructure, package metadata, distribution model, CI/CD pipelines, security scanning, and documentation. Where are the release workflows, build scripts, and compliance-related files?

**Q3** *(unanchored, open)*:
What does Evee's release and distribution setup look like? I need to evaluate whether to publish on PyPI or stick with GitHub Releases, and whether to stay in the Microsoft org. Where are the relevant build, release, and compliance files?

---

### #226 — Set default MLflow tracking URI to sqlite:///mlflow.db

**GitHub**: https://github.com/microsoft/evee/issues/226  
**Labels**: enhancement  
**Summary**: Change default MLflow tracking URI from filesystem backend (`./mlruns`) to `sqlite:///mlflow.db` due to filesystem backend deprecation in February 2026.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | packages/evee-mlflow/src/evee_mlflow/config.py | `MLflowTrackingConfig`: tracking_uri default, artifact_location | Edit |
| 2 | packages/evee-mlflow/src/evee_mlflow/tracking.py | `MLflowBackend.on_startup()` with `./mlruns` reference | Edit |
| 3 | packages/evee-mlflow/pyproject.toml | MLflow backend entry point | Supp/Docs |
| 4 | packages/evee-mlflow/tests/test_mlflow_backend.py | Tests for defaults, on_startup | Context/Test |
| 5 | packages/evee-mlflow/tests/test_integration.py | Integration tests using temp mlruns | Context/Test |
| 6 | packages/evee-mlflow/tests/test_mlflow_autolog.py | Autolog tests constructing config | Context/Test |
| 7 | src/evee/config/models.py | `TrackingBackendConfig` with tracking_uri default | Edit |
| 8 | tests/evee/config/test_models.py | Tests for TrackingBackendConfig defaults | Context/Test |
| 9 | src/evee/mcp/resources/config.py | Config schema showing tracking_uri example | Edit |
| 10 | docs/backends/mlflow.md | MLflow backend documentation | Supp/Docs |
| 11 | docs/user-guide/configuration.md | Configuration reference | Supp/Docs |
| 12 | docs/getting-started/quickstart.md | Quickstart tracking_uri example | Supp/Docs |
| 13 | docs/development/contributing.md | Development guide | Supp/Docs |
| 14 | docs/troubleshooting.md | Troubleshooting tracking_uri errors | Supp/Docs |
| 15 | docs/user-guide/cli.md | CLI config examples | Supp/Docs |
| 16 | example/experiment/config.mlflow.yaml | Example MLflow config | Supp/Docs |
| 17 | example/Makefile | MLflow UI command with backend-store-uri | Supp/Docs |
| 18 | example/.amlignore | Lists mlflow.db in ignore | Supp/Docs |
| 19 | samples/agent-sample/experiment/config.yaml | Agent sample config | Supp/Docs |
| 20 | samples/coding-sample/experiment/config.yaml | Coding sample config | Supp/Docs |
| 21 | src/evee/cli/templates/overlays/mlflow/experiment/config.yaml | MLflow template | Edit |
| 22 | cspell.config.yaml | "mlruns" in custom words | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to change the default MLflow tracking URI from the filesystem backend (`./mlruns`) to `sqlite:///mlflow.db` in the evee-mlflow package. The `MLflowTrackingConfig` in `packages/evee-mlflow/src/evee_mlflow/config.py` has `tracking_uri` defaulting to None with artifact_location defaulting to `./mlruns`. The `MLflowBackend.on_startup()` in `tracking.py` logs the `./mlruns directory` message when no URI is set. I need to update the config default, the startup logic, all tests that assert on `./mlruns`, example/sample configs, the MLflow project template, documentation references, and the core `TrackingBackendConfig` default.

**Q2** *(mixed, scoped)*:
Change the default MLflow tracking URI to use SQLite instead of the filesystem backend being deprecated. I need to find where the MLflow tracking URI default is set in the evee-mlflow package, all tests and configs that reference `./mlruns`, and documentation that describes the default tracking setup.

**Q3** *(unanchored, open)*:
MLflow's filesystem backend is being deprecated. I need to change Evee's default tracking to use SQLite instead. Where is the MLflow backend configured and what references `./mlruns` or the tracking URI?

---

### #233 — Early stop for evaluation on certain threshold of errors

**GitHub**: https://github.com/microsoft/evee/issues/233  
**Labels**: —  
**Summary**: Two parts: (1) Inference early stop when too many errors (bugs or permissions). (2) Evaluation optimization — mark metrics as optimization targets and stop when no significant improvement (e.g., grid search over K documents for RAG).

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/evaluation/model_evaluator.py | Core evaluator with `_evaluate_model_sync/async`, `_run_evaluation_loop`, `_evaluate_record` — error handling and loop logic | Edit |
| 2 | src/evee/evaluation/evaluate.py | `evaluate_main` entry point | Edit |
| 3 | src/evee/evaluation/progress_tracker.py | Progress tracking during evaluation | Edit |
| 4 | src/evee/evaluation/metrics_aggregator.py | Metrics aggregation | Edit |
| 5 | src/evee/evaluation/__init__.py | Evaluation module | Edit |
| 6 | src/evee/config/models.py | Config models — need early stop threshold and optimization target fields | Edit |
| 7 | src/evee/config/__init__.py | Config exports | Edit |
| 8 | src/evee/execution/experiment_runner.py | ExperimentRunner orchestrating runs | Edit |
| 9 | src/evee/execution/preflight.py | Preflight checks (MODEL_COUNT_WARNING_THRESHOLD) | Edit |
| 10 | src/evee/core/base_model.py | BaseModel with infer/infer_async — inference interface | Edit |
| 11 | src/evee/core/base_metric.py | BaseMetric with compute — metric interface | Edit |
| 12 | src/evee/core/models/evaluation_output.py | EvaluationOutput | Edit |
| 13 | src/evee/tracking/events.py | InferenceStartEvent, InferenceCompletedEvent | Edit |
| 14 | src/evee/tracking/backend.py | TrackingBackend with inference event hooks | Edit |
| 15 | tests/evee/evaluation/test_model_evaluator_evaluation.py | Evaluation tests | Context/Test |
| 16 | tests/evee/evaluation/test_model_evaluator_init.py | Evaluator init tests | Context/Test |
| 17 | tests/evee/evaluation/test_model_evaluator_metrics.py | Metric computation tests | Context/Test |
| 18 | tests/evee/evaluation/test_model_evaluator_output.py | Output tests | Context/Test |
| 19 | tests/evee/evaluation/test_progress_tracker.py | Progress tracker tests | Context/Test |
| 20 | tests/evee/evaluation/test_metrics_aggregator.py | Aggregator tests | Context/Test |
| 21 | tests/evee/config/test_models.py | Config model tests | Context/Test |
| 22 | docs/user-guide/configuration.md | Config docs — new early stop fields | Supp/Docs |
| 23 | docs/troubleshooting.md | Error threshold guidance | Supp/Docs |
| 24 | docs/design/architecture.md | Architecture doc | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to implement early stopping in Evee's evaluation pipeline. Part 1: In the inference phase (`_run_evaluation_loop` in `ModelEvaluator`), count consecutive or total errors and abort early if a threshold is exceeded, alerting the user. Part 2: In the evaluation phase, mark metrics as optimization targets in `MetricConfig` and stop when no significant improvement is observed across hyperparameter variations. I need the evaluation loop code, config models for new threshold fields, progress tracking, error handling patterns, and tests for the evaluation pipeline.

**Q2** *(mixed, scoped)*:
Add early stopping to Evee's evaluation. If too many inference errors occur, stop the evaluation early instead of running the full dataset. Also explore metric-based optimization stopping. I need to understand the evaluation loop, error handling, config schema, and progress tracking.

**Q3** *(unanchored, open)*:
Evee should stop evaluation early when there are too many errors. Where is the evaluation loop and how does error handling work? Can we also optimize the evaluation process to stop when metrics aren't improving?

---

### #234 — DEMO | use mcp server to create an agent evaluation

**GitHub**: https://github.com/microsoft/evee/issues/234  
**Labels**: kind/feature  
**Summary**: Demonstrate agent evaluation scaffolding using the MCP server. Fix any issues in MCP logic or documentation tools. Record a demo showing MCP can produce valuable projects for customers.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/mcp/server.py | MCP server with all tool handlers | Edit |
| 2 | src/evee/mcp/__init__.py | MCP package API | Edit |
| 3 | src/evee/mcp/constants.py | Server constants | Edit |
| 4 | src/evee/mcp/tools/experiment.py | run_experiment tool | Edit |
| 5 | src/evee/mcp/tools/validation.py | validate_config tool | Edit |
| 6 | src/evee/mcp/tools/discovery.py | list_components tool | Edit |
| 7 | src/evee/mcp/tools/view_results.py | view_results tool | Edit |
| 8 | src/evee/mcp/tools/base.py | Base tool class | Edit |
| 9 | src/evee/mcp/tools/__init__.py | Tool registry | Edit |
| 10 | src/evee/mcp/resources/config.py | Config schema (agent eval YAML example) | Edit |
| 11 | src/evee/mcp/resources/connections.py | Connections docs | Edit |
| 12 | src/evee/mcp/resources/model_patterns.py | Model patterns docs | Edit |
| 13 | src/evee/mcp/resources/metric_patterns.py | Metric patterns docs | Edit |
| 14 | src/evee/mcp/resources/patterns.py | Decorator patterns docs | Edit |
| 15 | src/evee/mcp/resources/evaluators.py | Azure evaluators resource | Edit |
| 16 | src/evee/mcp/resources/app_viewer.py | Results viewer | Edit |
| 17 | src/evee/mcp/resources/base.py | Base resource class | Edit |
| 18 | src/evee/mcp/resources/__init__.py | Resource registry | Edit |
| 19 | src/evee/mcp/README.md | MCP documentation | Edit |
| 20 | src/evee/cli/templates/base/.vscode/mcp.json | MCP VS Code config template | Edit |
| 21 | src/evee/execution/runner.py | ExecutionRunner | Edit |
| 22 | src/evee/execution/environment.py | EnvironmentResolver | Edit |
| 23 | tests/test_mcp_server.py | Basic MCP tests | Context/Test |
| 24 | tests/mcp/test_e2e.py | E2E MCP tests | Context/Test |
| 25 | tests/mcp/test_tools.py | Tool tests | Context/Test |
| 26 | tests/mcp/test_resources.py | Resource tests | Context/Test |
| 27 | tests/mcp/conftest.py | MCP test fixtures | Context/Test |
| 28 | docs/user-guide/mcp-server.md | MCP user docs | Supp/Docs |
| 29 | docs/design/mcp-server.md | MCP design doc | Supp/Docs |
| 30 | samples/agent-sample/README.md | Agent sample README | Supp/Docs |
| 31 | samples/agent-sample/experiment/config.yaml | Agent sample config | Supp/Docs |
| 32 | samples/agent-sample/models/baseline/baseline.py | Agent baseline model | Supp/Docs |
| 33 | samples/agent-sample/models/foundry_agent/agent.py | Foundry agent model | Supp/Docs |
| 34 | samples/agent-sample/models/maf_agent/agent.py | MAF agent model | Supp/Docs |
| 35 | samples/agent-sample/models/n8n_agent/agent.py | n8n agent model | Supp/Docs |
| 36 | samples/agent-sample/metrics/agent_tool_call_f1_metric.py | Agent F1 metric | Supp/Docs |
| 37 | samples/agent-sample/metrics/rouge_metric.py | Agent ROUGE metric | Supp/Docs |
| 38 | samples/agent-sample/pyproject.toml | Agent sample dependencies | Supp/Docs |
| 39 | pyproject.toml | Core MCP dependency | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to demonstrate creating an agent evaluation end-to-end using Evee's MCP server. The demo should show an AI assistant using MCP tools (list_components, validate_config, run_experiment, view_results) to scaffold and run an agent evaluation. I need all MCP tools and resources, the agent sample project in `samples/agent-sample/` as reference, the MCP VS Code config template, the execution infrastructure, and any documentation or test gaps to fix before the demo.

**Q2** *(mixed, scoped)*:
I want to demo Evee's MCP server creating an agent evaluation project. I need to understand the MCP tools, available resources/patterns, the agent sample project structure, and any issues that might need fixing for a smooth demo flow.

**Q3** *(unanchored, open)*:
How can I use Evee's MCP server to set up an agent evaluation from scratch? I need to create a demo. Where is the MCP server code and is there an example agent evaluation project?

---

### #236 — Add MCP analysis tool for LLM-based model suggestion and markdown reporting

**GitHub**: https://github.com/microsoft/evee/issues/236  
**Labels**: enhancement  
**Summary**: Add a new MCP tool that analyzes experiment results and suggests the best model. Uses MCP sampling capabilities with the user's LLM to produce a comprehensive markdown report.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/mcp/server.py | MCP server — add new tool definition | Edit |
| 2 | src/evee/mcp/tools/base.py | BaseTool, ToolResult — base class for new tool | Edit |
| 3 | src/evee/mcp/tools/__init__.py | Tool registry — register new tool | Edit |
| 4 | src/evee/mcp/tools/view_results.py | ViewResultsTool — reuse results loading pattern | Edit |
| 5 | src/evee/mcp/tools/experiment.py | RunExperimentTool — reference implementation | Edit |
| 6 | src/evee/mcp/tools/validation.py | ValidateConfigTool — reference implementation | Edit |
| 7 | src/evee/mcp/tools/discovery.py | ListComponentsTool — reference implementation | Edit |
| 8 | src/evee/mcp/constants.py | ToolNames — add ANALYZE_RESULTS | Edit |
| 9 | src/evee/mcp/resources/config.py | Config schema resource | Edit |
| 10 | src/evee/mcp/resources/evaluators.py | Evaluators resource | Edit |
| 11 | src/evee/mcp/__init__.py | MCP package init | Edit |
| 12 | src/evee/mcp/README.md | MCP documentation | Edit |
| 13 | tests/mcp/test_tools.py | Tool tests — add analysis tool tests | Context/Test |
| 14 | tests/mcp/test_e2e.py | E2E tests — add analysis tool e2e | Context/Test |
| 15 | tests/mcp/test_resources.py | Resource tests | Context/Test |
| 16 | tests/test_mcp_server.py | Server tests | Context/Test |
| 17 | docs/user-guide/mcp-server.md | MCP user documentation | Supp/Docs |
| 18 | docs/design/mcp-server.md | MCP design doc | Supp/Docs |
| 19 | pyproject.toml | Root pyproject | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to add a new MCP tool called `analyze_results` that analyzes experiment results and suggests the best model using LLM sampling. The tool should follow the existing tool pattern: extend `BaseTool` from `src/evee/mcp/tools/base.py`, register in `tools/__init__.py`, add a handler in `server.py`, and add the tool name to `constants.py`. It should reuse the results-loading logic from `ViewResultsTool` and leverage MCP's sampling capabilities to produce a markdown report. I also need to add tests in `tests/mcp/test_tools.py` and update MCP documentation.

**Q2** *(mixed, scoped)*:
Add an MCP tool that analyzes experiment results and recommends the best model. I need to understand the existing MCP tool architecture (BaseTool, tool registry, server handler), how results are loaded, and how MCP sampling works for LLM-powered analysis.

**Q3** *(unanchored, open)*:
How do I add a new tool to Evee's MCP server? I want one that analyzes results and suggests the best model using AI. Where are the existing tools and how do they work?

---

### #240 — Remote server evaluation

**GitHub**: https://github.com/microsoft/evee/issues/240  
**Labels**: —  
**Summary**: Add a Dedicated Server compute backend as a new package (`packages/evee-server/`) enabling remote evaluation on any Linux VM/container/AKS pod. Includes FastAPI server daemon, code sync, CLI commands, deployment scripts.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/compute/backend.py | ComputeBackend ABC, RunContext, JobInfo, JobStatus | Edit |
| 2 | src/evee/compute/local_compute_backend.py | LocalComputeBackend — reference implementation | Edit |
| 3 | src/evee/config/models.py | ComputeBackendConfig, ExecutionConfig, WorkspaceConfig | Edit |
| 4 | src/evee/execution/experiment_runner.py | `_get_backend_class` — loads backends via entry points | Edit |
| 5 | src/evee/cli/commands/compute.py | VALID_COMPUTE_BACKENDS, COMPUTE_TEMPLATES, BACKEND_PACKAGES | Edit |
| 6 | src/evee/cli/commands/run.py | `--remote` flag | Edit |
| 7 | src/evee/cli/main.py | CLI entry point — add `evee server` command group | Edit |
| 8 | src/evee/cli/constants.py | CLI constants | Edit |
| 9 | src/evee/cli/utils/backend_helpers.py | Backend management utilities | Edit |
| 10 | src/evee/cli/utils/config_manager.py | Config YAML management | Edit |
| 11 | src/evee/execution/runner.py | ExecutionRunner | Edit |
| 12 | src/evee/execution/environment.py | EnvironmentResolver | Edit |
| 13 | src/evee/execution/preflight.py | Preflight checks | Edit |
| 14 | packages/evee-azureml/pyproject.toml | Reference package structure with entry points | Supp/Docs |
| 15 | packages/evee-azureml/src/evee_azureml/compute.py | AzureMLComputeBackend — reference | Context/Test |
| 16 | packages/evee-azureml/src/evee_azureml/config.py | AzureML config models — reference | Context/Test |
| 17 | packages/evee-azureml/src/evee_azureml/auth.py | Auth module — reference | Context/Test |
| 18 | packages/evee-azureml/src/evee_azureml/__init__.py | Package init — reference | Context/Test |
| 19 | packages/evee-azureml/tests/test_azureml_backend_pkg.py | Backend tests — reference | Context/Test |
| 20 | pyproject.toml | Core entry points for `evee.compute_backends` | Supp/Docs |
| 21 | Makefile | Would need `setup-server`, `test-server` targets | Supp/Docs |
| 22 | CONTRIBUTING.md | Extension package structure | Supp/Docs |
| 23 | docs/backends/overview.md | Backend overview | Supp/Docs |
| 24 | docs/backends/custom-backends.md | Custom backend guide | Supp/Docs |
| 25 | docs/backends/azureml.md | AzureML docs — reference pattern | Supp/Docs |
| 26 | docs/user-guide/cli.md | CLI reference | Supp/Docs |
| 27 | docs/user-guide/configuration.md | Config reference | Supp/Docs |
| 28 | docs/design/architecture.md | Architecture doc | Supp/Docs |
| 29 | docs/troubleshooting.md | Troubleshooting | Supp/Docs |
| 30 | docs/getting-started/glossary.md | Glossary | Supp/Docs |
| 31 | mkdocs.yml | Docs navigation | Supp/Docs |
| 32 | tests/evee/execution/test_experiment_runner.py | Runner tests | Context/Test |
| 33 | tests/evee/cli/test_backend_integration.py | Backend integration tests | Context/Test |
| 34 | tests/evee/cli/test_backend_template_compatibility.py | Backend template tests | Context/Test |
| 35 | tests/evee/compute/test_local_compute_backend.py | Compute backend test reference | Context/Test |
| 36 | src/evee/compute/utils/wheels_provisioner.py | Wheels provisioner for remote env setup | Edit |

**Q1** *(anchored, precise)*:
I need to create a new `packages/evee-server/` package implementing a Dedicated Server compute backend for Evee. It should follow the `packages/evee-azureml/` pattern: implement `ComputeBackend` ABC from `src/evee/compute/backend.py`, register via entry points in `pyproject.toml`, include a FastAPI server daemon, code sync module, and HTTP client. The backend's `submit()` method should sync code, provision the remote env (reuse `wheels_provisioner`), and submit via HTTP. I need CLI commands for `evee server status/start`, updates to `VALID_COMPUTE_BACKENDS` in `cli/commands/compute.py`, deployment scripts, Makefile targets, and documentation.

**Q2** *(mixed, scoped)*:
Add a remote server compute backend to Evee that lets users push evaluations to any remote environment. I need to follow the AzureML backend package pattern, create a new package with compute backend, server daemon, and code sync. Where are the compute backend interfaces, the reference AzureML implementation, and the CLI compute commands?

**Q3** *(unanchored, open)*:
I want to add a way to run Evee evaluations on a remote server instead of locally or on Azure ML. There should be a server component and a client that pushes jobs to it. How do Evee's compute backends work and where is the AzureML one for reference?

---

### #259 — Raise explicit error when .env file is missing

**GitHub**: https://github.com/microsoft/evee/issues/259  
**Labels**: enhancement  
**Summary**: Add startup validation that checks for the `.env` file and raises a clear error if missing. Currently missing `.env` causes unclear downstream errors.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/cli/commands/run.py | CLI run — validates paths but silently skips .env | Edit |
| 2 | src/evee/cli/commands/validate.py | CLI validate — loads .env but silently skips if missing | Edit |
| 3 | src/evee/cli/main.py | CLI entry — loads .env in `_execute_in_project_env`, silently skips | Edit |
| 4 | src/evee/cli/constants.py | `DEFAULT_ENV_FILE = ".env"` | Edit |
| 5 | src/evee/evaluation/model_evaluator.py | `load_dotenv` call unconditionally | Edit |
| 6 | src/evee/evaluation/evaluate.py | Loads .env if env_path provided | Edit |
| 7 | src/evee/execution/experiment_runner.py | Loads .env if env_path provided | Edit |
| 8 | src/evee/execution/preflight.py | Pre-flight checks — add .env existence check | Edit |
| 9 | src/evee/execution/runner.py | ExecutionRunner — env_path parameter | Edit |
| 10 | src/evee/config/models.py | Config and env var interpolation | Edit |
| 11 | src/evee/core/base_model.py | Calls `load_dotenv` in `BaseModel.__init__` | Edit |
| 12 | src/evee/mcp/tools/experiment.py | MCP run_experiment passes env_file | Edit |
| 13 | src/evee/mcp/tools/validation.py | MCP validate_config passes env_file | Edit |
| 14 | src/evee/mcp/server.py | MCP server tool definitions accept env_file | Edit |
| 15 | tests/evee/cli/test_validate_command.py | Validate command tests | Context/Test |
| 16 | tests/evee/execution/test_experiment_runner.py | Runner tests | Context/Test |
| 17 | tests/evee/execution/test_preflight.py | Preflight tests | Context/Test |
| 18 | tests/evee/evaluation/test_model_evaluator_init.py | Evaluator init tests | Context/Test |
| 19 | tests/mcp/test_tools.py | MCP tool tests | Context/Test |
| 20 | tests/mcp/test_e2e.py | MCP E2E tests | Context/Test |
| 21 | docs/troubleshooting.md | Troubleshooting — add missing .env entry | Supp/Docs |
| 22 | docs/getting-started/quickstart.md | Quickstart .env setup | Supp/Docs |
| 23 | docs/user-guide/configuration.md | .env in config hierarchy | Supp/Docs |
| 24 | docs/user-guide/cli.md | `--env` flag documentation | Supp/Docs |
| 25 | docs/design/architecture.md | Config resolution priority | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to add startup validation in Evee that raises an explicit error when the `.env` file is missing. Currently, `.env` loading is handled silently in multiple places: `cli/commands/run.py`, `cli/commands/validate.py`, `cli/main.py` (`_execute_in_project_env`), `evaluation/model_evaluator.py`, `evaluation/evaluate.py`, `execution/experiment_runner.py`, and `core/base_model.py`. The `DEFAULT_ENV_FILE` constant is in `cli/constants.py`. I should add the check in `execution/preflight.py` (which already does pre-flight validation) and ensure it surfaces a clear error message before any downstream failures.

**Q2** *(mixed, scoped)*:
Add a check that raises an explicit error when the `.env` file is missing in Evee. Currently it fails silently and causes confusing downstream errors. I need to find all places where `.env` is loaded, the preflight validation system, and the constants defining the default `.env` path.

**Q3** *(unanchored, open)*:
Evee should tell users clearly when their `.env` file is missing instead of failing with confusing errors later. Where does Evee load the `.env` file and where should this validation go?

---

### #260 — Add Config Flag to Disable rich Progress Bars in CI

**GitHub**: https://github.com/microsoft/evee/issues/260  
**Labels**: enhancement  
**Summary**: Expose a config.yaml flag to disable Rich progress bars. Already suppressed for MCP/AzureML. Make it user-configurable for CI environments where rich output clutters logs.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/config/models.py | RuntimeConfig, ExperimentConfig — needs new flag | Edit |
| 2 | src/evee/config/__init__.py | Config exports | Edit |
| 3 | src/evee/utils/environment.py | `is_rich_compatible_environment()`, `is_mcp_mode()` — needs config flag check | Edit |
| 4 | src/evee/evaluation/progress_tracker.py | ProgressTracker uses `is_rich_compatible_environment()` | Edit |
| 5 | src/evee/logging/logger.py | `setup_logger`, `_create_console_handler` — uses `is_rich_compatible_environment()` | Edit |
| 6 | src/evee/evaluation/model_evaluator.py | Creates ProgressTracker | Edit |
| 7 | src/evee/cli/commands/run.py | Run command checks `is_mcp_mode()` | Edit |
| 8 | src/evee/utils/__init__.py | Exports `is_rich_compatible_environment` | Edit |
| 9 | tests/evee/evaluation/test_progress_tracker.py | Progress tracker tests | Context/Test |
| 10 | tests/evee/evaluation/test_model_evaluator_evaluation.py | Rich progress tests | Context/Test |
| 11 | tests/evee/cli/test_main.py | Run command MCP mode tests | Context/Test |
| 12 | tests/evee/conftest.py | Sets `EVEE_DISABLE_RICH_LOGGING=true` | Context/Test |
| 13 | tests/evee/log/test_logger.py | Logger Rich env var tests | Context/Test |
| 14 | pyproject.toml | No new deps | Supp/Docs |
| 15 | docs/user-guide/configuration.md | Config reference — new flag | Supp/Docs |
| 16 | docs/user-guide/cli.md | Environment variables table | Supp/Docs |
| 17 | docs/troubleshooting.md | Disable Rich Console section | Supp/Docs |
| 18 | src/evee/mcp/resources/config.py | Config schema MCP resource | Edit |

**Q1** *(anchored, precise)*:
I need to add a configuration flag in `config.yaml` to disable Rich progress bars for CI environments. The `is_rich_compatible_environment()` function in `src/evee/utils/environment.py` already checks `EVEE_DISABLE_RICH_LOGGING` env var and MCP mode. The `ProgressTracker` in `evaluation/progress_tracker.py` and logger in `logging/logger.py` both use this function. I need to add a new field to `RuntimeConfig` or `ExperimentConfig` in `config/models.py`, update the environment check to also consult the config flag, and update tests and documentation.

**Q2** *(mixed, scoped)*:
Add a config.yaml flag to disable Rich progress bars. Evee already suppresses them for MCP and AzureML, but users need to disable them for CI too. I need to find where Rich environment detection happens, the config model, and the progress tracker/logger code.

**Q3** *(unanchored, open)*:
Rich progress bars clutter CI logs. I need to add a way to disable them via config. Where does Evee decide whether to show Rich output and how do I add a config flag?

---

### #261 — Add pytest-aitest MCP interface test suite

**GitHub**: https://github.com/microsoft/evee/issues/261  
**Labels**: —  
**Summary**: Add AI interface tests using pytest-aitest validating that an LLM can correctly invoke all five MCP tools from natural language prompts. Minor description rewording in server/validation tools.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/mcp/server.py | MCP server — tool descriptions may need rewording | Edit |
| 2 | src/evee/mcp/__init__.py | MCP package exports | Edit |
| 3 | src/evee/mcp/constants.py | ToolNames, ResourceURIs | Edit |
| 4 | src/evee/mcp/tools/__init__.py | Tool registry | Edit |
| 5 | src/evee/mcp/tools/base.py | BaseTool, ToolMetadata, ToolResult, ToolSchema | Edit |
| 6 | src/evee/mcp/tools/experiment.py | RunExperimentTool | Edit |
| 7 | src/evee/mcp/tools/validation.py | ValidateConfigTool — description rewording | Edit |
| 8 | src/evee/mcp/tools/discovery.py | ListComponentsTool | Edit |
| 9 | src/evee/mcp/tools/view_results.py | ViewResultsTool | Edit |
| 10 | tests/mcp/conftest.py | MCP test fixtures | Context/Test |
| 11 | tests/mcp/test_tools.py | Existing MCP tool tests | Context/Test |
| 12 | tests/mcp/test_e2e.py | Existing E2E tests | Context/Test |
| 13 | tests/mcp/test_resources.py | Resource tests | Context/Test |
| 14 | tests/mcp/__init__.py | Test package | Context/Test |
| 15 | tests/test_mcp_server.py | Basic server tests | Context/Test |
| 16 | pyproject.toml | Need pytest-aitest dev dependency | Supp/Docs |
| 17 | Makefile | Need `test-mcp-aitest` target | Supp/Docs |
| 18 | src/evee/mcp/README.md | MCP docs | Edit |
| 19 | docs/user-guide/mcp-server.md | MCP user docs | Supp/Docs |
| 20 | src/evee/execution/runner.py | ExecutionRunner | Edit |
| 21 | src/evee/execution/environment.py | EnvironmentResolver | Edit |

**Q1** *(anchored, precise)*:
I need to add AI interface tests using `pytest-aitest` for Evee's MCP server. The tests (in `tests/mcp_aitest/`) should validate that an LLM can correctly invoke all 5 MCP tools (`validate_config`, `list_components`, `run_experiment`, `view_results`, `fetch_documentation`) from natural language prompts. I also need to reword the `validate_config` tool description in `tools/validation.py` so LLMs treat validation as advisory, remove empty `warnings: []` from error responses for token reduction, add `pytest-aitest>=0.5.6` to `pyproject.toml` dev dependencies, and add a `test-mcp-aitest` Makefile target.

**Q2** *(mixed, scoped)*:
Add AI-driven integration tests for Evee's MCP server using pytest-aitest. The tests should verify that an LLM can invoke all MCP tools from natural language. I need to understand the existing MCP tool implementations, their descriptions, and the existing test structure. Also need to adjust some tool descriptions.

**Q3** *(unanchored, open)*:
I want to add tests that verify an LLM can use Evee's MCP tools correctly from natural language. There's a library called pytest-aitest for this. Where are the MCP tools and how do I set up these tests?

---

### #262 — Support for Configurable REST-Based Models

**GitHub**: https://github.com/microsoft/evee/issues/262  
**Labels**: enhancement  
**Summary**: Define REST-based models through configuration instead of custom model classes. Specify endpoints, request/response formats, and behavior in config without boilerplate model code.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/core/base_model.py | `@model` decorator, BaseModel, MODEL_REGISTRY | Edit |
| 2 | src/evee/config/models.py | ModelVariantConfig, ConnectionConfig — needs REST model config | Edit |
| 3 | src/evee/config/__init__.py | Config exports | Edit |
| 4 | src/evee/evaluation/model_evaluator.py | `_register_model` — needs REST model handling | Edit |
| 5 | src/evee/core/execution_context.py | ExecutionContext with connections_registry | Edit |
| 6 | src/evee/core/decorator_discovery.py | `discover_components` — REST models bypass this | Edit |
| 7 | src/evee/core/decorator_helpers.py | Parameter injection for model init | Edit |
| 8 | src/evee/cli/commands/model.py | Model CLI commands | Edit |
| 9 | src/evee/cli/utils/model_operations.py | Model file creation, config update | Edit |
| 10 | src/evee/cli/templates/model/empty_model.py | Model template | Edit |
| 11 | src/evee/cli/templates/base/models/baseline.py | Baseline model template | Edit |
| 12 | src/evee/cli/utils/config_manager.py | Config YAML management | Edit |
| 13 | src/evee/cli/commands/validate.py | Validation for REST model config | Edit |
| 14 | src/evee/mcp/resources/model_patterns.py | Model patterns — needs REST pattern | Edit |
| 15 | src/evee/mcp/resources/config.py | Config schema — needs REST model schema | Edit |
| 16 | src/evee/__init__.py | Public API exports | Edit |
| 17 | pyproject.toml | May need httpx/aiohttp dependency | Supp/Docs |
| 18 | docs/user-guide/models.md | Model documentation | Supp/Docs |
| 19 | docs/user-guide/configuration.md | Config reference | Supp/Docs |
| 20 | docs/user-guide/cli.md | CLI reference | Supp/Docs |
| 21 | tests/evee/cli/test_model_commands.py | Model CLI tests | Context/Test |
| 22 | tests/evee/cli/utils/test_config_manager.py | Config manager tests | Context/Test |
| 23 | tests/evee/evaluation/test_model_evaluator_init.py | Model registration tests | Context/Test |
| 24 | tests/evee/evaluation/test_model_evaluator_evaluation.py | Model evaluation tests | Context/Test |
| 25 | example/models/baseline/baseline.py | Example model reference | Supp/Docs |
| 26 | src/evee/cli/templates/overlays/core/experiment/config.yaml | Config template | Edit |

**Q1** *(anchored, precise)*:
I need to add support for configurable REST-based models in Evee. Instead of writing custom `@model` decorated classes for each REST endpoint, users should define REST model configuration in `config.yaml` (endpoint URL, request/response format, headers, auth). This requires changes to `ModelVariantConfig` in `config/models.py`, a new `RestModel` class that doesn't use `@model` decorator but still works with `ModelEvaluator._register_model`, connection handling via `ExecutionContext.connections_registry`, and a REST model template for CLI scaffolding. The model should bypass `decorator_discovery` and instead be resolved from config.

**Q2** *(mixed, scoped)*:
Add configuration-driven REST models to Evee so users don't need to write model classes for simple REST endpoints. I need to understand the model registration system (`@model` decorator, MODEL_REGISTRY, `_register_model`), the config schema, and how the evaluation pipeline works with models.

**Q3** *(unanchored, open)*:
Users keep writing model classes that just wrap REST calls. I want to make REST models configurable instead. Where is the model system in Evee and how does model registration work?

---

### #263 — Implement Foundry metric automatically and fix metric scaffolding path

**GitHub**: https://github.com/microsoft/evee/issues/263  
**Labels**: enhancement  
**Summary**: When a Foundry metric is selected, generate a fully implemented and callable metric class (not just a stub). Fix metric file placement to the correct directory.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/cli/commands/metric.py | `_create_azure_metric()`, `_generate_metric_content()`, `_build_config_mapping()` | Edit |
| 2 | src/evee/cli/utils/metric_operations.py | `format_init_params()`, `get_metric_file_path()` | Edit |
| 3 | src/evee/cli/templates/metrics/azure_evaluator_metric.py | Template with `NotImplementedError` stub | Edit |
| 4 | src/evee/cli/azure_evaluators.json | Evaluator metadata | Edit |
| 5 | scripts/generate_azure_evaluators.py | Metadata generation script | Edit |
| 6 | src/evee/cli/constants.py | DEFAULT_METRICS_DIR, METRIC_FILE_SUFFIX | Edit |
| 7 | src/evee/cli/utils/validators.py | `validate_metric_name()` | Edit |
| 8 | src/evee/cli/utils/init_file_manager.py | `add_import_to_init()` | Edit |
| 9 | src/evee/cli/utils/config_manager.py | `add_metric()` | Edit |
| 10 | src/evee/core/base_metric.py | `@metric` decorator, BaseMetric, MetricWrapper | Edit |
| 11 | src/evee/core/decorator_helpers.py | Validation helpers | Edit |
| 12 | src/evee/config/models.py | MetricConfig | Edit |
| 13 | src/evee/evaluation/model_evaluator.py | `_register_metric()` | Edit |
| 14 | src/evee/mcp/resources/metric_patterns.py | Metric implementation patterns | Edit |
| 15 | src/evee/mcp/resources/evaluators.py | Evaluators resource | Edit |
| 16 | example/metrics/f1score_metric.py | Reference: working metric | Supp/Docs |
| 17 | example/metrics/rouge_metric.py | Reference: working metric | Supp/Docs |
| 18 | example/metrics/token_usage_metric.py | Reference: working metric | Supp/Docs |
| 19 | example/metrics/llm_judge_metric.py | Reference: complex metric | Supp/Docs |
| 20 | tests/evee/cli/test_metric_commands.py | Foundry metric creation tests | Context/Test |
| 21 | tests/evee/cli/utils/test_metric_operations.py | Metric operations tests | Context/Test |
| 22 | tests/evee/cli/utils/test_init_file_manager.py | Init file tests | Context/Test |
| 23 | tests/evee/test_azure_evaluators_metadata.py | Evaluator metadata tests | Context/Test |
| 24 | tests/scripts/test_generate_azure_evaluators.py | Metadata generation tests | Context/Test |
| 25 | tests/evee/core/test_base_metric.py | Metric registration tests | Context/Test |
| 26 | docs/user-guide/metrics.md | Metric implementation guide | Supp/Docs |
| 27 | docs/user-guide/cli.md | CLI metric add reference | Supp/Docs |
| 28 | docs/user-guide/configuration.md | Metric config reference | Supp/Docs |
| 29 | docs/troubleshooting.md | Metric errors | Supp/Docs |
| 30 | docs/design/mcp-server.md | References evaluators.json and scaffolding | Supp/Docs |
| 31 | pyproject.toml | azure-ai-evaluation dependency | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to make Foundry metric scaffolding fully implement the metric class instead of generating a stub with `NotImplementedError`. The `_create_azure_metric()` in `cli/commands/metric.py` uses the template at `cli/templates/metrics/azure_evaluator_metric.py` which produces a non-functional stub. I need to use the evaluator metadata from `cli/azure_evaluators.json` (parameters, callable_params) to generate a complete `compute()` and `aggregate()` implementation. Also fix `get_metric_file_path()` in `cli/utils/metric_operations.py` which places files in the wrong directory. Reference working implementations like `example/metrics/f1score_metric.py`.

**Q2** *(mixed, scoped)*:
Fix Foundry metric scaffolding to generate fully functional metric implementations instead of stubs. Also fix the file path where scaffolded metrics are created. I need the metric CLI commands, template system, evaluator metadata, and reference metric implementations.

**Q3** *(unanchored, open)*:
When I add a Foundry metric via the CLI, it creates a stub that doesn't work. I need it to generate a working implementation. Also, files end up in the wrong directory. Where is the metric scaffolding code?

---

### #268 — Add support for configuring metric sets for different use cases (1P and 3P RAI)

**GitHub**: https://github.com/microsoft/evee/issues/268  
**Labels**: enhancement  
**Summary**: Enable configurable metric sets (presets) for different RAI use cases. Support 1P (First Party) and 3P (Third Party) Responsible AI requirements via configuration.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/config/models.py | MetricConfig, ExperimentConfig — needs metric_sets or preset support | Edit |
| 2 | src/evee/cli/utils/config_manager.py | Config management — set-based operations | Edit |
| 3 | src/evee/cli/utils/metric_operations.py | Metric config operations — batch/set support | Edit |
| 4 | src/evee/cli/commands/metric.py | Metric CLI — set-based commands | Edit |
| 5 | src/evee/cli/constants.py | New constants for metric set types | Edit |
| 6 | src/evee/cli/azure_evaluators.json | Evaluator metadata — categorization source | Edit |
| 7 | scripts/generate_azure_evaluators.py | Metadata generation — add category/tags | Edit |
| 8 | src/evee/evaluation/model_evaluator.py | `_register_metric`, `_get_metrics_for_model` — resolve sets | Edit |
| 9 | src/evee/evaluation/metrics_aggregator.py | Set-grouped aggregation | Edit |
| 10 | src/evee/core/base_metric.py | METRIC_REGISTRY | Edit |
| 11 | src/evee/core/decorator_discovery.py | Component discovery | Edit |
| 12 | src/evee/cli/templates/metrics/azure_evaluator_metric.py | Template for set metrics | Edit |
| 13 | src/evee/cli/templates/metrics/empty_metric.py | Template for custom metrics | Edit |
| 14 | src/evee/mcp/resources/config.py | Config schema — metric_sets | Edit |
| 15 | src/evee/mcp/resources/evaluators.py | Categorized evaluator sets | Edit |
| 16 | src/evee/mcp/resources/metric_patterns.py | Metric patterns | Edit |
| 17 | tests/evee/config/test_models.py | Config tests — metric set model | Context/Test |
| 18 | tests/evee/evaluation/test_model_evaluator_metrics.py | Set-based resolution tests | Context/Test |
| 19 | tests/evee/evaluation/test_model_evaluator_init.py | Registration with sets | Context/Test |
| 20 | tests/evee/cli/test_metric_commands.py | CLI metric set tests | Context/Test |
| 21 | tests/evee/cli/utils/test_metric_operations.py | Set-based config tests | Context/Test |
| 22 | tests/evee/cli/utils/test_config_manager.py | Config set operations | Context/Test |
| 23 | tests/evee/test_azure_evaluators_metadata.py | Evaluator metadata tests | Context/Test |
| 24 | tests/scripts/test_generate_azure_evaluators.py | Generation tests | Context/Test |
| 25 | docs/user-guide/configuration.md | Metric sets config docs | Supp/Docs |
| 26 | docs/user-guide/metrics.md | Metric sets guide | Supp/Docs |
| 27 | docs/user-guide/cli.md | CLI metric set commands | Supp/Docs |
| 28 | docs/getting-started/glossary.md | Metric Set definition | Supp/Docs |
| 29 | docs/troubleshooting.md | Troubleshooting | Supp/Docs |
| 30 | docs/design/architecture.md | Architecture diagrams | Supp/Docs |

**Q1** *(anchored, precise)*:
I need to add configurable metric sets (presets) to Evee for Responsible AI compliance. Users should be able to specify a metric set name (e.g., "rai_1p", "rai_3p") in their config and have it resolve to a predefined list of metrics with specific parameters. This requires a new config model for metric sets in `config/models.py`, resolution logic in `ModelEvaluator._get_metrics_for_model()`, CLI commands for managing sets in `cli/commands/metric.py`, categorization metadata in `cli/azure_evaluators.json`, and documentation covering 1P and 3P RAI use cases.

**Q2** *(mixed, scoped)*:
Add metric set presets to Evee so users can select pre-configured groups of metrics for RAI (Responsible AI) evaluation. I need to understand the metric configuration schema, how metrics are registered and resolved, the evaluator metadata, and the CLI commands for metric management.

**Q3** *(unanchored, open)*:
Different users need different sets of metrics for compliance (1P vs 3P RAI). I want to add preset metric groups that users can select by name. How does Evee handle metric configuration and where would I add metric set support?

---

### #275 — Support Reusing Metric Implementations with Custom Instance Names

**GitHub**: https://github.com/microsoft/evee/issues/275  
**Labels**: kind/feature  
**Summary**: Enable reusing the same metric class multiple times with different parameters (e.g., different prompts). Currently `name` is used for both implementation lookup and reporting labels. Separate these concerns via `entry_point` or `display_name`.

| # | File | Relevance | Category |
|---|------|-----------|----------|
| 1 | src/evee/core/base_metric.py | METRIC_REGISTRY, `@metric` decorator, MetricWrapper — name = lookup key | Edit |
| 2 | src/evee/core/decorator_helpers.py | `get_missing_config_params`, `get_parameters_from_config` | Edit |
| 3 | src/evee/core/decorator_discovery.py | `discover_components` populating METRIC_REGISTRY | Edit |
| 4 | src/evee/config/models.py | MetricConfig — needs `entry_point` or `display_name` field | Edit |
| 5 | src/evee/evaluation/model_evaluator.py | `_register_metric` uses name for registry key and labeling | Edit |
| 6 | src/evee/evaluation/metrics_aggregator.py | Uses metric_name as aggregation key and prefix | Edit |
| 7 | src/evee/cli/commands/metric.py | Metric scaffolding uses name for both registration and config | Edit |
| 8 | src/evee/cli/commands/list.py | Lists metrics by METRIC_REGISTRY key | Edit |
| 9 | src/evee/cli/commands/validate.py | Looks up metric by config name | Edit |
| 10 | src/evee/cli/utils/metric_operations.py | `add_metric_to_config`, `metric_exists_in_config` | Edit |
| 11 | src/evee/cli/utils/config_manager.py | `add_metric`, `get_metrics` | Edit |
| 12 | src/evee/cli/constants.py | Metric-related constants | Edit |
| 13 | src/evee/cli/templates/metrics/empty_metric.py | Template uses `{name}` for `@metric` | Edit |
| 14 | src/evee/cli/templates/metrics/azure_evaluator_metric.py | Template uses `{name}` for `@metric` | Edit |
| 15 | src/evee/mcp/resources/config.py | Metric config schema docs | Edit |
| 16 | src/evee/mcp/resources/metric_patterns.py | Metric patterns | Edit |
| 17 | tests/evee/config/test_models.py | MetricConfig tests | Context/Test |
| 18 | tests/evee/core/test_base_metric.py | METRIC_REGISTRY and `@metric` tests | Context/Test |
| 19 | tests/evee/evaluation/test_model_evaluator_metrics.py | Metrics computation tests | Context/Test |
| 20 | tests/evee/evaluation/test_model_evaluator_init.py | `_register_metric` tests | Context/Test |
| 21 | tests/evee/evaluation/test_model_evaluator_evaluation.py | Evaluation loop tests | Context/Test |
| 22 | tests/evee/evaluation/test_metrics_aggregator.py | Aggregation tests | Context/Test |
| 23 | tests/evee/cli/test_metric_commands.py | Metric CLI tests | Context/Test |
| 24 | tests/evee/cli/test_list_command.py | List metrics tests | Context/Test |
| 25 | tests/evee/cli/test_validate_command.py | Validation tests | Context/Test |
| 26 | tests/evee/cli/utils/test_metric_operations.py | Metric operations tests | Context/Test |
| 27 | tests/evee/conftest.py | Mock fixtures (already has entry_point field) | Context/Test |
| 28 | docs/user-guide/metrics.md | Metric documentation | Supp/Docs |
| 29 | docs/user-guide/configuration.md | MetricConfig fields | Supp/Docs |
| 30 | docs/getting-started/glossary.md | Metric, METRIC_REGISTRY definitions | Supp/Docs |
| 31 | docs/troubleshooting.md | Metric Not Found errors | Supp/Docs |
| 32 | docs/design/architecture.md | METRIC_REGISTRY architecture | Supp/Docs |
| 33 | src/evee/__init__.py | Exports metric decorator | Edit |

**Q1** *(anchored, precise)*:
I need to decouple metric implementation lookup from display names in Evee. Currently `MetricConfig.name` in `config/models.py` serves as both the `METRIC_REGISTRY` lookup key (in `base_metric.py`) and the reporting label (in `metrics_aggregator.py`, `model_evaluator.py`). To reuse the same metric class with different parameters (e.g., LLM judge with different prompts), I need either an `entry_point` field for implementation lookup while `name` becomes display-only, or a `display_name` field for reporting while `name` stays as lookup key. This affects `_register_metric()`, metric templates, CLI commands, validation, list command, aggregation, and all related tests.

**Q2** *(mixed, scoped)*:
Evee can't reuse the same metric with different configurations because `name` is used for both lookup and display. I need to separate these concerns so I can have "Coherence" and "Violence" both using the `llm_judge` metric with different prompts. Where is the metric registry, config model, evaluation pipeline, and CLI metric management?

**Q3** *(unanchored, open)*:
I want to use the same metric class multiple times with different parameters, but Evee's metric naming system doesn't allow it. How does metric registration and lookup work, and where would I change it?

---

## Issue Summary

| Issue | GT | E | C | S | Difficulty |
|-------|-----|---|---|---|------------|
| #4 | 12 | 5 | 3 | 4 | Medium |
| #38 | 30 | 22 | 5 | 3 | Complex |
| #57 | 13 | 12 | 1 | 0 | Medium |
| #63 | 18 | 3 | 15 | 0 | Medium |
| #72 | 32 | 12 | 4 | 16 | Complex |
| #108 | 27 | 14 | 7 | 6 | Complex |
| #172 | 38 | 14 | 24 | 0 | Complex |
| #191 | 54 | 17 | 12 | 25 | Complex |
| #192 | 45 | 17 | 9 | 19 | Complex |
| #193 | 15 | 6 | 5 | 4 | Medium |
| #201 | 45 | 0 | 8 | 37 | Complex |
| #210 | 22 | 0 | 8 | 14 | Medium |
| #226 | 22 | 5 | 4 | 13 | Medium |
| #233 | 24 | 14 | 7 | 3 | Medium |
| #234 | 39 | 22 | 5 | 12 | Complex |
| #236 | 19 | 12 | 4 | 3 | Medium |
| #240 | 36 | 14 | 9 | 13 | Complex |
| #259 | 25 | 14 | 6 | 5 | Medium |
| #260 | 18 | 9 | 5 | 4 | Medium |
| #261 | 21 | 12 | 6 | 3 | Medium |
| #262 | 26 | 17 | 4 | 5 | Complex |
| #263 | 31 | 15 | 6 | 10 | Complex |
| #268 | 30 | 16 | 8 | 6 | Complex |
| #275 | 33 | 17 | 11 | 5 | Complex |

### Difficulty Criteria

| Difficulty | GT Files | Scope |
|-----------|----------|-------|
| Simple | 1–10 | Single module |
| Medium | 11–25 | Cross-module |
| Complex | 26+ | Cross-package, infra, docs |

### Cross-Issue Notes

- **#72 / #191 / #192**: Azure AI Foundry triple (parent → phase 1 → phase 2). Large GT overlap; tests Recon's scope sensitivity.
- **#201 / #210**: Zero-Edit issues — all files are C or S. Tests whether Recon correctly produces empty `edit_target` bucket.

---

## Evaluation Agent Instructions

### Setup

1. Parse this file: extract GT file lists and queries per issue.
2. Start Recon against the evee repo.

### Execution

```python
for issue in issues:
    for q in ["Q1", "Q2", "Q3"]:
        result = recon(query=issue.queries[q])
        returned = set(extract_file_paths(result))
        gt = set(issue.ground_truth_files)
        gt_edit = {f for f in gt if f.category == "E"}

        p = len(returned & gt) / len(returned) if returned else 0
        r = len(returned & gt) / len(gt)
        er = len(returned & gt_edit) / len(gt_edit) if gt_edit else None

        record(issue, q,
               precision=p, recall=r,
               f1=2*p*r/(p+r) if p+r else 0,
               edit_recall=er,
               noise_ratio=len(returned - gt) / len(returned) if returned else 0,
               returned_files=list(returned),
               bucket_alignment=compute_bucket_alignment(result, gt))
```

### Output

Write to `benchmarking/evee/results/recon_{pipeline}_{date}.json`.

### Flags

- 🔴 Q1 Recall < 0.5 → critical gap
- 🟡 Precision < 0.3 → excessive noise
- 🔴 Q1 Edit Recall = 0 → missing all edit targets
- Report which file categories Recon consistently misses (tests? docs? infra? config?)
