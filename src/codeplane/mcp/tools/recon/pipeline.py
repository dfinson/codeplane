"""Pipeline orchestrator and MCP tool registration.

Single Responsibility: Wire the full recon pipeline together and register
the ``recon`` tool with FastMCP.

This is the only module that imports from all sub-modules — it composes
them but adds no domain logic of its own (Dependency Inversion principle).

v4 design: ONE call, ALL context.
- Agent controls nothing — only ``task`` and optional ``seeds`` exposed.
- Backend decides depth, seed count, format (no knobs).
- Fan out aggressively, prune via distribution-relative cutoff, return
  full source for every surviving seed.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from codeplane.mcp.tools.recon.assembly import (
    _build_failure_actions,
    _trim_to_budget,
)
from codeplane.mcp.tools.recon.expansion import (
    _build_import_scaffolds,
    _expand_seed,
)
from codeplane.mcp.tools.recon.harvesters import (
    _enrich_candidates,
    _enrich_file_candidates,
    _harvest_explicit,
    _harvest_file_embedding,
    _harvest_graph,
    _harvest_imports,
    _harvest_lexical,
    _harvest_term_match,
    _merge_candidates,
)
from codeplane.mcp.tools.recon.models import (
    _INTERNAL_BUDGET_BYTES,
    _INTERNAL_DEPTH,
    FileCandidate,
    OutputTier,
    ParsedTask,
    ReconBucket,
    _classify_artifact,
    _is_test_file,
)
from codeplane.mcp.tools.recon.parsing import parse_task
from codeplane.mcp.tools.recon.scoring import (
    _aggregate_to_files,
    _aggregate_to_files_dual,
    _apply_filters,
    _assign_buckets,
    _compute_context_value,
    _compute_edit_likelihood,
    _score_candidates,
    assign_tiers,
    compute_anchor_floor,
    compute_noise_metric,
    find_elbow,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.index.models import DefFact
    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)

# Max content bytes to include for unindexed files
_UNINDEXED_MAX_BYTES = 8192
_UNINDEXED_MAX_FILES = 15


# ===================================================================
# Unindexed file discovery (path-based)
# ===================================================================


def _find_unindexed_files(
    app_ctx: Any,
    parsed: ParsedTask,
    indexed_paths: set[str],
) -> list[tuple[str, float]]:
    """Find git-tracked files NOT in the structural index whose paths match query terms.

    This catches .yaml, .md, .toml, .json, dotfiles, Makefiles, etc. that
    the tree-sitter-based index never processes.  Uses the same terms the
    harvesters use so the query drives inclusion.

    Returns:
        List of ``(repo_relative_path, match_score)`` sorted descending
        by score, capped at ``_UNINDEXED_MAX_FILES``.
    """
    # Collect terms to match against file paths
    terms: set[str] = set()
    for t in parsed.primary_terms:
        if len(t) >= 3:
            terms.add(t.lower())
    for t in parsed.secondary_terms:
        if len(t) >= 3:
            terms.add(t.lower())
    for sym in parsed.explicit_symbols or []:
        if len(sym) >= 3:
            terms.add(sym.lower())
    # Path fragments from explicit mentions
    for p in parsed.explicit_paths or []:
        for component in re.split(r"[/._\-]", p):
            if len(component) >= 3:
                terms.add(component.lower())

    if not terms:
        return []

    # Get all git-tracked files
    try:
        all_files = app_ctx.git_ops.tracked_files()
    except Exception:  # noqa: BLE001
        log.debug("recon.unindexed_files.git_error")
        return []

    # Filter to files NOT in the structural index
    unindexed = [f for f in all_files if f not in indexed_paths]

    matches: list[tuple[str, float]] = []
    for fpath in unindexed:
        # Tokenize path components
        fpath_lower = fpath.lower()
        path_tokens = set(re.split(r"[/._\-]", fpath_lower))
        path_tokens = {t for t in path_tokens if len(t) >= 2}

        # Token overlap
        hits = terms & path_tokens

        # Also substring match (catches partial matches like "mlflow" in path)
        if not hits:
            hits = {t for t in terms if t in fpath_lower}

        if hits:
            # Score: fraction of query terms that match + bonus for
            # filename (leaf) matches
            fname = PurePosixPath(fpath).name.lower()
            fname_tokens = set(re.split(r"[._\-]", fname))
            leaf_hits = terms & fname_tokens
            score = (len(hits) + len(leaf_hits) * 0.5) / max(len(terms), 1)
            matches.append((fpath, score))

    # Sort by score desc, cap
    matches.sort(key=lambda x: (-x[1], x[0]))
    return matches[:_UNINDEXED_MAX_FILES]


def _read_unindexed_content(repo_root: Path, rel_path: str) -> str | None:
    """Read content of a non-indexed file, capped for budget."""
    full = repo_root / rel_path
    if not full.exists() or not full.is_file():
        return None
    try:
        raw = full.read_bytes()
        # Skip binary files
        if b"\x00" in raw[:512]:
            return None
        text = raw.decode("utf-8", errors="replace")
        if len(text) > _UNINDEXED_MAX_BYTES:
            text = text[:_UNINDEXED_MAX_BYTES] + "\n... (truncated)"
        return text
    except Exception:  # noqa: BLE001
        return None


def _compute_sha256(path: Path) -> str:
    """Compute file SHA-256 for write_source compatibility."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ===================================================================
# Seed Selection Pipeline (v4: no arbitrary caps)
# ===================================================================


async def _select_seeds(
    app_ctx: AppContext,
    task: str,
    explicit_seeds: list[str] | None = None,
    *,
    pinned_paths: list[str] | None = None,
    min_seeds: int = 3,
) -> tuple[
    list[DefFact],
    ParsedTask,
    list[tuple[str, float]],
    dict[str, Any],
    dict[str, Any],
    dict[int, ReconBucket],
]:
    """Select seed definitions using the full harvest -> filter -> score pipeline.

    v5 pipeline — dual-score bucketed output:
    1. Parse task -> ParsedTask (with intent classification)
    2. Run 4 harvesters in parallel
    3. Merge candidates (accumulate evidence)
    4. Graph harvester (1-hop from top merged)
    5. Enrich with structural metadata + artifact kind
    6. Apply query-conditioned filter pipeline
    7. Score with bounded features + artifact-kind weights
    7b. Compute dual scores (edit-likelihood + context-value)
    8. Aggregate to file level (dual: file_score, edit_score, context_score)
    9. Anchor-calibrated file inclusion (MAD-based band + saturation)
    10. Bucket assignment (edit_target / context / supplementary)
    11. Multi-seed per file: all defs above global def-score median

    Args:
        pinned_paths: Explicit file paths supplied by the agent as
            high-confidence anchors.  Not inferred — only what the caller
            passes.  Files only, not directories.

    Returns:
        (seeds, parsed_task, scored_candidates, diagnostics,
         gated_candidates, file_buckets)
    """
    diagnostics: dict[str, Any] = {}
    t0 = time.monotonic()

    # 1. Parse task
    parsed = parse_task(task)
    diagnostics["intent"] = parsed.intent.value

    log.debug(
        "recon.parsed_task",
        intent=parsed.intent.value,
        primary=parsed.primary_terms[:5],
        secondary=parsed.secondary_terms[:3],
        paths=parsed.explicit_paths,
        symbols=parsed.explicit_symbols[:5],
    )

    # 2. Run harvesters in parallel (independent, no shared state)
    t_harvest = time.monotonic()
    term_candidates, lex_candidates, exp_candidates = await asyncio.gather(
        _harvest_term_match(app_ctx, parsed),
        _harvest_lexical(app_ctx, parsed),
        _harvest_explicit(app_ctx, parsed, explicit_seeds),
    )
    diagnostics["harvest_ms"] = round((time.monotonic() - t_harvest) * 1000)

    # 3. Merge
    merged = _merge_candidates(term_candidates, lex_candidates, exp_candidates)

    diagnostics["harvested"] = {
        "term_match": len(term_candidates),
        "lexical": len(lex_candidates),
        "explicit": len(exp_candidates),
        "merged": len(merged),
    }

    log.debug(
        "recon.merged",
        total=len(merged),
        term_match=len(term_candidates),
        lexical=len(lex_candidates),
        explicit=len(exp_candidates),
    )

    if not merged:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics, {}, {}

    # 3.5. Graph harvester — walk 1-hop edges from top merged candidates
    graph_candidates = await _harvest_graph(app_ctx, merged, parsed)
    if graph_candidates:
        merged = _merge_candidates(merged, graph_candidates)
        diagnostics["harvested"]["graph"] = len(graph_candidates)
        diagnostics["harvested"]["merged_with_graph"] = len(merged)
        log.debug("recon.graph_merged", graph=len(graph_candidates), total=len(merged))

    # 3.6. Import-chain harvester — trace resolved imports from top seeds
    import_candidates = await _harvest_imports(app_ctx, merged, parsed)
    if import_candidates:
        merged = _merge_candidates(merged, import_candidates)
        diagnostics["harvested"]["imports"] = len(import_candidates)
        diagnostics["harvested"]["merged_with_imports"] = len(merged)
        log.debug("recon.import_merged", imports=len(import_candidates), total=len(merged))

    # 4. Enrich with structural metadata + artifact kind
    await _enrich_candidates(app_ctx, merged)

    # 5. Intent-aware filter pipeline (query-conditioned)
    gated = _apply_filters(merged, parsed)

    if not gated:
        log.info("recon.filter_empty", pre_filter=len(merged))
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics, {}, {}

    diagnostics["post_filter"] = len(gated)

    # 6. Score
    scored = _score_candidates(gated, parsed)

    if not scored:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics, {}, {}

    # 7. Aggregate to file level (legacy — still used for file_score)
    _aggregate_to_files(scored, gated)

    # 7b. Compute dual scores (edit-likelihood + context-value)
    _compute_edit_likelihood(gated, parsed)
    _compute_context_value(gated, parsed)

    # 7c. Aggregate to file level with dual scores
    file_ranked_dual = _aggregate_to_files_dual(scored, gated)

    # Diagnostics: file ranking with paths (for debugging)
    coordinator = app_ctx.coordinator
    with coordinator.db.session() as session:
        from codeplane.index._internal.indexing.graph import FactQueries

        fq = FactQueries(session)
        _diag_ranking = []
        for i, (fid, fs, fe, fc, fdefs) in enumerate(file_ranked_dual[:20]):
            frec = fq.get_file(fid)
            fpath = frec.path if frec else f"?{fid}"
            _diag_ranking.append(
                {
                    "rank": i + 1,
                    "path": fpath,
                    "score": round(fs, 4),
                    "edit": round(fe, 4),
                    "ctx": round(fc, 4),
                    "n_defs": len(fdefs),
                }
            )
        diagnostics["_file_ranking_top20"] = _diag_ranking

    # 8. File inclusion: anchor-calibrated band + def-level saturation
    #    File ranking is a candidate generator, not a classifier.
    #    No gap-based cutoff — inclusion is driven by anchor calibration
    #    and downstream seed contribution.

    # 8a. Identify anchor files (explicit paths + pinned)
    anchor_fids: set[int] = set()
    _all_anchor_paths = list(parsed.explicit_paths or [])
    if pinned_paths:
        _all_anchor_paths.extend(pinned_paths)
    if _all_anchor_paths:
        coordinator = app_ctx.coordinator
        with coordinator.db.session() as session:
            from codeplane.index._internal.indexing.graph import FactQueries

            fq = FactQueries(session)
            for apath in _all_anchor_paths:
                frec = fq.get_file_by_path(apath)
                if frec is not None and frec.id is not None:
                    anchor_fids.add(frec.id)

    # 8b. Anchor-calibrated relevance band
    #     Uses MAD (median absolute deviation) around anchor scores to
    #     define the floor.  Files scoring >= (min_anchor - MAD) survive.
    file_score_values = [fs for _, fs, _, _, _ in file_ranked_dual]
    anchor_indices = [
        i for i, (fid, _, _, _, _) in enumerate(file_ranked_dual) if fid in anchor_fids
    ]
    floor_score = compute_anchor_floor(file_score_values, anchor_indices)
    n_band = sum(1 for fs in file_score_values if fs >= floor_score) if floor_score > 0 else 0
    n_files = max(min(min_seeds, len(file_ranked_dual)), n_band)

    # Safety net: ensure ALL anchor files are in the surviving window
    # (normally already inside the band, but handles edge cases)
    for idx in range(n_files, len(file_ranked_dual)):
        fid, _, _, _, _ = file_ranked_dual[idx]
        if fid in anchor_fids:
            file_ranked_dual[n_files], file_ranked_dual[idx] = (
                file_ranked_dual[idx],
                file_ranked_dual[n_files],
            )
            n_files += 1

    # 8c. Saturation pass — extend inclusion while files contribute seeds
    all_def_scores = sorted((s for _, s in scored), reverse=True)
    def_score_median = all_def_scores[len(all_def_scores) // 2] if all_def_scores else 0.0

    _K_SATURATE = 3  # anchor-case consecutive-miss limit

    # Hard ceiling: never include more than 40 files regardless of path.
    _MAX_FILES = 40

    if floor_score > 0:
        # ── Anchor case: gate on floor_score ──
        consecutive_empty = 0
        upper = min(len(file_ranked_dual), _MAX_FILES)
        for idx in range(n_files, upper):
            _fid, _fscore, _, _, fdefs = file_ranked_dual[idx]
            if _fscore >= floor_score:
                n_files = idx + 1
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= _K_SATURATE:
                    break
    else:
        # ── No-anchor case: two-phase adaptive inclusion ──
        # Phase 1 — elbow detection on file scores.
        # Phase 2 — tail extension using MAD-derived floor from the
        #           elbow-selected files (mirrors the anchor-case MAD
        #           approach, fully data-adaptive).
        file_scores = [fs for _, fs, _, _, _ in file_ranked_dual]
        n_candidates = len(file_scores)
        # Adaptive minimum: at least 20 % of candidates (capped at 12)
        # so broad queries don't get starved.
        adaptive_min = max(n_files, 5, min(n_candidates // 5, 12))
        elbow_n = find_elbow(
            file_scores,
            min_seeds=adaptive_min,
            max_seeds=min(n_candidates, _MAX_FILES),
        )
        n_files = max(n_files, elbow_n)

        # Phase 2: gap-based tail extension
        # Extend inclusion by one "typical step" below the minimum
        # included score.  The median consecutive-score gap captures
        # the natural spacing; subtracting it gives a floor that adapts
        # to each distribution shape (no constants).
        if n_files < n_candidates and n_files >= 2:
            included = file_scores[:n_files]
            gaps = [included[i] - included[i + 1] for i in range(len(included) - 1)]
            gaps_sorted = sorted(gaps)
            median_gap = gaps_sorted[len(gaps_sorted) // 2]
            min_inc = included[-1]
            # Safety net: never drop below half the minimum included
            # score to prevent runaway in pathological distributions.
            tail_floor = max(min_inc - median_gap, min_inc * 0.5)
            for idx in range(n_files, min(n_candidates, _MAX_FILES)):
                if file_scores[idx] >= tail_floor:
                    n_files = idx + 1
                else:
                    break

    log.info(
        "recon.file_inclusion",
        n_files=n_files,
        n_anchors=len(anchor_fids),
        n_band=n_band,
        floor_score=round(floor_score, 4) if floor_score > 0 else None,
    )
    diagnostics["n_files"] = n_files
    diagnostics["n_anchors"] = len(anchor_fids)
    diagnostics["anchor_floor"] = round(floor_score, 4) if floor_score > 0 else None

    # 9. Bucket assignment (edit_target / context / supplementary)
    surviving_files = file_ranked_dual[:n_files]
    file_buckets = _assign_buckets(surviving_files, gated)

    # Ensure anchor files are at least in the context bucket
    for fid in anchor_fids:
        if fid in file_buckets and file_buckets[fid] == ReconBucket.supplementary:
            file_buckets[fid] = ReconBucket.context

    diagnostics["buckets"] = {
        "edit_target": sum(1 for b in file_buckets.values() if b == ReconBucket.edit_target),
        "context": sum(1 for b in file_buckets.values() if b == ReconBucket.context),
        "supplementary": sum(1 for b in file_buckets.values() if b == ReconBucket.supplementary),
    }

    # 10. Multi-seed per file selection
    #    Phase 1: best def per surviving file (file diversity)
    #    Phase 2: additional defs above global def-score median (depth)
    #    Stage-aware test seed cap
    seeds: list[DefFact] = []
    selected_uids: set[str] = set()
    test_seed_count = 0
    max_test_ratio = 1.0 if parsed.is_test_driven else 0.33

    # Phase 1: one seed per file (diversity)
    for _fid, _fscore, _, _, fdefs in file_ranked_dual[:n_files]:
        for uid, _dscore in fdefs:
            if uid in selected_uids:
                continue
            cand = gated[uid]
            if cand.def_fact is None:
                continue
            if cand.is_test:
                test_seed_count += 1
            seeds.append(cand.def_fact)
            selected_uids.add(uid)
            break

    # Phase 2: additional defs above median (within surviving files)
    for _fid, _fscore, _, _, fdefs in file_ranked_dual[:n_files]:
        for uid, dscore in fdefs:
            if uid in selected_uids:
                continue
            if dscore < def_score_median:
                break  # fdefs are sorted desc — rest will be below too
            cand = gated[uid]
            if cand.def_fact is None:
                continue
            total_seeds = len(seeds)
            test_limit = max(2, int(total_seeds * max_test_ratio))
            if cand.is_test and test_seed_count >= test_limit:
                continue
            if cand.is_test:
                test_seed_count += 1
            seeds.append(cand.def_fact)
            selected_uids.add(uid)

    diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
    diagnostics["seeds_selected"] = len(seeds)
    diagnostics["file_ranked"] = len(file_ranked_dual)
    diagnostics["def_score_median"] = round(def_score_median, 4)

    log.info(
        "recon.seeds_selected",
        count=len(seeds),
        n_files=n_files,
        scored_total=len(scored),
        names=[s.name for s in seeds],
        intent=parsed.intent.value,
        total_ms=diagnostics["total_ms"],
    )

    # Return gated candidates for per-seed evidence breakdown
    gated_export = {
        uid: cand for uid, cand in gated.items() if any(s.def_uid == uid for s in seeds)
    }
    return seeds, parsed, scored, diagnostics, gated_export, file_buckets


# ===================================================================
# File-centric pipeline (v6: file-level embedding + two-elbow tiers)
# ===================================================================


async def _file_centric_pipeline(
    app_ctx: AppContext,
    task: str,
    explicit_seeds: list[str] | None = None,
    *,
    pinned_paths: list[str] | None = None,
) -> tuple[
    list[FileCandidate],
    ParsedTask,
    dict[str, Any],
    dict[str, Any],
]:
    """File-centric recon pipeline using file-level embeddings as primary signal.

    v6 pipeline — file-level embedding + two-elbow tiers:
    1. Parse task → ParsedTask
    2. File-level embedding search (PRIMARY) → ranked files
    3. Def-level harvesters in parallel (SECONDARY) → enrichment signals
    4a. Inject indexed files with path-token matches (≥2 query terms in path)
    4b. Enrich file candidates with 6-source RRF scoring
    5. Two-elbow tier assignment (FULL_FILE / MIN_SCAFFOLD / SUMMARY_ONLY)
    5.5. Test co-retrieval via direct imports (source → test)
    5.6. Reverse co-retrieval (test → source, promote-only)
    5.7. Directory cohesion (≥2 siblings → pull in rest of package)
    6. Noise metric → conditional mapRepo inclusion

    Returns:
        (file_candidates, parsed_task, diagnostics, session_info)
    """
    diagnostics: dict[str, Any] = {}
    session_info: dict[str, Any] = {}
    t0 = time.monotonic()

    # 1. Parse task
    parsed = parse_task(task)
    diagnostics["intent"] = parsed.intent.value

    log.debug(
        "recon.v6.parsed_task",
        intent=parsed.intent.value,
        primary=parsed.primary_terms[:5],
        paths=parsed.explicit_paths,
    )

    # 2. File-level embedding search (PRIMARY)
    t_file_emb = time.monotonic()
    file_candidates = await _harvest_file_embedding(app_ctx, parsed, top_k=100)
    diagnostics["file_embed_ms"] = round((time.monotonic() - t_file_emb) * 1000)
    diagnostics["file_embed_count"] = len(file_candidates)

    # 3. Def-level harvesters in parallel (SECONDARY enrichment)
    t_def_harvest = time.monotonic()
    term_cands, lex_cands, exp_cands = await asyncio.gather(
        _harvest_term_match(app_ctx, parsed),
        _harvest_lexical(app_ctx, parsed),
        _harvest_explicit(app_ctx, parsed, explicit_seeds),
    )
    merged_def = _merge_candidates(term_cands, lex_cands, exp_cands)

    # Graph + import harvesters for structural signal
    graph_cands = await _harvest_graph(app_ctx, merged_def, parsed)
    if graph_cands:
        merged_def = _merge_candidates(merged_def, graph_cands)
    import_cands = await _harvest_imports(app_ctx, merged_def, parsed)
    if import_cands:
        merged_def = _merge_candidates(merged_def, import_cands)

    # Enrich def-level candidates with structural metadata
    await _enrich_candidates(app_ctx, merged_def)

    diagnostics["def_harvest_ms"] = round((time.monotonic() - t_def_harvest) * 1000)
    diagnostics["def_candidates"] = len(merged_def)

    # 4a. Inject indexed files whose paths match query terms.
    #     term_match only checks def names, lexical only searches code content.
    #     Neither finds indexed files whose PATH components match the query
    #     (e.g. files under packages/evee-azureml/ when query says "azureml").
    #     This step fills that gap by adding path-matched indexed files to the
    #     candidate set before RRF scoring gives them a fair rank.
    coordinator = app_ctx.coordinator
    indexed_paths: set[str] = set()
    with coordinator.db.session() as session:
        from codeplane.index._internal.indexing.graph import FactQueries

        fq = FactQueries(session)
        for frec in fq.list_files(limit=50000):
            indexed_paths.add(frec.path)

    _PATH_INJECT_MAX = 10
    # Only use primary terms for path injection — secondary terms are
    # too common (config, model, test, etc.) and match nearly every file.
    # Also require len≥4 to avoid short noise tokens.
    path_inject_terms: set[str] = set()
    # Common path tokens that match too many files — skip these.
    _PATH_STOP_TOKENS = frozenset(
        {
            "src",
            "test",
            "tests",
            "config",
            "models",
            "utils",
            "core",
            "cli",
            "docs",
            "init",
            "main",
            "base",
            "common",
            "tools",
            "commands",
            "templates",
            "integration",
            "lib",
            "internal",
            "helpers",
            "types",
            "api",
            "app",
            "pkg",
            "evee",
            "codeplane",
            "python",
        }
    )
    for t in parsed.primary_terms:
        tl = t.lower()
        if len(tl) >= 4 and tl not in _PATH_STOP_TOKENS:
            path_inject_terms.add(tl)

    if path_inject_terms:
        existing_paths = {fc.path for fc in file_candidates}
        path_inject_scored: list[tuple[str, int]] = []
        for ip in indexed_paths:
            if ip in existing_paths:
                continue
            path_lower = ip.lower()
            path_tokens = set(re.split(r"[/._\-]", path_lower))
            path_tokens = {t for t in path_tokens if len(t) >= 2}

            hits = path_inject_terms & path_tokens
            if not hits:
                hits = {t for t in path_inject_terms if t in path_lower}

            # Require ≥2 distinctive path-token matches to avoid noise
            if len(hits) >= 2:
                path_inject_scored.append((ip, len(hits)))

        # Sort by match count desc, cap
        path_inject_scored.sort(key=lambda x: -x[1])
        for ip, _score in path_inject_scored[:_PATH_INJECT_MAX]:
            file_candidates.append(
                FileCandidate(
                    path=ip,
                    similarity=0.0,
                    combined_score=0.0,  # will be set by RRF
                    artifact_kind=_classify_artifact(ip),
                )
            )
        diagnostics["path_inject_count"] = min(len(path_inject_scored), _PATH_INJECT_MAX)

    # 4b. Enrich file candidates with RRF scoring
    _enrich_file_candidates(file_candidates, merged_def, parsed)

    # Determine max RRF score for guaranteeing pinned/explicit placement.
    max_rrf = max((fc.combined_score for fc in file_candidates), default=0.0)
    # Ensure a minimum floor so pinned/explicit paths always surface.
    _RRF_K = 60
    pin_floor = max(max_rrf, 1.0 / (_RRF_K + 1))

    # Handle pinned paths: guarantee top-tier placement
    if pinned_paths:
        existing_paths = {fc.path for fc in file_candidates}
        for pp in pinned_paths:
            if pp not in existing_paths:
                file_candidates.append(
                    FileCandidate(
                        path=pp,
                        similarity=0.0,
                        combined_score=pin_floor,
                        has_explicit_mention=True,
                        artifact_kind=_classify_artifact(pp),
                    )
                )
            else:
                for fc in file_candidates:
                    if fc.path == pp:
                        fc.has_explicit_mention = True
                        fc.combined_score = max(fc.combined_score, pin_floor)
                        break

    # Handle explicit paths from task text
    if parsed.explicit_paths:
        existing_paths = {fc.path for fc in file_candidates}
        for ep in parsed.explicit_paths:
            if ep not in existing_paths:
                file_candidates.append(
                    FileCandidate(
                        path=ep,
                        similarity=0.0,
                        combined_score=pin_floor,
                        has_explicit_mention=True,
                        artifact_kind=_classify_artifact(ep),
                    )
                )
            else:
                for fc in file_candidates:
                    if fc.path == ep:
                        fc.has_explicit_mention = True
                        fc.combined_score = max(fc.combined_score, pin_floor)
                        break

    # Also discover unindexed files (yaml, md, etc.) via path matching
    # (indexed_paths already collected in step 4a above)
    unindexed_matches = _find_unindexed_files(app_ctx, parsed, indexed_paths)
    existing_paths = {fc.path for fc in file_candidates}
    for upath, uscore in unindexed_matches:
        if upath not in existing_paths:
            # Scale path-match score into the RRF range
            file_candidates.append(
                FileCandidate(
                    path=upath,
                    similarity=0.0,
                    combined_score=uscore * pin_floor * 0.5,
                    artifact_kind=_classify_artifact(upath),
                )
            )

    # 5. Two-elbow tier assignment
    file_candidates = assign_tiers(file_candidates)

    # 5.5. Deterministic test co-retrieval via direct imports
    #       Single-hop query: find test files whose import_facts have
    #       resolved_path pointing to a surviving source candidate.
    #       (NOT transitive — avoids fan-out through barrel re-exports.)
    #       The test inherits the source's tier:
    #         - source FULL_FILE  → test FULL_FILE
    #         - source MIN_SCAFFOLD → test MIN_SCAFFOLD
    #       Test files NOT linked to any surviving source get demoted.
    t_test_disco = time.monotonic()
    full_source_paths = [
        fc.path
        for fc in file_candidates
        if fc.tier == OutputTier.FULL_FILE and not _is_test_file(fc.path)
    ]
    scaffold_source_paths = [
        fc.path
        for fc in file_candidates
        if fc.tier == OutputTier.MIN_SCAFFOLD and not _is_test_file(fc.path)
    ]
    linked_test_paths: set[str] = set()
    # Maps test_path → best tier it should inherit
    test_target_tier: dict[str, OutputTier] = {}
    test_co_promoted = 0
    test_co_added = 0
    test_demoted = 0

    all_source_paths = full_source_paths + scaffold_source_paths
    if all_source_paths:
        from sqlmodel import col, select

        from codeplane.core.languages import is_test_file as _is_test_path
        from codeplane.index.models import File, ImportFact

        # Single-hop direct-import query: test files whose resolved_path
        # points to one of our source candidates.  No BFS, no transitive
        # closure — just "who directly imports this file?"
        full_tests: set[str] = set()
        scaffold_tests: set[str] = set()
        with coordinator.db.session() as session:
            if full_source_paths:
                stmt = (
                    select(File.path)
                    .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(col(ImportFact.resolved_path).in_(full_source_paths))
                ).distinct()
                for path in session.exec(stmt).all():
                    if path and _is_test_path(path):
                        full_tests.add(path)
            if scaffold_source_paths:
                stmt = (
                    select(File.path)
                    .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(col(ImportFact.resolved_path).in_(scaffold_source_paths))
                ).distinct()
                for path in session.exec(stmt).all():
                    if path and _is_test_path(path):
                        scaffold_tests.add(path)

        # FULL_FILE wins over MIN_SCAFFOLD
        for tp in full_tests:
            test_target_tier[tp] = OutputTier.FULL_FILE
        for tp in scaffold_tests:
            if tp not in test_target_tier:
                test_target_tier[tp] = OutputTier.MIN_SCAFFOLD
        linked_test_paths = set(test_target_tier.keys())

        existing_by_path = {fc.path: fc for fc in file_candidates}

        for test_path, target_tier in test_target_tier.items():
            if test_path in existing_by_path:
                fc = existing_by_path[test_path]
                # Promote if current tier is worse than target
                tier_rank = {
                    OutputTier.FULL_FILE: 0,
                    OutputTier.MIN_SCAFFOLD: 1,
                    OutputTier.SUMMARY_ONLY: 2,
                }
                if tier_rank.get(fc.tier, 2) > tier_rank[target_tier]:
                    fc.tier = target_tier
                    fc.graph_connected = True
                    test_co_promoted += 1
            else:
                # Not in candidates at all — add with the target tier
                ref_scores = [c.combined_score for c in file_candidates if c.tier == target_tier]
                score = max(ref_scores, default=pin_floor) * 0.9
                file_candidates.append(
                    FileCandidate(
                        path=test_path,
                        similarity=0.0,
                        combined_score=score,
                        graph_connected=True,
                        artifact_kind=_classify_artifact(test_path),
                    ),
                )
                # Set tier after construction (assign_tiers already ran)
                file_candidates[-1].tier = target_tier
                test_co_added += 1

    # Demote unlinked test files by one tier (not a full drop):
    #   FULL_FILE → MIN_SCAFFOLD, MIN_SCAFFOLD → SUMMARY_ONLY
    for fc in file_candidates:
        if (
            _is_test_file(fc.path)
            and fc.path not in linked_test_paths
            and not fc.has_explicit_mention
            and fc.tier in (OutputTier.FULL_FILE, OutputTier.MIN_SCAFFOLD)
        ):
            if fc.tier == OutputTier.FULL_FILE:
                fc.tier = OutputTier.MIN_SCAFFOLD
            else:
                fc.tier = OutputTier.SUMMARY_ONLY
            test_demoted += 1

    # 5.6. Reverse direction: test → source co-retrieval
    #       For test files in the top elbows, find the source files they
    #       directly import and promote/add those.  Fan-out is naturally
    #       low (test typically imports 1-3 source modules).
    source_co_promoted = 0
    source_co_added = 0
    linked_source_paths: set[str] = set()

    full_test_paths = [
        fc.path
        for fc in file_candidates
        if fc.tier == OutputTier.FULL_FILE and _is_test_file(fc.path)
    ]
    scaffold_test_paths = [
        fc.path
        for fc in file_candidates
        if fc.tier == OutputTier.MIN_SCAFFOLD and _is_test_file(fc.path)
    ]
    all_test_query_paths = full_test_paths + scaffold_test_paths
    if all_test_query_paths:
        from sqlmodel import col, select

        from codeplane.core.languages import is_test_file as _is_test_path
        from codeplane.index.models import File, ImportFact

        # Source target tier: test FULL → source FULL, test SCAFFOLD → source SCAFFOLD
        source_target_tier: dict[str, OutputTier] = {}
        full_sources: set[str] = set()
        scaffold_sources: set[str] = set()
        with coordinator.db.session() as session:
            if full_test_paths:
                stmt = (
                    select(File.path, ImportFact.resolved_path)
                    .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(col(File.path).in_(full_test_paths))
                    .where(ImportFact.resolved_path != None)  # noqa: E711
                ).distinct()
                for _test_path, resolved in session.exec(stmt).all():
                    if resolved and not _is_test_path(resolved):
                        full_sources.add(resolved)
            if scaffold_test_paths:
                stmt = (
                    select(File.path, ImportFact.resolved_path)
                    .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(col(File.path).in_(scaffold_test_paths))
                    .where(ImportFact.resolved_path != None)  # noqa: E711
                ).distinct()
                for _test_path, resolved in session.exec(stmt).all():
                    if resolved and not _is_test_path(resolved):
                        scaffold_sources.add(resolved)

        # FULL wins over SCAFFOLD
        for sp in full_sources:
            source_target_tier[sp] = OutputTier.FULL_FILE
        for sp in scaffold_sources:
            if sp not in source_target_tier:
                source_target_tier[sp] = OutputTier.MIN_SCAFFOLD
        linked_source_paths = set(source_target_tier.keys())

        existing_by_path = {fc.path: fc for fc in file_candidates}
        for src_path, target_tier in source_target_tier.items():
            if src_path in existing_by_path:
                fc = existing_by_path[src_path]
                tier_rank = {
                    OutputTier.FULL_FILE: 0,
                    OutputTier.MIN_SCAFFOLD: 1,
                    OutputTier.SUMMARY_ONLY: 2,
                }
                if tier_rank.get(fc.tier, 2) > tier_rank[target_tier]:
                    fc.tier = target_tier
                    fc.graph_connected = True
                    source_co_promoted += 1
            # NOTE: no 'else' branch — we promote existing candidates
            # but do NOT add new source files that were not already
            # retrieved by the core pipeline.  Adding was too noisy
            # (~4.3 files/query, all noise → precision -27%).

    diagnostics["test_co_retrieval_ms"] = round((time.monotonic() - t_test_disco) * 1000)
    diagnostics["test_co_promoted"] = test_co_promoted
    diagnostics["test_co_added"] = test_co_added
    diagnostics["test_demoted"] = test_demoted
    diagnostics["source_co_promoted"] = source_co_promoted
    diagnostics["source_co_added"] = source_co_added
    if test_co_promoted + test_co_added + test_demoted + source_co_promoted + source_co_added > 0:
        log.info(
            "recon.test_co_retrieval",
            promoted=test_co_promoted,
            added=test_co_added,
            demoted=test_demoted,
            source_files=len(all_source_paths),
            source_co_promoted=source_co_promoted,
            source_co_added=source_co_added,
        )

    # ── 5.7  Directory cohesion expansion ──────────────────────────
    #   When ≥2 non-test files from the same directory survive at
    #   FULL_FILE or MIN_SCAFFOLD, pull in remaining indexed files
    #   from that directory at one tier lower.  This captures package
    #   co-relevance: if tracking.py and compute.py from a package
    #   are relevant, config.py and utils.py likely are too.
    from collections import defaultdict as _defaultdict

    t_dir_cohesion = time.monotonic()
    _DIR_COHESION_MIN_FILES = 2  # ≥2 siblings needed to trigger expansion
    _DIR_COHESION_MAX_PER_DIR = 8  # cap siblings added per directory

    dir_best_tier: dict[str, OutputTier] = {}
    dir_tier_counts: dict[str, int] = _defaultdict(int)

    for fc in file_candidates:
        if fc.tier in (OutputTier.FULL_FILE, OutputTier.MIN_SCAFFOLD) and not _is_test_file(
            fc.path
        ):
            parent = str(PurePosixPath(fc.path).parent)
            dir_tier_counts[parent] += 1
            # Track the best tier present in this directory
            if parent not in dir_best_tier or fc.tier == OutputTier.FULL_FILE:
                dir_best_tier[parent] = fc.tier

    # Only expand directories with enough surviving source files
    dirs_to_expand = {
        d: tier
        for d, tier in dir_best_tier.items()
        if dir_tier_counts[d] >= _DIR_COHESION_MIN_FILES
    }

    dir_co_added = 0
    dir_co_dirs: list[str] = []
    if dirs_to_expand:
        existing_paths = {fc.path for fc in file_candidates}
        for ip in indexed_paths:
            parent = str(PurePosixPath(ip).parent)
            if parent not in dirs_to_expand:
                continue
            if ip in existing_paths:
                continue
            if _is_test_file(ip):
                continue  # test co-retrieval handles tests separately

            # Count how many we've already added for this directory
            if dir_co_dirs.count(parent) >= _DIR_COHESION_MAX_PER_DIR:
                continue

            anchor_tier = dirs_to_expand[parent]
            # Siblings get one tier below the anchor
            sibling_tier = (
                OutputTier.MIN_SCAFFOLD
                if anchor_tier == OutputTier.FULL_FILE
                else OutputTier.SUMMARY_ONLY
            )
            ref_scores = [c.combined_score for c in file_candidates if c.tier == sibling_tier]
            score = max(ref_scores, default=pin_floor) * 0.8
            new_fc = FileCandidate(
                path=ip,
                similarity=0.0,
                combined_score=score,
                graph_connected=True,  # structurally connected via directory
                artifact_kind=_classify_artifact(ip),
            )
            new_fc.tier = sibling_tier
            file_candidates.append(new_fc)
            existing_paths.add(ip)
            dir_co_dirs.append(parent)
            dir_co_added += 1

    diagnostics["dir_cohesion_ms"] = round((time.monotonic() - t_dir_cohesion) * 1000)
    diagnostics["dir_cohesion_added"] = dir_co_added
    diagnostics["dir_cohesion_dirs"] = len(set(dir_co_dirs))
    if dir_co_added > 0:
        log.info(
            "recon.dir_cohesion",
            added=dir_co_added,
            dirs=len(set(dir_co_dirs)),
        )

    # Build expand_reason for each candidate
    for fc in file_candidates:
        reasons: list[str] = []
        if fc.similarity > 0.5:
            reasons.append("high semantic similarity")
        elif fc.similarity > 0.3:
            reasons.append("moderate semantic match")
        if fc.term_match_count > 0:
            reasons.append(f"{fc.term_match_count} term matches")
        if fc.lexical_hit_count > 0:
            reasons.append(f"{fc.lexical_hit_count} text hits")
        if fc.has_explicit_mention:
            reasons.append("explicitly mentioned")
        if fc.graph_connected:
            reasons.append("structurally connected")
        if fc.path in linked_test_paths:
            reasons.append("test for surviving source")
        if fc.path in linked_source_paths:
            reasons.append("source imported by surviving test")
        # Check if this file was added by directory cohesion
        parent = str(PurePosixPath(fc.path).parent)
        if (
            parent in dirs_to_expand
            and fc.similarity == 0.0
            and not fc.has_explicit_mention
            and not any(
                r.startswith("test for") or r.startswith("source imported") for r in reasons
            )
        ):
            reasons.append("directory sibling")
        fc.expand_reason = "; ".join(reasons) if reasons else "embedding match"

    # 6. Noise metric
    scores = [fc.combined_score for fc in file_candidates]
    noise = compute_noise_metric(scores)
    session_info["noise_metric"] = round(noise, 4)
    session_info["include_map_repo"] = noise > 0.6  # high noise → include map_repo

    # Session window check (if available)
    try:
        session_mgr = app_ctx.session
        if hasattr(session_mgr, "pattern_detector"):
            pd = session_mgr.pattern_detector
            if hasattr(pd, "call_count"):
                session_info["session_call_count"] = pd.call_count
                # Early in session → include map_repo
                if pd.call_count <= 3:
                    session_info["include_map_repo"] = True
    except Exception:  # noqa: BLE001
        pass

    diagnostics["n_file_candidates"] = len(file_candidates)
    diagnostics["noise_metric"] = round(noise, 4)
    n_full = sum(1 for fc in file_candidates if fc.tier == OutputTier.FULL_FILE)
    n_scaffold = sum(1 for fc in file_candidates if fc.tier == OutputTier.MIN_SCAFFOLD)
    n_summary = sum(1 for fc in file_candidates if fc.tier == OutputTier.SUMMARY_ONLY)
    diagnostics["tiers"] = {
        "full_file": n_full,
        "min_scaffold": n_scaffold,
        "summary_only": n_summary,
    }
    diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)

    log.info(
        "recon.v6.pipeline_done",
        n_candidates=len(file_candidates),
        n_full=n_full,
        n_scaffold=n_scaffold,
        n_summary=n_summary,
        noise=round(noise, 4),
        total_ms=diagnostics["total_ms"],
    )

    return file_candidates, parsed, diagnostics, session_info


# ===================================================================
# Evidence string builder (compact single-string format)
# ===================================================================


def _build_evidence_string(cand: Any) -> str:
    """Build a compact evidence string from a HarvestCandidate.

    Format: ``"emb(0.82) term(config,model) lex(3) graph(→Config.validate)"``
    """
    parts: list[str] = []
    if cand.from_term_match:
        terms = ",".join(sorted(cand.matched_terms)[:3])
        parts.append(f"term({terms})")
    if cand.from_lexical:
        parts.append(f"lex({cand.lexical_hit_count})")
    if cand.from_explicit:
        parts.append("explicit")
    if cand.from_graph:
        graph_details = [e.detail for e in cand.evidence if e.category == "graph"]
        if graph_details:
            parts.append(f"graph({'; '.join(graph_details[:2])})")
        else:
            parts.append("graph")
    return " ".join(parts)


# ===================================================================
# Consecutive Recon Gating
# ===================================================================

# Minimum expand_reason length for 2nd consecutive call
_RECON_EXPAND_REASON_MIN = 250
# Minimum gate_reason length for 3rd+ consecutive call
_RECON_GATE_REASON_MIN = 500


def _check_recon_gate(
    app_ctx: Any,
    ctx: Any,
    *,
    expand_reason: str | None,
    pinned_paths: list[str] | None,
    gate_token: str | None,
    gate_reason: str | None,
) -> dict[str, Any] | None:
    """Enforce escalating requirements for consecutive recon calls.

    Returns a gate/error response dict if the call is blocked,
    or None if the call should proceed.

    Rules:
        1st call (counter=0): No restrictions.
        2nd call (counter=1): Must provide expand_reason (≥250 chars)
            AND pinned_paths with semantic anchors in query.
        3rd+ call (counter≥2): Must provide gate_token + gate_reason
            (≥500 chars) AND pinned_paths.

    Counter resets when write_source is called (tracked in middleware).
    """
    try:
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
    except Exception:  # noqa: BLE001
        return None

    consecutive = session.counters.get("recon_consecutive", 0)

    if consecutive == 0:
        # First call — no restrictions
        return None

    if consecutive == 1:
        # 2nd call — require expand_reason + pinned_paths
        errors: list[str] = []

        if not expand_reason or len(expand_reason.strip()) < _RECON_EXPAND_REASON_MIN:
            got = len((expand_reason or "").strip())
            errors.append(
                f"expand_reason must be at least {_RECON_EXPAND_REASON_MIN} "
                f"characters explaining what was missing from the first "
                f"recon call and what needs expansion (got {got})."
            )

        if not pinned_paths:
            errors.append(
                "pinned_paths is required on 2nd consecutive recon call. "
                "Pin specific files you want to expand on as semantic anchors."
            )

        if errors:
            return {
                "status": "blocked",
                "error": {
                    "code": "RECON_FOLLOW_UP_REQUIRES_JUSTIFICATION",
                    "message": " ".join(errors),
                },
                "agentic_hint": (
                    "This is your 2nd consecutive recon call without a "
                    "write_source in between.  You must provide:\n"
                    "1. expand_reason (≥250 chars) explaining what was "
                    "missing and what needs expansion\n"
                    "2. pinned_paths with specific files to anchor on\n"
                    "3. A task query with semantic anchors (symbol names, "
                    "file paths, or domain terms)\n\n"
                    "If you have enough context, proceed to write_source "
                    "instead of calling recon again."
                ),
                "consecutive_recon_calls": consecutive + 1,
            }
        return None

    # 3rd+ call — require gate token + gate_reason + pinned_paths
    from codeplane.mcp.gate import GateSpec

    if not pinned_paths:
        return {
            "status": "blocked",
            "error": {
                "code": "RECON_EXCESSIVE_REQUIRES_GATE",
                "message": (
                    "3rd+ consecutive recon call requires pinned_paths. "
                    "Pin specific files to anchor your search."
                ),
            },
            "agentic_hint": (
                f"This is recon call #{consecutive + 1} without a "
                "write_source in between.  You must provide pinned_paths "
                "along with gate_token and gate_reason."
            ),
            "consecutive_recon_calls": consecutive + 1,
        }

    if gate_token:
        # Validate the gate
        gate_reason_str = gate_reason if isinstance(gate_reason, str) else ""
        gate_result = session.gate_manager.validate(gate_token, gate_reason_str)
        if gate_result.ok:
            return None  # Gate passed — proceed
        # Gate validation failed — re-issue
        gate_spec = GateSpec(
            kind="recon_repeat",
            reason_min_chars=_RECON_GATE_REASON_MIN,
            reason_prompt=(
                f"This is recon call #{consecutive + 1}. Explain in ≥500 "
                "characters why your previous recon calls were insufficient "
                "and what specific context is still missing that cannot be "
                "obtained via read_source or search."
            ),
            expires_calls=3,
            message=(
                f"Recon call #{consecutive + 1} blocked. "
                f"Gate validation failed: {gate_result.error}"
            ),
        )
        gate_block = session.gate_manager.issue(gate_spec)
        return {
            "status": "blocked",
            "error": {
                "code": "GATE_VALIDATION_FAILED",
                "message": gate_result.error,
            },
            "gate": gate_block,
            "consecutive_recon_calls": consecutive + 1,
        }

    # No gate token — issue a new gate
    gate_spec = GateSpec(
        kind="recon_repeat",
        reason_min_chars=_RECON_GATE_REASON_MIN,
        reason_prompt=(
            f"This is recon call #{consecutive + 1}. Explain in ≥500 "
            "characters why your previous recon calls were insufficient "
            "and what specific context is still missing that cannot be "
            "obtained via read_source or search."
        ),
        expires_calls=3,
        message=(
            f"Recon call #{consecutive + 1} requires gate confirmation. "
            "You have called recon multiple times without making progress "
            "via write_source.  Consider using read_source to expand on "
            "specific files, or proceed to write_source with the context "
            "you already have."
        ),
    )
    gate_block = session.gate_manager.issue(gate_spec)
    return {
        "status": "blocked",
        "gate": gate_block,
        "agentic_hint": (
            f"This is recon call #{consecutive + 1} without a "
            "write_source in between.  You must:\n"
            "1. Provide gate_token from the gate block below\n"
            f"2. Provide gate_reason (≥{_RECON_GATE_REASON_MIN} chars) "
            "explaining why previous recon calls were insufficient\n"
            "3. Include pinned_paths\n\n"
            "Alternative: use read_source to expand on specific files "
            "from previous recon results, or proceed to write_source."
        ),
        "consecutive_recon_calls": consecutive + 1,
    }


# ===================================================================
# Tool Registration
# ===================================================================


def register_tools(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register recon tool with FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Recon: task-aware code discovery",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon(
        ctx: Context,  # noqa: ARG001
        task: str = Field(
            description=(
                "Natural language description of the task. "
                "Be specific: include symbol names, file paths, "
                "or domain terms when known.  The server extracts "
                "structured signals automatically."
            ),
        ),
        seeds: list[str] | None = Field(
            None,
            description=(
                "Optional explicit seed symbol names "
                "(e.g., ['IndexCoordinator', 'FactQueries']). "
                "Treated as high-priority explicit mentions."
            ),
        ),
        pinned_paths: list[str] | None = Field(
            None,
            description=(
                "Optional file paths to pin as high-confidence "
                "anchors (e.g., ['src/core/base_model.py']). "
                "Pinned files calibrate the inclusion band and "
                "always survive selection.  Use when you know "
                "specific files are relevant."
            ),
        ),
        expand_reason: str | None = Field(
            None,
            description=(
                "REQUIRED on 2nd+ consecutive recon call (before any "
                "write_source).  Explain what was missing from the "
                "first call, what needs expansion, and why (~250 chars "
                "min).  Must accompany pinned_paths and semantic "
                "anchors in the task query."
            ),
        ),
        gate_token: str | None = Field(
            None,
            description=(
                "Gate confirmation token from a previous recon gate "
                "block.  Required on 3rd+ consecutive recon call."
            ),
        ),
        gate_reason: str | None = Field(
            None,
            description=(
                "Justification for 3rd+ consecutive recon call (min "
                "500 chars).  Explain why 2 recon calls were "
                "insufficient and what specific context is still missing."
            ),
        ),
    ) -> dict[str, Any]:
        """Task-aware code discovery — ONE call, ALL context.

        Returns file-level results ranked by embedding similarity,
        with three fidelity tiers:
        - FULL_FILE: complete file content for top matches
        - MIN_SCAFFOLD: imports + signatures for middle tier
        - SUMMARY_ONLY: path + summary for tail

        Pipeline: parse_task → file-level embedding harvest →
        def-level enrichment → two-elbow tier assignment →
        content assembly → deliver.
        """
        recon_id = uuid.uuid4().hex[:12]
        t_total = time.monotonic()

        # ── Consecutive recon call gating ──
        # Env var bypass for benchmarking
        gate_bypass = os.environ.get("CODEPLANE_RECON_GATE_BYPASS", "") == "1"

        if not gate_bypass:
            gate_block = _check_recon_gate(
                app_ctx,
                ctx,
                expand_reason=expand_reason,
                pinned_paths=pinned_paths,
                gate_token=gate_token,
                gate_reason=gate_reason,
            )
            if gate_block is not None:
                return gate_block

        # Increment consecutive recon counter
        try:
            session = app_ctx.session_manager.get_or_create(ctx.session_id)
            session.counters["recon_consecutive"] = session.counters.get("recon_consecutive", 0) + 1
        except Exception:  # noqa: BLE001
            pass

        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root
        budget = _INTERNAL_BUDGET_BYTES

        # Check if file-level embeddings are available
        has_file_embeddings = (
            hasattr(coordinator, "_file_embedding")
            and coordinator._file_embedding is not None
            and coordinator._file_embedding.count > 0
        )

        if has_file_embeddings:
            # ── v6: File-centric pipeline ──
            (
                file_candidates,
                parsed_task,
                diagnostics,
                session_info,
            ) = await _file_centric_pipeline(
                app_ctx,
                task,
                seeds,
                pinned_paths=pinned_paths,
            )

            if not file_candidates:
                task_preview = task[:40] + "..." if len(task) > 40 else task
                failure_actions = _build_failure_actions(
                    parsed_task.primary_terms,
                    parsed_task.explicit_paths,
                )
                return {
                    "recon_id": recon_id,
                    "files": [],
                    "summary": f'No relevant files found for "{task_preview}"',
                    "agentic_hint": (
                        "No relevant files found. See 'next_actions' for recovery steps."
                    ),
                    "next_actions": failure_actions,
                }

            # ── Assemble file-centric response ──
            t_assemble = time.monotonic()
            files_output: list[dict[str, Any]] = []

            for fc in file_candidates:
                entry: dict[str, Any] = {
                    "path": fc.path,
                    "tier": fc.tier.value,
                    "similarity": round(fc.similarity, 4),
                    "combined_score": round(fc.combined_score, 4),
                    "evidence": fc.evidence_summary,
                    "expand_reason": fc.expand_reason,
                    "artifact_kind": fc.artifact_kind.value,
                }

                full_path = repo_root / fc.path

                if fc.tier == OutputTier.FULL_FILE:
                    # Include full file content
                    content = _read_unindexed_content(repo_root, fc.path)
                    if content is not None:
                        entry["content"] = content
                    elif full_path.exists():
                        try:
                            raw = full_path.read_bytes()
                            if b"\x00" not in raw[:512]:
                                text = raw.decode("utf-8", errors="replace")
                                # Apply larger budget for full_file tier
                                if len(text) > 50_000:
                                    entry["content"] = text[:50_000] + "\n... (truncated at 50KB)"
                                else:
                                    entry["content"] = text
                        except Exception:  # noqa: BLE001
                            pass
                    if full_path.exists():
                        entry["file_sha256"] = _compute_sha256(full_path)

                elif fc.tier == OutputTier.MIN_SCAFFOLD:
                    # Include scaffold: imports + signatures
                    try:
                        from codeplane.mcp.tools.files import _build_scaffold

                        scaffold = await _build_scaffold(app_ctx, fc.path, full_path)
                        entry["scaffold"] = scaffold
                    except Exception:  # noqa: BLE001
                        # Fallback: read first 100 lines
                        if full_path.exists():
                            content = _read_unindexed_content(repo_root, fc.path)
                            if content is not None:
                                lines = content.splitlines()[:100]
                                entry["scaffold_preview"] = "\n".join(lines)
                    if full_path.exists():
                        entry["file_sha256"] = _compute_sha256(full_path)

                else:
                    # SUMMARY_ONLY: just path + one-line summary
                    if full_path.exists():
                        try:
                            raw = full_path.read_bytes()
                            if b"\x00" not in raw[:512]:
                                text = raw.decode("utf-8", errors="replace")
                                first_line = text.split("\n", 1)[0].strip()
                                if first_line:
                                    entry["summary_line"] = first_line[:200]
                        except Exception:  # noqa: BLE001
                            pass

                files_output.append(entry)

            assemble_ms = round((time.monotonic() - t_assemble) * 1000)
            diagnostics["assemble_ms"] = assemble_ms

            # Group by tier for structured output
            full_files = [f for f in files_output if f["tier"] == "full_file"]
            scaffold_files = [f for f in files_output if f["tier"] == "min_scaffold"]
            summary_files = [f for f in files_output if f["tier"] == "summary_only"]

            # Build response
            n_files = len(files_output)
            paths_str = ", ".join(f["path"] for f in full_files[:5])
            if len(full_files) > 5:
                paths_str += f" (+{len(full_files) - 5} more)"

            response: dict[str, Any] = {
                "recon_id": recon_id,
                # Tier-based output (primary structure)
                "full_file": full_files,
                "min_scaffold": scaffold_files,
                "summary_only": summary_files,
                # Flat files list for backward compat
                "files": files_output,
                "summary": (
                    f"{len(full_files)} full file(s), "
                    f"{len(scaffold_files)} scaffold(s), "
                    f"{len(summary_files)} summary(ies) "
                    f"across {n_files} file(s): {paths_str}"
                ),
                "scoring_summary": {
                    "pipeline": "file_embed→def_enrich→two_elbow→tier→assemble",
                    "intent": parsed_task.intent.value,
                    "file_candidates": len(file_candidates),
                    "tiers": diagnostics.get("tiers", {}),
                    "parsed_terms": parsed_task.primary_terms[:8],
                    "noise_metric": session_info.get("noise_metric", 0),
                },
            }

            if parsed_task.explicit_paths:
                response["scoring_summary"]["explicit_paths"] = parsed_task.explicit_paths
            if parsed_task.negative_mentions:
                response["scoring_summary"]["negative_mentions"] = parsed_task.negative_mentions

            # Conditional mapRepo inclusion hint
            if session_info.get("include_map_repo"):
                response["include_map_repo"] = True

            diagnostics["total_ms"] = round((time.monotonic() - t_total) * 1000)
            response["diagnostics"] = diagnostics

            # Budget trimming
            response = _trim_to_budget(response, budget)

            # Agentic hint with expand_reason
            intent = parsed_task.intent
            top_paths = [f["path"] for f in full_files[:3]]
            top_paths_str = ", ".join(top_paths) if top_paths else "(none)"
            top_reasons = [
                f"{fc.path}: {fc.expand_reason}"
                for fc in file_candidates
                if fc.tier == OutputTier.FULL_FILE
            ][:3]
            reasons_str = "; ".join(top_reasons) if top_reasons else ""

            hint_parts = [
                f"Recon found {n_files} file(s) (intent: {intent.value}).",
                f"Full content ({len(full_files)}): {top_paths_str}.",
                f"Scaffolds ({len(scaffold_files)}), Summaries ({len(summary_files)}).",
            ]
            if reasons_str:
                hint_parts.append(f"Top files: {reasons_str}.")
            hint_parts.append(
                "Start with full_file entries — these have complete source. "
                "Use read_source to expand min_scaffold files as needed. "
                "Use checkpoint after edits."
            )
            if session_info.get("include_map_repo"):
                hint_parts.append("Signal is noisy — consider calling map_repo for orientation.")
            response["agentic_hint"] = " ".join(hint_parts)

            # Coverage hint
            if parsed_task.explicit_paths:
                found_paths = {f["path"] for f in files_output}
                missing_paths = [p for p in parsed_task.explicit_paths if p not in found_paths]
                if missing_paths:
                    response["coverage_hint"] = (
                        "Mentioned paths not found: "
                        f"{', '.join(missing_paths)}. "
                        "Use read_source to examine them directly."
                    )

            from codeplane.mcp.delivery import wrap_existing_response

            return wrap_existing_response(
                response,
                resource_kind="recon_result",
            )

        # ── Legacy fallback: def-centric pipeline (v5) ──
        # Used when file-level embeddings are not available
        return await _legacy_recon(
            app_ctx,
            task,
            seeds,
            pinned_paths=pinned_paths,
            recon_id=recon_id,
            t_total=t_total,
        )

    async def _legacy_recon(
        app_ctx: AppContext,
        task: str,
        seeds: list[str] | None,
        pinned_paths: list[str] | None,
        recon_id: str,
        t_total: float,
    ) -> dict[str, Any]:
        """Legacy def-centric pipeline (v5) — used when file embeddings unavailable."""
        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root
        depth = _INTERNAL_DEPTH
        budget = _INTERNAL_BUDGET_BYTES

        # Pipeline: parse, harvest, filter, score, select, bucket
        (
            selected_seeds,
            parsed_task,
            scored_all,
            diagnostics,
            gated,
            file_buckets,
        ) = await _select_seeds(app_ctx, task, seeds, pinned_paths=pinned_paths, min_seeds=3)

        if not selected_seeds:
            task_preview = task[:40] + "..." if len(task) > 40 else task
            failure_actions = _build_failure_actions(
                parsed_task.primary_terms,
                parsed_task.explicit_paths,
            )
            result: dict[str, Any] = {
                "recon_id": recon_id,
                "seeds": [],
                "summary": f'No seeds found for "{task_preview}"',
                "agentic_hint": (
                    "No relevant definitions found. See 'next_actions' for concrete recovery steps."
                ),
                "next_actions": failure_actions,
            }
            return result

        # Expand each seed (full source included)
        t_expand = time.monotonic()
        seed_results: list[dict[str, Any]] = []
        seed_paths: set[str] = set()

        terms = parsed_task.keywords
        scored_map: dict[str, float] = dict(scored_all)

        # Build file_id lookup for bucket assignment
        _file_id_by_uid: dict[str, int] = {}
        for uid, cand_obj in gated.items():
            if cand_obj.def_fact is not None:
                _file_id_by_uid[uid] = cand_obj.def_fact.file_id

        for seed_def in selected_seeds:
            expanded = await _expand_seed(
                app_ctx,
                seed_def,
                repo_root,
                depth=depth,
                task_terms=terms,
            )

            # Add compact metadata
            expanded["artifact_kind"] = _classify_artifact(expanded["path"]).value

            uid = seed_def.def_uid
            score = scored_map.get(uid, 0.0)
            expanded["score"] = round(score, 4)

            # Compact evidence string (replaces nested evidence dict)
            cand = gated.get(uid)
            if cand is not None:
                expanded["evidence"] = _build_evidence_string(cand)
                expanded["edit_score"] = round(cand.edit_score, 4)
                expanded["context_score"] = round(cand.context_score, 4)

            # Bucket assignment
            fid = _file_id_by_uid.get(uid)
            bucket = (
                file_buckets.get(fid, ReconBucket.supplementary)
                if fid
                else ReconBucket.supplementary
            )
            expanded["bucket"] = bucket.value

            seed_results.append(expanded)
            seed_paths.add(expanded["path"])

        # ── Unindexed file discovery ──
        # Find non-indexed files (.yaml, .md, .toml, .json, etc.)
        # matching query terms and add as supplementary seeds.
        indexed_paths: set[str] = set()
        with coordinator.db.session() as session:
            from codeplane.index._internal.indexing.graph import FactQueries

            fq = FactQueries(session)
            for frec in fq.list_files(limit=50000):
                indexed_paths.add(frec.path)

        unindexed_matches = _find_unindexed_files(app_ctx, parsed_task, indexed_paths)

        for upath, uscore in unindexed_matches:
            if upath in seed_paths:
                continue  # already included via def-level pipeline
            content = _read_unindexed_content(repo_root, upath)
            if content is None:
                continue
            full_path = repo_root / upath
            fname = PurePosixPath(upath).name
            u_entry: dict[str, Any] = {
                "def_uid": f"__file__{upath}",
                "path": upath,
                "symbol": f"file: {fname}",
                "kind": "file",
                "span": "1-*",
                "source": content,
                "artifact_kind": _classify_artifact(upath).value,
                "score": round(uscore, 4),
                "evidence": f"path_match({uscore:.2f})",
                "edit_score": 0.0,
                "context_score": round(uscore, 4),
                "bucket": ReconBucket.supplementary.value,
            }
            if full_path.exists():
                u_entry["file_sha256"] = _compute_sha256(full_path)
            seed_results.append(u_entry)
            seed_paths.add(upath)

        log.info(
            "recon.unindexed_files",
            matched=len(unindexed_matches),
            added=len([m for m in unindexed_matches if m[0] in seed_paths]),
        )

        expand_ms = round((time.monotonic() - t_expand) * 1000)

        # Import scaffolds
        scaffolds: list[dict[str, Any]] = []
        if depth >= 1:
            scaffolds = await _build_import_scaffolds(app_ctx, seed_paths, repo_root)

        # Group seeds by bucket for structured output
        edit_targets = [s for s in seed_results if s.get("bucket") == "edit_target"]
        context_seeds = [s for s in seed_results if s.get("bucket") == "context"]
        supplementary = [s for s in seed_results if s.get("bucket") == "supplementary"]

        # Assign within-bucket rank
        for group in (edit_targets, context_seeds, supplementary):
            for rank, seed_data in enumerate(group, 1):
                seed_data["bucket_rank"] = rank

        # Assemble response
        n_seeds = len(seed_results)
        n_files = len(seed_paths)
        seed_paths_list = sorted(seed_paths)
        paths_str = ", ".join(seed_paths_list[:5])
        if len(seed_paths_list) > 5:
            paths_str += f" (+{len(seed_paths_list) - 5} more)"

        response: dict[str, Any] = {
            "recon_id": recon_id,
            # Bucketed output — primary structure
            "edit_targets": edit_targets,
            "context": context_seeds,
            "supplementary": supplementary,
            # Flat seeds list kept for backward compatibility
            "seeds": seed_results,
            "summary": (
                f"{len(edit_targets)} edit target(s), "
                f"{len(context_seeds)} context, "
                f"{len(supplementary)} supplementary "
                f"across {n_files} file(s): {paths_str}"
            ),
        }

        if scaffolds:
            response["import_scaffolds"] = scaffolds

        # Scoring summary (always included — no verbosity knob)
        response["scoring_summary"] = {
            "pipeline": (
                "harvest->filter->score->dual_score->"
                "file_aggregate->anchor_band->saturate->bucket->expand"
            ),
            "intent": parsed_task.intent.value,
            "candidates_harvested": len(scored_all),
            "seeds_selected": n_seeds,
            "parsed_terms": parsed_task.primary_terms[:8],
            "buckets": {
                "edit_targets": len(edit_targets),
                "context": len(context_seeds),
                "supplementary": len(supplementary),
            },
        }
        if parsed_task.explicit_paths:
            response["scoring_summary"]["explicit_paths"] = parsed_task.explicit_paths
        if parsed_task.negative_mentions:
            response["scoring_summary"]["negative_mentions"] = parsed_task.negative_mentions

        # Diagnostics (always included in compact form)
        diagnostics["expand_ms"] = expand_ms
        diagnostics["total_ms"] = round((time.monotonic() - t_total) * 1000)
        response["diagnostics"] = diagnostics

        # Budget trimming (internal shaping, not agent-facing)
        response = _trim_to_budget(response, budget)

        # Agentic hint — facts about what was found + bucket-aware guidance
        intent = parsed_task.intent
        edit_paths = [s["path"] for s in edit_targets[:3]]
        edit_paths_str = ", ".join(edit_paths) if edit_paths else "(none)"
        response["agentic_hint"] = (
            f"Recon found {n_seeds} seed(s) "
            f"(intent: {intent.value}). "
            f"Edit targets ({len(edit_targets)}): {edit_paths_str}. "
            f"Context ({len(context_seeds)}), Supplementary ({len(supplementary)}). "
            f"Start with edit_targets — these are the files to modify. "
            f"Use write_source with file_sha256 to edit. "
            f"Use checkpoint after edits."
        )

        # Coverage hint
        if parsed_task.explicit_paths:
            missing_paths = [p for p in parsed_task.explicit_paths if p not in seed_paths]
            if missing_paths:
                response["coverage_hint"] = (
                    "Mentioned paths not in seeds: "
                    f"{', '.join(missing_paths)}. "
                    "Use read_source to examine them directly."
                )

        from codeplane.mcp.delivery import wrap_existing_response

        return wrap_existing_response(
            response,
            resource_kind="recon_result",
        )
