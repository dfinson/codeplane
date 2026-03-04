"""Feature extraction from raw retrieval signals.

Converts the candidate pool returned by ``recon_raw_signals()`` into
feature matrices suitable for the LightGBM ranker, cutoff, and gate
models.

See §3 of ranking-design.md for feature definitions.
"""

from __future__ import annotations

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
    raise NotImplementedError


def extract_cutoff_features(
    ranked_candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
) -> dict[str, Any]:
    """Build query-level feature dict for the cutoff model.

    Computes score distribution features (ordered scores, pairwise gaps,
    cumulative mass, entropy, variance) from the ranked list.

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
    raise NotImplementedError


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
    raise NotImplementedError
