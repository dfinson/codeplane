"""Scoring — elbow detection, tier assignment, and noise metrics.

Single Responsibility: Score distribution analysis and file-tier assignment.
No I/O, no database access, no async.  Pure functions on scores.
"""

from __future__ import annotations

import structlog

from codeplane.mcp.tools.recon.models import (
    FileCandidate,
    OutputTier,
)

log = structlog.get_logger(__name__)


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
    full_file_indices: list[int] | None = None,
) -> float:
    """Compute floor score for anchor-calibrated file inclusion.

    Uses ``max(MAD_anchor, MAD_full_file)`` as the tolerance band.
    Anchor MAD alone can be dangerously small when anchors are tightly
    clustered (2-3 files with near-identical scores → MAD ≈ 0 → floor ≈
    ``min(anchor)``, demoting nearly everything).  Using the wider of
    anchor-only or full-tier dispersion prevents over-pruning while
    still anchoring the floor to real data — no arbitrary constants.

    The inclusion floor is::

        min(anchor_scores) - max(MAD_anchor, MAD_full_file)

    Args:
        scores: File scores, sorted descending.
        anchor_indices: 0-based indices of anchor files in the sorted
            score list.
        full_file_indices: 0-based indices of all FULL_FILE files.
            When *None* or empty, falls back to anchor-only MAD.

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

    # MAD = Median Absolute Deviation of anchor scores
    anchor_median = anchor_scores[len(anchor_scores) // 2]
    abs_devs = sorted(abs(s - anchor_median) for s in anchor_scores)
    mad_anchor = abs_devs[len(abs_devs) // 2] if abs_devs else 0.0

    # MAD of full-tier scores — captures natural spread across the tier
    mad_full = 0.0
    if full_file_indices:
        full_scores = sorted(scores[i] for i in full_file_indices if i < n)
        if full_scores:
            full_median = full_scores[len(full_scores) // 2]
            full_abs_devs = sorted(abs(s - full_median) for s in full_scores)
            mad_full = full_abs_devs[len(full_abs_devs) // 2] if full_abs_devs else 0.0

    # Use the wider of the two spreads — prevents over-pruning when
    # anchors are tightly clustered but the tier is naturally dispersed.
    mad = max(mad_anchor, mad_full)

    floor_score = s_anchor_min - mad

    log.debug(
        "recon.anchor_floor",
        s_anchor_min=round(s_anchor_min, 4),
        mad_anchor=round(mad_anchor, 4),
        mad_full=round(mad_full, 4),
        mad=round(mad, 4),
        floor=round(floor_score, 4),
        n_anchors=len(anchor_scores),
    )

    return floor_score
