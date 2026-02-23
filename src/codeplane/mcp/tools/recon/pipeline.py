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
    _classify_artifact,
)
from codeplane.mcp.tools.recon.parsing import parse_task
from codeplane.mcp.tools.recon.scoring import (
    _aggregate_to_files,
    _apply_filters,
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
) -> tuple[list[DefFact], ParsedTask, list[tuple[str, float]], dict[str, Any], dict[str, Any]]:
    """Select seed definitions using the full harvest -> filter -> score pipeline.

    v4 pipeline — no arbitrary caps:
    1. Parse task -> ParsedTask (with intent classification)
    2. Run 4 harvesters in parallel
    3. Merge candidates (accumulate evidence)
    4. Graph harvester (1-hop from top merged)
    5. Enrich with structural metadata + artifact kind
    6. Apply query-conditioned filter pipeline
    7. Score with bounded features + artifact-kind weights
    8. Aggregate to file level
    9. Anchor-calibrated file inclusion (MAD-based band + saturation)
    10. Multi-seed per file: all defs above global def-score median

    Args:
        pinned_paths: Explicit file paths supplied by the agent as
            high-confidence anchors.  Not inferred — only what the caller
            passes.  Files only, not directories.

    Returns:
        (seeds, parsed_task, scored_candidates, diagnostics, gated_candidates)
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
        return [], parsed, [], diagnostics, {}

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
            return [], parsed, [], diagnostics, {}

    diagnostics["post_filter"] = len(gated)

    # 6. Score
    scored = _score_candidates(gated, parsed)

    if not scored:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics, {}

    # 7. Aggregate to file level
    file_ranked = _aggregate_to_files(scored, gated)

    # Diagnostics: file ranking with paths (for debugging gap cutoff)
    coordinator = app_ctx.coordinator
    with coordinator.db.session() as session:
        from codeplane.index._internal.indexing.graph import FactQueries

        fq = FactQueries(session)
        _diag_ranking = []
        for i, (fid, fs, fdefs) in enumerate(file_ranked[:20]):
            frec = fq.get_file(fid)
            fpath = frec.path if frec else f"?{fid}"
            _diag_ranking.append(
                {"rank": i + 1, "path": fpath, "score": round(fs, 4), "n_defs": len(fdefs)}
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
    file_score_values = [fs for _, fs, _ in file_ranked]
    anchor_indices = [i for i, (fid, _, _) in enumerate(file_ranked) if fid in anchor_fids]
    floor_score = compute_anchor_floor(file_score_values, anchor_indices)
    n_band = sum(1 for fs in file_score_values if fs >= floor_score) if floor_score > 0 else 0
    n_files = max(min(min_seeds, len(file_ranked)), n_band)

    # Safety net: ensure ALL anchor files are in the surviving window
    # (normally already inside the band, but handles edge cases)
    for idx in range(n_files, len(file_ranked)):
        fid, _, _ = file_ranked[idx]
        if fid in anchor_fids:
            file_ranked[n_files], file_ranked[idx] = (
                file_ranked[idx],
                file_ranked[n_files],
            )
            n_files += 1

    # 8c. Saturation pass — extend inclusion while files contribute seeds
    #     Anchor case: gate on floor_score (anchor-calibrated band).
    #     No-anchor case: evidence-gain patience.  Instead of thresholding
    #     on any score statistic (median, gap, etc.) we check whether each
    #     successive file actually contributes an accepted def.  We stop
    #     once we have enough accepted defs AND we've gone `patience`
    #     consecutive files without gaining any.
    #
    #     patience = ceil(log2(1 + n_candidates)) — grows slowly with
    #     problem size, is not tuned per repo, and is defined on evidence
    #     gain rather than score geometry.
    all_def_scores = sorted((s for _, s in scored), reverse=True)
    def_score_median = all_def_scores[len(all_def_scores) // 2] if all_def_scores else 0.0

    _K_SATURATE = 3  # anchor-case consecutive-miss limit
    _patience = math.ceil(math.log2(1 + len(scored)))  # no-anchor patience window

    if floor_score > 0:
        # ── Anchor case: gate on floor_score (unchanged) ──
        consecutive_empty = 0
        for idx in range(n_files, len(file_ranked)):
            _fid, _fscore, fdefs = file_ranked[idx]
            if _fscore >= floor_score:
                n_files = idx + 1
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= _K_SATURATE:
                    break
    else:
        # ── No-anchor case: evidence-gain patience ──
        # Without anchors, there's nothing to calibrate a score floor
        # against.  Instead, we stop based on *evidence gain*: does
        # each successive file contribute a def with genuine structural
        # corroboration?
        #
        # Acceptance test: file's best def must have been graph-
        # discovered (from_graph) — meaning it was found by walking
        # call/import edges from already-relevant defs.  This is a
        # true structural dependency signal that embedding similarity
        # and term matching alone cannot provide.
        #
        # The initial window (top min_seeds files) is unconditionally
        # accepted — their ranking quality justifies inclusion.
        # Beyond that, gain requires structural evidence.

        # Initial window: all counted as contributing
        accepted_seed_count = n_files

        last_gain_rank = n_files - 1  # rank of last file that added a seed
        for idx in range(n_files, len(file_ranked)):
            _fid, _fscore, fdefs = file_ranked[idx]
            # Does this file contribute any graph-corroborated def?
            file_contributes = False
            for uid, _dscore in fdefs:
                cand = gated.get(uid)
                if cand is not None and cand.def_fact is not None and cand.from_graph:
                    file_contributes = True
                    break

            if file_contributes:
                n_files = idx + 1
                accepted_seed_count += 1
                last_gain_rank = idx
            else:
                # Check stop: enough seeds AND exhausted patience
                if accepted_seed_count >= min_seeds and (idx - last_gain_rank) >= _patience:
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

    # 9. Multi-seed per file selection
    #    Phase 1: best def per surviving file (file diversity)
    #    Phase 2: additional defs above global def-score median (depth)
    #    Stage-aware test seed cap
    seeds: list[DefFact] = []
    selected_uids: set[str] = set()
    test_seed_count = 0
    # Test seeds: uncapped if test-driven, otherwise 1/3 of total (no hard max)
    max_test_ratio = 1.0 if parsed.is_test_driven else 0.33

    # def_score_median already computed in step 8c

    # Phase 1: one seed per file (diversity)
    for _fid, _fscore, fdefs in file_ranked[:n_files]:
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
    for _fid, _fscore, fdefs in file_ranked[:n_files]:
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
    diagnostics["file_ranked"] = len(file_ranked)
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
    return seeds, parsed, scored, diagnostics, gated_export


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
        file aggregation -> anchor band + saturation -> expand ->
        compress -> deliver.
        """
        recon_id = uuid.uuid4().hex[:12]
        t_total = time.monotonic()

        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root
        depth = _INTERNAL_DEPTH
        budget = _INTERNAL_BUDGET_BYTES

        # Pipeline: parse, harvest, filter, score, select
        selected_seeds, parsed_task, scored_all, diagnostics, gated = await _select_seeds(
            app_ctx, task, seeds, pinned_paths=pinned_paths, min_seeds=3
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

            seed_results.append(expanded)
            seed_paths.add(expanded["path"])

        expand_ms = round((time.monotonic() - t_expand) * 1000)

        # Import scaffolds
        scaffolds: list[dict[str, Any]] = []
        if depth >= 1:
            scaffolds = await _build_import_scaffolds(app_ctx, seed_paths, repo_root)

        # Assemble response
        n_seeds = len(seed_results)
        n_files = len(seed_paths)
        seed_paths_list = sorted(seed_paths)
        paths_str = ", ".join(seed_paths_list[:5])
        if len(seed_paths_list) > 5:
            paths_str += f" (+{len(seed_paths_list) - 5} more)"

        response: dict[str, Any] = {
            "recon_id": recon_id,
            "seeds": seed_results,
            "summary": f"{n_seeds} seed(s) across {n_files} file(s): {paths_str}",
        }

        if scaffolds:
            response["import_scaffolds"] = scaffolds

        # Scoring summary (always included — no verbosity knob)
        response["scoring_summary"] = {
            "pipeline": "harvest->filter->score->file_aggregate->anchor_band->saturate->expand",
            "intent": parsed_task.intent.value,
            "candidates_harvested": len(scored_all),
            "seeds_selected": n_seeds,
            "parsed_terms": parsed_task.primary_terms[:8],
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

        # Agentic hint — facts about what was found + universal next step
        intent = parsed_task.intent
        response["agentic_hint"] = (
            f"Recon found {n_seeds} seed(s) "
            f"(intent: {intent.value}) across: {paths_str}. "
            f"Source included for all seeds. "
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
