"""Pipeline orchestrator and MCP tool registration.

Single Responsibility: Wire the full recon pipeline together and register
the ``recon`` tool with FastMCP.

This is the only module that imports from all sub-modules — it composes
them but adds no domain logic of its own (Dependency Inversion principle).
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
    _summarize_recon,
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
    _harvest_lexical,
    _harvest_term_match,
    _merge_candidates,
)
from codeplane.mcp.tools.recon.models import (
    _DEFAULT_BUDGET_BYTES,
    _DEFAULT_DEPTH,
    _MAX_BUDGET_BYTES,
    TaskIntent,
    _classify_artifact,
)
from codeplane.mcp.tools.recon.parsing import parse_task
from codeplane.mcp.tools.recon.scoring import (
    _apply_filters,
    _score_candidates,
    find_elbow,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.index.models import DefFact
    from codeplane.mcp.context import AppContext
    from codeplane.mcp.tools.recon.models import ParsedTask

log = structlog.get_logger(__name__)


# ===================================================================
# Seed Selection Pipeline
# ===================================================================


async def _select_seeds(
    app_ctx: AppContext,
    task: str,
    explicit_seeds: list[str] | None = None,
    *,
    min_seeds: int = 3,
    max_seeds: int = 15,
) -> tuple[list[DefFact], ParsedTask, list[tuple[str, float]], dict[str, Any], dict[str, Any]]:
    """Select seed definitions using the full harvest -> filter -> score pipeline.

    Pipeline:
    1. Parse task -> ParsedTask (with intent classification)
    2. Run 4 harvesters in parallel
    3. Merge candidates (accumulate evidence)
    4. Enrich with structural metadata + artifact kind
    5. Apply query-conditioned filter pipeline (OR gate + negative gating)
    6. Score with bounded features + artifact-kind weights
    7. Find elbow for dynamic seed count
    8. Enforce file diversity + stage-aware test seed cap

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

    # 4. Enrich with structural metadata + artifact kind
    await _enrich_candidates(app_ctx, merged)

    # 5. Intent-aware filter pipeline (now query-conditioned)
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

    # 7. Elbow detection
    score_values = [s for _, s in scored]
    n_seeds = find_elbow(score_values, min_seeds=min_seeds, max_seeds=max_seeds)

    # 8. File diversity: max 2 seeds per file
    #    Stage-aware test seed selection (Section 5):
    #    - Cap test seeds unless task is test-driven
    #    - Tests pass the filter pipeline but are capped at seed selection
    seeds: list[DefFact] = []
    file_counts: dict[int, int] = {}
    test_seed_count = 0
    max_test_seeds = n_seeds if parsed.is_test_driven else max(2, n_seeds // 3)

    for uid, _score in scored:
        if len(seeds) >= n_seeds:
            break
        cand = gated[uid]
        if cand.def_fact is None:
            continue

        # Test seed cap (Section 5: stage-aware, not global penalty)
        if cand.is_test and test_seed_count >= max_test_seeds:
            continue

        fid = cand.def_fact.file_id
        if file_counts.get(fid, 0) >= 2:
            continue
        file_counts[fid] = file_counts.get(fid, 0) + 1
        if cand.is_test:
            test_seed_count += 1
        seeds.append(cand.def_fact)

    diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)
    diagnostics["seeds_selected"] = len(seeds)
    diagnostics["elbow_k"] = n_seeds

    log.info(
        "recon.seeds_selected",
        count=len(seeds),
        elbow=n_seeds,
        scored_total=len(scored),
        names=[s.name for s in seeds],
        intent=parsed.intent.value,
        total_ms=diagnostics["total_ms"],
    )

    # Return gated candidates for per-seed evidence breakdown (Section 7)
    gated_export = {
        uid: cand for uid, cand in gated.items() if any(s.def_uid == uid for s in seeds)
    }
    return seeds, parsed, scored, diagnostics, gated_export


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
        ctx: Context,
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
        depth: int = Field(
            default=_DEFAULT_DEPTH,
            ge=0,
            le=3,
            description=(
                "Graph expansion depth. 0 = seeds only (no callees/callers/imports). "
                ">=1 = expand seeds with callees, callers, imports, and siblings. "
                "Default 2."
            ),
        ),
        budget: int = Field(
            default=_DEFAULT_BUDGET_BYTES,
            le=_MAX_BUDGET_BYTES,
            description="Response size budget in bytes.",
        ),
        max_seeds: int = Field(
            default=15,
            ge=1,
            le=20,
            description=(
                "Upper bound on seed count. Actual count is determined "
                "dynamically by score distribution (elbow detection)."
            ),
        ),
        verbosity: str = Field(
            default="normal",
            description=(
                "Response verbosity: 'minimal' (seeds only, no evidence), "
                "'normal' (seeds + scoring summary), 'detailed' (+ evidence + diagnostics)."
            ),
        ),
    ) -> dict[str, Any]:
        """Task-aware code discovery in a single call.

        Pipeline: parse_task (with intent classification) ->
        4 harvesters (embedding, term-match, lexical, explicit) ->
        intent-aware filter pipeline -> bounded scoring ->
        elbow detection -> graph expansion (with siblings) ->
        evidence-annotated structured response.

        Returns seeds with evidence, context files, scoring summary,
        diagnostics, and file_sha256 for write_source compatibility.
        """
        recon_id = uuid.uuid4().hex[:12]
        t_total = time.monotonic()

        coordinator = app_ctx.coordinator
        repo_root = coordinator.repo_root

        # Pipeline: parse, harvest, filter, score, select
        selected_seeds, parsed_task, scored_all, diagnostics, gated = await _select_seeds(
            app_ctx, task, seeds, min_seeds=3, max_seeds=max_seeds
        )

        if not selected_seeds:
            task_preview = task[:40] + "..." if len(task) > 40 else task
            # Failure-mode next actions (Section 7)
            failure_actions = _build_failure_actions(
                parsed_task.primary_terms,
                parsed_task.explicit_paths,
            )
            result: dict[str, Any] = {
                "recon_id": recon_id,
                "seeds": [],
                "summary": _summarize_recon(0, 0, 0, 0, 0, task_preview),
                "agentic_hint": (
                    "No relevant definitions found. See 'next_actions' for concrete recovery steps."
                ),
                "next_actions": failure_actions,
            }
            if verbosity == "detailed":
                result["diagnostics"] = diagnostics
            return result

        # Expand each seed
        t_expand = time.monotonic()
        seed_results: list[dict[str, Any]] = []
        seed_paths: set[str] = set()
        total_callees = 0
        total_callers = 0
        total_import_defs = 0
        total_siblings = 0

        terms = parsed_task.keywords

        # Build per-seed evidence from scored candidates + gated pool
        scored_map: dict[str, float] = dict(scored_all)
        budget_remaining = budget

        for seed_def in selected_seeds:
            expanded = await _expand_seed(
                app_ctx,
                seed_def,
                repo_root,
                depth=depth,
                task_terms=terms,
                budget_remaining=budget_remaining,
            )

            # Add artifact_kind to each seed result
            expanded["artifact_kind"] = _classify_artifact(expanded["path"]).value

            # Per-seed evidence breakdown (Section 7)
            if verbosity != "minimal":
                uid = seed_def.def_uid
                score = scored_map.get(uid, 0.0)
                expanded["seed_score"] = round(score, 4)

                # Build evidence summary from the gated candidate
                cand = gated.get(uid)
                if cand is not None:
                    evidence_breakdown: dict[str, Any] = {
                        "relevance_score": round(cand.relevance_score, 4),
                        "hub_score": cand.hub_score,
                        "evidence_axes": cand.evidence_axes,
                        "sources": [],
                    }
                    if cand.from_embedding:
                        evidence_breakdown["sources"].append(
                            f"embedding (sim={cand.embedding_similarity:.3f})"
                        )
                    if cand.from_term_match:
                        evidence_breakdown["sources"].append(
                            f"term_match ({', '.join(sorted(cand.matched_terms)[:3])})"
                        )
                    if cand.from_lexical:
                        evidence_breakdown["sources"].append(
                            f"lexical ({cand.lexical_hit_count} hits)"
                        )
                    if cand.from_explicit:
                        evidence_breakdown["sources"].append("explicit")
                    expanded["evidence"] = evidence_breakdown

            seed_results.append(expanded)
            seed_paths.add(expanded["path"])
            total_callees += len(expanded.get("callees", []))
            total_callers += len(expanded.get("callers", []))
            total_import_defs += len(expanded.get("import_defs", []))
            total_siblings += len(expanded.get("siblings", []))

            # Update budget_remaining for fan-out brake
            from codeplane.mcp.tools.recon.assembly import _estimate_bytes

            budget_remaining = max(0, budget - _estimate_bytes({"seeds": seed_results}))

        expand_ms = round((time.monotonic() - t_expand) * 1000)

        # Import scaffolds
        scaffolds: list[dict[str, Any]] = []
        if depth >= 1:
            scaffolds = await _build_import_scaffolds(app_ctx, seed_paths, repo_root)

        # Assemble response
        task_preview = task[:40] + "..." if len(task) > 40 else task
        response: dict[str, Any] = {
            "recon_id": recon_id,
            "seeds": seed_results,
            "summary": _summarize_recon(
                len(seed_results),
                total_callees,
                total_callers,
                total_import_defs,
                len(scaffolds),
                task_preview,
            ),
        }

        if scaffolds:
            response["import_scaffolds"] = scaffolds

        # Scoring summary (normal + detailed)
        if verbosity != "minimal":
            response["scoring_summary"] = {
                "pipeline": "harvest->filter(query-conditioned)->score(bounded)->elbow",
                "intent": parsed_task.intent.value,
                "is_test_driven": parsed_task.is_test_driven,
                "is_stacktrace_driven": parsed_task.is_stacktrace_driven,
                "candidates_harvested": len(scored_all),
                "seeds_selected": len(selected_seeds),
                "parsed_terms": parsed_task.primary_terms[:8],
                "explicit_paths": parsed_task.explicit_paths,
                "explicit_symbols": parsed_task.explicit_symbols[:5],
            }
            if parsed_task.negative_mentions:
                response["scoring_summary"]["negative_mentions"] = parsed_task.negative_mentions

        # Diagnostics (detailed only)
        if verbosity == "detailed":
            diagnostics["expand_ms"] = expand_ms
            diagnostics["total_ms"] = round((time.monotonic() - t_total) * 1000)
            response["diagnostics"] = diagnostics

        # Budget trimming
        response = _trim_to_budget(response, budget)

        # Deterministic agentic hint based on intent
        seed_paths_list = sorted(seed_paths)
        paths_str = ", ".join(seed_paths_list[:5])
        if len(seed_paths_list) > 5:
            paths_str += f" (+{len(seed_paths_list) - 5} more)"

        intent = parsed_task.intent
        if intent == TaskIntent.debug:
            action_hint = (
                "Focus on the seed with highest score. "
                "Check callers for how the buggy code is invoked. "
                "Use read_source on caller paths for full context."
            )
        elif intent == TaskIntent.implement:
            action_hint = (
                "Use write_source with file_sha256 from seed source to edit. "
                "Check siblings for patterns to follow. "
                "Use checkpoint after edits."
            )
        elif intent == TaskIntent.refactor:
            action_hint = (
                "Check callers to understand impact of changes. "
                "Use refactor_rename for symbol renames across files. "
                "Use checkpoint after edits."
            )
        elif intent == TaskIntent.understand:
            action_hint = (
                "Read seed sources for implementation details. "
                "Check callees for dependencies and callers for usage. "
                "Use read_source for additional spans."
            )
        else:
            action_hint = (
                "Use write_source with file_sha256 from seed source to edit. "
                "Use read_source for additional spans. "
                "Use checkpoint after edits."
            )

        response["agentic_hint"] = (
            f"Recon found {len(seed_results)} seed(s) "
            f"(intent: {intent.value}) across: {paths_str}. "
            f"{action_hint}"
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

        # Follow-up pointers (structured suggestions)
        follow_ups: list[dict[str, str]] = []
        if parsed_task.explicit_paths:
            for p in parsed_task.explicit_paths:
                if p not in seed_paths:
                    follow_ups.append(
                        {
                            "action": "read_source",
                            "target": p,
                            "reason": "mentioned in task but not in seeds",
                        }
                    )
        if follow_ups:
            response["follow_up"] = follow_ups

        from codeplane.mcp.delivery import wrap_existing_response

        return wrap_existing_response(
            response,
            resource_kind="recon_result",
        )
