#!/usr/bin/env python3
"""Hybrid RRF embedding benchmark: anglicised-only vs RRF(anglicised + full_content).

Compares two models under two retrieval strategies:
  A) ang_only   — anglicised scaffold vectors only (current production)
  B) hybrid_rrf — RRF fusion of anglicised + full_content vectors
                  (full_content only for files < 15KB source)

Models:
  1) BAAI/bge-small-en-v1.5              (384-dim, 0.067 GB)
  2) jinaai/jina-embeddings-v2-base-code (768-dim, 0.64 GB) ← current production

CUDA-accelerated via fastembed(cuda=True).

Query set: 24 real queries from 8 evee GitHub issues × 3 difficulty levels.
Corpus: ~84 Python files from the evee repo.
"""

from __future__ import annotations

import gc
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
SCAFFOLD_MAX_CHARS = 2048
CHUNK_SIZE = 8000
CHUNK_OVERLAP = 500
FULL_CONTENT_SIZE_LIMIT = 15_000  # only embed full content for files < 15KB
RRF_K = 60  # RRF smoothing constant

MODELS = [
    ("BAAI/bge-small-en-v1.5", 384, 0.067),
    ("jinaai/jina-embeddings-v2-base-code", 768, 0.64),
]


# ---------------------------------------------------------------------------
# Issue queries (identical to _bench_embed.py)
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
    source_chars: int
    defs: list[dict]
    imports: list[dict]
    anglicised: str = ""
    full_chunks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# File discovery + extraction
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
            source_chars=len(content),
            defs=result.defs,
            imports=result.imports,
        )
        files.append(fd)

    return files


# ---------------------------------------------------------------------------
# Build text representations
# ---------------------------------------------------------------------------


def build_anglicised(fd: FileData) -> str:
    from codeplane.index._internal.indexing.file_embedding import (
        _build_embed_text,
        build_file_scaffold,
    )

    scaffold = build_file_scaffold(fd.path, fd.defs, fd.imports)
    return _build_embed_text(scaffold, fd.content, defs=fd.defs)


def build_full_chunks(fd: FileData) -> list[str]:
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


def prepare_texts(files: list[FileData]) -> None:
    """Build anglicised + full_content chunks for each file."""
    for fd in files:
        fd.anglicised = build_anglicised(fd)[:SCAFFOLD_MAX_CHARS]
        fd.full_chunks = build_full_chunks(fd)


# ---------------------------------------------------------------------------
# Embedding (CUDA)
# ---------------------------------------------------------------------------


def load_model(model_name: str):
    from fastembed import TextEmbedding

    t0 = time.monotonic()
    m = TextEmbedding(model_name=model_name, max_length=MAX_LENGTH, cuda=True)
    dt = time.monotonic() - t0
    print(f"  Loaded {model_name.split('/')[-1]} in {dt:.1f}s (CUDA)")
    return m


def embed_batch(model, texts: list[str], batch_size: int = 32) -> np.ndarray:
    """Embed texts on GPU, L2-normalize, return float32 matrix."""
    vecs = list(model.embed(texts, batch_size=batch_size))
    mat = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    mat /= norms
    return mat


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


def build_ang_index(
    model,
    files: list[FileData],
) -> tuple[np.ndarray, list[int], float]:
    """Embed anglicised text — one vector per file."""
    texts = [fd.anglicised for fd in files]
    file_indices = list(range(len(files)))
    t0 = time.monotonic()
    vecs = embed_batch(model, texts)
    embed_ms = (time.monotonic() - t0) * 1000
    return vecs, file_indices, embed_ms


def build_full_index(
    model,
    files: list[FileData],
) -> tuple[np.ndarray, list[int], float]:
    """Embed full_content chunks — only for files < FULL_CONTENT_SIZE_LIMIT.

    Returns vectors + file index mapping. Files >= limit get NO full vectors.
    """
    texts: list[str] = []
    file_indices: list[int] = []
    skipped = 0

    for fi, fd in enumerate(files):
        if fd.source_chars >= FULL_CONTENT_SIZE_LIMIT:
            skipped += 1
            continue
        for chunk in fd.full_chunks:
            texts.append(chunk)
            file_indices.append(fi)

    if not texts:
        return np.zeros((0, 1), dtype=np.float32), [], 0.0

    t0 = time.monotonic()
    vecs = embed_batch(model, texts)
    embed_ms = (time.monotonic() - t0) * 1000

    eligible = len(files) - skipped
    print(
        f"    full_content: {eligible}/{len(files)} files eligible (<{FULL_CONTENT_SIZE_LIMIT // 1000}KB), "
        f"{len(texts)} chunks, {skipped} skipped"
    )
    return vecs, file_indices, embed_ms


# ---------------------------------------------------------------------------
# Retrieval strategies
# ---------------------------------------------------------------------------


def retrieve_ranked(
    query_vec: np.ndarray,
    doc_vecs: np.ndarray,
    file_indices: list[int],
    top_n: int = 50,
) -> list[tuple[int, float]]:
    """Top-N unique files by max cosine similarity across chunks."""
    if len(doc_vecs) == 0:
        return []
    sims = query_vec @ doc_vecs.T
    file_max: dict[int, float] = {}
    for vi, fi in enumerate(file_indices):
        s = float(sims[vi])
        if fi not in file_max or s > file_max[fi]:
            file_max[fi] = s
    ranked = sorted(file_max.items(), key=lambda x: -x[1])
    return ranked[:top_n]


def rrf_fuse(
    rank_list_a: list[tuple[int, float]],
    rank_list_b: list[tuple[int, float]],
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion of two ranked file lists.

    RRF_score(file) = 1/(k + rank_a) + 1/(k + rank_b)
    Files appearing in only one list get rank = len(that_list) + 1 (worst-case).
    """
    # Build rank dicts (1-indexed)
    rank_a: dict[int, int] = {fi: r + 1 for r, (fi, _) in enumerate(rank_list_a)}
    rank_b: dict[int, int] = {fi: r + 1 for r, (fi, _) in enumerate(rank_list_b)}

    all_files = set(rank_a.keys()) | set(rank_b.keys())
    default_a = len(rank_list_a) + 1
    default_b = len(rank_list_b) + 1

    scores: dict[int, float] = {}
    for fi in all_files:
        ra = rank_a.get(fi, default_a)
        rb = rank_b.get(fi, default_b)
        scores[fi] = 1.0 / (k + ra) + 1.0 / (k + rb)

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

EVAL_K_VALUES = [5, 10, 20]


@dataclass
class StrategyResult:
    name: str
    recall_at: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    ang_embed_ms: float = 0.0
    full_embed_ms: float = 0.0
    total_vectors: int = 0
    per_query: list[dict] = field(default_factory=list)


def evaluate_strategy(
    files: list[FileData],
    queries: list[IssueQuery],
    query_vecs: np.ndarray,
    ranked_lists: list[list[tuple[int, float]]],  # one per query
    strategy_name: str,
) -> StrategyResult:
    """Compute Recall@K and MRR for a list of per-query ranked results."""
    file_paths = [f.path for f in files]

    recall_sums: dict[int, float] = dict.fromkeys(EVAL_K_VALUES, 0.0)
    rr_sum = 0.0
    per_query: list[dict] = []

    for qi, iq in enumerate(queries):
        gt_set = set(iq.gt_edit_files)
        gt_indices = {i for i, p in enumerate(file_paths) if p in gt_set}
        if not gt_indices:
            continue

        ranked = ranked_lists[qi]
        top_k_max = max(EVAL_K_VALUES)

        for k_val in EVAL_K_VALUES:
            retrieved = {fi for fi, _ in ranked[:k_val]}
            hits = len(retrieved & gt_indices)
            recall_sums[k_val] += hits / len(gt_indices)

        rr = 0.0
        for rank, (fi, _) in enumerate(ranked, 1):
            if fi in gt_indices:
                rr = 1.0 / rank
                break
        rr_sum += rr

        per_query.append(
            {
                "issue": iq.issue,
                "level": iq.level,
                "gt_count": len(gt_indices),
                "recall@5": sum(1 for fi, _ in ranked[:5] if fi in gt_indices) / len(gt_indices),
                "recall@10": sum(1 for fi, _ in ranked[:10] if fi in gt_indices) / len(gt_indices),
                "recall@20": sum(1 for fi, _ in ranked[:20] if fi in gt_indices) / len(gt_indices),
                "rr": rr,
                "top5": [(file_paths[fi], round(s, 4)) for fi, s in ranked[:5]],
            }
        )

    n = len(queries)
    result = StrategyResult(name=strategy_name)
    result.recall_at = {k: recall_sums[k] / n for k in EVAL_K_VALUES}
    result.mrr = rr_sum / n
    result.per_query = per_query
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Hybrid RRF embedding benchmark (CUDA)")
    parser.add_argument(
        "--model",
        type=int,
        default=None,
        help="Run only model at this index (0=bge, 1=jina-base-code). Omit for both.",
    )
    args = parser.parse_args()

    run_models = [MODELS[args.model]] if args.model is not None else MODELS

    print("=" * 90)
    print("HYBRID RRF EMBEDDING BENCHMARK (CUDA)")
    print("  Strategy A: ang_only    — anglicised scaffold vectors")
    print(
        f"  Strategy B: hybrid_rrf  — RRF(anglicised + full_content for files <{FULL_CONTENT_SIZE_LIMIT // 1000}KB)"
    )
    print(f"  RRF k={RRF_K}, max_length={MAX_LENGTH}, scaffold_cap={SCAFFOLD_MAX_CHARS}")
    print("=" * 90)

    # ── 1. Discover + extract ──
    print(f"\n[1/3] Discovering Python files in {EVEE_ROOT} ...")
    rel_paths = discover_files()
    print(f"  Found {len(rel_paths)} files")

    print("\n  Extracting with tree-sitter...")
    files = extract_all_files(rel_paths)
    print(f"  {len(files)} files extracted successfully")
    if len(files) < 20:
        print("ERROR: Too few files. Aborting.")
        sys.exit(1)

    # ── 2. Build text representations ──
    print("\n[2/3] Building text representations...")
    prepare_texts(files)

    # Stats
    small_files = sum(1 for f in files if f.source_chars < FULL_CONTENT_SIZE_LIMIT)
    total_full_chunks = sum(
        len(f.full_chunks) for f in files if f.source_chars < FULL_CONTENT_SIZE_LIMIT
    )
    print(
        f"  Files: {len(files)} total, {small_files} eligible for full_content (<{FULL_CONTENT_SIZE_LIMIT // 1000}KB)"
    )
    print(f"  Anglicised vectors: {len(files)}")
    print(f"  Full content vectors: {total_full_chunks} (from {small_files} files)")

    sizes = [f.source_chars for f in files]
    print(
        f"  Source size: min={min(sizes)}, median={sorted(sizes)[len(sizes) // 2]}, "
        f"max={max(sizes)}, mean={sum(sizes) / len(sizes):.0f}"
    )

    # ── 3. Run each model ──
    queries = ISSUE_QUERIES
    print(f"\n[3/3] Running {len(run_models)} models × 2 strategies × {len(queries)} queries...")

    all_results: dict[str, dict[str, StrategyResult]] = {}  # model_short -> strategy -> result

    for model_name, dim, size_gb in run_models:
        short_name = model_name.split("/")[-1]
        print(f"\n{'━' * 90}")
        print(f"  MODEL: {model_name}  (dim={dim}, {size_gb} GB)")
        print(f"{'━' * 90}")

        model = load_model(model_name)

        # Embed queries
        print("  Embedding queries...")
        query_texts = [iq.query for iq in queries]
        t0 = time.monotonic()
        query_vecs = embed_batch(model, query_texts)
        query_ms = (time.monotonic() - t0) * 1000
        print(f"    {len(queries)} queries in {query_ms:.0f}ms")

        # ── Strategy A: ang_only ──
        print("\n  Strategy A: ang_only")
        ang_vecs, ang_fi, ang_ms = build_ang_index(model, files)
        print(f"    {len(ang_vecs)} vectors in {ang_ms:.0f}ms")

        ang_ranked = []
        for qi in range(len(queries)):
            ranked = retrieve_ranked(query_vecs[qi], ang_vecs, ang_fi, top_n=50)
            ang_ranked.append(ranked)

        ang_result = evaluate_strategy(files, queries, query_vecs, ang_ranked, "ang_only")
        ang_result.ang_embed_ms = ang_ms
        ang_result.total_vectors = len(ang_vecs)

        # ── Strategy B: hybrid_rrf ──
        print("\n  Strategy B: hybrid_rrf")
        full_vecs, full_fi, full_ms = build_full_index(model, files)
        print(f"    {len(full_vecs)} vectors in {full_ms:.0f}ms")

        rrf_ranked = []
        for qi in range(len(queries)):
            ang_r = retrieve_ranked(query_vecs[qi], ang_vecs, ang_fi, top_n=50)
            full_r = retrieve_ranked(query_vecs[qi], full_vecs, full_fi, top_n=50)
            fused = rrf_fuse(ang_r, full_r)
            rrf_ranked.append(fused)

        rrf_result = evaluate_strategy(files, queries, query_vecs, rrf_ranked, "hybrid_rrf")
        rrf_result.ang_embed_ms = ang_ms
        rrf_result.full_embed_ms = full_ms
        rrf_result.total_vectors = len(ang_vecs) + len(full_vecs)

        all_results[short_name] = {"ang_only": ang_result, "hybrid_rrf": rrf_result}

        # ── Per-model summary ──
        print(
            f"\n  {'Strategy':<14} {'Vecs':>6} {'Embed ms':>10} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}"
        )
        print(f"  {'─' * 64}")
        for strat in [ang_result, rrf_result]:
            total_ms = strat.ang_embed_ms + strat.full_embed_ms
            print(
                f"  {strat.name:<14} {strat.total_vectors:>6} {total_ms:>10,.0f} "
                f"{strat.recall_at[5]:>7.3f} {strat.recall_at[10]:>7.3f} "
                f"{strat.recall_at[20]:>7.3f} {strat.mrr:>7.3f}"
            )

        # Deltas
        d5 = rrf_result.recall_at[5] - ang_result.recall_at[5]
        d10 = rrf_result.recall_at[10] - ang_result.recall_at[10]
        d20 = rrf_result.recall_at[20] - ang_result.recall_at[20]
        dmrr = rrf_result.mrr - ang_result.mrr
        print(
            f"  {'Δ (rrf-ang)':<14} {'':>6} {'':>10} "
            f"{d5:>+7.3f} {d10:>+7.3f} {d20:>+7.3f} {dmrr:>+7.3f}"
        )

        # Per-query breakdown
        print("\n  Per-query R@5 comparison:")
        print(f"  {'Issue':>5} {'Lvl':>3} {'GT':>3} {'ang_only':>10} {'hybrid_rrf':>12} {'Δ':>8}")
        print(f"  {'─' * 45}")
        for qi, iq in enumerate(queries):
            r5_ang = ang_result.per_query[qi]["recall@5"]
            r5_rrf = rrf_result.per_query[qi]["recall@5"]
            delta = r5_rrf - r5_ang
            marker = " ✓" if delta > 0.001 else (" ✗" if delta < -0.001 else "  ")
            print(
                f"  #{iq.issue:<4} {iq.level:>3} {len(iq.gt_edit_files):>3} "
                f"{r5_ang:>10.1%} {r5_rrf:>12.1%} {delta:>+7.1%}{marker}"
            )

        del model
        gc.collect()

    # ───────────────────────────────────────────────────────────────────────
    # Cross-model comparison
    # ───────────────────────────────────────────────────────────────────────
    if len(all_results) < 2:
        single = list(all_results.keys())[0]
        print(f"\n{'=' * 90}")
        print(f"Single-model run complete ({single}). Run without --model for full comparison.")
        print(f"{'=' * 90}")
        return

    print(f"\n{'=' * 90}")
    print("CROSS-MODEL COMPARISON")
    print(f"{'=' * 90}")

    print(
        f"\n  {'Model':<35} {'Strategy':<14} {'Vecs':>6} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}"
    )
    print(f"  {'─' * 90}")
    for mname in all_results:
        for sname in ["ang_only", "hybrid_rrf"]:
            r = all_results[mname][sname]
            print(
                f"  {mname:<35} {sname:<14} {r.total_vectors:>6} "
                f"{r.recall_at[5]:>7.3f} {r.recall_at[10]:>7.3f} "
                f"{r.recall_at[20]:>7.3f} {r.mrr:>7.3f}"
            )
        print()

    # Best combo
    print("\n  RANKED BY R@10:")
    combos = []
    for mname in all_results:
        for sname in ["ang_only", "hybrid_rrf"]:
            r = all_results[mname][sname]
            combos.append((mname, sname, r.recall_at[5], r.recall_at[10], r.recall_at[20], r.mrr))
    combos.sort(key=lambda x: (-x[3], -x[5]))
    print(
        f"  {'#':>3} {'Model':<35} {'Strategy':<14} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}"
    )
    print(f"  {'─' * 90}")
    for i, (mname, sname, r5, r10, r20, mrr) in enumerate(combos, 1):
        print(f"  {i:>3} {mname:<35} {sname:<14} {r5:>7.3f} {r10:>7.3f} {r20:>7.3f} {mrr:>7.3f}")

    # Per-issue winner table
    print(f"\n{'=' * 90}")
    print("PER-ISSUE R@10 — ALL 4 COMBOS")
    print(f"{'=' * 90}")
    model_names = list(all_results.keys())
    combos_labels = [(m, s) for m in model_names for s in ["ang_only", "hybrid_rrf"]]
    print(f"  {'Issue':>5} {'Lvl':>3} {'GT':>3}", end="")
    for m, s in combos_labels:
        label = f"{m[:8]}_{s[:3]}"
        print(f"  {label:>14}", end="")
    print()
    print(f"  {'─' * 5} {'─' * 3} {'─' * 3}", end="")
    for _ in combos_labels:
        print(f"  {'─' * 14}", end="")
    print()

    for qi, iq in enumerate(queries):
        print(f"  #{iq.issue:<4} {iq.level:>3} {len(iq.gt_edit_files):>3}", end="")
        best_r10 = -1.0
        for m, s in combos_labels:
            r10 = all_results[m][s].per_query[qi]["recall@10"]
            if r10 > best_r10:
                best_r10 = r10
        for m, s in combos_labels:
            r10 = all_results[m][s].per_query[qi]["recall@10"]
            marker = " *" if abs(r10 - best_r10) < 0.001 and best_r10 > 0 else "  "
            print(f"  {r10:>12.1%}{marker}", end="")
        print()


if __name__ == "__main__":
    main()
