"""Scoring — filter pipeline, bounded scoring model, and elbow detection.

Single Responsibility: Candidate evaluation and selection.
No I/O, no database access, no async.  Pure functions on candidates.
"""

from __future__ import annotations

import math

import structlog

from codeplane.mcp.tools.recon.models import (
    ArtifactKind,
    HarvestCandidate,
    ParsedTask,
    TaskIntent,
)

log = structlog.get_logger(__name__)


# ===================================================================
# Filter Pipeline — query-conditioned, with OR gate + negative gating
# ===================================================================


def _apply_dual_gate(
    candidates: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Legacy dual-gate wrapper — delegates to ``_apply_filters``."""
    return _apply_filters(
        candidates,
        ParsedTask(raw="", intent=TaskIntent.unknown),
    )


def _apply_filters(
    candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Apply query-conditioned filter pipeline with OR gate and negative gating.

    Filter stages:
    1. Negative gating — exclude candidates matching negative_mentions.
    2. Explicit bypass — always pass (trusted input).
    3. OR gate — any *strong* single axis can pass alone.
    4. Minimum evidence — require at least one signal.
    5. Intent-aware artifact filtering.
    6. Barrel exclusion — barrel files are low-signal re-exports.

    Compared to the old dual-gate:
    - Accepts ``ParsedTask`` for full query context (not just intent)
    - OR gate: strong single-axis evidence passes without corroboration
    - Negative gating: user-excluded terms are respected
    - Test files are NOT globally penalized here (Section 5: handled in pipeline)
    """
    intent = parsed.intent
    filtered: dict[str, HarvestCandidate] = {}
    stats = {"bypassed": 0, "passed": 0, "filtered": 0, "negated": 0}

    for uid, cand in candidates.items():
        # Stage 1: Negative gating
        if cand.matches_negative(parsed.negative_mentions):
            stats["negated"] += 1
            continue

        # Stage 2: Explicit bypass
        if cand.from_explicit:
            filtered[uid] = cand
            stats["bypassed"] += 1
            continue

        # Stage 3: OR gate — strong single-axis evidence passes alone
        if cand.has_strong_single_axis:
            # Still exclude barrels even with strong signal
            if cand.is_barrel and not cand.from_explicit:
                stats["filtered"] += 1
                continue
            filtered[uid] = cand
            stats["passed"] += 1
            continue

        # Stage 4: Minimum evidence gate
        if not cand.has_semantic_evidence:
            stats["filtered"] += 1
            continue

        # Stage 5: Intent-aware artifact filtering
        if intent in (TaskIntent.debug, TaskIntent.implement, TaskIntent.refactor):
            # For action intents: require structural evidence for code without
            # strong embedding, but do NOT penalize tests here (Section 5)
            if cand.artifact_kind == ArtifactKind.code:
                if not cand.has_structural_evidence and cand.embedding_similarity < 0.4:
                    stats["filtered"] += 1
                    continue
            elif (
                cand.artifact_kind in (ArtifactKind.config, ArtifactKind.doc)
                and cand.embedding_similarity < 0.45
            ):
                stats["filtered"] += 1
                continue
        elif intent == TaskIntent.test:
            # For test intent: keep tests easily, require more from code
            if (
                cand.artifact_kind == ArtifactKind.code
                and not cand.has_structural_evidence
                and cand.embedding_similarity < 0.35
            ):
                stats["filtered"] += 1
                continue
        elif intent == TaskIntent.understand:
            # Understanding intent: keep anything with reasonable evidence
            if cand.evidence_axes < 1 and cand.embedding_similarity < 0.3:
                stats["filtered"] += 1
                continue
        else:
            # Unknown intent: still use OR gate (already handled above),
            # fall back to requiring semantic evidence (already checked above)
            if not cand.has_structural_evidence:
                stats["filtered"] += 1
                continue

        # Stage 6: Barrel exclusion
        if cand.is_barrel and not cand.from_explicit:
            stats["filtered"] += 1
            continue

        filtered[uid] = cand
        stats["passed"] += 1

    log.debug("recon.filter_pipeline", intent=intent.value, **stats)
    return filtered


# ===================================================================
# Elbow Detection — dynamic seed count from score distribution
# ===================================================================


def find_elbow(scores: list[float], *, min_seeds: int = 3, max_seeds: int = 15) -> int:
    """Find the natural cutoff in a sorted-descending score list.

    Uses the "maximum distance to chord" method.
    """
    n = len(scores)
    if n <= min_seeds:
        return n

    analysis = scores[:max_seeds]
    n_analysis = len(analysis)
    if n_analysis <= min_seeds:
        return n_analysis

    x1, y1 = 0, analysis[0]
    x2, y2 = n_analysis - 1, analysis[-1]

    if y1 - y2 < 0.5:
        return min(n_analysis, max_seeds)

    max_dist = 0.0
    elbow_idx = min_seeds

    dx = x2 - x1
    dy = y2 - y1
    chord_len = (dx * dx + dy * dy) ** 0.5

    if chord_len < 1e-10:
        return min_seeds

    for i in range(min_seeds, n_analysis):
        dist = abs(dy * i - dx * analysis[i] + x2 * y1 - y2 * x1) / chord_len
        if dist > max_dist:
            max_dist = dist
            elbow_idx = i

    result = elbow_idx + 1
    return max(min_seeds, min(result, max_seeds))


def compute_anchor_floor(
    scores: list[float],
    anchor_indices: list[int],
) -> float:
    """Compute floor score for anchor-calibrated file inclusion.

    Uses the **Median Absolute Deviation** (MAD) of anchor scores only
    to estimate natural score variation among known-relevant files.

    The inclusion floor is::

        min(anchor_scores) - MAD(anchor_scores)

    Non-anchor files are excluded from the MAD computation to prevent
    high-scoring false positives (e.g., cross-cutting utility modules)
    from inflating the variation estimate and widening the band.

    Args:
        scores: File scores, sorted descending.
        anchor_indices: 0-based indices of anchor files in the sorted
            score list.

    Returns:
        Floor score.  Files with ``score >= floor`` should be included.
        Returns ``0.0`` if *anchor_indices* is empty (no anchor signal).
    """
    if not anchor_indices or not scores:
        return 0.0

    n = len(scores)
    anchor_scores = sorted(scores[i] for i in anchor_indices if i < n)
    if not anchor_scores:
        return 0.0

    s_anchor_min = anchor_scores[0]

    # MAD = Median Absolute Deviation of anchor scores only
    anchor_median = anchor_scores[len(anchor_scores) // 2]
    abs_devs = sorted(abs(s - anchor_median) for s in anchor_scores)
    mad = abs_devs[len(abs_devs) // 2] if abs_devs else 0.0

    floor_score = s_anchor_min - mad

    log.debug(
        "recon.anchor_floor",
        s_anchor_min=round(s_anchor_min, 4),
        mad=round(mad, 4),
        floor=round(floor_score, 4),
        n_anchors=len(anchor_scores),
    )

    return floor_score


# ===================================================================
# Scoring — bounded features with separated relevance/seed scores
# ===================================================================


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


# Artifact-kind weights: how much to boost/penalize each kind
_ARTIFACT_WEIGHTS: dict[ArtifactKind, dict[TaskIntent, float]] = {
    ArtifactKind.code: {
        TaskIntent.debug: 1.0,
        TaskIntent.implement: 1.0,
        TaskIntent.refactor: 1.0,
        TaskIntent.understand: 1.0,
        TaskIntent.test: 0.7,
        TaskIntent.unknown: 1.0,
    },
    ArtifactKind.test: {
        TaskIntent.debug: 0.6,
        TaskIntent.implement: 0.3,
        TaskIntent.refactor: 0.3,
        TaskIntent.understand: 0.5,
        TaskIntent.test: 1.0,
        TaskIntent.unknown: 0.3,
    },
    ArtifactKind.config: {
        TaskIntent.debug: 0.3,
        TaskIntent.implement: 0.4,
        TaskIntent.refactor: 0.2,
        TaskIntent.understand: 0.6,
        TaskIntent.test: 0.1,
        TaskIntent.unknown: 0.3,
    },
    ArtifactKind.doc: {
        TaskIntent.debug: 0.2,
        TaskIntent.implement: 0.3,
        TaskIntent.refactor: 0.1,
        TaskIntent.understand: 0.8,
        TaskIntent.test: 0.1,
        TaskIntent.unknown: 0.2,
    },
    ArtifactKind.build: {
        TaskIntent.debug: 0.1,
        TaskIntent.implement: 0.2,
        TaskIntent.refactor: 0.1,
        TaskIntent.understand: 0.3,
        TaskIntent.test: 0.1,
        TaskIntent.unknown: 0.1,
    },
}


def _aggregate_to_files(
    scored: list[tuple[str, float]],
    candidates: dict[str, HarvestCandidate],
) -> list[tuple[int, float, list[tuple[str, float]]]]:
    """Aggregate def-level scores to file-level using rank statistics.

    Each file contributes a multiset of its defs' scores.  The file score
    is the sum of the top *m* def scores, where *m* is derived from the
    global score distribution — specifically, the count of this file's
    defs that exceed the global median, clamped to [1, 3].

    This is distribution-relative: no invented additive constants.
    Files with multiple defs above the global median accumulate more
    signal, rewarding broad consistent relevance over a single spike.

    Returns:
        List of ``(file_id, file_score, [(def_uid, score), ...])``
        sorted descending by file_score (stable on file_id for ties).
    """
    if not scored:
        return []

    # Global median score (distribution anchor)
    all_scores = sorted((s for _, s in scored), reverse=True)
    global_median = all_scores[len(all_scores) // 2]

    # Group by file_id
    file_defs: dict[int, list[tuple[str, float]]] = {}
    for uid, score in scored:
        cand = candidates.get(uid)
        if cand is None or cand.def_fact is None:
            continue
        fid = cand.def_fact.file_id
        if fid not in file_defs:
            file_defs[fid] = []
        file_defs[fid].append((uid, score))

    # Score each file
    result: list[tuple[int, float, list[tuple[str, float]]]] = []
    for fid, defs in file_defs.items():
        defs.sort(key=lambda x: -x[1])
        # m = count of this file's defs above the global median, clamped to [1, 3]
        n_above_median = sum(1 for _, s in defs if s >= global_median)
        m = max(1, min(n_above_median, 3))
        file_score = sum(s for _, s in defs[:m])
        result.append((fid, file_score, defs))

    result.sort(key=lambda x: (-x[1], x[0]))

    log.debug(
        "recon.file_aggregation",
        n_files=len(result),
        global_median=round(global_median, 4),
        top5=[(fid, round(fs, 4), len(ds)) for fid, fs, ds in result[:5]],
    )
    return result


def _score_candidates(
    candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> list[tuple[str, float]]:
    """Score candidates with bounded features and separated scores.

    Features (all normalized to [0, 1]):
      f_emb:      Embedding similarity (already [0, 1]).
      f_hub:      Hub score, log-scaled and capped.
      f_terms:    Term match count, bounded.
      f_axes:     Evidence axis diversity, bounded.
      f_name:     Name contains primary term (binary).
      f_path:     Path contains primary term (binary).
      f_lexical:  Lexical hit presence (binary, avoids double-counting with term).
      f_graph:    Graph-discovered (binary, structural adjacency).
      f_artifact: Intent-aware artifact weight [0, 1].

    Explicit-path and pinned-file signals are deliberately EXCLUDED from
    the relevance score.  They inflate scores and create an artificial
    cluster boundary that the gap cutoff misinterprets as a real semantic
    gap.  Those files are already guaranteed to survive via anchoring
    (step 8b) — including them here would be double-dipping.

    Relevance score = weighted sum of semantic features.
    Seed score = relevance * seed_multiplier (hub-based).

    Args:
        candidates: Candidate defs to score.
        parsed: Parsed task description.
        pinned_paths: Explicit file paths the agent pinned as
            high-confidence.  ``None`` when the agent didn't pin anything.
            Used only for anchor survival (step 8b), not for scoring.

    Returns [(def_uid, seed_score)] sorted descending by seed_score.
    """
    scored: list[tuple[str, float]] = []

    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue

        # --- Bounded features (semantic / structural only) ---
        f_emb = _clamp(cand.embedding_similarity)
        f_hub = _clamp(math.log1p(min(cand.hub_score, 30)) / math.log1p(30))
        f_terms = _clamp(len(cand.matched_terms) / 5.0)
        f_axes = _clamp((cand.evidence_axes - 1) / 3.0)
        f_lexical = _clamp(min(cand.lexical_hit_count, 5) / 5.0)
        f_graph = 0.5 if cand.from_graph else 0.0

        name_lower = cand.def_fact.name.lower()
        f_name = 1.0 if any(t in name_lower for t in parsed.primary_terms) else 0.0
        f_path = 1.0 if any(t in cand.file_path.lower() for t in parsed.primary_terms) else 0.0

        # Artifact-kind weight based on intent
        kind_weights = _ARTIFACT_WEIGHTS.get(cand.artifact_kind, {})
        f_artifact = kind_weights.get(parsed.intent, 0.5)

        # --- Relevance score (pure semantic + structural) ---
        # f_explicit and f_pinned are intentionally excluded — they create
        # artificial score cliffs that distort the gap cutoff.  Those files
        # survive via anchoring (step 8b), not via scoring.
        relevance = (
            f_emb * 0.30
            + f_hub * 0.08
            + f_terms * 0.15
            + f_axes * 0.12
            + f_name * 0.13
            + f_path * 0.06
            + f_lexical * 0.06
            + f_graph * 0.10
        ) * f_artifact

        # --- Seed score (how good as graph expansion entry) ---
        seed_multiplier = 0.5 + f_hub * 0.5
        seed_sc = relevance * seed_multiplier

        cand.relevance_score = relevance
        cand.seed_score = seed_sc

        scored.append((uid, seed_sc))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored
