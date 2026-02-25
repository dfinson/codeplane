#!/usr/bin/env python3
"""Signal permutation benchmark: which tree-sitter signals help bge-small retrieval?

Tests all 2^4 = 16 combinations of 4 enrichment signals:
  S = string_literals   (env vars, config keys, error messages)
  I = full_imports       (full dotted import paths, not just last segment)
  C = sem_calls          (function/method names called within each def)
  D = decorator_names    (short decorator names like click.command, dataclass)

Each signal combo is tested in two modes:
  1chunk  — single vector per file, truncated at 512 tokens naturally
  2chunk  — if enriched text > TOKEN_SPLIT tokens, emit base scaffold
            as chunk 0 + enrichment-only as chunk 1, max-pool similarity

Total: 16 combos × 2 modes = 32 variants, all on bge-small.
CPU-only. ~5-8 min for 105 files × 32 variants × 24 queries.
"""

from __future__ import annotations

import gc
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EVEE_ROOT = Path("/home/dave01/wsl-repos/evees/evee_cpl/evee")
MAX_LENGTH = 512
SCAFFOLD_MAX_CHARS = 2048  # char-level cap (model tokenizes further)
TOKEN_SPLIT = 450  # if enriched scaffold > this many tokens → split into 2 chunks

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_DIM = 384

# The 4 signals under test
SIGNAL_NAMES = ("S", "I", "C", "D")  # strings, imports, calls, decorators

# ---------------------------------------------------------------------------
# Queries (same 24 queries, 8 issues × 3 levels)
# ---------------------------------------------------------------------------


@dataclass
class IssueQuery:
    issue: int
    level: str
    query: str
    gt_edit_files: list[str]


ISSUE_QUERIES: list[IssueQuery] = [
    # --- #4: Cache model inference ---
    IssueQuery(
        issue=4,
        level="Q1",
        query="I need to implement inference result caching in Evee's evaluation pipeline. The cache should intercept model inference calls in the ModelEvaluator (_infer_record and _infer_record_async), store InferenceOutput results keyed by input record hash, and skip re-inference on cache hits. I need to add cache configuration fields to Config/ModelVariantConfig in the config models, update the evaluation loop, add cache hit/miss logging, and write tests for the caching behavior.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py"],
    ),
    IssueQuery(
        issue=4,
        level="Q2",
        query="Add caching support for deterministic model inference results in Evee. When a model's results are deterministic, re-running evaluation should reuse cached inference outputs instead of calling the model again. This involves changes to the evaluation pipeline, configuration schema, and model infrastructure.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py"],
    ),
    IssueQuery(
        issue=4,
        level="Q3",
        query="How can I add result caching to Evee so that re-running experiments with the same models doesn't repeat inference? I want to save time and costs during iterative development.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py"],
    ),
    # --- #259: Raise error when .env missing ---
    IssueQuery(
        issue=259,
        level="Q1",
        query="I need to add startup validation in Evee that raises an explicit error when the .env file is missing. Currently .env loading is handled silently in multiple places: cli/commands/run.py, cli/commands/validate.py, cli/main.py, evaluation/model_evaluator.py, evaluation/evaluate.py, execution/experiment_runner.py, and core/base_model.py. The DEFAULT_ENV_FILE constant is in cli/constants.py. I should add the check in execution/preflight.py.",
        gt_edit_files=[
            "src/evee/cli/commands/run.py",
            "src/evee/cli/commands/validate.py",
            "src/evee/cli/main.py",
            "src/evee/execution/preflight.py",
            "src/evee/execution/experiment_runner.py",
            "src/evee/evaluation/evaluate.py",
            "src/evee/evaluation/model_evaluator.py",
        ],
    ),
    IssueQuery(
        issue=259,
        level="Q2",
        query="Add a check that raises an explicit error when the .env file is missing in Evee. Currently it fails silently and causes confusing downstream errors. I need to find all places where .env is loaded, the preflight validation system, and the constants defining the default .env path.",
        gt_edit_files=[
            "src/evee/cli/commands/run.py",
            "src/evee/cli/commands/validate.py",
            "src/evee/cli/main.py",
            "src/evee/execution/preflight.py",
            "src/evee/execution/experiment_runner.py",
            "src/evee/evaluation/evaluate.py",
            "src/evee/evaluation/model_evaluator.py",
        ],
    ),
    IssueQuery(
        issue=259,
        level="Q3",
        query="Evee should tell users clearly when their .env file is missing instead of failing with confusing errors later. Where does Evee load the .env file and where should this validation go?",
        gt_edit_files=[
            "src/evee/cli/commands/run.py",
            "src/evee/cli/commands/validate.py",
            "src/evee/cli/main.py",
            "src/evee/execution/preflight.py",
            "src/evee/execution/experiment_runner.py",
            "src/evee/evaluation/evaluate.py",
            "src/evee/evaluation/model_evaluator.py",
        ],
    ),
    # --- #260: Config flag to disable rich progress bars ---
    IssueQuery(
        issue=260,
        level="Q1",
        query="I need to add a configuration flag in config.yaml to disable Rich progress bars for CI environments. The is_rich_compatible_environment() function in src/evee/utils/environment.py already checks EVEE_DISABLE_RICH_LOGGING env var and MCP mode. The ProgressTracker in evaluation/progress_tracker.py and logger in logging/logger.py both use this function. I need to add a new field to RuntimeConfig or ExperimentConfig in config/models.py.",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/utils/environment.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/mcp/resources/config.py",
        ],
    ),
    IssueQuery(
        issue=260,
        level="Q2",
        query="Add a config.yaml flag to disable Rich progress bars. Evee already suppresses them for MCP and AzureML, but users need to disable them for CI too. I need to find where Rich environment detection happens, the config model, and the progress tracker/logger code.",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/utils/environment.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/mcp/resources/config.py",
        ],
    ),
    IssueQuery(
        issue=260,
        level="Q3",
        query="Rich progress bars clutter CI logs. I need to add a way to disable them via config. Where does Evee decide whether to show Rich output and how do I add a config flag?",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/utils/environment.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/mcp/resources/config.py",
        ],
    ),
    # --- #226: Set default MLflow tracking URI to sqlite ---
    IssueQuery(
        issue=226,
        level="Q1",
        query="I need to change the default MLflow tracking URI from the filesystem backend (./mlruns) to sqlite:///mlflow.db in the evee-mlflow package. The MLflowTrackingConfig in packages/evee-mlflow/src/evee_mlflow/config.py has tracking_uri defaulting to None with artifact_location defaulting to ./mlruns. The MLflowBackend.on_startup() in tracking.py logs the ./mlruns directory message when no URI is set.",
        gt_edit_files=[
            "packages/evee-mlflow/src/evee_mlflow/config.py",
            "packages/evee-mlflow/src/evee_mlflow/tracking.py",
            "src/evee/mcp/resources/config.py",
        ],
    ),
    IssueQuery(
        issue=226,
        level="Q2",
        query="Change the default MLflow tracking URI to use SQLite instead of the filesystem backend being deprecated. I need to find where the MLflow tracking URI default is set in the evee-mlflow package, all tests and configs that reference ./mlruns, and documentation.",
        gt_edit_files=[
            "packages/evee-mlflow/src/evee_mlflow/config.py",
            "packages/evee-mlflow/src/evee_mlflow/tracking.py",
            "src/evee/mcp/resources/config.py",
        ],
    ),
    IssueQuery(
        issue=226,
        level="Q3",
        query="MLflow's filesystem backend is being deprecated. I need to change Evee's default tracking to use SQLite instead. Where is the MLflow backend configured and what references ./mlruns or the tracking URI?",
        gt_edit_files=[
            "packages/evee-mlflow/src/evee_mlflow/config.py",
            "packages/evee-mlflow/src/evee_mlflow/tracking.py",
            "src/evee/mcp/resources/config.py",
        ],
    ),
    # --- #233: Early stop for evaluation ---
    IssueQuery(
        issue=233,
        level="Q1",
        query="I need to implement early stopping in Evee's evaluation pipeline. In the inference phase (_run_evaluation_loop in ModelEvaluator), count consecutive or total errors and abort early if a threshold is exceeded. I need the evaluation loop code, config models for new threshold fields, progress tracking, error handling patterns, and the tracking events for an EarlyStopEvent.",
        gt_edit_files=[
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/tracking/events.py",
        ],
    ),
    IssueQuery(
        issue=233,
        level="Q2",
        query="Add early stopping to Evee's evaluation. If too many inference errors occur, stop the evaluation early instead of running the full dataset. I need to understand the evaluation loop, error handling, config schema, and progress tracking.",
        gt_edit_files=[
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/tracking/events.py",
        ],
    ),
    IssueQuery(
        issue=233,
        level="Q3",
        query="Evee should stop evaluation early when there are too many errors. Where is the evaluation loop and how does error handling work?",
        gt_edit_files=[
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/tracking/events.py",
        ],
    ),
    # --- #262: REST-based models ---
    IssueQuery(
        issue=262,
        level="Q1",
        query="I need to add support for configurable REST-based models in Evee. Instead of writing custom @model decorated classes for each REST endpoint, users should define REST model configuration in config.yaml. This requires changes to ModelVariantConfig in config/models.py, a new RestModel class, and changes to ModelEvaluator._register_model to detect type rest and instantiate RestModel. The model should bypass decorator_discovery.",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/mcp/resources/model_patterns.py",
            "src/evee/mcp/resources/config.py",
            "src/evee/cli/commands/model.py",
            "src/evee/cli/utils/model_operations.py",
            "src/evee/cli/commands/validate.py",
        ],
    ),
    IssueQuery(
        issue=262,
        level="Q2",
        query="Add configuration-driven REST models to Evee so users don't need to write model classes for simple REST endpoints. I need to understand the model registration system (@model decorator, MODEL_REGISTRY, _register_model), the config schema, and how the evaluation pipeline works with models.",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/mcp/resources/model_patterns.py",
            "src/evee/mcp/resources/config.py",
            "src/evee/cli/commands/model.py",
            "src/evee/cli/utils/model_operations.py",
            "src/evee/cli/commands/validate.py",
        ],
    ),
    IssueQuery(
        issue=262,
        level="Q3",
        query="Users keep writing model classes that just wrap REST calls. I want to make REST models configurable instead. Where is the model system in Evee and how does model registration work?",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/mcp/resources/model_patterns.py",
            "src/evee/mcp/resources/config.py",
            "src/evee/cli/commands/model.py",
            "src/evee/cli/utils/model_operations.py",
            "src/evee/cli/commands/validate.py",
        ],
    ),
    # --- #275: Reuse metrics with custom instance names ---
    IssueQuery(
        issue=275,
        level="Q1",
        query="I need to decouple metric implementation lookup from display names in Evee. Currently MetricConfig.name in config/models.py serves as both the METRIC_REGISTRY lookup key and the reporting label. To reuse the same metric class with different parameters, I need an entry_point field for implementation lookup while name becomes display-only. This affects _register_metric(), metric templates, CLI commands, validation, and aggregation.",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/cli/commands/validate.py",
            "src/evee/cli/commands/metric.py",
            "src/evee/cli/utils/metric_operations.py",
            "src/evee/mcp/resources/config.py",
            "src/evee/mcp/resources/metric_patterns.py",
        ],
    ),
    IssueQuery(
        issue=275,
        level="Q2",
        query="Evee can't reuse the same metric with different configurations because name is used for both lookup and display. I need to separate these concerns so I can have Coherence and Violence both using the llm_judge metric with different prompts. Where is the metric registry, config model, evaluation pipeline, and CLI metric management?",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/cli/commands/validate.py",
            "src/evee/cli/commands/metric.py",
            "src/evee/cli/utils/metric_operations.py",
            "src/evee/mcp/resources/config.py",
            "src/evee/mcp/resources/metric_patterns.py",
        ],
    ),
    IssueQuery(
        issue=275,
        level="Q3",
        query="I want to use the same metric class multiple times with different parameters, but Evee's metric naming system doesn't allow it. How does metric registration and lookup work, and where would I change it?",
        gt_edit_files=[
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/cli/commands/validate.py",
            "src/evee/cli/commands/metric.py",
            "src/evee/cli/utils/metric_operations.py",
            "src/evee/mcp/resources/config.py",
            "src/evee/mcp/resources/metric_patterns.py",
        ],
    ),
    # --- #193: Configurable dependency sources in `evee new` ---
    IssueQuery(
        issue=193,
        level="Q1",
        query="I need to add an interactive source selection flow to the evee new CLI command. Currently new.py hardcodes Git-based dependencies via _GIT_BASE. I need to add interactive prompts for choosing between Git, pre-built wheels, and local source, with flags (--from-git, --wheels, --from-repo, --from-source) for automation. The pyproject.toml templates in src/evee/cli/templates/overlays/ use placeholders that need different rendering per source type.",
        gt_edit_files=[
            "src/evee/cli/commands/new.py",
            "src/evee/cli/utils/new_project_operations.py",
        ],
    ),
    IssueQuery(
        issue=193,
        level="Q2",
        query="I want to add dependency source selection to evee new so users can choose between Git, wheels, or local source for installing Evee packages. I need to understand the current scaffolding flow in the CLI, the template overlay system, and how pyproject.toml placeholders are rendered.",
        gt_edit_files=[
            "src/evee/cli/commands/new.py",
            "src/evee/cli/utils/new_project_operations.py",
        ],
    ),
    IssueQuery(
        issue=193,
        level="Q3",
        query="How does evee new work for creating new projects? I need to add support for different ways of installing Evee packages — not just from Git. Where is the scaffolding code and template system?",
        gt_edit_files=[
            "src/evee/cli/commands/new.py",
            "src/evee/cli/utils/new_project_operations.py",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FileData:
    path: str
    content: str
    defs: list[dict]
    imports: list[dict]
    base_scaffold: str = ""  # production scaffold (no enrichment)
    # Pre-computed enrichment lines per signal
    signal_lines: dict[str, str] = field(default_factory=dict)  # signal_name → extra line


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")


def _word_split(name: str) -> list[str]:
    words: list[str] = []
    for part in name.split("_"):
        if not part:
            continue
        camel = _CAMEL.findall(part)
        if camel:
            words.extend(w.lower() for w in camel)
        else:
            words.append(part.lower())
    return words


# ---------------------------------------------------------------------------
# File discovery + extraction
# ---------------------------------------------------------------------------


def discover_files() -> list[str]:
    files: list[str] = []
    for base in [EVEE_ROOT / "src", EVEE_ROOT / "packages"]:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if p.name == "__init__.py" or "/templates/" in str(p):
                continue
            files.append(str(p.relative_to(EVEE_ROOT)))
    return files


def extract_all_files(rel_paths: list[str]) -> list[FileData]:
    from codeplane.index._internal.indexing.file_embedding import (
        _build_embed_text,
        build_file_scaffold,
    )
    from codeplane.index._internal.indexing.structural import _extract_file

    files: list[FileData] = []
    for rel_path in rel_paths:
        full = EVEE_ROOT / rel_path
        if not full.exists():
            continue
        result = _extract_file(str(full), str(EVEE_ROOT), unit_id=1)
        if result.error:
            continue
        content = full.read_text(encoding="utf-8", errors="replace")

        # Build base scaffold (production, no enrichment)
        scaffold = build_file_scaffold(rel_path, result.defs, result.imports)
        base_text = _build_embed_text(scaffold, content, defs=result.defs)

        fd = FileData(
            path=rel_path,
            content=content,
            defs=result.defs,
            imports=result.imports,
            base_scaffold=base_text,
        )

        # --- Pre-compute each signal's enrichment line ---

        # S: string_literals → "mentions EVEE_MCP_MODE, config.yaml, ..."
        all_lits: list[str] = []
        seen_lits: set[str] = set()
        for d in result.defs:
            for lit in d.get("_string_literals", []):
                lit_clean = lit.strip()
                if lit_clean.lower() in ("true", "false", "none", "", "0", "1"):
                    continue
                if len(lit_clean) < 3:
                    continue
                if lit_clean not in seen_lits:
                    seen_lits.add(lit_clean)
                    all_lits.append(lit_clean)
        if all_lits:
            # Budget: max 300 chars of literal text
            parts: list[str] = []
            chars_used = 0
            for lit in all_lits:
                if chars_used + len(lit) + 2 > 300:
                    break
                parts.append(lit)
                chars_used += len(lit) + 2
            fd.signal_lines["S"] = "mentions " + ", ".join(parts)

        # I: full_imports → replace imports line with full dotted paths
        if result.imports:
            import_tokens: list[str] = []
            seen_imp: set[str] = set()
            for imp in result.imports:
                source = imp.get("source_literal", "") or imp.get("module_path", "") or ""
                name = imp.get("imported_name", "") or ""
                if source:
                    token = " ".join(_word_split(source.replace(".", "_")))
                elif name:
                    token = " ".join(_word_split(name))
                else:
                    continue
                if token and token not in seen_imp:
                    seen_imp.add(token)
                    import_tokens.append(token)
            if import_tokens:
                fd.signal_lines["I"] = "imports " + ", ".join(import_tokens)

        # C: sem_calls → "calls load_dotenv, setup_logger, Progress, ..."
        all_calls: set[str] = set()
        for d in result.defs:
            sf = d.get("_sem_facts", {})
            for call_name in sf.get("calls", []):
                if call_name and len(call_name) >= 2:
                    all_calls.add(call_name)
        if all_calls:
            # Budget: max 20 call names
            sorted_calls = sorted(all_calls)[:20]
            fd.signal_lines["C"] = "calls " + ", ".join(sorted_calls)

        # D: decorator_names → "decorated click.command, dataclass, property"
        all_decs: set[str] = set()
        for d in result.defs:
            dec_json = d.get("decorators_json", "")
            if dec_json and dec_json != "[]":
                try:
                    for dec_str in json.loads(dec_json):
                        name = dec_str.lstrip("@").split("(")[0].strip()
                        if name and len(name) >= 2:
                            all_decs.add(name)
                except (json.JSONDecodeError, TypeError):
                    pass
        if all_decs:
            sorted_decs = sorted(all_decs)[:10]
            fd.signal_lines["D"] = "decorated " + ", ".join(sorted_decs)

        files.append(fd)
    return files


# ---------------------------------------------------------------------------
# Scaffold assembly per signal combo
# ---------------------------------------------------------------------------


def build_variant_text(fd: FileData, signals: tuple[bool, bool, bool, bool]) -> str:
    """Assemble scaffold text for a specific signal combination.

    signals = (S, I, C, D) — booleans for each signal on/off.

    If I (full_imports) is on, replace the imports line in the base scaffold.
    Other signals are appended as extra lines.
    """
    use_s, use_i, use_c, use_d = signals

    base = fd.base_scaffold
    if not base:
        return ""

    lines = base.split("\n")

    # I: replace the imports line with the full-path version
    if use_i and "I" in fd.signal_lines:
        full_import_line = fd.signal_lines["I"]
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith("imports "):
                lines[i] = full_import_line
                replaced = True
                break
        if not replaced:
            # Insert after FILE_SCAFFOLD and module lines
            insert_at = min(2, len(lines))
            lines.insert(insert_at, full_import_line)

    # Append other signals
    if use_s and "S" in fd.signal_lines:
        lines.append(fd.signal_lines["S"])
    if use_c and "C" in fd.signal_lines:
        lines.append(fd.signal_lines["C"])
    if use_d and "D" in fd.signal_lines:
        lines.append(fd.signal_lines["D"])

    text = "\n".join(lines)
    return text[:SCAFFOLD_MAX_CHARS]


def build_variant_chunks(
    fd: FileData,
    signals: tuple[bool, bool, bool, bool],
    tokenizer,
) -> list[str]:
    """Build 1 or 2 chunks for a signal combination (two-chunk split mode).

    If enriched text fits in TOKEN_SPLIT tokens → 1 chunk (same as 1chunk mode).
    If it overflows → chunk 0 = base scaffold, chunk 1 = enrichment-only text.
    Both chunks are capped at SCAFFOLD_MAX_CHARS.
    """
    full_text = build_variant_text(fd, signals)
    if not full_text:
        return [full_text]

    tok_count = len(tokenizer.encode(full_text).ids)
    if tok_count <= TOKEN_SPLIT:
        return [full_text]

    # Split: chunk 0 = base scaffold (no enrichment signals)
    chunk0 = fd.base_scaffold[:SCAFFOLD_MAX_CHARS]

    # chunk 1 = module context + enrichment signals only
    # Include module line for context, then just the enrichment lines
    use_s, use_i, use_c, use_d = signals
    enrich_lines = ["FILE_SCAFFOLD"]

    # Add module line from base for context
    for line in fd.base_scaffold.split("\n"):
        if line.startswith("module "):
            enrich_lines.append(line)
            break

    if use_i and "I" in fd.signal_lines:
        enrich_lines.append(fd.signal_lines["I"])
    if use_s and "S" in fd.signal_lines:
        enrich_lines.append(fd.signal_lines["S"])
    if use_c and "C" in fd.signal_lines:
        enrich_lines.append(fd.signal_lines["C"])
    if use_d and "D" in fd.signal_lines:
        enrich_lines.append(fd.signal_lines["D"])

    chunk1 = "\n".join(enrich_lines)[:SCAFFOLD_MAX_CHARS]

    # Only return chunk1 if it has actual enrichment (not just FILE_SCAFFOLD + module)
    if len(enrich_lines) <= 2:
        return [chunk0]

    return [chunk0, chunk1]


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def load_model():
    from fastembed import TextEmbedding

    t0 = time.monotonic()
    m = TextEmbedding(
        model_name=MODEL_NAME,
        max_length=MAX_LENGTH,
        providers=["CPUExecutionProvider"],
    )
    dt = time.monotonic() - t0
    print(f"  Loaded {MODEL_NAME} in {dt:.1f}s")
    return m


def embed_batch(model, texts: list[str], batch_size: int = 32) -> np.ndarray:
    vecs = list(model.embed(texts, batch_size=batch_size))
    mat = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    mat /= norms
    return mat


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

EVAL_K_VALUES = [5, 10, 20]


def compute_metrics_1chunk(
    files: list[FileData],
    queries: list[IssueQuery],
    doc_vecs: np.ndarray,
    query_vecs: np.ndarray,
) -> tuple[dict[int, float], float]:
    """Standard recall/MRR — one vector per file."""
    file_paths = [f.path for f in files]
    recall_sums: dict[int, float] = dict.fromkeys(EVAL_K_VALUES, 0.0)
    rr_sum = 0.0

    for qi, iq in enumerate(queries):
        gt_set = set(iq.gt_edit_files)
        gt_indices = {i for i, p in enumerate(file_paths) if p in gt_set}
        if not gt_indices:
            continue

        sims = query_vecs[qi] @ doc_vecs.T
        ranked_indices = np.argsort(-sims)

        for k in EVAL_K_VALUES:
            retrieved = set(ranked_indices[:k].tolist())
            hits = len(retrieved & gt_indices)
            recall_sums[k] += hits / len(gt_indices)

        rr = 0.0
        for rank, fi in enumerate(ranked_indices, 1):
            if int(fi) in gt_indices:
                rr = 1.0 / rank
                break
        rr_sum += rr

    n = len(queries)
    recall_at = {k: recall_sums[k] / n for k in EVAL_K_VALUES}
    mrr = rr_sum / n
    return recall_at, mrr


def compute_metrics_2chunk(
    files: list[FileData],
    queries: list[IssueQuery],
    chunk_vecs: np.ndarray,
    chunk_to_file: list[int],  # chunk index → file index
    query_vecs: np.ndarray,
) -> tuple[dict[int, float], float]:
    """Recall/MRR with max-pool over chunks per file."""
    n_files = len(files)
    file_paths = [f.path for f in files]

    # For each query, compute per-file max similarity
    recall_sums: dict[int, float] = dict.fromkeys(EVAL_K_VALUES, 0.0)
    rr_sum = 0.0

    for qi, iq in enumerate(queries):
        gt_set = set(iq.gt_edit_files)
        gt_indices = {i for i, p in enumerate(file_paths) if p in gt_set}
        if not gt_indices:
            continue

        # Similarities for all chunks
        chunk_sims = query_vecs[qi] @ chunk_vecs.T

        # Max-pool: for each file, take the max similarity across its chunks
        file_sims = np.full(n_files, -1.0, dtype=np.float32)
        for ci, fi in enumerate(chunk_to_file):
            if chunk_sims[ci] > file_sims[fi]:
                file_sims[fi] = chunk_sims[ci]

        ranked_indices = np.argsort(-file_sims)

        for k in EVAL_K_VALUES:
            retrieved = set(ranked_indices[:k].tolist())
            hits = len(retrieved & gt_indices)
            recall_sums[k] += hits / len(gt_indices)

        rr = 0.0
        for rank, fi in enumerate(ranked_indices, 1):
            if int(fi) in gt_indices:
                rr = 1.0 / rank
                break
        rr_sum += rr

    n = len(queries)
    recall_at = {k: recall_sums[k] / n for k in EVAL_K_VALUES}
    mrr = rr_sum / n
    return recall_at, mrr


# ---------------------------------------------------------------------------
# Signal combo label
# ---------------------------------------------------------------------------


def combo_label(signals: tuple[bool, bool, bool, bool]) -> str:
    """Human-readable label like 'S+I+C' or 'baseline'."""
    parts = []
    for name, on in zip(SIGNAL_NAMES, signals):
        if on:
            parts.append(name)
    return "+".join(parts) if parts else "baseline"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 90)
    print("SIGNAL PERMUTATION BENCHMARK")
    print("4 signals × 2 modes (1chunk / 2chunk) × 24 queries on bge-small")
    print("=" * 90)

    # Load tokenizer for 2-chunk splitting decisions
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(
        "/tmp/fastembed_cache/models--qdrant--bge-small-en-v1.5-onnx-q/"
        "snapshots/52398278842ec682c6f32300af41344b1c0b0bb2/tokenizer.json"
    )

    # 1. Discover + extract
    sys.path.insert(0, "src")
    print(f"\n[1/4] Discovering files in {EVEE_ROOT} ...")
    rel_paths = discover_files()
    print(f"  Found {len(rel_paths)} Python files")

    print("\n[2/4] Extracting with tree-sitter + pre-computing signals...")
    files = extract_all_files(rel_paths)
    print(f"  {len(files)} files extracted")

    # Signal coverage stats
    for sig_name, sig_full in [
        ("S", "string_literals"),
        ("I", "full_imports"),
        ("C", "sem_calls"),
        ("D", "decorator_names"),
    ]:
        count = sum(1 for fd in files if sig_name in fd.signal_lines)
        print(
            f"  {sig_name} ({sig_full}): {count}/{len(files)} files ({100 * count / len(files):.0f}%)"
        )

    # 2. Generate all 16 signal combos
    all_combos: list[tuple[bool, bool, bool, bool]] = []
    for bits in range(16):
        combo = (bool(bits & 8), bool(bits & 4), bool(bits & 2), bool(bits & 1))
        all_combos.append(combo)

    print("\n[3/4] Loading model...")
    model = load_model()

    # Embed queries once
    queries = ISSUE_QUERIES
    query_texts = [iq.query for iq in queries]
    query_vecs = embed_batch(model, query_texts)
    print(f"  Embedded {len(queries)} queries")

    # 3. Run all combos × 2 modes
    print(
        f"\n[4/4] Running {len(all_combos)} signal combos × 2 modes = {len(all_combos) * 2} variants..."
    )
    print(f"  (each variant embeds {len(files)} files)")

    results: list[dict] = []  # list of {label, mode, signals, R@5, R@10, R@20, MRR, n_chunks, ...}

    for combo in all_combos:
        label = combo_label(combo)

        # --- Mode 1: 1chunk (truncate at 512 tokens naturally) ---
        texts_1c = [build_variant_text(fd, combo) for fd in files]
        # Measure token stats for this combo
        tok_counts = [len(tokenizer.encode(t).ids) for t in texts_1c]
        overflow_count = sum(1 for tc in tok_counts if tc > 512)

        doc_vecs_1c = embed_batch(model, texts_1c)
        recall_1c, mrr_1c = compute_metrics_1chunk(files, queries, doc_vecs_1c, query_vecs)

        results.append(
            {
                "label": label,
                "mode": "1chunk",
                "signals": combo,
                "R@5": recall_1c[5],
                "R@10": recall_1c[10],
                "R@20": recall_1c[20],
                "MRR": mrr_1c,
                "n_chunks": len(files),
                "overflow": overflow_count,
                "median_tok": int(np.median(tok_counts)),
                "max_tok": max(tok_counts),
            }
        )

        # --- Mode 2: 2chunk (split if > TOKEN_SPLIT tokens) ---
        all_chunks: list[str] = []
        chunk_to_file: list[int] = []
        for fi, fd in enumerate(files):
            chunks = build_variant_chunks(fd, combo, tokenizer)
            for chunk_text in chunks:
                all_chunks.append(chunk_text)
                chunk_to_file.append(fi)

        chunk_vecs = embed_batch(model, all_chunks)
        recall_2c, mrr_2c = compute_metrics_2chunk(
            files, queries, chunk_vecs, chunk_to_file, query_vecs
        )

        results.append(
            {
                "label": label,
                "mode": "2chunk",
                "signals": combo,
                "R@5": recall_2c[5],
                "R@10": recall_2c[10],
                "R@20": recall_2c[20],
                "MRR": mrr_2c,
                "n_chunks": len(all_chunks),
                "overflow": 0,
                "median_tok": int(np.median(tok_counts)),
                "max_tok": max(tok_counts),
            }
        )

        # Progress
        print(
            f"  {label:<12s}  1c: R@5={recall_1c[5]:.3f} MRR={mrr_1c:.3f}  "
            f"2c: R@5={recall_2c[5]:.3f} MRR={mrr_2c:.3f}  "
            f"({len(all_chunks)} chunks, {overflow_count} overflow)"
        )

    del model
    gc.collect()

    # -----------------------------------------------------------------------
    # Results tables
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("FULL RESULTS — sorted by R@5 (1chunk mode)")
    print(f"{'=' * 100}")

    # Sort by R@5 descending within each mode
    results_1c = sorted(
        [r for r in results if r["mode"] == "1chunk"], key=lambda r: r["R@5"], reverse=True
    )
    results_2c = sorted(
        [r for r in results if r["mode"] == "2chunk"], key=lambda r: r["R@5"], reverse=True
    )

    baseline_1c = next(r for r in results_1c if r["label"] == "baseline")

    print(
        f"\n  {'Rank':>4} {'Signals':<15} {'R@5':>7} {'R@10':>7} {'R@20':>7} "
        f"{'MRR':>7} {'dR@5':>7} {'dMRR':>7} {'MedTok':>7} {'OvFlow':>7}"
    )
    print(f"  {'-' * 88}")
    for rank, r in enumerate(results_1c, 1):
        dr5 = r["R@5"] - baseline_1c["R@5"]
        dmrr = r["MRR"] - baseline_1c["MRR"]
        marker = " ***" if r["label"] == "baseline" else ""
        print(
            f"  {rank:>4} {r['label']:<15} {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
            f"{r['R@20']:>7.3f} {r['MRR']:>7.3f} {dr5:>+7.3f} {dmrr:>+7.3f} "
            f"{r['median_tok']:>7} {r['overflow']:>7}{marker}"
        )

    print(f"\n{'=' * 100}")
    print("FULL RESULTS — sorted by R@5 (2chunk mode)")
    print(f"{'=' * 100}")

    baseline_2c = next(r for r in results_2c if r["label"] == "baseline")

    print(
        f"\n  {'Rank':>4} {'Signals':<15} {'R@5':>7} {'R@10':>7} {'R@20':>7} "
        f"{'MRR':>7} {'dR@5':>7} {'dMRR':>7} {'Chunks':>7}"
    )
    print(f"  {'-' * 80}")
    for rank, r in enumerate(results_2c, 1):
        dr5 = r["R@5"] - baseline_2c["R@5"]
        dmrr = r["MRR"] - baseline_2c["MRR"]
        marker = " ***" if r["label"] == "baseline" else ""
        print(
            f"  {rank:>4} {r['label']:<15} {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
            f"{r['R@20']:>7.3f} {r['MRR']:>7.3f} {dr5:>+7.3f} {dmrr:>+7.3f} "
            f"{r['n_chunks']:>7}{marker}"
        )

    # -----------------------------------------------------------------------
    # Signal marginal contribution (averaging over all combos that include it)
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("SIGNAL MARGINAL CONTRIBUTION")
    print("(avg metric when signal ON minus avg when OFF)")
    print(f"{'=' * 100}")

    for mode in ["1chunk", "2chunk"]:
        print(f"\n  Mode: {mode}")
        mode_results = [r for r in results if r["mode"] == mode]
        print(f"  {'Signal':<18} {'dR@5':>8} {'dR@10':>8} {'dR@20':>8} {'dMRR':>8}")
        print(f"  {'-' * 50}")
        for si, sig_name in enumerate(SIGNAL_NAMES):
            on_results = [r for r in mode_results if r["signals"][si]]
            off_results = [r for r in mode_results if not r["signals"][si]]
            for metric in ["R@5", "R@10", "R@20", "MRR"]:
                on_avg = np.mean([r[metric] for r in on_results])
                off_avg = np.mean([r[metric] for r in off_results])
                locals()[f"d_{metric}"] = on_avg - off_avg
            full_names = {"S": "strings", "I": "full_imports", "C": "sem_calls", "D": "decorators"}
            print(
                f"  {sig_name} ({full_names[sig_name]:<12s})  "
                f"{locals()['d_R@5']:>+8.3f} {locals()['d_R@10']:>+8.3f} "
                f"{locals()['d_R@20']:>+8.3f} {locals()['d_MRR']:>+8.3f}"
            )

    # -----------------------------------------------------------------------
    # 1chunk vs 2chunk comparison (same signal combo)
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("1CHUNK vs 2CHUNK — same signal combo")
    print(f"{'=' * 100}")

    print(
        f"\n  {'Signals':<15} {'1c R@5':>7} {'2c R@5':>7} {'delta':>7}  "
        f"{'1c MRR':>7} {'2c MRR':>7} {'delta':>7}  {'OvFlow':>7} {'Chunks':>7}"
    )
    print(f"  {'-' * 88}")

    for combo in all_combos:
        label = combo_label(combo)
        r1 = next(r for r in results if r["label"] == label and r["mode"] == "1chunk")
        r2 = next(r for r in results if r["label"] == label and r["mode"] == "2chunk")
        dr5 = r2["R@5"] - r1["R@5"]
        dmrr = r2["MRR"] - r1["MRR"]
        print(
            f"  {label:<15} {r1['R@5']:>7.3f} {r2['R@5']:>7.3f} {dr5:>+7.3f}  "
            f"{r1['MRR']:>7.3f} {r2['MRR']:>7.3f} {dmrr:>+7.3f}  "
            f"{r1['overflow']:>7} {r2['n_chunks']:>7}"
        )

    # -----------------------------------------------------------------------
    # Top-5 overall configurations
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("TOP-5 CONFIGURATIONS (by R@5)")
    print(f"{'=' * 100}")

    all_sorted = sorted(results, key=lambda r: (r["R@5"], r["MRR"]), reverse=True)
    print(f"\n  {'Rank':>4} {'Config':<22} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}")
    print(f"  {'-' * 60}")
    for rank, r in enumerate(all_sorted[:5], 1):
        config_label = f"{r['label']} ({r['mode']})"
        print(
            f"  {rank:>4} {config_label:<22} {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
            f"{r['R@20']:>7.3f} {r['MRR']:>7.3f}"
        )

    # Reference: jina-base-code × current from previous benchmark
    print("\n  Reference (previous benchmark):")
    print("       jina-base-code × current   R@5=0.495  R@10=0.637  R@20=0.779  MRR=0.772")


if __name__ == "__main__":
    main()
