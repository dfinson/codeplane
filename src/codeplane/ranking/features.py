"""Feature extraction from raw retrieval signals.

Converts the candidate pool returned by ``recon_raw_signals()`` into
feature matrices suitable for the LightGBM ranker, cutoff, and gate
models.

See §2.1 of recon-lab/README.md for feature definitions.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any


def extract_ranker_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build per-candidate feature dicts for the ranker model.

    Parameters
    ----------
    candidates
        Raw candidate dicts from ``recon_raw_signals()``.
    query_features
        Query-level features (query_len, has_identifier, etc.).

    Returns
    -------
    list[dict]
        One feature dict per candidate, ready for LightGBM prediction.
    """
    features_out: list[dict[str, Any]] = []

    for cand in candidates:
        f: dict[str, Any] = {}

        # Embedding signal
        f["emb_score"] = cand.get("emb_score") or 0.0
        f["emb_rank"] = cand.get("emb_rank") or 0

        # Term match signal
        f["term_match_count"] = cand.get("term_match_count") or 0
        f["term_total_matches"] = cand.get("term_total_matches") or 0

        # Lexical signal
        f["lex_hit_count"] = cand.get("lex_hit_count") or 0

        # Graph signal (categorical → encoded)
        edge_type = cand.get("graph_edge_type")
        f["graph_is_callee"] = edge_type == "callee"
        f["graph_is_caller"] = edge_type == "caller"
        f["graph_is_sibling"] = edge_type == "sibling"
        f["graph_seed_rank"] = cand.get("graph_seed_rank") or 0

        # Symbol signal (categorical → encoded)
        sym_src = cand.get("symbol_source")
        f["sym_agent_seed"] = sym_src == "agent_seed"
        f["sym_auto_seed"] = sym_src == "auto_seed"
        f["sym_task_extracted"] = sym_src == "task_extracted"
        f["sym_path_mention"] = sym_src == "path_mention"

        # Import signal (categorical → encoded)
        imp_dir = cand.get("import_direction")
        f["import_forward"] = imp_dir == "forward"
        f["import_reverse"] = imp_dir == "reverse"
        f["import_barrel"] = imp_dir == "barrel"
        f["import_test_pair"] = imp_dir == "test_pair"

        # Retriever agreement
        f["retriever_hits"] = cand.get("retriever_hits", 0)

        # Object metadata
        f["object_size_lines"] = cand.get("object_size_lines", 0)
        f["path_depth"] = cand.get("path_depth", 0)
        f["nesting_depth"] = cand.get("nesting_depth", 0)
        f["hub_score"] = cand.get("hub_score", 0)
        f["is_test"] = cand.get("is_test", False)
        f["has_docstring"] = cand.get("has_docstring", False)
        f["has_decorators"] = cand.get("has_decorators", False)
        f["has_return_type"] = cand.get("has_return_type", False)
        f["has_parent_scope"] = cand.get("has_parent_scope", False)
        f["has_signature"] = cand.get("signature_text") is not None

        # Query features (same for all candidates in a group)
        f["query_len"] = query_features.get("query_len", 0)
        f["has_identifier"] = query_features.get("has_identifier", False)
        f["has_path"] = query_features.get("has_path", False)
        f["identifier_density"] = query_features.get("identifier_density", 0.0)
        f["term_count"] = query_features.get("term_count", 0)

        features_out.append(f)

    return features_out


def extract_cutoff_features(
    ranked_candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> dict[str, Any]:
    """Build query-level feature dict for the cutoff model.

    Computes score distribution features from the ranked list.

    Parameters
    ----------
    ranked_candidates
        Candidates sorted by ranker score descending.
    query_features
        Query-level features.
    repo_features
        Repo-level features (object_count, file_count).

    Returns
    -------
    dict
        Feature dict for cutoff prediction.
    """
    scores = [c.get("ranker_score", 0.0) for c in ranked_candidates]
    n = len(scores)

    f: dict[str, Any] = {}

    # Query features
    f["query_len"] = query_features.get("query_len", 0)
    f["has_identifier"] = query_features.get("has_identifier", False)
    f["has_path"] = query_features.get("has_path", False)

    # Repo features
    f["object_count"] = repo_features.get("object_count", 0)
    f["file_count"] = repo_features.get("file_count", 0)

    # Score distribution features
    f["total_candidates"] = n
    if n == 0:
        f["top_score"] = 0.0
        f["score_p10"] = 0.0
        f["score_p25"] = 0.0
        f["score_p50"] = 0.0
        f["score_p75"] = 0.0
        f["score_p90"] = 0.0
        f["max_gap"] = 0.0
        f["max_gap_position"] = 0.0
        f["score_variance"] = 0.0
        f["score_entropy"] = 0.0
        f["cumulative_mass_top10"] = 0.0
    else:
        f["top_score"] = scores[0]
        f["score_p10"] = scores[min(int(n * 0.1), n - 1)]
        f["score_p25"] = scores[min(int(n * 0.25), n - 1)]
        f["score_p50"] = scores[min(int(n * 0.5), n - 1)]
        f["score_p75"] = scores[min(int(n * 0.75), n - 1)]
        f["score_p90"] = scores[min(int(n * 0.9), n - 1)]

        # Max gap and its position
        gaps = [scores[i] - scores[i + 1] for i in range(n - 1)] if n > 1 else [0.0]
        f["max_gap"] = max(gaps)
        f["max_gap_position"] = (gaps.index(max(gaps)) + 1) / n if gaps else 0.0

        # Variance
        mean_s = sum(scores) / n
        f["score_variance"] = sum((s - mean_s) ** 2 for s in scores) / n

        # Entropy of score distribution
        total = sum(scores) if sum(scores) > 0 else 1.0
        probs = [s / total for s in scores if s > 0]
        f["score_entropy"] = -sum(p * math.log(p) for p in probs) if probs else 0.0

        # Cumulative mass in top 10
        top10_sum = sum(scores[:10])
        f["cumulative_mass_top10"] = top10_sum / total if total > 0 else 0.0

    # Multi-retriever agreement distribution
    hits = [c.get("retriever_hits", 0) for c in ranked_candidates]
    f["agreement_mean"] = sum(hits) / max(len(hits), 1)
    f["agreement_max"] = max(hits) if hits else 0

    return f


def extract_gate_features(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> dict[str, Any]:
    """Build query-level feature dict for the gate classifier.

    Computes retrieval distribution features: top score, score decay
    profile, path entropy, cluster count, multi-retriever agreement.

    Parameters
    ----------
    candidates
        Raw candidate dicts from ``recon_raw_signals()``.
    query_features
        Query text features.
    repo_features
        Repo-level features.

    Returns
    -------
    dict
        Feature dict for gate classification.
    """
    f: dict[str, Any] = {}

    # Query features
    f["query_len"] = query_features.get("query_len", 0)
    f["identifier_density"] = query_features.get("identifier_density", 0.0)
    f["has_path"] = query_features.get("has_path", False)
    f["has_numbers"] = query_features.get("has_numbers", False)
    f["has_quoted_strings"] = query_features.get("has_quoted_strings", False)
    f["has_agent_seeds"] = any(
        c.get("symbol_source") == "agent_seed" for c in candidates
    )
    f["agent_seed_count"] = sum(
        1 for c in candidates if c.get("symbol_source") == "agent_seed"
    )

    # Repo features
    f["object_count"] = repo_features.get("object_count", 0)
    f["file_count"] = repo_features.get("file_count", 0)

    # Candidate pool features
    f["total_candidates"] = len(candidates)

    emb_scores = sorted(
        [c.get("emb_score", 0.0) or 0.0 for c in candidates], reverse=True
    )
    n = len(emb_scores)

    if n == 0:
        f["top_score"] = 0.0
        f["score_p25"] = 0.0
        f["score_p50"] = 0.0
        f["score_p75"] = 0.0
        f["path_entropy"] = 0.0
        f["cluster_count"] = 0
    else:
        f["top_score"] = emb_scores[0]
        f["score_p25"] = emb_scores[min(int(n * 0.25), n - 1)]
        f["score_p50"] = emb_scores[min(int(n * 0.5), n - 1)]
        f["score_p75"] = emb_scores[min(int(n * 0.75), n - 1)]

        # Path entropy: Shannon entropy of directory distribution
        dirs = [c.get("parent_dir", "") for c in candidates if c.get("parent_dir")]
        dir_counts = Counter(dirs)
        total_dirs = sum(dir_counts.values())
        if total_dirs > 0:
            probs = [count / total_dirs for count in dir_counts.values()]
            f["path_entropy"] = -sum(p * math.log(p) for p in probs if p > 0)
        else:
            f["path_entropy"] = 0.0

        # Cluster count: number of distinct parent directories at depth 2
        depth2_dirs = set()
        for c in candidates:
            path = c.get("path", "")
            parts = path.split("/")
            if len(parts) >= 2:
                depth2_dirs.add("/".join(parts[:2]))
        f["cluster_count"] = len(depth2_dirs)

    # Multi-retriever agreement
    hits = [c.get("retriever_hits", 0) for c in candidates]
    f["agreement_mean"] = sum(hits) / max(len(hits), 1)
    f["agreement_std"] = (
        (sum((h - f["agreement_mean"]) ** 2 for h in hits) / max(len(hits), 1)) ** 0.5
        if hits
        else 0.0
    )

    return f
