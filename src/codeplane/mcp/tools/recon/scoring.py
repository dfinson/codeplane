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


def _score_candidates(
    candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> list[tuple[str, float]]:
    """Score candidates with bounded features and separated scores.

    Features (all normalized to [0, 1]):
      f_emb:    Embedding similarity (already [0, 1]).
      f_hub:    Hub score, log-scaled and capped.
      f_terms:  Term match count, bounded.
      f_axes:   Evidence axis diversity, bounded.
      f_name:   Name contains primary term (binary).
      f_path:   Path contains primary term (binary).
      f_lexical: Lexical hit presence (binary, avoids double-counting with term).
      f_explicit: Explicit mention (binary).
      f_artifact: Intent-aware artifact weight [0, 1].

    Relevance score = weighted sum of all features (how relevant to task).
    Seed score = relevance * seed_multiplier (how good as entry point).
      - seed_multiplier boosts hub score and penalizes leaf nodes.

    Returns [(def_uid, seed_score)] sorted descending by seed_score.
    """
    scored: list[tuple[str, float]] = []

    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue

        # --- Bounded features ---
        f_emb = _clamp(cand.embedding_similarity)
        f_hub = _clamp(math.log1p(min(cand.hub_score, 30)) / math.log1p(30))
        f_terms = _clamp(len(cand.matched_terms) / 5.0)
        f_axes = _clamp((cand.evidence_axes - 1) / 3.0)
        f_lexical = _clamp(min(cand.lexical_hit_count, 5) / 5.0)
        f_explicit = 1.0 if cand.from_explicit else 0.0

        name_lower = cand.def_fact.name.lower()
        f_name = 1.0 if any(t in name_lower for t in parsed.primary_terms) else 0.0
        f_path = 1.0 if any(t in cand.file_path.lower() for t in parsed.primary_terms) else 0.0

        # Artifact-kind weight based on intent
        kind_weights = _ARTIFACT_WEIGHTS.get(cand.artifact_kind, {})
        f_artifact = kind_weights.get(parsed.intent, 0.5)

        # --- Relevance score (how relevant to the task) ---
        relevance = (
            f_emb * 0.30
            + f_hub * 0.10
            + f_terms * 0.15
            + f_axes * 0.10
            + f_name * 0.12
            + f_path * 0.05
            + f_lexical * 0.08
            + f_explicit * 0.10
        ) * f_artifact

        # --- Seed score (how good as graph expansion entry) ---
        # Hub score matters more for seed selection (central = better root)
        seed_multiplier = 0.5 + f_hub * 0.3 + f_explicit * 0.2
        seed_sc = relevance * seed_multiplier

        cand.relevance_score = relevance
        cand.seed_score = seed_sc

        scored.append((uid, seed_sc))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored
