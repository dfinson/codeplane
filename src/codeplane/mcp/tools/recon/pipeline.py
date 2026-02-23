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
import math
import time
import uuid
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
    _harvest_embedding,
    _harvest_explicit,
    _harvest_graph,
    _harvest_lexical,
    _harvest_term_match,
    _merge_candidates,
)
from codeplane.mcp.tools.recon.models import (
    _INTERNAL_BUDGET_BYTES,
    _INTERNAL_DEPTH,
    ParsedTask,
    ReconBucket,
    _classify_artifact,
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
    compute_anchor_floor,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.index.models import DefFact
    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


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
    emb_candidates, term_candidates, lex_candidates, exp_candidates = await asyncio.gather(
        _harvest_embedding(app_ctx, parsed),
        _harvest_term_match(app_ctx, parsed),
        _harvest_lexical(app_ctx, parsed),
        _harvest_explicit(app_ctx, parsed, explicit_seeds),
    )
    diagnostics["harvest_ms"] = round((time.monotonic() - t_harvest) * 1000)

    # 3. Merge
    merged = _merge_candidates(emb_candidates, term_candidates, lex_candidates, exp_candidates)

    diagnostics["harvested"] = {
        "embedding": len(emb_candidates),
        "term_match": len(term_candidates),
        "lexical": len(lex_candidates),
        "explicit": len(exp_candidates),
        "merged": len(merged),
    }

    log.debug(
        "recon.merged",
        total=len(merged),
        embedding=len(emb_candidates),
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

    # 4. Enrich with structural metadata + artifact kind
    await _enrich_candidates(app_ctx, merged)

    # 5. Intent-aware filter pipeline (query-conditioned)
    gated = _apply_filters(merged, parsed)

    if not gated:
        log.info("recon.filter_empty", pre_filter=len(merged))
        # Fall back to ungated top embedding candidates
        gated = {
            uid: cand
            for uid, cand in merged.items()
            if cand.from_embedding and cand.embedding_similarity >= 0.3
        }
        if not gated:
            diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
            return [], parsed, [], diagnostics, {}, {}

    diagnostics["post_filter"] = len(gated)

    # 6. Score
    scored = _score_candidates(gated, parsed)

    if not scored:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics, {}, {}

    # 7. Aggregate to file level (legacy — still used for file_score)
    file_ranked = _aggregate_to_files(scored, gated)

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
    _patience = math.ceil(math.log2(1 + len(scored)))  # no-anchor patience window

    if floor_score > 0:
        # ── Anchor case: gate on floor_score (unchanged) ──
        consecutive_empty = 0
        for idx in range(n_files, len(file_ranked_dual)):
            _fid, _fscore, _, _, fdefs = file_ranked_dual[idx]
            if _fscore >= floor_score:
                n_files = idx + 1
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= _K_SATURATE:
                    break
    else:
        # ── No-anchor case: score-decay patience ──
        # Include files while their score is within a reasonable fraction
        # of the top file's score.  Any surviving file with a meaningful
        # score should be included — not just graph-discovered ones.
        top_score = file_ranked_dual[0][1] if file_ranked_dual else 0.0
        # Floor: 15% of top score — below this the file is unlikely relevant.
        score_floor = top_score * 0.15
        consecutive_below = 0
        for idx in range(n_files, len(file_ranked_dual)):
            _fid, _fscore, _, _, fdefs = file_ranked_dual[idx]

            if _fscore >= score_floor:
                n_files = idx + 1
                consecutive_below = 0
            else:
                consecutive_below += 1
                if consecutive_below >= _patience:
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
        "supplementary": sum(
            1 for b in file_buckets.values() if b == ReconBucket.supplementary
        ),
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
# Evidence string builder (compact single-string format)
# ===================================================================


def _build_evidence_string(cand: Any) -> str:
    """Build a compact evidence string from a HarvestCandidate.

    Format: ``"emb(0.82) term(config,model) lex(3) graph(→Config.validate)"``
    """
    parts: list[str] = []
    if cand.from_embedding:
        parts.append(f"emb({cand.embedding_similarity:.2f})")
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
    ) -> dict[str, Any]:
        """Task-aware code discovery — ONE call, ALL context.

        Returns full source for every relevant definition found,
        with compressed metadata (evidence, callees, callers, siblings).
        No follow-up calls needed.

        Pipeline: parse_task -> 5 harvesters -> filter -> score ->
        file aggregation -> dual scoring -> bucketing -> expand ->
        compress -> deliver.
        """
        recon_id = uuid.uuid4().hex[:12]
        t_total = time.monotonic()

        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root
        depth = _INTERNAL_DEPTH
        budget = _INTERNAL_BUDGET_BYTES

        # Pipeline: parse, harvest, filter, score, select, bucket
        selected_seeds, parsed_task, scored_all, diagnostics, gated, file_buckets = (
            await _select_seeds(
                app_ctx, task, seeds, pinned_paths=pinned_paths, min_seeds=3
            )
        )

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
            bucket = file_buckets.get(fid, ReconBucket.supplementary) if fid else ReconBucket.supplementary
            expanded["bucket"] = bucket.value

            seed_results.append(expanded)
            seed_paths.add(expanded["path"])

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
