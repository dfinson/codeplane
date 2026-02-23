"""Recon MCP tool — task-aware code discovery.

Package layout (SOLID decomposition):

    models.py      — Enums, dataclasses, constants, classifiers  (S: types only)
    parsing.py     — Task parsing, query views                   (S: text analysis)
    harvesters.py  — Five independent candidate harvesters       (O: extensible)
    scoring.py     — Filter pipeline, scoring, cutoff detection  (S: evaluation)
    expansion.py   — Graph expansion, IO helpers, scaffolds      (S: context build)
    assembly.py    — Budget trimming, summary generation         (S: response shape)
    pipeline.py    — Select-seeds orchestrator + register_tools  (D: composition)

All public symbols are re-exported here for backward compatibility:

    from codeplane.mcp.tools.recon import register_tools
    from codeplane.mcp.tools.recon import parse_task, ParsedTask, ...
"""

from __future__ import annotations

# --- assembly ---
from codeplane.mcp.tools.recon.assembly import (
    _build_failure_actions,
    _estimate_bytes,
    _summarize_recon,
    _trim_to_budget,
)

# --- expansion ---
from codeplane.mcp.tools.recon.expansion import (
    _build_import_scaffolds,
    _collect_barrel_paths,
    _compute_sha256,
    _def_signature_text,
    _expand_seed,
    _file_path_for_id,
    _read_lines,
)

# --- harvesters ---
from codeplane.mcp.tools.recon.harvesters import (
    _enrich_candidates,
    _harvest_embedding,
    _harvest_explicit,
    _harvest_lexical,
    _harvest_term_match,
    _merge_candidates,
)

# --- models ---
from codeplane.mcp.tools.recon.models import (
    ArtifactKind,
    EvidenceRecord,
    HarvestCandidate,
    ParsedTask,
    ReconBucket,
    TaskIntent,
    _classify_artifact,
    _extract_intent,
    _is_barrel_file,
    _is_test_file,
)

# --- parsing ---
from codeplane.mcp.tools.recon.parsing import (
    _build_query_views,
    _detect_stacktrace_driven,
    _detect_test_driven,
    _extract_negative_mentions,
    _merge_multi_view_results,
    parse_task,
)

# --- pipeline (orchestrator + tool registration) ---
from codeplane.mcp.tools.recon.pipeline import (
    _build_evidence_string,
    _select_seeds,
    register_tools,
)

# --- scoring ---
from codeplane.mcp.tools.recon.scoring import (
    _aggregate_to_files,
    _aggregate_to_files_dual,
    _apply_dual_gate,
    _apply_filters,
    _assign_buckets,
    _compute_context_value,
    _compute_edit_likelihood,
    _score_candidates,
    find_elbow,
    compute_anchor_floor,
)

__all__ = [
    # Types / enums
    "ArtifactKind",
    "EvidenceRecord",
    "HarvestCandidate",
    "ParsedTask",
    "ReconBucket",
    "TaskIntent",
    # Parsing
    "parse_task",
    "_extract_negative_mentions",
    "_detect_stacktrace_driven",
    "_detect_test_driven",
    "_build_query_views",
    "_merge_multi_view_results",
    "_extract_intent",
    "_classify_artifact",
    "_is_test_file",
    "_is_barrel_file",
    # Harvesters
    "_harvest_embedding",
    "_harvest_term_match",
    "_harvest_lexical",
    "_harvest_explicit",
    "_merge_candidates",
    "_enrich_candidates",
    # Scoring
    "_aggregate_to_files",
    "_aggregate_to_files_dual",
    "_apply_dual_gate",
    "_apply_filters",
    "_assign_buckets",
    "_compute_context_value",
    "_compute_edit_likelihood",
    "_score_candidates",
    "find_elbow",
    "compute_anchor_floor",
    # Expansion
    "_compute_sha256",
    "_read_lines",
    "_def_signature_text",
    "_file_path_for_id",
    "_expand_seed",
    "_collect_barrel_paths",
    "_build_import_scaffolds",
    # Assembly
    "_build_failure_actions",
    "_estimate_bytes",
    "_trim_to_budget",
    "_summarize_recon",
    # Pipeline
    "_build_evidence_string",
    "_select_seeds",
    "register_tools",
]
