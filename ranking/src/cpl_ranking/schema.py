"""Dataset table schemas for ranking model training.

Two groups of tables, collected separately:

**Ground truth** (stable, collected once per task):
- ``Run``             — one row per task run
- ``TouchedObject``   — ground-truth touched DefFacts per run
- ``Query``           — authored queries (OK and non-OK) per run

**Retrieval signals** (re-collected when harvesters change):
- ``CandidateRank``   — per-candidate retrieval features per query

**Derived at training time** (computed from the above):
- ``QueryCutoff``     — per-query cutoff features (OK queries only)
- ``QueryGate``       — per-query gate features (all query types)

See §7 of ranking-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Ground truth (stable, collected once) ────────────────────────


@dataclass(frozen=True)
class Run:
    """§7.1 — one task execution against a repo."""

    run_id: str
    repo_id: str
    repo_sha: str
    task_id: str
    task_text: str
    agent_version: str
    status: str


@dataclass(frozen=True)
class TouchedObject:
    """§7.2 — a DefFact touched during a task run."""

    run_id: str
    def_uid: str
    path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    touch_type: str  # edited | read_necessary


@dataclass(frozen=True)
class Query:
    """§7.3 — an authored query for a task run."""

    run_id: str
    query_id: str
    query_text: str
    query_type: str  # Q_SEMANTIC | Q_LEXICAL | Q_IDENTIFIER | Q_STRUCTURAL | Q_NAVIGATIONAL | UNSAT | BROAD | AMBIG
    label_gate: str  # OK | UNSAT | BROAD | AMBIG


# ── Retrieval signals (re-collected when harvesters change) ──────


@dataclass(frozen=True)
class CandidateRank:
    """§7.4 — per-candidate retrieval features for ranker training."""

    run_id: str
    query_id: str
    def_uid: str
    emb_score: float | None
    emb_rank: int | None
    lex_score: float | None
    lex_rank: int | None
    term_score: float | None
    term_rank: int | None
    graph_score: float | None
    graph_rank: int | None
    symbol_score: float | None
    symbol_rank: int | None
    retriever_hits: int
    object_kind: str
    object_size_lines: int
    file_ext: str
    query_len: int
    has_identifier: bool
    has_path: bool
    label_rank: int  # graded: edited > read_necessary > untouched


# ── Derived at training time ─────────────────────────────────────


@dataclass(frozen=True)
class QueryCutoff:
    """§7.5 — per-query cutoff features (OK queries only).

    Score distribution features are computed from out-of-fold ranker
    outputs.  Field list is intentionally sparse here — the full set
    of distribution features is computed dynamically in training.
    """

    run_id: str
    query_id: str
    query_len: int
    has_identifier: bool
    has_path: bool
    object_count: int
    n_star: int  # empirically optimal cutoff


@dataclass(frozen=True)
class QueryGate:
    """§7.6 — per-query gate features (all query types)."""

    query_id: str
    query_len: int
    identifier_density: float
    path_presence: bool
    has_numbers: bool
    has_quoted_strings: bool
    object_count: int
    file_count: int
    top_score: float
    total_candidates: int
    label_gate: str  # OK | UNSAT | BROAD | AMBIG
