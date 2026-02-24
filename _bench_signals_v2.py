#!/usr/bin/env python3
"""Signal permutation benchmark v2: 8 signals, targeted combos, bge-small only.

Signals (4 existing + 4 new):
  S = string_literals   (env vars, config keys, error messages)
  I = full_imports       (full dotted import paths)
  C = sem_calls          (function/method names called within each def)
  D = decorator_names    (short decorator names)
  B = base_classes       (superclass names from class definitions) ← NEW
  M = module_constants   (top-level ALL_CAPS assignments)          ← NEW
  R = sem_returns        (identifiers in return statements)        ← NEW
  K = sem_dict_keys      (dict literal key names)                  ← NEW

Tested combos (20 variants × 2 chunk modes = 40 total):
  - baseline, each signal alone (9)
  - prior best combos: S+I, S+I+C+D (2)
  - each new signal added to S+I+C+D (4)
  - B+M alone, B+M+R+K alone (2)
  - S+I+B+M (cheap high-value) (1)
  - S+I+C+D+B+M (1)
  - S+I+C+D+B+M+R+K (all 8) (1)

CPU-only. ~5-8 min for 84 files × 40 variants × 24 queries.
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
SCAFFOLD_MAX_CHARS = 2048
TOKEN_SPLIT = 450

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_DIM = 384

# Signal order: S I C D B M R K
SIGNAL_NAMES = ("S", "I", "C", "D", "B", "M", "R", "K")
SIGNAL_FULL = {
    "S": "strings", "I": "full_imports", "C": "sem_calls", "D": "decorators",
    "B": "base_classes", "M": "module_consts", "R": "sem_returns", "K": "sem_keys",
}

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
    base_scaffold: str = ""
    # Pre-computed enrichment lines per signal
    signal_lines: dict[str, str] = field(default_factory=dict)


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
# Tree-sitter queries for NEW signals (B, M)
# ---------------------------------------------------------------------------

def _extract_base_classes(file_path: Path) -> list[str]:
    """Extract base class names from class definitions using tree-sitter."""
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser, Query, QueryCursor

    PY_LANGUAGE = Language(tspython.language())
    parser = Parser(PY_LANGUAGE)
    code = file_path.read_bytes()
    tree = parser.parse(code)

    q = Query(PY_LANGUAGE, """
        (class_definition
            superclasses: (argument_list (identifier) @base_class))
        (class_definition
            superclasses: (argument_list (attribute) @base_class_dotted))
    """)
    cursor = QueryCursor(q)
    matches = cursor.matches(tree.root_node)

    bases: list[str] = []
    seen: set[str] = set()
    for _, caps in matches:
        for _, nodes in caps.items():
            for n in nodes:
                text = code[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                if text not in seen and len(text) >= 2:
                    seen.add(text)
                    bases.append(text)
    return bases


def _extract_module_constants(file_path: Path) -> list[str]:
    """Extract module-level constant names (ALL_CAPS assignments) using tree-sitter."""
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser, Query, QueryCursor

    PY_LANGUAGE = Language(tspython.language())
    parser = Parser(PY_LANGUAGE)
    code = file_path.read_bytes()
    tree = parser.parse(code)

    q = Query(PY_LANGUAGE, """
        (module (expression_statement (assignment left: (identifier) @mod_var)))
    """)
    cursor = QueryCursor(q)
    matches = cursor.matches(tree.root_node)

    consts: list[str] = []
    seen: set[str] = set()
    for _, caps in matches:
        for _, nodes in caps.items():
            for n in nodes:
                name = code[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                # Only include ALL_CAPS or UPPER_SNAKE names (constants)
                if name not in seen and len(name) >= 2 and (
                    name.isupper() or (name[0].isupper() and "_" in name and name.replace("_", "").isupper())
                ):
                    seen.add(name)
                    consts.append(name)
    return consts


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
    from codeplane.index._internal.indexing.structural import _extract_file
    from codeplane.index._internal.indexing.file_embedding import (
        build_file_scaffold, _build_embed_text,
    )

    files: list[FileData] = []
    for rel_path in rel_paths:
        full = EVEE_ROOT / rel_path
        if not full.exists():
            continue
        result = _extract_file(str(full), str(EVEE_ROOT), unit_id=1)
        if result.error:
            continue
        content = full.read_text(encoding="utf-8", errors="replace")

        scaffold = build_file_scaffold(rel_path, result.defs, result.imports)
        base_text = _build_embed_text(scaffold, content, defs=result.defs)

        fd = FileData(
            path=rel_path,
            content=content,
            defs=result.defs,
            imports=result.imports,
            base_scaffold=base_text,
        )

        # --- S: string_literals ---
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
            parts: list[str] = []
            chars_used = 0
            for lit in all_lits:
                if chars_used + len(lit) + 2 > 300:
                    break
                parts.append(lit)
                chars_used += len(lit) + 2
            fd.signal_lines["S"] = "mentions " + ", ".join(parts)

        # --- I: full_imports ---
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

        # --- C: sem_calls ---
        all_calls: set[str] = set()
        for d in result.defs:
            sf = d.get("_sem_facts", {})
            for call_name in sf.get("calls", []):
                if call_name and len(call_name) >= 2:
                    all_calls.add(call_name)
        if all_calls:
            sorted_calls = sorted(all_calls)[:20]
            fd.signal_lines["C"] = "calls " + ", ".join(sorted_calls)

        # --- D: decorator_names ---
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

        # --- B: base_classes (NEW) ---
        bases = _extract_base_classes(full)
        if bases:
            fd.signal_lines["B"] = "extends " + ", ".join(bases[:10])

        # --- M: module_constants (NEW) ---
        consts = _extract_module_constants(full)
        if consts:
            fd.signal_lines["M"] = "constants " + ", ".join(consts[:15])

        # --- R: sem_returns (NEW) ---
        all_returns: set[str] = set()
        for d in result.defs:
            sf = d.get("_sem_facts", {})
            for ret_name in sf.get("returns", []):
                if ret_name and len(ret_name) >= 2:
                    all_returns.add(ret_name)
        if all_returns:
            sorted_rets = sorted(all_returns)[:15]
            fd.signal_lines["R"] = "returns " + ", ".join(sorted_rets)

        # --- K: sem_dict_keys (NEW) ---
        all_keys: set[str] = set()
        for d in result.defs:
            sf = d.get("_sem_facts", {})
            for key_name in sf.get("literals", []):
                if key_name and len(key_name) >= 2:
                    all_keys.add(key_name)
        if all_keys:
            sorted_keys = sorted(all_keys)[:15]
            fd.signal_lines["K"] = "keys " + ", ".join(sorted_keys)

        files.append(fd)
    return files


# ---------------------------------------------------------------------------
# Scaffold assembly
# ---------------------------------------------------------------------------

def _signal_set_from_string(combo_str: str) -> set[str]:
    """Parse 'S+I+C+D' into {'S','I','C','D'}."""
    if combo_str == "baseline":
        return set()
    return set(combo_str.split("+"))


def build_variant_text(fd: FileData, active_signals: set[str]) -> str:
    """Assemble scaffold text for a set of active signals."""
    base = fd.base_scaffold
    if not base:
        return ""

    lines = base.split("\n")

    # I: replace the imports line with full-path version
    if "I" in active_signals and "I" in fd.signal_lines:
        full_import_line = fd.signal_lines["I"]
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith("imports "):
                lines[i] = full_import_line
                replaced = True
                break
        if not replaced:
            insert_at = min(2, len(lines))
            lines.insert(insert_at, full_import_line)

    # Append other signals (in consistent order)
    for sig in ("S", "C", "D", "B", "M", "R", "K"):
        if sig in active_signals and sig in fd.signal_lines:
            lines.append(fd.signal_lines[sig])

    text = "\n".join(lines)
    return text[:SCAFFOLD_MAX_CHARS]


def build_variant_chunks(
    fd: FileData,
    active_signals: set[str],
    tokenizer,
) -> list[str]:
    """Build 1 or 2 chunks (two-chunk split mode)."""
    full_text = build_variant_text(fd, active_signals)
    if not full_text:
        return [full_text]

    tok_count = len(tokenizer.encode(full_text).ids)
    if tok_count <= TOKEN_SPLIT:
        return [full_text]

    chunk0 = fd.base_scaffold[:SCAFFOLD_MAX_CHARS]

    enrich_lines = ["FILE_SCAFFOLD"]
    for line in fd.base_scaffold.split("\n"):
        if line.startswith("module "):
            enrich_lines.append(line)
            break

    if "I" in active_signals and "I" in fd.signal_lines:
        enrich_lines.append(fd.signal_lines["I"])
    for sig in ("S", "C", "D", "B", "M", "R", "K"):
        if sig in active_signals and sig in fd.signal_lines:
            enrich_lines.append(fd.signal_lines[sig])

    chunk1 = "\n".join(enrich_lines)[:SCAFFOLD_MAX_CHARS]

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


def compute_metrics_1chunk(files, queries, doc_vecs, query_vecs):
    file_paths = [f.path for f in files]
    recall_sums = {k: 0.0 for k in EVAL_K_VALUES}
    rr_sum = 0.0

    for qi, iq in enumerate(queries):
        gt_set = set(iq.gt_edit_files)
        gt_indices = {i for i, p in enumerate(file_paths) if p in gt_set}
        if not gt_indices:
            continue
        sims = query_vecs[qi] @ doc_vecs.T
        ranked = np.argsort(-sims)
        for k in EVAL_K_VALUES:
            hits = len(set(ranked[:k].tolist()) & gt_indices)
            recall_sums[k] += hits / len(gt_indices)
        for rank, fi in enumerate(ranked, 1):
            if int(fi) in gt_indices:
                rr_sum += 1.0 / rank
                break

    n = len(queries)
    return {k: recall_sums[k] / n for k in EVAL_K_VALUES}, rr_sum / n


def compute_metrics_2chunk(files, queries, chunk_vecs, chunk_to_file, query_vecs):
    n_files = len(files)
    file_paths = [f.path for f in files]
    recall_sums = {k: 0.0 for k in EVAL_K_VALUES}
    rr_sum = 0.0

    for qi, iq in enumerate(queries):
        gt_set = set(iq.gt_edit_files)
        gt_indices = {i for i, p in enumerate(file_paths) if p in gt_set}
        if not gt_indices:
            continue
        chunk_sims = query_vecs[qi] @ chunk_vecs.T
        file_sims = np.full(n_files, -1.0, dtype=np.float32)
        for ci, fi in enumerate(chunk_to_file):
            if chunk_sims[ci] > file_sims[fi]:
                file_sims[fi] = chunk_sims[ci]
        ranked = np.argsort(-file_sims)
        for k in EVAL_K_VALUES:
            hits = len(set(ranked[:k].tolist()) & gt_indices)
            recall_sums[k] += hits / len(gt_indices)
        for rank, fi in enumerate(ranked, 1):
            if int(fi) in gt_indices:
                rr_sum += 1.0 / rank
                break

    n = len(queries)
    return {k: recall_sums[k] / n for k in EVAL_K_VALUES}, rr_sum / n


# ---------------------------------------------------------------------------
# Variant definitions (targeted combos, not 2^8=256)
# ---------------------------------------------------------------------------

VARIANTS = [
    # Baseline
    "baseline",
    # Singles
    "S", "I", "C", "D", "B", "M", "R", "K",
    # Prior best combos
    "S+I",
    "S+I+C+D",
    # Each new signal added to S+I+C+D
    "S+I+C+D+B",
    "S+I+C+D+M",
    "S+I+C+D+R",
    "S+I+C+D+K",
    # Promising combos of new signals
    "B+M",
    "B+M+R+K",
    "S+I+B+M",
    "S+I+C+D+B+M",
    # Full
    "S+I+C+D+B+M+R+K",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 90)
    print("SIGNAL PERMUTATION BENCHMARK v2 — 8 signals, targeted combos")
    print("S I C D (existing) + B M R K (new) × 2 modes × 24 queries")
    print("=" * 90)

    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(
        "/tmp/fastembed_cache/models--qdrant--bge-small-en-v1.5-onnx-q/"
        "snapshots/52398278842ec682c6f32300af41344b1c0b0bb2/tokenizer.json"
    )

    sys.path.insert(0, "src")
    print(f"\n[1/4] Discovering files in {EVEE_ROOT} ...")
    rel_paths = discover_files()
    print(f"  Found {len(rel_paths)} Python files")

    print("\n[2/4] Extracting with tree-sitter + pre-computing all 8 signals...")
    files = extract_all_files(rel_paths)
    print(f"  {len(files)} files extracted")

    for sig in SIGNAL_NAMES:
        count = sum(1 for fd in files if sig in fd.signal_lines)
        print(f"  {sig} ({SIGNAL_FULL[sig]:<14s}): {count}/{len(files)} files ({100*count/len(files):.0f}%)")

    print(f"\n[3/4] Loading model...")
    model = load_model()

    queries = ISSUE_QUERIES
    query_texts = [iq.query for iq in queries]
    query_vecs = embed_batch(model, query_texts)
    print(f"  Embedded {len(queries)} queries")

    print(f"\n[4/4] Running {len(VARIANTS)} combos × 2 modes = {len(VARIANTS)*2} variants...")

    results: list[dict] = []

    for combo_str in VARIANTS:
        active_sigs = _signal_set_from_string(combo_str)

        # --- 1chunk ---
        texts_1c = [build_variant_text(fd, active_sigs) for fd in files]
        tok_counts = [len(tokenizer.encode(t).ids) for t in texts_1c]
        overflow = sum(1 for tc in tok_counts if tc > 512)

        doc_vecs = embed_batch(model, texts_1c)
        recall_1c, mrr_1c = compute_metrics_1chunk(files, queries, doc_vecs, query_vecs)

        results.append({
            "label": combo_str, "mode": "1chunk",
            "R@5": recall_1c[5], "R@10": recall_1c[10], "R@20": recall_1c[20],
            "MRR": mrr_1c,
            "n_chunks": len(files), "overflow": overflow,
            "median_tok": int(np.median(tok_counts)), "max_tok": max(tok_counts),
        })

        # --- 2chunk ---
        all_chunks: list[str] = []
        chunk_to_file: list[int] = []
        for fi, fd in enumerate(files):
            for chunk_text in build_variant_chunks(fd, active_sigs, tokenizer):
                all_chunks.append(chunk_text)
                chunk_to_file.append(fi)

        chunk_vecs = embed_batch(model, all_chunks)
        recall_2c, mrr_2c = compute_metrics_2chunk(
            files, queries, chunk_vecs, chunk_to_file, query_vecs
        )

        results.append({
            "label": combo_str, "mode": "2chunk",
            "R@5": recall_2c[5], "R@10": recall_2c[10], "R@20": recall_2c[20],
            "MRR": mrr_2c,
            "n_chunks": len(all_chunks), "overflow": 0,
            "median_tok": int(np.median(tok_counts)), "max_tok": max(tok_counts),
        })

        print(f"  {combo_str:<20s}  1c: R@5={recall_1c[5]:.3f} MRR={mrr_1c:.3f}  "
              f"2c: R@5={recall_2c[5]:.3f} MRR={mrr_2c:.3f}  "
              f"({len(all_chunks)} chunks, {overflow} overflow)")

    del model
    gc.collect()

    # -----------------------------------------------------------------------
    # Results tables
    # -----------------------------------------------------------------------
    baseline_1c = next(r for r in results if r["label"] == "baseline" and r["mode"] == "1chunk")
    baseline_2c = next(r for r in results if r["label"] == "baseline" and r["mode"] == "2chunk")
    sicd_1c = next(r for r in results if r["label"] == "S+I+C+D" and r["mode"] == "1chunk")

    print(f"\n{'='*110}")
    print("FULL RESULTS — 1chunk mode (sorted by R@5)")
    print(f"{'='*110}")
    r1c = sorted([r for r in results if r["mode"] == "1chunk"], key=lambda r: (r["R@5"], r["MRR"]), reverse=True)
    print(f"\n  {'Rank':>4} {'Signals':<22} {'R@5':>7} {'R@10':>7} {'R@20':>7} "
          f"{'MRR':>7} {'dR@5':>7} {'dMRR':>7} {'MedTok':>7} {'OvFlow':>7}")
    print(f"  {'-'*96}")
    for rank, r in enumerate(r1c, 1):
        dr5 = r["R@5"] - baseline_1c["R@5"]
        dmrr = r["MRR"] - baseline_1c["MRR"]
        marker = " ***" if r["label"] == "baseline" else ""
        print(f"  {rank:>4} {r['label']:<22} {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
              f"{r['R@20']:>7.3f} {r['MRR']:>7.3f} {dr5:>+7.3f} {dmrr:>+7.3f} "
              f"{r['median_tok']:>7} {r['overflow']:>7}{marker}")

    print(f"\n{'='*110}")
    print("FULL RESULTS — 2chunk mode (sorted by R@5)")
    print(f"{'='*110}")
    r2c = sorted([r for r in results if r["mode"] == "2chunk"], key=lambda r: (r["R@5"], r["MRR"]), reverse=True)
    print(f"\n  {'Rank':>4} {'Signals':<22} {'R@5':>7} {'R@10':>7} {'R@20':>7} "
          f"{'MRR':>7} {'dR@5':>7} {'dMRR':>7} {'Chunks':>7}")
    print(f"  {'-'*88}")
    for rank, r in enumerate(r2c, 1):
        dr5 = r["R@5"] - baseline_2c["R@5"]
        dmrr = r["MRR"] - baseline_2c["MRR"]
        marker = " ***" if r["label"] == "baseline" else ""
        print(f"  {rank:>4} {r['label']:<22} {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
              f"{r['R@20']:>7.3f} {r['MRR']:>7.3f} {dr5:>+7.3f} {dmrr:>+7.3f} "
              f"{r['n_chunks']:>7}{marker}")

    # -----------------------------------------------------------------------
    # 1chunk vs 2chunk comparison
    # -----------------------------------------------------------------------
    print(f"\n{'='*110}")
    print("1CHUNK vs 2CHUNK — same signal combo")
    print(f"{'='*110}")
    print(f"\n  {'Signals':<22} {'1c R@5':>7} {'2c R@5':>7} {'delta':>7}  "
          f"{'1c MRR':>7} {'2c MRR':>7} {'delta':>7}  {'OvFlow':>7} {'Chunks':>7}")
    print(f"  {'-'*96}")
    for combo_str in VARIANTS:
        r1 = next(r for r in results if r["label"] == combo_str and r["mode"] == "1chunk")
        r2 = next(r for r in results if r["label"] == combo_str and r["mode"] == "2chunk")
        dr5 = r2["R@5"] - r1["R@5"]
        dmrr = r2["MRR"] - r1["MRR"]
        print(f"  {combo_str:<22} {r1['R@5']:>7.3f} {r2['R@5']:>7.3f} {dr5:>+7.3f}  "
              f"{r1['MRR']:>7.3f} {r2['MRR']:>7.3f} {dmrr:>+7.3f}  "
              f"{r1['overflow']:>7} {r2['n_chunks']:>7}")

    # -----------------------------------------------------------------------
    # New signals marginal contribution ON TOP OF S+I+C+D
    # -----------------------------------------------------------------------
    print(f"\n{'='*110}")
    print("NEW SIGNAL MARGINAL CONTRIBUTION (added to S+I+C+D)")
    print(f"{'='*110}")
    for mode in ("1chunk", "2chunk"):
        print(f"\n  Mode: {mode}")
        sicd = next(r for r in results if r["label"] == "S+I+C+D" and r["mode"] == mode)
        print(f"  {'Config':<22} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}  "
              f"{'dR@5':>7} {'dR@10':>7} {'dR@20':>7} {'dMRR':>7}")
        print(f"  {'-'*88}")
        print(f"  {'S+I+C+D (base)':<22} {sicd['R@5']:>7.3f} {sicd['R@10']:>7.3f} "
              f"{sicd['R@20']:>7.3f} {sicd['MRR']:>7.3f}  {'---':>7} {'---':>7} {'---':>7} {'---':>7}")
        for sig in ("B", "M", "R", "K"):
            combo = f"S+I+C+D+{sig}"
            r = next((r for r in results if r["label"] == combo and r["mode"] == mode), None)
            if r:
                print(f"  +{sig} ({SIGNAL_FULL[sig]:<12s})     {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
                      f"{r['R@20']:>7.3f} {r['MRR']:>7.3f}  "
                      f"{r['R@5']-sicd['R@5']:>+7.3f} {r['R@10']-sicd['R@10']:>+7.3f} "
                      f"{r['R@20']-sicd['R@20']:>+7.3f} {r['MRR']-sicd['MRR']:>+7.3f}")
        bm = next((r for r in results if r["label"] == "S+I+C+D+B+M" and r["mode"] == mode), None)
        if bm:
            print(f"  +B+M                {bm['R@5']:>7.3f} {bm['R@10']:>7.3f} "
                  f"{bm['R@20']:>7.3f} {bm['MRR']:>7.3f}  "
                  f"{bm['R@5']-sicd['R@5']:>+7.3f} {bm['R@10']-sicd['R@10']:>+7.3f} "
                  f"{bm['R@20']-sicd['R@20']:>+7.3f} {bm['MRR']-sicd['MRR']:>+7.3f}")
        full = next((r for r in results if r["label"] == "S+I+C+D+B+M+R+K" and r["mode"] == mode), None)
        if full:
            print(f"  +B+M+R+K (all 8)    {full['R@5']:>7.3f} {full['R@10']:>7.3f} "
                  f"{full['R@20']:>7.3f} {full['MRR']:>7.3f}  "
                  f"{full['R@5']-sicd['R@5']:>+7.3f} {full['R@10']-sicd['R@10']:>+7.3f} "
                  f"{full['R@20']-sicd['R@20']:>+7.3f} {full['MRR']-sicd['MRR']:>+7.3f}")

    # -----------------------------------------------------------------------
    # Top-5 overall
    # -----------------------------------------------------------------------
    print(f"\n{'='*110}")
    print("TOP-5 CONFIGURATIONS (by R@5, then MRR)")
    print(f"{'='*110}")
    all_sorted = sorted(results, key=lambda r: (r["R@5"], r["MRR"]), reverse=True)
    print(f"\n  {'Rank':>4} {'Config':<30} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7}")
    print(f"  {'-'*68}")
    for rank, r in enumerate(all_sorted[:5], 1):
        config_label = f"{r['label']} ({r['mode']})"
        print(f"  {rank:>4} {config_label:<30} {r['R@5']:>7.3f} {r['R@10']:>7.3f} "
              f"{r['R@20']:>7.3f} {r['MRR']:>7.3f}")

    print(f"\n  Reference (previous benchmark):")
    print(f"       jina-base-code × current   R@5=0.495  R@10=0.637  R@20=0.779  MRR=0.772")


if __name__ == "__main__":
    main()
