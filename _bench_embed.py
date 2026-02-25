#!/usr/bin/env python3
"""Embedding benchmark: variations × models × real evee repo files × real issue queries.

Uses the evee repo (microsoft/evee) as corpus — 70+ real Python source files.
Queries are drawn from 8 real GitHub issues with curated ground-truth file sets
from benchmarking/evee/ReconEveeEvaluation.md.

Compares 4 text representations:
  A) raw_scaffold  — read_scaffold output: "class Foo(x: int)  [1-20]" format
  B) anglicised    — anglicified scaffold: "module foo\ndefines class foo, method bar(x)"
  C) scaffold+ang  — both combined (concatenated)
  D) full_content  — raw file source code (FULL — no truncation; uses chunking for long files)

Across 3 models:
  1) BAAI/bge-small-en-v1.5       (384-dim, 0.067 GB, 512 tokens)
  2) jinaai/jina-embeddings-v2-small-en (512-dim, 0.12 GB, 8192 tokens)
  3) jinaai/jina-embeddings-v2-base-code (768-dim, 0.64 GB, 8192 tokens) ← current

Metric: For each query, we measure Recall@K against the curated ground-truth
edit-target files for the issue. This gives a proper retrieval quality signal
rather than raw cosine similarity.
"""

from __future__ import annotations

import gc
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EVEE_ROOT = Path("/home/dave01/wsl-repos/evees/evee_cpl/evee")
CODEPLANE_ROOT = Path(__file__).resolve().parent
MAX_LENGTH = 512
SCAFFOLD_MAX_CHARS = 2048  # cap scaffold representations (they're already compact)
CHUNK_SIZE = 8000  # chars per chunk for full content (well within 8192 token window)
CHUNK_OVERLAP = 500  # overlap between chunks

MODELS = [
    ("BAAI/bge-small-en-v1.5", 384, 0.067),
    ("jinaai/jina-embeddings-v2-small-en", 512, 0.12),
    ("jinaai/jina-embeddings-v2-base-code", 768, 0.64),
]


# ---------------------------------------------------------------------------
# Real issue queries with ground-truth edit targets (verified against repo)
# From benchmarking/evee/ReconEveeEvaluation.md — 8 issues × 3 query levels
# ---------------------------------------------------------------------------


@dataclass
class IssueQuery:
    issue: int
    level: str  # Q1 (anchored/precise), Q2 (mixed/scoped), Q3 (unanchored/open)
    query: str
    gt_edit_files: list[str]  # files that need actual code changes


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
    path: str  # relative to EVEE_ROOT (e.g. "src/evee/config/models.py")
    content: str
    defs: list[dict]
    imports: list[dict]
    raw_scaffold: str = ""
    anglicised: str = ""
    combined: str = ""
    full_chunks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# File discovery + tree-sitter extraction
# ---------------------------------------------------------------------------


def discover_files() -> list[str]:
    """Find all non-template, non-init Python source files in evee/src and packages/."""
    files: list[str] = []
    for base in [EVEE_ROOT / "src", EVEE_ROOT / "packages"]:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if p.name == "__init__.py":
                continue
            if "/templates/" in str(p):
                continue
            files.append(str(p.relative_to(EVEE_ROOT)))
    return files


def extract_all_files(rel_paths: list[str]) -> list[FileData]:
    """Run tree-sitter extraction on all target files."""
    from codeplane.index._internal.indexing.structural import _extract_file

    files: list[FileData] = []
    for rel_path in rel_paths:
        full = EVEE_ROOT / rel_path
        if not full.exists():
            continue

        result = _extract_file(rel_path, str(EVEE_ROOT), unit_id=1)
        if result.error:
            print(f"  SKIP (error): {rel_path}: {result.error}")
            continue

        content = full.read_text(encoding="utf-8", errors="replace")
        fd = FileData(
            path=rel_path,
            content=content,
            defs=result.defs,
            imports=result.imports,
        )
        files.append(fd)

    return files


# ---------------------------------------------------------------------------
# Build 4 text variations per file
# ---------------------------------------------------------------------------


def build_raw_scaffold(fd: FileData) -> str:
    """Build read_scaffold-style output: compact code-like symbol tree."""
    lines: list[str] = []

    source_groups: defaultdict[str, list[str]] = defaultdict(list)
    bare: list[str] = []
    for imp in fd.imports:
        name = imp.get("imported_name", "")
        source = imp.get("source_literal", "") or imp.get("module_path", "")
        if source and source != name:
            source_groups[source].append(name)
        else:
            bare.append(name)

    if bare:
        lines.append(f"imports: {', '.join(bare)}")
    for src, names in sorted(source_groups.items()):
        lines.append(f"  {src}: {', '.join(names)}")

    container_kinds = frozenset(
        {"class", "struct", "enum", "interface", "trait", "module", "namespace"}
    )
    sorted_defs = sorted(fd.defs, key=lambda d: (d.get("start_line", 0), d.get("start_col", 0)))

    stack: list[tuple[int, int]] = []
    for d in sorted_defs:
        sl = d.get("start_line", 0)
        el = d.get("end_line", 0)
        while stack and sl >= stack[-1][0]:
            stack.pop()
        depth = len(stack)
        indent = "  " * depth

        kind = d.get("kind", "")
        name = d.get("name", "")
        sig = d.get("signature_text", "") or ""
        if sig and not sig.startswith("("):
            sig = f"({sig})"

        line = f"{indent}{kind} {name}{sig}  [{sl}-{el}]"
        lines.append(line)

        if kind in container_kinds:
            stack.append((el, depth + 1))

    return "\n".join(lines)


def build_anglicised(fd: FileData) -> str:
    """Build anglicified scaffold — the current production format."""
    from codeplane.index._internal.indexing.file_embedding import (
        _build_embed_text,
        build_file_scaffold,
    )

    scaffold = build_file_scaffold(fd.path, fd.defs, fd.imports)
    return _build_embed_text(scaffold, fd.content, defs=fd.defs)


def build_full_chunks(fd: FileData) -> list[str]:
    """Split full file content into overlapping chunks. No truncation."""
    content = fd.content
    if len(content) <= CHUNK_SIZE:
        return [content]
    chunks = []
    start = 0
    while start < len(content):
        end = start + CHUNK_SIZE
        chunks.append(content[start:end])
        start = end - CHUNK_OVERLAP
    return chunks


def prepare_variations(files: list[FileData]) -> None:
    """Build all 4 text variations for each file."""
    for fd in files:
        fd.raw_scaffold = build_raw_scaffold(fd)[:SCAFFOLD_MAX_CHARS]
        fd.anglicised = build_anglicised(fd)[:SCAFFOLD_MAX_CHARS]
        fd.combined = f"{fd.raw_scaffold}\n---\n{fd.anglicised}"[:SCAFFOLD_MAX_CHARS]
        fd.full_chunks = build_full_chunks(fd)


# ---------------------------------------------------------------------------
# Embedding + evaluation
# ---------------------------------------------------------------------------


def load_model(model_name: str):
    from fastembed import TextEmbedding

    t0 = time.monotonic()
    m = TextEmbedding(model_name=model_name, max_length=MAX_LENGTH)
    dt = time.monotonic() - t0
    print(f"  Loaded in {dt:.1f}s")
    return m


def embed_batch(model, texts: list[str], batch_size: int = 4) -> np.ndarray:
    """Embed texts, L2-normalize, return float32 matrix."""
    vecs = list(model.embed(texts, batch_size=batch_size))
    mat = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    mat /= norms
    return mat


def build_variation_index(
    model,
    files: list[FileData],
    var_name: str,
) -> tuple[np.ndarray, list[int], float]:
    """Embed a variation and return (vectors, file_index_per_vec, embed_time_ms).

    For single-text variations (scaffold, anglicised, combined), there's one
    vector per file. For full_content with chunking, there may be multiple
    vectors per file — we track which file each vector belongs to.
    """
    texts: list[str] = []
    file_indices: list[int] = []  # maps each text -> index into files[]

    for fi, fd in enumerate(files):
        if var_name == "raw_scaffold":
            texts.append(fd.raw_scaffold)
            file_indices.append(fi)
        elif var_name == "anglicised":
            texts.append(fd.anglicised)
            file_indices.append(fi)
        elif var_name == "scaffold+ang":
            texts.append(fd.combined)
            file_indices.append(fi)
        elif var_name == "full_content":
            for chunk in fd.full_chunks:
                texts.append(chunk)
                file_indices.append(fi)

    t0 = time.monotonic()
    vecs = embed_batch(model, texts)
    embed_ms = (time.monotonic() - t0) * 1000

    return vecs, file_indices, embed_ms


def retrieve_topk(
    query_vec: np.ndarray,
    doc_vecs: np.ndarray,
    file_indices: list[int],
    k: int = 10,
) -> list[tuple[int, float]]:
    """Return top-K unique file indices by max similarity.

    For chunked variations, the same file may have multiple vectors.
    We take the max sim across chunks for each file.
    """
    sims = query_vec @ doc_vecs.T  # (D,)
    # Aggregate: max sim per file
    file_max_sim: dict[int, float] = {}
    for vi, fi in enumerate(file_indices):
        s = float(sims[vi])
        if fi not in file_max_sim or s > file_max_sim[fi]:
            file_max_sim[fi] = s

    # Sort by sim descending
    ranked = sorted(file_max_sim.items(), key=lambda x: -x[1])
    return ranked[:k]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

EVAL_K_VALUES = [5, 10, 20]


@dataclass
class VariationMetrics:
    name: str
    avg_chars: float = 0.0
    num_vectors: int = 0
    embed_time_ms: float = 0.0
    # Recall@K averaged across all queries
    recall_at: dict[int, float] = field(default_factory=dict)
    # MRR (mean reciprocal rank of first GT hit)
    mrr: float = 0.0


def compute_metrics(
    files: list[FileData],
    queries: list[IssueQuery],
    doc_vecs: np.ndarray,
    file_indices: list[int],
    query_vecs: np.ndarray,
) -> tuple[dict[int, float], float, list[dict]]:
    """Compute Recall@K and MRR across all queries.

    Returns (recall_at_k_dict, mrr, per_query_details).
    """
    file_paths = [f.path for f in files]

    recall_sums: dict[int, float] = dict.fromkeys(EVAL_K_VALUES, 0.0)
    rr_sum = 0.0
    per_query: list[dict] = []

    for qi, iq in enumerate(queries):
        gt_set = set(iq.gt_edit_files)
        gt_indices = {i for i, p in enumerate(file_paths) if p in gt_set}

        if not gt_indices:
            continue

        top_k_max = max(EVAL_K_VALUES)
        ranked = retrieve_topk(query_vecs[qi], doc_vecs, file_indices, k=top_k_max)

        # Recall@K
        for k in EVAL_K_VALUES:
            retrieved = {fi for fi, _ in ranked[:k]}
            hits = len(retrieved & gt_indices)
            recall_sums[k] += hits / len(gt_indices)

        # MRR — reciprocal rank of first GT hit
        rr = 0.0
        for rank, (fi, _sim) in enumerate(ranked, 1):
            if fi in gt_indices:
                rr = 1.0 / rank
                break
        rr_sum += rr

        per_query.append(
            {
                "issue": iq.issue,
                "level": iq.level,
                "gt_count": len(gt_indices),
                "top5": [(file_paths[fi], round(s, 3)) for fi, s in ranked[:5]],
                "recall@5": sum(1 for fi, _ in ranked[:5] if fi in gt_indices) / len(gt_indices),
                "recall@10": sum(1 for fi, _ in ranked[:10] if fi in gt_indices) / len(gt_indices),
                "rr": rr,
            }
        )

    n = len(queries)
    recall_at = {k: recall_sums[k] / n for k in EVAL_K_VALUES}
    mrr = rr_sum / n

    return recall_at, mrr, per_query


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=int,
        default=None,
        help="Run only model at this index (0-2). Omit to run all.",
    )
    args = parser.parse_args()

    run_models = [MODELS[args.model]] if args.model is not None else MODELS

    print("=" * 90)
    print("EMBEDDING BENCHMARK: 4 variations x 3 models x evee repo x real issue queries")
    print("=" * 90)

    # 1. Discover + extract files
    print(f"\n[1/4] Discovering Python files in {EVEE_ROOT}/src ...")
    rel_paths = discover_files()
    print(f"  Found {len(rel_paths)} files")

    print("\n[2/4] Extracting with tree-sitter...")
    files = extract_all_files(rel_paths)
    print(f"  {len(files)} files extracted successfully")

    if len(files) < 20:
        print("ERROR: Too few files extracted. Aborting.")
        sys.exit(1)

    # 2. Build variations
    print("\n[3/4] Building 4 text variations per file...")
    prepare_variations(files)

    var_names = ["raw_scaffold", "anglicised", "scaffold+ang", "full_content"]

    print(f"\n  {'Variation':<16} {'Files':>6} {'Vectors':>8} {'Mean chars':>12} {'Max chars':>10}")
    print(f"  {'-' * 56}")
    for vname in var_names:
        if vname == "full_content":
            all_texts = [c for f in files for c in f.full_chunks]
        elif vname == "raw_scaffold":
            all_texts = [f.raw_scaffold for f in files]
        elif vname == "anglicised":
            all_texts = [f.anglicised for f in files]
        else:
            all_texts = [f.combined for f in files]
        lens = [len(t) for t in all_texts]
        print(
            f"  {vname:<16} {len(files):>6} {len(all_texts):>8} "
            f"{sum(lens) / len(lens):>12,.0f} {max(lens):>10,}"
        )

    # 3. Run each model
    queries = ISSUE_QUERIES
    print(
        f"\n[4/4] Running {len(MODELS)} models x {len(var_names)} variations "
        f"x {len(queries)} queries (8 issues x 3 levels)..."
    )

    # Collect results
    all_metrics: dict[str, dict[str, VariationMetrics]] = {}  # model -> var -> metrics
    all_per_query: dict[str, dict[str, list[dict]]] = {}

    for model_name, dim, size_gb in run_models:
        short_name = model_name.split("/")[-1]
        print(f"\n{'─' * 90}")
        print(f"  {model_name}  (dim={dim}, {size_gb} GB)")

        model = load_model(model_name)

        # Embed all queries once
        query_texts = [iq.query for iq in queries]
        query_vecs = embed_batch(model, query_texts)

        model_metrics: dict[str, VariationMetrics] = {}
        model_per_query: dict[str, list[dict]] = {}

        for vname in var_names:
            doc_vecs, file_indices, embed_ms = build_variation_index(model, files, vname)

            recall_at, mrr, per_query_details = compute_metrics(
                files,
                queries,
                doc_vecs,
                file_indices,
                query_vecs,
            )

            # Compute avg chars
            if vname == "full_content":
                all_texts = [c for f in files for c in f.full_chunks]
            elif vname == "raw_scaffold":
                all_texts = [f.raw_scaffold for f in files]
            elif vname == "anglicised":
                all_texts = [f.anglicised for f in files]
            else:
                all_texts = [f.combined for f in files]

            vm = VariationMetrics(
                name=vname,
                avg_chars=sum(len(t) for t in all_texts) / len(all_texts),
                num_vectors=len(doc_vecs),
                embed_time_ms=embed_ms,
                recall_at=recall_at,
                mrr=mrr,
            )
            model_metrics[vname] = vm
            model_per_query[vname] = per_query_details

        all_metrics[short_name] = model_metrics
        all_per_query[short_name] = model_per_query

        # Per-model summary table
        print(
            f"\n  {'Variation':<16} {'Vecs':>6} {'ms':>8} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}"
        )
        print(f"  {'-' * 62}")
        for vname in var_names:
            vm = model_metrics[vname]
            print(
                f"  {vm.name:<16} {vm.num_vectors:>6} {vm.embed_time_ms:>8,.0f} "
                f"{vm.recall_at[5]:>7.3f} {vm.recall_at[10]:>7.3f} "
                f"{vm.recall_at[20]:>7.3f} {vm.mrr:>7.3f}"
            )

        # Per-query breakdown for this model
        print("\n  Per-query detail (best variation marked with *):")
        print(f"  {'Issue':>5} {'Lvl':>3} {'GT':>3}", end="")
        for v in var_names:
            print(f"  {v[:10]:>10}", end="")
        print()
        print(f"  {'-' * 5} {'-' * 3} {'-' * 3}", end="")
        for _ in var_names:
            print(f"  {'─' * 10}", end="")
        print()

        for qi, iq in enumerate(queries):
            print(f"  {iq.issue:>5} {iq.level:>3} {len(iq.gt_edit_files):>3}", end="")
            best_recall = -1.0
            best_var = ""
            for vname in var_names:
                pq = model_per_query[vname]
                r5 = pq[qi]["recall@5"] if qi < len(pq) else 0
                if r5 > best_recall:
                    best_recall = r5
                    best_var = vname
            for vname in var_names:
                pq = model_per_query[vname]
                r5 = pq[qi]["recall@5"] if qi < len(pq) else 0
                marker = " *" if vname == best_var and best_recall > 0 else "  "
                print(f"  {r5:>8.1%}{marker}", end="")
            print()

        del model
        gc.collect()

    # -----------------------------------------------------------------------
    # Summary tables (only when running all models)
    # -----------------------------------------------------------------------
    model_names = list(all_metrics.keys())

    if len(model_names) < len(MODELS):
        print(f"\n{'=' * 90}")
        print(
            f"Single-model run complete ({model_names[0]}). Run without --model for full comparison."
        )
        print(f"{'=' * 90}")
        return

    print(f"\n{'=' * 90}")
    print("SUMMARY: VARIATION COMPARISON (averaged across all models)")
    print(f"{'=' * 90}")
    print(
        f"\n  {'Variation':<16} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7} {'Avg ms':>8} {'Avg chars':>10}"
    )
    print(f"  {'-' * 68}")
    for vname in var_names:
        r5 = np.mean([all_metrics[m][vname].recall_at[5] for m in model_names])
        r10 = np.mean([all_metrics[m][vname].recall_at[10] for m in model_names])
        r20 = np.mean([all_metrics[m][vname].recall_at[20] for m in model_names])
        mrr = np.mean([all_metrics[m][vname].mrr for m in model_names])
        ms = np.mean([all_metrics[m][vname].embed_time_ms for m in model_names])
        chars = all_metrics[model_names[0]][vname].avg_chars
        print(
            f"  {vname:<16} {r5:>7.3f} {r10:>7.3f} {r20:>7.3f} "
            f"{mrr:>7.3f} {ms:>8,.0f} {chars:>10,.0f}"
        )

    print(f"\n{'=' * 90}")
    print("SUMMARY: MODEL COMPARISON (averaged across all variations)")
    print(f"{'=' * 90}")
    print(
        f"\n  {'Model':<40} {'dim':>5} {'GB':>6} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7} {'ms':>8}"
    )
    print(f"  {'-' * 88}")
    for mname in model_names:
        info = next(m for m in MODELS if m[0].endswith(mname))
        r5 = np.mean([all_metrics[mname][v].recall_at[5] for v in var_names])
        r10 = np.mean([all_metrics[mname][v].recall_at[10] for v in var_names])
        r20 = np.mean([all_metrics[mname][v].recall_at[20] for v in var_names])
        mrr = np.mean([all_metrics[mname][v].mrr for v in var_names])
        ms = np.mean([all_metrics[mname][v].embed_time_ms for v in var_names])
        print(
            f"  {mname:<40} {info[1]:>5} {info[2]:>6.2f} "
            f"{r5:>7.3f} {r10:>7.3f} {r20:>7.3f} {mrr:>7.3f} {ms:>8,.0f}"
        )

    # Best combo
    print(f"\n{'=' * 90}")
    print("BEST MODEL x VARIATION COMBOS (by Recall@10)")
    print(f"{'=' * 90}")
    combos = []
    for mname in model_names:
        for vname in var_names:
            vm = all_metrics[mname][vname]
            combos.append((mname, vname, vm.recall_at[10], vm.mrr, vm.embed_time_ms))
    combos.sort(key=lambda x: -x[2])
    print(f"\n  {'Model':<35} {'Variation':<16} {'R@10':>7} {'MRR':>7} {'ms':>8}")
    print(f"  {'-' * 78}")
    for mname, vname, r10, mrr, ms in combos[:8]:
        print(f"  {mname:<35} {vname:<16} {r10:>7.3f} {mrr:>7.3f} {ms:>8,.0f}")

    # Per-issue breakdown (best model)
    best_model = max(
        model_names, key=lambda m: np.mean([all_metrics[m][v].recall_at[10] for v in var_names])
    )
    print(f"\n{'=' * 90}")
    print(f"PER-ISSUE RECALL@10 DETAIL (best model: {best_model})")
    print(f"{'=' * 90}")
    print(f"\n  {'Issue':>5} {'Lvl':>3} {'GT':>3}", end="")
    for v in var_names:
        print(f"  {v[:12]:>12}", end="")
    print()

    issues_seen: set[int] = set()
    for qi, iq in enumerate(queries):
        if iq.issue not in issues_seen:
            if issues_seen:
                print()
            issues_seen.add(iq.issue)
        pqs = all_per_query[best_model]
        print(f"  #{iq.issue:<4} {iq.level:>3} {len(iq.gt_edit_files):>3}", end="")
        for vname in var_names:
            pq = pqs[vname]
            r10 = pq[qi]["recall@10"] if qi < len(pq) else 0
            print(f"  {r10:>10.1%}  ", end="")
        print()

    # Sample retrieval
    print(f"\n{'=' * 90}")
    print(f"SAMPLE RETRIEVAL: Issue #4 Q1 (best model: {best_model})")
    print(f"{'=' * 90}")
    for vname in var_names:
        pq = all_per_query[best_model][vname][0]  # first query = #4 Q1
        print(f"\n  {vname}:")
        for path, sim in pq["top5"]:
            gt_marker = " <GT>" if path in queries[0].gt_edit_files else ""
            print(f"    {sim:.3f}  {path}{gt_marker}")


if __name__ == "__main__":
    main()
