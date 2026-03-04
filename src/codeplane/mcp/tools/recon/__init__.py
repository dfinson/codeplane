"""Recon MCP tool — task-aware code discovery.

Package layout (SOLID decomposition):

    models.py      — Enums, dataclasses, constants, classifiers  (S: types only)
    parsing.py     — Task parsing, query views                   (S: text analysis)
    harvesters.py  — Independent candidate sources (A-F)         (O: extensible)
    merge.py       — Candidate merge, enrichment, seed selection (S: combine)
    rrf.py         — Reciprocal Rank Fusion scoring              (S: scoring)
    scoring.py     — Elbow detection, tier assignment, noise     (S: evaluation)
    expansion.py   — IO helpers, signature text, sha256          (S: context build)
    assembly.py    — Failure actions, hints, response helpers    (S: response shape)
    pipeline.py    — File-centric orchestrator + register_tools  (D: composition)

All public symbols are re-exported here:

    from codeplane.mcp.tools.recon import register_tools
    from codeplane.mcp.tools.recon import parse_task, ParsedTask, ...
"""

from __future__ import annotations

# --- assembly ---
from codeplane.mcp.tools.recon.assembly import (
    _build_failure_actions,
    build_agentic_hint,
    build_gate_hint,
)

# --- expansion ---
from codeplane.mcp.tools.recon.expansion import (
    _compute_sha256,
    _def_signature_text,
    _file_path_for_id,
    _read_lines,
)

# --- harvesters ---
from codeplane.mcp.tools.recon.harvesters import (
    _harvest_file_embedding,
)

# --- merge ---
from codeplane.mcp.tools.recon.merge import (
    _enrich_candidates,
    _merge_candidates,
    _select_graph_seeds,
)

# --- models ---
from codeplane.mcp.tools.recon.models import (
    ArtifactKind,
    EvidenceRecord,
    FileCandidate,
    HarvestCandidate,
    OutputTier,
    ParsedTask,
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
    _file_centric_pipeline,
    register_tools,
)

# --- rrf ---
from codeplane.mcp.tools.recon.rrf import (
    _enrich_file_candidates,
)

# --- scoring ---
from codeplane.mcp.tools.recon.scoring import (
    assign_tiers,
    compute_anchor_floor,
    compute_noise_metric,
    compute_two_elbows,
    find_elbow,
)

__all__ = [
    # Types / enums
    "ArtifactKind",
    "EvidenceRecord",
    "FileCandidate",
    "HarvestCandidate",
    "OutputTier",
    "ParsedTask",
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
    "_harvest_file_embedding",
    # Merge
    "_enrich_candidates",
    "_merge_candidates",
    "_select_graph_seeds",
    # RRF
    "_enrich_file_candidates",
    # Scoring
    "assign_tiers",
    "compute_anchor_floor",
    "compute_noise_metric",
    "compute_two_elbows",
    "find_elbow",
    # Expansion
    "_compute_sha256",
    "_read_lines",
    "_def_signature_text",
    "_file_path_for_id",
    # Assembly
    "_build_failure_actions",
    "build_agentic_hint",
    "build_gate_hint",
    # Pipeline
    "_file_centric_pipeline",
    "register_tools",
]
