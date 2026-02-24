"""Scoring — filter pipeline, bounded scoring model, and elbow detection.

Single Responsibility: Candidate evaluation and selection.
No I/O, no database access, no async.  Pure functions on candidates.
"""

from __future__ import annotations

import math

import structlog

from codeplane.mcp.tools.recon.models import (
    ArtifactKind,
    FileCandidate,
    HarvestCandidate,
    OutputTier,
    ParsedTask,
    ReconBucket,
    TaskIntent,
)

log = structlog.get_logger(__name__)


# ===================================================================
# Filter Pipeline — query-conditioned, with OR gate + negative gating
# ===================================================================


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

        # Stage 5: Structural evidence gate
        #
        # Candidates without structural evidence and without semantic
        # evidence are filtered out.
        if not cand.has_structural_evidence and not cand.has_semantic_evidence:
            stats["filtered"] += 1
            continue

        # Stage 6: Barrel soft-pass
        #
        # Barrel files (__init__.py re-exports) were previously excluded
        # outright, but many __init__.py files ARE ground-truth edit/context
        # targets (e.g. package-level API surfaces).  We now let them
        # through; the scoring layer handles their rank appropriately.

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
# Two-Elbow Detection — file-level tier assignment
# ===================================================================


def compute_two_elbows(
    scores: list[float],
    *,
    min_full: int = 2,
    max_full: int = 20,
    min_total: int = 5,
    max_total: int = 40,
) -> tuple[int, int]:
    """Find two elbows in a sorted-descending score list.

    Returns ``(n_full, n_scaffold)`` where:
    - ``scores[:n_full]`` → FULL_FILE tier (above elbow-1)
    - ``scores[n_full:n_scaffold]`` → MIN_SCAFFOLD tier (between elbows)
    - ``scores[n_scaffold:]`` → SUMMARY_ONLY tier (below elbow-2)

    Uses the "maximum distance to chord" method applied twice:
    1. First elbow on the full score list → FULL_FILE boundary
    2. Second elbow on the remaining tail → MIN_SCAFFOLD boundary

    Args:
        scores: Similarity scores sorted descending.
        min_full: Minimum number of FULL_FILE results.
        max_full: Maximum number of FULL_FILE results.
        min_total: Minimum total files included (FULL + SCAFFOLD).
        max_total: Maximum total files included.

    Returns:
        ``(n_full, n_scaffold)`` — counts, not indices.
    """
    n = len(scores)
    if n == 0:
        return (0, 0)

    if n <= min_full:
        return (n, n)

    # --- Elbow 1: FULL_FILE cutoff ---
    n_full = _find_elbow_raw(
        scores,
        lo=min_full,
        hi=min(n, max_full),
    )

    # --- Elbow 2: MIN_SCAFFOLD cutoff (applied to tail) ---
    tail = scores[n_full:]
    if not tail:
        return (n_full, n_full)

    remaining_min = max(0, min_total - n_full)
    remaining_max = max(0, max_total - n_full)
    n_scaffold_tail = _find_elbow_raw(
        tail,
        lo=min(remaining_min, len(tail)),
        hi=min(len(tail), remaining_max),
    )
    n_scaffold = n_full + n_scaffold_tail

    log.debug(
        "recon.two_elbows",
        n=n,
        n_full=n_full,
        n_scaffold=n_scaffold,
        top_score=round(scores[0], 4) if scores else 0,
        elbow1_score=round(scores[n_full - 1], 4) if n_full > 0 else 0,
        elbow2_score=round(scores[n_scaffold - 1], 4) if n_scaffold > 0 else 0,
    )

    return (n_full, n_scaffold)


def _find_elbow_raw(
    scores: list[float],
    lo: int,
    hi: int,
) -> int:
    """Raw elbow detection on a sorted-descending sublist.

    Returns the number of items above the elbow (clamped to [lo, hi]).
    """
    n = len(scores)
    lo = max(1, min(lo, n))
    hi = max(lo, min(hi, n))

    if hi <= lo:
        return lo

    analysis = scores[:hi]
    n_a = len(analysis)
    if n_a <= lo:
        return n_a

    top, bottom = analysis[0], analysis[-1]
    # Flat distribution → include everything up to hi
    spread = top - bottom
    if top > 0 and spread / top < 0.05:
        return hi

    # Chord from first to last
    x2 = n_a - 1
    dx = float(x2)
    dy = bottom - top  # negative
    chord_len = (dx * dx + dy * dy) ** 0.5
    if chord_len < 1e-10:
        return lo

    max_dist = 0.0
    elbow_idx = lo

    for i in range(lo, n_a):
        dist = abs(dy * i - dx * analysis[i] + x2 * top) / chord_len
        if dist > max_dist:
            max_dist = dist
            elbow_idx = i

    result = elbow_idx + 1
    return max(lo, min(result, hi))


def assign_tiers(
    candidates: list[FileCandidate],
) -> list[FileCandidate]:
    """Assign OutputTier to file candidates based on two-elbow detection.

    Mutates candidates in-place and returns them sorted by combined_score
    descending.

    Explicit mentions are always promoted to at least MIN_SCAFFOLD.
    """
    if not candidates:
        return candidates

    # Sort by combined_score descending
    candidates.sort(key=lambda c: -c.combined_score)
    scores = [c.combined_score for c in candidates]

    n_full, n_scaffold = compute_two_elbows(scores)

    for i, cand in enumerate(candidates):
        if i < n_full:
            cand.tier = OutputTier.FULL_FILE
        elif i < n_scaffold:
            cand.tier = OutputTier.MIN_SCAFFOLD
        else:
            cand.tier = OutputTier.SUMMARY_ONLY

    # Promote explicit mentions to at least MIN_SCAFFOLD
    for cand in candidates:
        if cand.has_explicit_mention and cand.tier == OutputTier.SUMMARY_ONLY:
            cand.tier = OutputTier.MIN_SCAFFOLD

    n_full_final = sum(1 for c in candidates if c.tier == OutputTier.FULL_FILE)
    n_scaffold_final = sum(1 for c in candidates if c.tier == OutputTier.MIN_SCAFFOLD)
    n_summary_final = sum(1 for c in candidates if c.tier == OutputTier.SUMMARY_ONLY)
    log.debug(
        "recon.tier_assignment",
        full=n_full_final,
        scaffold=n_scaffold_final,
        summary=n_summary_final,
    )

    return candidates


def compute_noise_metric(scores: list[float]) -> float:
    """Compute a noise metric for the score distribution.

    Returns a value in [0, 1] where:
    - 0 = clear signal (well-separated scores, strong top candidates)
    - 1 = pure noise (flat distribution, no meaningful separation)

    Used to decide whether to include mapRepo data in the response:
    high noise → agent needs map_repo to orient; low noise → embeddings
    are sufficient.
    """
    if len(scores) < 2:
        return 1.0  # Not enough data → assume noisy

    top = scores[0]
    if top <= 0:
        return 1.0

    # Metric 1: Relative spread (how much dynamic range)
    bottom = scores[-1] if scores else 0
    spread = (top - bottom) / top  # [0, 1]

    # Metric 2: Concentration (what fraction of total similarity is in top-3)
    total = sum(scores)
    if total <= 0:
        return 1.0
    top3_sum = sum(scores[:3])
    concentration = top3_sum / total  # higher = more concentrated = less noise

    # Noise = 1 - signal quality
    signal_quality = spread * 0.5 + concentration * 0.5
    return max(0.0, min(1.0, 1.0 - signal_quality))


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
        TaskIntent.implement: 0.5,
        TaskIntent.refactor: 0.4,
        TaskIntent.understand: 0.5,
        TaskIntent.test: 1.0,
        TaskIntent.unknown: 0.4,
    },
    ArtifactKind.config: {
        TaskIntent.debug: 0.4,
        TaskIntent.implement: 0.6,
        TaskIntent.refactor: 0.3,
        TaskIntent.understand: 0.6,
        TaskIntent.test: 0.2,
        TaskIntent.unknown: 0.4,
    },
    ArtifactKind.doc: {
        TaskIntent.debug: 0.2,
        TaskIntent.implement: 0.4,
        TaskIntent.refactor: 0.2,
        TaskIntent.understand: 0.8,
        TaskIntent.test: 0.1,
        TaskIntent.unknown: 0.3,
    },
    ArtifactKind.build: {
        TaskIntent.debug: 0.2,
        TaskIntent.implement: 0.4,
        TaskIntent.refactor: 0.2,
        TaskIntent.understand: 0.4,
        TaskIntent.test: 0.2,
        TaskIntent.unknown: 0.2,
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
            f_hub * 0.10
            + f_terms * 0.25
            + f_axes * 0.15
            + f_name * 0.20
            + f_path * 0.08
            + f_lexical * 0.10
            + f_graph * 0.12
        ) * f_artifact

        # --- Seed score (how good as graph expansion entry) ---
        seed_multiplier = 0.5 + f_hub * 0.5
        seed_sc = relevance * seed_multiplier

        cand.relevance_score = relevance
        cand.seed_score = seed_sc

        scored.append((uid, seed_sc))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


# ===================================================================
# Dual Scoring — edit-likelihood and context-value
# ===================================================================

# Executable def kinds: definitions that contain logic the agent would edit.
# Headings, keys, and type aliases are structural but not typically edit targets.
_EXECUTABLE_KINDS: frozenset[str] = frozenset(
    {"function", "method", "class", "async_function", "async_method"}
)


def _compute_edit_likelihood(
    candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> None:
    """Compute per-candidate edit-likelihood score.

    This score estimates how likely a def's containing file is to be
    edited by the agent.  It combines three independent signals:

    1. **Def-level intent alignment** — how well the def's name aligns
       with the task, using embedding similarity and term overlap.
       Aggregated to file via max(top-2 def scores).

    2. **Task-local graph centrality** — how many edges connect this def
       to other candidates in the task-local subgraph.  Uses from_graph,
       is_callee_of_top, is_imported_by_top.

    3. **Change-surface prior** — whether the def is executable code
       (function/class/method) vs structural-only (variable, heading).

    The score is written to ``cand.edit_score``.
    """
    for cand in candidates.values():
        if cand.def_fact is None:
            cand.edit_score = 0.0
            continue

        # Signal 1: Def-level intent alignment
        name_lower = cand.def_fact.name.lower()
        f_name_hit = 1.0 if any(t in name_lower for t in parsed.primary_terms) else 0.0
        f_terms = _clamp(len(cand.matched_terms) / 5.0)
        intent_alignment = f_name_hit * 0.55 + f_terms * 0.45

        # Signal 2: Task-local graph centrality
        # Each graph relationship (from_graph, callee-of-top, imported-by-top)
        # provides evidence of structural coupling to the edit neighbourhood.
        graph_edges = sum(
            [
                cand.from_graph,
                cand.is_callee_of_top,
                cand.is_imported_by_top,
                cand.shares_file_with_seed,
            ]
        )
        f_centrality = _clamp(graph_edges / 3.0)

        # Signal 3: Change-surface prior
        kind = cand.def_fact.kind.lower() if cand.def_fact.kind else ""
        f_executable = 1.0 if kind in _EXECUTABLE_KINDS else 0.3

        # Combine (no artifact weight — edit-likelihood is artifact-agnostic
        # except for the executable prior)
        edit_score = intent_alignment * 0.55 + f_centrality * 0.25 + f_executable * 0.20

        # Down-weight test files for edit-likelihood (tests are context,
        # not edit targets — unless the task is explicitly about tests)
        if cand.is_test and not parsed.is_test_driven:
            edit_score *= 0.3

        # Down-weight docs and config (they're context by nature)
        if cand.artifact_kind in (ArtifactKind.doc, ArtifactKind.config, ArtifactKind.build):
            edit_score *= 0.15

        cand.edit_score = _clamp(edit_score)


def _compute_context_value(
    candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> None:
    """Compute per-candidate context-value score.

    This score estimates how useful a def is as *background context* for
    the agent — even if it won't be edited.  It combines:

    1. **Test adjacency** — graph edges to edit-likely defs.  Tests of
       the code being edited are high-value context.

    2. **Config/doc adjacency** — usage evidence (symbol mentions in
       config/docs, not just keyword matches).

    3. **Semantic proximity** — embedding similarity provides general
       topic relevance even without structural links.

    The score is written to ``cand.context_score``.
    """
    for cand in candidates.values():
        if cand.def_fact is None:
            cand.context_score = 0.0
            continue

        # Signal 1: Test adjacency (high value for test files, lower for code)
        f_test_adj = 0.0
        if cand.is_test:
            # Tests are valuable context if they're graph-connected or
            # semantically close to the task
            graph_link = cand.from_graph or cand.is_callee_of_top or cand.is_imported_by_top
            f_test_adj = 0.8 if graph_link else 0.3 * _clamp(len(cand.matched_terms) / 3.0)

        # Signal 2: Config/doc adjacency — symbol-level mentions
        f_doc_adj = 0.0
        if cand.artifact_kind in (ArtifactKind.config, ArtifactKind.doc):
            # Docs/configs are valuable when they mention task symbols
            term_overlap = _clamp(len(cand.matched_terms) / 3.0)
            f_doc_adj = term_overlap * 0.7 + 0.3 * _clamp(cand.lexical_hit_count / 5.0)

        # Signal 3: Semantic proximity (general topic relevance)
        f_semantic = (
            _clamp(len(cand.matched_terms) / 5.0) * 0.5 + _clamp(cand.lexical_hit_count / 5.0) * 0.2
        )

        # Signal 4: Structural coupling (any graph evidence)
        f_structural = 0.0
        if cand.from_graph or cand.is_callee_of_top or cand.is_imported_by_top:
            f_structural = 0.5
        if cand.shares_file_with_seed:
            f_structural = max(f_structural, 0.3)

        # Combine with artifact-aware weighting
        if cand.is_test:
            context_score = f_test_adj * 0.50 + f_semantic * 0.30 + f_structural * 0.20
        elif cand.artifact_kind in (ArtifactKind.config, ArtifactKind.doc):
            context_score = f_doc_adj * 0.50 + f_semantic * 0.30 + f_structural * 0.20
        else:
            # Code files: context value comes from structural coupling
            # (e.g., type definitions, utility modules)
            context_score = (
                f_semantic * 0.40
                + f_structural * 0.40
                + (_clamp(math.log1p(min(cand.hub_score, 30)) / math.log1p(30)) * 0.20)
            )

        cand.context_score = _clamp(context_score)


# ===================================================================
# File-level Dual Aggregation
# ===================================================================


def _aggregate_to_files_dual(
    scored: list[tuple[str, float]],
    candidates: dict[str, HarvestCandidate],
) -> list[tuple[int, float, float, float, list[tuple[str, float]]]]:
    """Aggregate def-level scores to file-level with dual scores.

    Per file, computes:
    - file_score: same as _aggregate_to_files (sum of top-m def relevance scores)
    - file_edit_score: max(top-2 def edit_scores) — edit-likelihood at file level
    - file_context_score: max(top-2 def context_scores) — context-value at file level

    Returns:
        List of ``(file_id, file_score, file_edit_score, file_context_score,
        [(def_uid, score), ...])`` sorted descending by file_score
        (stable on file_id for ties).
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
    result: list[tuple[int, float, float, float, list[tuple[str, float]]]] = []
    for fid, defs in file_defs.items():
        defs.sort(key=lambda x: -x[1])
        # m = count of this file's defs above the global median, clamped to [1, 3]
        n_above_median = sum(1 for _, s in defs if s >= global_median)
        m = max(1, min(n_above_median, 3))
        file_score = sum(s for _, s in defs[:m])

        # Dual scores: aggregate def-level edit/context to file-level
        # Use mean of top-2 (or top-1 if only one def) — captures breadth
        edit_scores_sorted = sorted(
            (candidates[uid].edit_score for uid, _ in defs if uid in candidates),
            reverse=True,
        )
        context_scores_sorted = sorted(
            (candidates[uid].context_score for uid, _ in defs if uid in candidates),
            reverse=True,
        )

        top_n = min(2, len(edit_scores_sorted))
        file_edit = sum(edit_scores_sorted[:top_n]) / top_n if top_n > 0 else 0.0
        top_n_c = min(2, len(context_scores_sorted))
        file_ctx = sum(context_scores_sorted[:top_n_c]) / top_n_c if top_n_c > 0 else 0.0

        result.append((fid, file_score, file_edit, file_ctx, defs))

    result.sort(key=lambda x: (-x[1], x[0]))

    log.debug(
        "recon.file_aggregation_dual",
        n_files=len(result),
        global_median=round(global_median, 4),
        top5=[
            (fid, round(fs, 4), round(fe, 4), round(fc, 4), len(ds))
            for fid, fs, fe, fc, ds in result[:5]
        ],
    )
    return result


# ===================================================================
# Bucketing — rank-based assignment with product budgets
# ===================================================================


def _assign_buckets(
    file_ranked: list[tuple[int, float, float, float, list[tuple[str, float]]]],
    candidates: dict[str, HarvestCandidate],
) -> dict[int, ReconBucket]:
    """Assign each file to a bucket using score-based classification.

    Strategy: each file goes to the bucket matching its dominant signal.
    - edit_target: file_edit_score > file_context_score AND edit_score >= 0.10
    - context: file_context_score >= file_edit_score AND context_score >= 0.05
    - supplementary: everything else (weak on both axes)

    No hard caps — all qualifying files get the correct bucket.
    If no file qualifies for edit_target, the top file by edit_score is promoted
    (at least one edit_target ensures agents have somewhere to start).

    Args:
        file_ranked: Output of _aggregate_to_files_dual.
        candidates: Candidate defs (for updating bucket field).

    Returns:
        Dict mapping file_id to its assigned ReconBucket.
    """
    if not file_ranked:
        return {}

    buckets: dict[int, ReconBucket] = {}
    edit_count = 0
    context_count = 0

    for fid, _fs, f_edit, f_ctx, _defs in file_ranked:
        if f_edit >= 0.10 and f_edit > f_ctx:
            buckets[fid] = ReconBucket.edit_target
            edit_count += 1
        elif f_ctx >= 0.05:
            buckets[fid] = ReconBucket.context
            context_count += 1
        else:
            buckets[fid] = ReconBucket.supplementary

    # Safety net: if nothing qualified as edit_target, promote the top file
    if edit_count == 0 and file_ranked:
        top_fid = max(file_ranked, key=lambda x: x[2])[0]  # highest edit score
        if top_fid in buckets:
            old_bucket = buckets[top_fid]
            buckets[top_fid] = ReconBucket.edit_target
            edit_count = 1
            if old_bucket == ReconBucket.context:
                context_count -= 1

    # Propagate bucket to candidates
    for cand in candidates.values():
        if cand.def_fact is not None:
            fid = cand.def_fact.file_id
            cand.bucket = buckets.get(fid, ReconBucket.supplementary)

    log.debug(
        "recon.bucketing",
        edit_targets=edit_count,
        context=context_count,
        supplementary=len(file_ranked) - edit_count - context_count,
        total=len(file_ranked),
    )

    return buckets
