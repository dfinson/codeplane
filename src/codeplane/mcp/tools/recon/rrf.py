"""Reciprocal Rank Fusion scoring — combine file-level and def-level signals.

Single Responsibility: RRF scoring algorithm for file candidates.
No I/O, no database access, no async.  Pure scoring on ranked lists.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from codeplane.mcp.tools.recon.models import (
    _PATH_STOP_TOKENS,
    FileCandidate,
    HarvestCandidate,
    _classify_artifact,
)

if TYPE_CHECKING:
    from codeplane.mcp.tools.recon.models import ParsedTask

# RRF constants
_RRF_K = 60
_EMBEDDING_WEIGHT = 2.0


def _enrich_file_candidates(
    file_candidates: list[FileCandidate],
    def_candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> list[FileCandidate]:
    """Combine file-level embedding with def-level signals via Reciprocal Rank Fusion.

    RRF(d) = sum over sources of weight_s / (k + rank_s(d))

    Sources (with weights):
      1. Embedding  — file_candidates ranked by similarity (weight 2.0×)
      2. Term match — files ranked by IDF-weighted term score
      3. Lexical    — files ranked by aggregated lexical hit count
      4. Graph      — files ranked by graded edge quality
      5. Explicit   — agent-mentioned paths (always rank 1)
      6. Path token — files ranked by query-term overlap with path components

    Mutates candidates in-place (sets combined_score, signal fields).
    Also appends files found only by secondary sources.
    """
    # ── Aggregate def-level signals by file path ──
    path_signals: dict[str, dict[str, Any]] = {}
    for _uid, cand in def_candidates.items():
        fp = cand.file_path
        if not fp:
            continue
        if fp not in path_signals:
            path_signals[fp] = {
                "term_count": 0,
                "term_idf": 0.0,
                "lex_count": 0,
                "explicit": False,
                "graph": False,
                "graph_quality": 0.0,
            }
        sig = path_signals[fp]
        sig["term_count"] += len(cand.matched_terms)
        sig["term_idf"] += cand.term_idf_score
        sig["lex_count"] += cand.lexical_hit_count
        sig["explicit"] = sig["explicit"] or cand.from_explicit
        sig["graph"] = sig["graph"] or cand.from_graph
        sig["graph_quality"] = max(sig["graph_quality"], cand.graph_quality)

    explicit_paths = set(parsed.explicit_paths or [])

    # ── Populate signal fields on existing file candidates ──
    for fc in file_candidates:
        sig = path_signals.get(fc.path, {})
        fc.term_match_count = sig.get("term_count", 0)
        fc.lexical_hit_count = sig.get("lex_count", 0)
        fc.has_explicit_mention = sig.get("explicit", False) or fc.path in explicit_paths
        fc.graph_connected = sig.get("graph", False)
        fc.graph_quality = sig.get("graph_quality", 0.0)

    # ── Append files found ONLY by def-level harvesters ──
    existing_paths = {fc.path for fc in file_candidates}
    for fp, sig in path_signals.items():
        if fp in existing_paths:
            continue
        term_count = sig.get("term_count", 0)
        lex_count = sig.get("lex_count", 0)
        is_explicit = sig.get("explicit", False) or fp in explicit_paths
        is_graph = sig.get("graph", False)
        gq = sig.get("graph_quality", 0.0)

        if term_count > 0 or lex_count > 0 or is_explicit or is_graph:
            fc = FileCandidate(
                path=fp,
                similarity=0.0,
                combined_score=0.0,
                term_match_count=term_count,
                lexical_hit_count=lex_count,
                has_explicit_mention=is_explicit,
                graph_connected=is_graph,
                graph_quality=gq,
                artifact_kind=_classify_artifact(fp),
            )
            file_candidates.append(fc)

    # ── Build per-source ranked lists (1-based) ──

    # Source 1: Embedding similarity
    embedding_ranked = sorted(
        [(fc.path, fc.similarity) for fc in file_candidates if fc.similarity > 0],
        key=lambda x: -x[1],
    )
    embedding_rank: dict[str, int] = {
        path: rank for rank, (path, _) in enumerate(embedding_ranked, 1)
    }

    # Source 2: Term match — ranked by IDF-weighted score
    term_idf_by_path: dict[str, float] = {}
    for fp, sig in path_signals.items():
        idf_val = sig.get("term_idf", 0.0)
        if idf_val > 0:
            term_idf_by_path[fp] = idf_val
    term_ranked = sorted(term_idf_by_path.items(), key=lambda x: -x[1])
    term_rank: dict[str, int] = {path: rank for rank, (path, _) in enumerate(term_ranked, 1)}

    # Source 3: Lexical hit count
    lex_ranked = sorted(
        [(fc.path, fc.lexical_hit_count) for fc in file_candidates if fc.lexical_hit_count > 0],
        key=lambda x: -x[1],
    )
    lex_rank: dict[str, int] = {path: rank for rank, (path, _) in enumerate(lex_ranked, 1)}

    # Source 4: Graph — ranked by graded edge quality
    graph_ranked = sorted(
        [(fc.path, fc.graph_quality) for fc in file_candidates if fc.graph_quality > 0],
        key=lambda x: -x[1],
    )
    graph_rank: dict[str, int] = {path: rank for rank, (path, _) in enumerate(graph_ranked, 1)}

    # Source 5: Explicit mention (binary — rank 1 for all)
    explicit_all = {fc.path for fc in file_candidates if fc.has_explicit_mention}

    # Source 6: Path-token matching
    path_query_terms: set[str] = set()
    for t in parsed.primary_terms:
        tl = t.lower()
        if len(tl) >= 4 and tl not in _PATH_STOP_TOKENS:
            path_query_terms.add(tl)

    path_match_scores: dict[str, int] = {}
    if path_query_terms:
        for fc in file_candidates:
            path_lower = fc.path.lower()
            path_tokens = set(re.split(r"[/._\-]", path_lower))
            path_tokens = {t for t in path_tokens if len(t) >= 2}
            hits = path_query_terms & path_tokens
            if not hits:
                hits = {t for t in path_query_terms if t in path_lower}
            if hits:
                path_match_scores[fc.path] = len(hits)

    path_match_ranked = sorted(path_match_scores.items(), key=lambda x: -x[1])
    path_match_rank: dict[str, int] = {
        path: rank for rank, (path, _) in enumerate(path_match_ranked, 1)
    }

    # ── Compute RRF score for each candidate ──
    for fc in file_candidates:
        rrf = 0.0
        if fc.path in embedding_rank:
            rrf += _EMBEDDING_WEIGHT / (_RRF_K + embedding_rank[fc.path])
        if fc.path in term_rank:
            rrf += 1.0 / (_RRF_K + term_rank[fc.path])
        if fc.path in lex_rank:
            rrf += 1.0 / (_RRF_K + lex_rank[fc.path])
        if fc.path in graph_rank:
            rrf += 1.0 / (_RRF_K + graph_rank[fc.path])
        if fc.path in explicit_all:
            rrf += 1.0 / (_RRF_K + 1)
        if fc.path in path_match_rank:
            rrf += 1.0 / (_RRF_K + path_match_rank[fc.path])
        fc.combined_score = rrf

    return file_candidates
