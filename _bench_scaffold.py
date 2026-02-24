#!/usr/bin/env python3
"""Scaffold enrichment benchmark: does adding string_literals + full import paths
to the scaffold close the gap between bge-small and jina-base-code?

Hypothesis: The current scaffold drops two categories of signal that tree-sitter
ALREADY extracts:
  1. String literals (env vars, config keys, error messages) → `_string_literals`
  2. Full import source paths (`rich.progress` → scaffold only says `progress`)

If we add these back, the smaller/cheaper bge-small (384d, 0.067GB) might match
the expensive jina-base-code (768d, 0.64GB) that currently wins.

Approach:
  - Build 3 scaffold variants per file:
      A) current   — existing build_file_scaffold()
      B) enriched  — current + string_literals + full import paths
      C) enriched+ — enriched + expanded docstrings
  - Embed all 3 variants with both models
  - Compare Recall@5/10/20 and MRR across 24 real queries

CPU-only (fast enough for 84 files × 2 models × 3 variants).
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
MAX_LENGTH = 512
SCAFFOLD_MAX_CHARS = 2048

MODELS = [
    ("BAAI/bge-small-en-v1.5", 384, 0.067),
    ("jinaai/jina-embeddings-v2-base-code", 768, 0.64),
]


# ---------------------------------------------------------------------------
# Real issue queries (same as _bench_embed.py)
# ---------------------------------------------------------------------------

@dataclass
class IssueQuery:
    issue: int
    level: str
    query: str
    gt_edit_files: list[str]


ISSUE_QUERIES: list[IssueQuery] = [
    # --- #4: Cache model inference ---
    IssueQuery(issue=4, level="Q1",
        query="I need to implement inference result caching in Evee's evaluation pipeline. The cache should intercept model inference calls in the ModelEvaluator (_infer_record and _infer_record_async), store InferenceOutput results keyed by input record hash, and skip re-inference on cache hits. I need to add cache configuration fields to Config/ModelVariantConfig in the config models, update the evaluation loop, add cache hit/miss logging, and write tests for the caching behavior.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py"]),
    IssueQuery(issue=4, level="Q2",
        query="Add caching support for deterministic model inference results in Evee. When a model's results are deterministic, re-running evaluation should reuse cached inference outputs instead of calling the model again. This involves changes to the evaluation pipeline, configuration schema, and model infrastructure.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py"]),
    IssueQuery(issue=4, level="Q3",
        query="How can I add result caching to Evee so that re-running experiments with the same models doesn't repeat inference? I want to save time and costs during iterative development.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py"]),

    # --- #259: Raise error when .env missing ---
    IssueQuery(issue=259, level="Q1",
        query="I need to add startup validation in Evee that raises an explicit error when the .env file is missing. Currently .env loading is handled silently in multiple places: cli/commands/run.py, cli/commands/validate.py, cli/main.py, evaluation/model_evaluator.py, evaluation/evaluate.py, execution/experiment_runner.py, and core/base_model.py. The DEFAULT_ENV_FILE constant is in cli/constants.py. I should add the check in execution/preflight.py.",
        gt_edit_files=["src/evee/cli/commands/run.py", "src/evee/cli/commands/validate.py", "src/evee/cli/main.py", "src/evee/execution/preflight.py", "src/evee/execution/experiment_runner.py", "src/evee/evaluation/evaluate.py", "src/evee/evaluation/model_evaluator.py"]),
    IssueQuery(issue=259, level="Q2",
        query="Add a check that raises an explicit error when the .env file is missing in Evee. Currently it fails silently and causes confusing downstream errors. I need to find all places where .env is loaded, the preflight validation system, and the constants defining the default .env path.",
        gt_edit_files=["src/evee/cli/commands/run.py", "src/evee/cli/commands/validate.py", "src/evee/cli/main.py", "src/evee/execution/preflight.py", "src/evee/execution/experiment_runner.py", "src/evee/evaluation/evaluate.py", "src/evee/evaluation/model_evaluator.py"]),
    IssueQuery(issue=259, level="Q3",
        query="Evee should tell users clearly when their .env file is missing instead of failing with confusing errors later. Where does Evee load the .env file and where should this validation go?",
        gt_edit_files=["src/evee/cli/commands/run.py", "src/evee/cli/commands/validate.py", "src/evee/cli/main.py", "src/evee/execution/preflight.py", "src/evee/execution/experiment_runner.py", "src/evee/evaluation/evaluate.py", "src/evee/evaluation/model_evaluator.py"]),

    # --- #260: Config flag to disable rich progress bars ---
    IssueQuery(issue=260, level="Q1",
        query="I need to add a configuration flag in config.yaml to disable Rich progress bars for CI environments. The is_rich_compatible_environment() function in src/evee/utils/environment.py already checks EVEE_DISABLE_RICH_LOGGING env var and MCP mode. The ProgressTracker in evaluation/progress_tracker.py and logger in logging/logger.py both use this function. I need to add a new field to RuntimeConfig or ExperimentConfig in config/models.py.",
        gt_edit_files=["src/evee/config/models.py", "src/evee/utils/environment.py", "src/evee/evaluation/progress_tracker.py", "src/evee/evaluation/model_evaluator.py", "src/evee/mcp/resources/config.py"]),
    IssueQuery(issue=260, level="Q2",
        query="Add a config.yaml flag to disable Rich progress bars. Evee already suppresses them for MCP and AzureML, but users need to disable them for CI too. I need to find where Rich environment detection happens, the config model, and the progress tracker/logger code.",
        gt_edit_files=["src/evee/config/models.py", "src/evee/utils/environment.py", "src/evee/evaluation/progress_tracker.py", "src/evee/evaluation/model_evaluator.py", "src/evee/mcp/resources/config.py"]),
    IssueQuery(issue=260, level="Q3",
        query="Rich progress bars clutter CI logs. I need to add a way to disable them via config. Where does Evee decide whether to show Rich output and how do I add a config flag?",
        gt_edit_files=["src/evee/config/models.py", "src/evee/utils/environment.py", "src/evee/evaluation/progress_tracker.py", "src/evee/evaluation/model_evaluator.py", "src/evee/mcp/resources/config.py"]),

    # --- #226: Set default MLflow tracking URI to sqlite ---
    IssueQuery(issue=226, level="Q1",
        query="I need to change the default MLflow tracking URI from the filesystem backend (./mlruns) to sqlite:///mlflow.db in the evee-mlflow package. The MLflowTrackingConfig in packages/evee-mlflow/src/evee_mlflow/config.py has tracking_uri defaulting to None with artifact_location defaulting to ./mlruns. The MLflowBackend.on_startup() in tracking.py logs the ./mlruns directory message when no URI is set.",
        gt_edit_files=["packages/evee-mlflow/src/evee_mlflow/config.py", "packages/evee-mlflow/src/evee_mlflow/tracking.py", "src/evee/mcp/resources/config.py"]),
    IssueQuery(issue=226, level="Q2",
        query="Change the default MLflow tracking URI to use SQLite instead of the filesystem backend being deprecated. I need to find where the MLflow tracking URI default is set in the evee-mlflow package, all tests and configs that reference ./mlruns, and documentation.",
        gt_edit_files=["packages/evee-mlflow/src/evee_mlflow/config.py", "packages/evee-mlflow/src/evee_mlflow/tracking.py", "src/evee/mcp/resources/config.py"]),
    IssueQuery(issue=226, level="Q3",
        query="MLflow's filesystem backend is being deprecated. I need to change Evee's default tracking to use SQLite instead. Where is the MLflow backend configured and what references ./mlruns or the tracking URI?",
        gt_edit_files=["packages/evee-mlflow/src/evee_mlflow/config.py", "packages/evee-mlflow/src/evee_mlflow/tracking.py", "src/evee/mcp/resources/config.py"]),

    # --- #233: Early stop for evaluation ---
    IssueQuery(issue=233, level="Q1",
        query="I need to implement early stopping in Evee's evaluation pipeline. In the inference phase (_run_evaluation_loop in ModelEvaluator), count consecutive or total errors and abort early if a threshold is exceeded. I need the evaluation loop code, config models for new threshold fields, progress tracking, error handling patterns, and the tracking events for an EarlyStopEvent.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py", "src/evee/evaluation/progress_tracker.py", "src/evee/tracking/events.py"]),
    IssueQuery(issue=233, level="Q2",
        query="Add early stopping to Evee's evaluation. If too many inference errors occur, stop the evaluation early instead of running the full dataset. I need to understand the evaluation loop, error handling, config schema, and progress tracking.",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py", "src/evee/evaluation/progress_tracker.py", "src/evee/tracking/events.py"]),
    IssueQuery(issue=233, level="Q3",
        query="Evee should stop evaluation early when there are too many errors. Where is the evaluation loop and how does error handling work?",
        gt_edit_files=["src/evee/evaluation/model_evaluator.py", "src/evee/config/models.py", "src/evee/evaluation/progress_tracker.py", "src/evee/tracking/events.py"]),

    # --- #262: REST-based models ---
    IssueQuery(issue=262, level="Q1",
        query="I need to add support for configurable REST-based models in Evee. Instead of writing custom @model decorated classes for each REST endpoint, users should define REST model configuration in config.yaml. This requires changes to ModelVariantConfig in config/models.py, a new RestModel class, and changes to ModelEvaluator._register_model to detect type rest and instantiate RestModel. The model should bypass decorator_discovery.",
        gt_edit_files=["src/evee/config/models.py", "src/evee/evaluation/model_evaluator.py", "src/evee/mcp/resources/model_patterns.py", "src/evee/mcp/resources/config.py", "src/evee/cli/commands/model.py", "src/evee/cli/utils/model_operations.py", "src/evee/cli/commands/validate.py"]),
    IssueQuery(issue=262, level="Q2",
        query="Add configuration-driven REST models to Evee so users don't need to write model classes for simple REST endpoints. I need to understand the model registration system (@model decorator, MODEL_REGISTRY, _register_model), the config schema, and how the evaluation pipeline works with models.",
        gt_edit_files=["src/evee/config/models.py", "src/evee/evaluation/model_evaluator.py", "src/evee/mcp/resources/model_patterns.py", "src/evee/mcp/resources/config.py", "src/evee/cli/commands/model.py", "src/evee/cli/utils/model_operations.py", "src/evee/cli/commands/validate.py"]),
    IssueQuery(issue=262, level="Q3",
        query="Users keep writing model classes that just wrap REST calls. I want to make REST models configurable instead. Where is the model system in Evee and how does model registration work?",
        gt_edit_files=["src/evee/config/models.py", "src/evee/evaluation/model_evaluator.py", "src/evee/mcp/resources/model_patterns.py", "src/evee/mcp/resources/config.py", "src/evee/cli/commands/model.py", "src/evee/cli/utils/model_operations.py", "src/evee/cli/commands/validate.py"]),

    # --- #275: Reuse metrics with custom instance names ---
    IssueQuery(issue=275, level="Q1",
        query="I need to decouple metric implementation lookup from display names in Evee. Currently MetricConfig.name in config/models.py serves as both the METRIC_REGISTRY lookup key and the reporting label. To reuse the same metric class with different parameters, I need an entry_point field for implementation lookup while name becomes display-only. This affects _register_metric(), metric templates, CLI commands, validation, and aggregation.",
        gt_edit_files=["src/evee/config/models.py", "src/evee/evaluation/model_evaluator.py", "src/evee/cli/commands/validate.py", "src/evee/cli/commands/metric.py", "src/evee/cli/utils/metric_operations.py", "src/evee/mcp/resources/config.py", "src/evee/mcp/resources/metric_patterns.py"]),
    IssueQuery(issue=275, level="Q2",
        query="Evee can't reuse the same metric with different configurations because name is used for both lookup and display. I need to separate these concerns so I can have Coherence and Violence both using the llm_judge metric with different prompts. Where is the metric registry, config model, evaluation pipeline, and CLI metric management?",
        gt_edit_files=["src/evee/config/models.py", "src/evee/evaluation/model_evaluator.py", "src/evee/cli/commands/validate.py", "src/evee/cli/commands/metric.py", "src/evee/cli/utils/metric_operations.py", "src/evee/mcp/resources/config.py", "src/evee/mcp/resources/metric_patterns.py"]),
    IssueQuery(issue=275, level="Q3",
        query="I want to use the same metric class multiple times with different parameters, but Evee's metric naming system doesn't allow it. How does metric registration and lookup work, and where would I change it?",
        gt_edit_files=["src/evee/config/models.py", "src/evee/evaluation/model_evaluator.py", "src/evee/cli/commands/validate.py", "src/evee/cli/commands/metric.py", "src/evee/cli/utils/metric_operations.py", "src/evee/mcp/resources/config.py", "src/evee/mcp/resources/metric_patterns.py"]),

    # --- #193: Configurable dependency sources in `evee new` ---
    IssueQuery(issue=193, level="Q1",
        query="I need to add an interactive source selection flow to the evee new CLI command. Currently new.py hardcodes Git-based dependencies via _GIT_BASE. I need to add interactive prompts for choosing between Git, pre-built wheels, and local source, with flags (--from-git, --wheels, --from-repo, --from-source) for automation. The pyproject.toml templates in src/evee/cli/templates/overlays/ use placeholders that need different rendering per source type.",
        gt_edit_files=["src/evee/cli/commands/new.py", "src/evee/cli/utils/new_project_operations.py"]),
    IssueQuery(issue=193, level="Q2",
        query="I want to add dependency source selection to evee new so users can choose between Git, wheels, or local source for installing Evee packages. I need to understand the current scaffolding flow in the CLI, the template overlay system, and how pyproject.toml placeholders are rendered.",
        gt_edit_files=["src/evee/cli/commands/new.py", "src/evee/cli/utils/new_project_operations.py"]),
    IssueQuery(issue=193, level="Q3",
        query="How does evee new work for creating new projects? I need to add support for different ways of installing Evee packages — not just from Git. Where is the scaffolding code and template system?",
        gt_edit_files=["src/evee/cli/commands/new.py", "src/evee/cli/utils/new_project_operations.py"]),
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
    current_scaffold: str = ""
    enriched_scaffold: str = ""
    enriched_plus_scaffold: str = ""


# ---------------------------------------------------------------------------
# File discovery + tree-sitter extraction
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
    from codeplane.index._internal.indexing.structural import _extract_file
    files: list[FileData] = []
    for rel_path in rel_paths:
        full = EVEE_ROOT / rel_path
        if not full.exists():
            continue
        result = _extract_file(rel_path, str(EVEE_ROOT), unit_id=1)
        if result.error:
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
# Scaffold builders
# ---------------------------------------------------------------------------

def build_current_scaffold(fd: FileData) -> str:
    """Current production scaffold — anglicised, no string literals."""
    from codeplane.index._internal.indexing.file_embedding import (
        _build_embed_text, build_file_scaffold,
    )
    scaffold = build_file_scaffold(fd.path, fd.defs, fd.imports)
    return _build_embed_text(scaffold, fd.content, defs=fd.defs)


def _word_split(name: str) -> list[str]:
    import re
    _CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")
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


def build_enriched_scaffold(fd: FileData) -> str:
    """Enriched scaffold: current + string_literals + full import paths."""
    from codeplane.index._internal.indexing.file_embedding import build_file_scaffold

    # Start with the current scaffold
    scaffold = build_file_scaffold(fd.path, fd.defs, fd.imports)
    lines = scaffold.split("\n") if scaffold else []

    # --- Enhancement 1: Replace imports line with FULL source paths ---
    # Current: "imports logging, typing, console, progress, environment"
    # Enriched: "imports logging, typing, rich console, rich progress, evee utils environment"
    if fd.imports:
        import_tokens: list[str] = []
        seen: set[str] = set()
        for imp in fd.imports:
            source = imp.get("source_literal", "") or imp.get("module_path", "") or ""
            name = imp.get("imported_name", "") or ""
            if source:
                # Use full dotted path split into words (not just last segment)
                token = " ".join(_word_split(source.replace(".", "_")))
            elif name:
                token = " ".join(_word_split(name))
            else:
                continue
            if token and token not in seen:
                seen.add(token)
                import_tokens.append(token)
        if import_tokens:
            new_import_line = f"imports {', '.join(import_tokens)}"
            # Replace existing imports line
            for i, line in enumerate(lines):
                if line.startswith("imports "):
                    lines[i] = new_import_line
                    break
            else:
                lines.insert(1, new_import_line)  # after module line

    # --- Enhancement 2: Add string literals as `mentions` line ---
    all_lits: list[str] = []
    seen_lits: set[str] = set()
    for d in fd.defs:
        for lit in d.get("_string_literals", []):
            lit_clean = lit.strip()
            # Skip trivial values (true/false/empty/short)
            if lit_clean.lower() in ("true", "false", "none", "", "0", "1"):
                continue
            if len(lit_clean) < 3:
                continue
            if lit_clean not in seen_lits:
                seen_lits.add(lit_clean)
                all_lits.append(lit_clean)
    if all_lits:
        # Budget: max 300 chars of literals to keep scaffold compact
        mentions_parts: list[str] = []
        chars_used = 0
        for lit in all_lits:
            if chars_used + len(lit) + 2 > 300:
                break
            mentions_parts.append(lit)
            chars_used += len(lit) + 2
        lines.append(f"mentions {', '.join(mentions_parts)}")

    text = "\n".join(lines) if lines else ""
    if text:
        text = f"FILE_SCAFFOLD\n{text}"
    return text[:SCAFFOLD_MAX_CHARS]


def build_enriched_plus_scaffold(fd: FileData) -> str:
    """Enriched+ scaffold: enriched + module docstring + expanded docstrings."""
    from codeplane.index._internal.indexing.file_embedding import build_file_scaffold

    scaffold = build_file_scaffold(fd.path, fd.defs, fd.imports)
    lines = scaffold.split("\n") if scaffold else []

    # Enhancement 1: Full import paths (same as enriched)
    if fd.imports:
        import_tokens: list[str] = []
        seen: set[str] = set()
        for imp in fd.imports:
            source = imp.get("source_literal", "") or imp.get("module_path", "") or ""
            name = imp.get("imported_name", "") or ""
            if source:
                token = " ".join(_word_split(source.replace(".", "_")))
            elif name:
                token = " ".join(_word_split(name))
            else:
                continue
            if token and token not in seen:
                seen.add(token)
                import_tokens.append(token)
        if import_tokens:
            new_import_line = f"imports {', '.join(import_tokens)}"
            for i, line in enumerate(lines):
                if line.startswith("imports "):
                    lines[i] = new_import_line
                    break
            else:
                lines.insert(1, new_import_line)

    # Enhancement 2: Module-level docstring (first triple-quoted string)
    content_stripped = fd.content.lstrip()
    if content_stripped.startswith('"""') or content_stripped.startswith("'''"):
        quote = content_stripped[:3]
        end = content_stripped.find(quote, 3)
        if end > 3:
            module_doc = content_stripped[3:end].strip()
            if module_doc and len(module_doc) > 10:
                lines.insert(1, f"purpose {module_doc[:150]}")

    # Enhancement 3: String literals (same as enriched)
    all_lits: list[str] = []
    seen_lits: set[str] = set()
    for d in fd.defs:
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
        mentions_parts: list[str] = []
        chars_used = 0
        for lit in all_lits:
            if chars_used + len(lit) + 2 > 300:
                break
            mentions_parts.append(lit)
            chars_used += len(lit) + 2
        lines.append(f"mentions {', '.join(mentions_parts)}")

    # Enhancement 4: Expanded docstrings (full docstring, not just first sentence)
    # Replace existing "describes" lines with fuller versions
    new_lines: list[str] = []
    for line in lines:
        if line.startswith("describes "):
            new_lines.append(line)  # keep existing
        else:
            new_lines.append(line)
    # Add additional docstring content beyond first sentence
    doc_budget = 400
    doc_used = 0
    for d in fd.defs:
        doc = (d.get("docstring") or "").strip()
        if doc and len(doc) > 50 and doc_used < doc_budget:  # only substantial docs
            # Include full docstring, not just first sentence
            name = d.get("name", "")
            remaining = doc_budget - doc_used
            full_doc = doc[:remaining]
            # Don't duplicate first-sentence describes lines already present
            if full_doc and len(full_doc) > len(doc.split(".")[0]) + 10:
                prefix = " ".join(_word_split(name)) if name else ""
                new_lines.append(f"details {prefix}: {full_doc}")
                doc_used += len(full_doc)
    lines = new_lines

    text = "\n".join(lines) if lines else ""
    if text:
        text = f"FILE_SCAFFOLD\n{text}"
    return text[:SCAFFOLD_MAX_CHARS]


def prepare_scaffolds(files: list[FileData]) -> None:
    for fd in files:
        fd.current_scaffold = build_current_scaffold(fd)
        fd.enriched_scaffold = build_enriched_scaffold(fd)
        fd.enriched_plus_scaffold = build_enriched_plus_scaffold(fd)


# ---------------------------------------------------------------------------
# Embedding + Retrieval
# ---------------------------------------------------------------------------

def load_model(model_name: str):
    from fastembed import TextEmbedding
    t0 = time.monotonic()
    providers = ["CPUExecutionProvider"]  # CPU for reliability
    m = TextEmbedding(model_name=model_name, max_length=MAX_LENGTH, providers=providers)
    dt = time.monotonic() - t0
    print(f"  Loaded {model_name.split('/')[-1]} in {dt:.1f}s")
    return m


def embed_batch(model, texts: list[str], batch_size: int = 8) -> np.ndarray:
    vecs = list(model.embed(texts, batch_size=batch_size))
    mat = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    mat /= norms
    return mat


EVAL_K_VALUES = [5, 10, 20]


def compute_metrics(
    files: list[FileData],
    queries: list[IssueQuery],
    doc_vecs: np.ndarray,
    query_vecs: np.ndarray,
) -> tuple[dict[int, float], float, list[dict]]:
    """Compute Recall@K and MRR. One vector per file (no chunking)."""
    file_paths = [f.path for f in files]
    recall_sums: dict[int, float] = {k: 0.0 for k in EVAL_K_VALUES}
    rr_sum = 0.0
    per_query: list[dict] = []

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

        per_query.append({
            "issue": iq.issue, "level": iq.level,
            "gt_count": len(gt_indices),
            "recall@5": sum(1 for fi in ranked_indices[:5] if int(fi) in gt_indices) / len(gt_indices),
            "recall@10": sum(1 for fi in ranked_indices[:10] if int(fi) in gt_indices) / len(gt_indices),
            "rr": rr,
            "top5": [(file_paths[int(fi)], round(float(sims[fi]), 3)) for fi in ranked_indices[:5]],
        })

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
    parser.add_argument("--model", type=int, default=None, help="0=bge-small, 1=jina-base-code")
    args = parser.parse_args()

    run_models = [MODELS[args.model]] if args.model is not None else MODELS

    print("=" * 90)
    print("SCAFFOLD ENRICHMENT BENCHMARK")
    print("Can enriched scaffolds on bge-small match jina-base-code?")
    print("=" * 90)

    # 1. Discover + extract
    print(f"\n[1/3] Discovering files in {EVEE_ROOT} ...")
    rel_paths = discover_files()
    print(f"  Found {len(rel_paths)} files")

    print("\n[2/3] Extracting with tree-sitter + building 3 scaffold variants...")
    files = extract_all_files(rel_paths)
    print(f"  {len(files)} files extracted")

    if len(files) < 20:
        print("ERROR: Too few files. Aborting.")
        sys.exit(1)

    prepare_scaffolds(files)

    variants = ["current", "enriched", "enriched+"]

    # Show scaffold size stats
    print(f"\n  {'Variant':<12} {'Mean chars':>10} {'Max chars':>10} {'P50 chars':>10}")
    print(f"  {'-'*44}")
    for vname in variants:
        texts = [getattr(fd, f"{vname.replace('+', '_plus')}_scaffold") for fd in files]
        lens = [len(t) for t in texts]
        print(f"  {vname:<12} {np.mean(lens):>10.0f} {max(lens):>10} {np.median(lens):>10.0f}")

    # Show example enrichment for environment.py
    for fd in files:
        if "environment.py" in fd.path:
            print(f"\n  --- Example: {fd.path} ---")
            print(f"\n  CURRENT ({len(fd.current_scaffold)} chars):")
            for line in fd.current_scaffold.split("\n"):
                print(f"    {line}")
            print(f"\n  ENRICHED ({len(fd.enriched_scaffold)} chars):")
            for line in fd.enriched_scaffold.split("\n"):
                print(f"    {line}")
            break

    # 2. Run each model
    queries = ISSUE_QUERIES
    print(f"\n[3/3] Running {len(run_models)} models × {len(variants)} scaffold variants × {len(queries)} queries...")

    all_results: dict[str, dict[str, tuple[dict, float, list]]] = {}

    for model_name, dim, size_gb in run_models:
        short = model_name.split("/")[-1]
        print(f"\n{'─' * 90}")
        print(f"  {model_name}  (dim={dim}, {size_gb} GB)")
        model = load_model(model_name)

        # Embed queries once
        query_texts = [iq.query for iq in queries]
        query_vecs = embed_batch(model, query_texts)

        model_results: dict[str, tuple[dict, float, list]] = {}

        for vname in variants:
            attr = f"{vname.replace('+', '_plus')}_scaffold"
            texts = [getattr(fd, attr) for fd in files]

            t0 = time.monotonic()
            doc_vecs = embed_batch(model, texts)
            embed_ms = (time.monotonic() - t0) * 1000

            recall_at, mrr, per_query = compute_metrics(files, queries, doc_vecs, query_vecs)
            model_results[vname] = (recall_at, mrr, per_query)

            print(f"    {vname:<12} R@5={recall_at[5]:.3f}  R@10={recall_at[10]:.3f}  "
                  f"R@20={recall_at[20]:.3f}  MRR={mrr:.3f}  ({embed_ms:.0f}ms)")

        all_results[short] = model_results

        del model
        gc.collect()

    # --- Summary ---
    print(f"\n{'=' * 90}")
    print("SUMMARY: Can enriched bge-small match current jina-base-code?")
    print(f"{'=' * 90}")

    print(f"\n  {'Model × Variant':<45} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}")
    print(f"  {'-' * 73}")
    for short, model_results in all_results.items():
        for vname in variants:
            recall_at, mrr, _ = model_results[vname]
            label = f"{short} × {vname}"
            print(f"  {label:<45} {recall_at[5]:>7.3f} {recall_at[10]:>7.3f} "
                  f"{recall_at[20]:>7.3f} {mrr:>7.3f}")
        print()

    # Per-issue comparison (if both models ran)
    if len(all_results) >= 2:
        model_keys = list(all_results.keys())
        m0, m1 = model_keys[0], model_keys[1]
        print(f"\n{'=' * 90}")
        print(f"PER-ISSUE: {m0} enriched+ vs {m1} current")
        print(f"{'=' * 90}")
        print(f"\n  {'Issue':>5} {'Lvl':>3} {'GT':>3}  {m0+' enr+':>15}  {m1+' curr':>15}  {'Delta':>7}")
        print(f"  {'-' * 52}")

        pq_bge = all_results[m0]["enriched+"][2]
        pq_jina = all_results[m1]["current"][2]

        wins = draws = losses = 0
        for qi in range(len(queries)):
            iq = queries[qi]
            r5_bge = pq_bge[qi]["recall@5"]
            r5_jina = pq_jina[qi]["recall@5"]
            delta = r5_bge - r5_jina
            marker = "  ✓" if delta > 0.001 else ("  ✗" if delta < -0.001 else "  =")
            if delta > 0.001:
                wins += 1
            elif delta < -0.001:
                losses += 1
            else:
                draws += 1
            print(f"  #{iq.issue:<4} {iq.level:>3} {len(iq.gt_edit_files):>3}  "
                  f"{r5_bge:>13.1%}  {r5_jina:>13.1%}  {delta:>+6.1%}{marker}")

        print(f"\n  Wins: {wins}  Draws: {draws}  Losses: {losses}")

    # Show delta analysis
    if len(all_results) >= 2:
        m0_key = list(all_results.keys())[0]
        print(f"\n{'=' * 90}")
        print(f"ENRICHMENT IMPACT on {m0_key} (enriched+ vs current)")
        print(f"{'=' * 90}")
        curr = all_results[m0_key]["current"]
        enr = all_results[m0_key]["enriched+"]
        print(f"\n  {'Metric':<10} {'Current':>8} {'Enriched+':>10} {'Delta':>8} {'Change':>8}")
        print(f"  {'-' * 46}")
        for k in EVAL_K_VALUES:
            c = curr[0][k]
            e = enr[0][k]
            d = e - c
            pct = d / c * 100 if c > 0 else 0
            print(f"  R@{k:<7} {c:>8.3f} {e:>10.3f} {d:>+8.3f} {pct:>+7.1f}%")
        c_mrr = curr[1]
        e_mrr = enr[1]
        d_mrr = e_mrr - c_mrr
        pct_mrr = d_mrr / c_mrr * 100 if c_mrr > 0 else 0
        print(f"  {'MRR':<10} {c_mrr:>8.3f} {e_mrr:>10.3f} {d_mrr:>+8.3f} {pct_mrr:>+7.1f}%")


if __name__ == "__main__":
    main()
