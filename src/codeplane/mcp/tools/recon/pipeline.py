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
    find_gap_cutoff,
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
    9. Gap-based file cutoff (distribution-relative, no upper bound)
    10. Multi-seed per file: all defs above global def-score median

    Returns:
        (seeds, parsed_task, scored_candidates, diagnostics, gated_candidates)
    """
    diagnostics: dict[str, Any] = {}
    t0 = time.monotonic()

    # 1. Parse task
    parsed = parse_task(task)
    diagnostics["intent"] = parsed.intent.value

    # 1b. Validate candidate directory paths against the filesystem.
    #     Parsing is pure (no I/O); OS validation happens here.
    validated_dirs: list[str] = []
    if parsed.explicit_dirs:
        repo_root_for_dirs = app_ctx.coordinator.repo_root
        for d in parsed.explicit_dirs:
            if (repo_root_for_dirs / d.rstrip("/")).is_dir():
                validated_dirs.append(d)
        # Replace with validated subset (frozen dataclass → rebuild)
        if len(validated_dirs) != len(parsed.explicit_dirs):
            parsed = ParsedTask(
                raw=parsed.raw,
                intent=parsed.intent,
                primary_terms=parsed.primary_terms,
                secondary_terms=parsed.secondary_terms,
                explicit_paths=parsed.explicit_paths,
                explicit_dirs=validated_dirs,
                explicit_symbols=parsed.explicit_symbols,
                keywords=parsed.keywords,
                query_text=parsed.query_text,
                negative_mentions=parsed.negative_mentions,
                is_stacktrace_driven=parsed.is_stacktrace_driven,
                is_test_driven=parsed.is_test_driven,
            )

    log.debug(
        "recon.parsed_task",
        intent=parsed.intent.value,
        primary=parsed.primary_terms[:5],
        secondary=parsed.secondary_terms[:3],
        paths=parsed.explicit_paths,
        dirs=parsed.explicit_dirs,
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

    # 5b. Compute directory file counts for scoring (size-scaled boost).
    explicit_dir_sizes: dict[str, int] | None = None
    if parsed.explicit_dirs:
        coordinator = app_ctx.coordinator
        with coordinator.db.session() as session:
            from codeplane.index._internal.indexing.graph import FactQueries

            fq = FactQueries(session)
            explicit_dir_sizes = {}
            for d in parsed.explicit_dirs:
                n = fq.count_files_in_dir(d)
                if n > 0:
                    explicit_dir_sizes[d] = n
        if explicit_dir_sizes:
            log.debug("recon.dir_sizes", dirs=explicit_dir_sizes)

    # 6. Score
    scored = _score_candidates(gated, parsed, explicit_dir_sizes=explicit_dir_sizes)

    if not scored:
        diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
        return [], parsed, [], diagnostics, {}

    # 7. Aggregate to file level
    file_ranked = _aggregate_to_files(scored, gated)

    # 8. Gap-based file cutoff (distribution-relative, NO upper bound)
    file_score_values = [fs for _, fs, _ in file_ranked]
    n_files = find_gap_cutoff(
        file_score_values,
        min_keep=min(min_seeds, len(file_ranked)),
    )

    # 8b. Anchor explicit-path files past the cutoff.
    #     If the user named a file path in the task, it survives regardless
    #     of score.  Resolve parsed.explicit_paths → file_ids directly
    #     (NOT from_explicit, which is too broad — includes symbol matches).
    explicit_fids: set[int] = set()
    if parsed.explicit_paths:
        coordinator = app_ctx.coordinator
        with coordinator.db.session() as session:
            from codeplane.index._internal.indexing.graph import FactQueries

            fq = FactQueries(session)
            for epath in parsed.explicit_paths:
                frec = fq.get_file_by_path(epath)
                if frec is not None and frec.id is not None:
                    explicit_fids.add(frec.id)

    surviving_fids = {fid for fid, _, _ in file_ranked[:n_files]}
    anchored = 0
    for idx in range(n_files, len(file_ranked)):
        fid, _fs, _fdefs = file_ranked[idx]
        if fid in explicit_fids and fid not in surviving_fids:
            # Swap this file into the surviving window
            file_ranked[n_files + anchored], file_ranked[idx] = (
                file_ranked[idx],
                file_ranked[n_files + anchored],
            )
            surviving_fids.add(fid)
            anchored += 1
    n_files += anchored
    if anchored:
        log.info("recon.explicit_anchor", anchored=anchored, n_files=n_files)
        diagnostics["explicit_anchored"] = anchored

    # 8c. Anchor directory files past the cutoff (threshold-based).
    #     Unlike explicit-path anchoring (unconditional), directory anchoring
    #     is softened: only files scoring >= 80% of the cutoff score survive.
    #     This avoids flooding from large directories.
    dir_anchored = 0
    if explicit_dir_sizes:
        cutoff_score = file_ranked[n_files - 1][1] if n_files > 0 else 0.0
        dir_threshold = cutoff_score * 0.8

        dir_fids: set[int] = set()
        coordinator = app_ctx.coordinator
        with coordinator.db.session() as session:
            from codeplane.index._internal.indexing.graph import FactQueries

            fq = FactQueries(session)
            for d in explicit_dir_sizes:
                for fid in fq.list_file_ids_in_dir(d):
                    dir_fids.add(fid)

        for idx in range(n_files, len(file_ranked)):
            fid, fs, _fdefs = file_ranked[idx]
            if fid in dir_fids and fid not in surviving_fids and fs >= dir_threshold:
                file_ranked[n_files + dir_anchored], file_ranked[idx] = (
                    file_ranked[idx],
                    file_ranked[n_files + dir_anchored],
                )
                surviving_fids.add(fid)
                dir_anchored += 1
        n_files += dir_anchored
        if dir_anchored:
            log.info(
                "recon.dir_anchor",
                anchored=dir_anchored,
                n_files=n_files,
                threshold=round(dir_threshold, 4),
            )
            diagnostics["dir_anchored"] = dir_anchored

    # 9. Multi-seed per file selection
    #    Phase 1: best def per surviving file (file diversity)
    #    Phase 2: additional defs above global def-score median (depth)
    #    Stage-aware test seed cap
    seeds: list[DefFact] = []
    selected_uids: set[str] = set()
    test_seed_count = 0
    # Test seeds: uncapped if test-driven, otherwise 1/3 of total (no hard max)
    max_test_ratio = 1.0 if parsed.is_test_driven else 0.33

    # Global def-score median for phase 2 threshold
    all_def_scores = sorted((s for _, s in scored), reverse=True)
    def_score_median = all_def_scores[len(all_def_scores) // 2] if all_def_scores else 0.0

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
    diagnostics["gap_cutoff_files"] = n_files
    diagnostics["file_ranked"] = len(file_ranked)
    diagnostics["def_score_median"] = round(def_score_median, 4)

    log.info(
        "recon.seeds_selected",
        count=len(seeds),
        gap_files=n_files,
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
    ) -> dict[str, Any]:
        """Task-aware code discovery — ONE call, ALL context.

        Returns full source for every relevant definition found,
        with compressed metadata (evidence, callees, callers, siblings).
        No follow-up calls needed.

        Pipeline: parse_task -> 5 harvesters -> filter -> score ->
        file aggregation -> gap-based cutoff -> expand with source ->
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
            app_ctx, task, seeds, min_seeds=3
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
                    "No relevant definitions found. "
                    "See 'next_actions' for concrete recovery steps."
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
            "pipeline": "harvest->filter->score->file_aggregate->gap_cutoff->expand",
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
