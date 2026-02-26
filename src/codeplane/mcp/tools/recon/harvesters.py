"""Harvesters — four independent candidate sources + merge + enrich.

Single Responsibility: Each harvester queries one data source and produces
``HarvestCandidate`` dicts.  ``_merge_candidates`` combines them.
``_enrich_candidates`` resolves missing DefFacts and populates structural
metadata.

Open/Closed: New harvesters can be added without modifying existing ones.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from codeplane.mcp.tools.recon.models import (
    EvidenceRecord,
    FileCandidate,
    HarvestCandidate,
    _classify_artifact,
    _is_barrel_file,
    _is_test_file,
)

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext
    from codeplane.mcp.tools.recon.models import ParsedTask

log = structlog.get_logger(__name__)


# ===================================================================
# Harvester A2: File-level embedding (Jina v2 base — PRIMARY)
# ===================================================================


async def _harvest_file_embedding(
    app_ctx: AppContext,
    parsed: ParsedTask,
    *,
    top_k: int = 100,
) -> list[FileCandidate]:
    """File-level embedding harvest using Jina v2 base.

    This is the PRIMARY retrieval mechanism for the recon pipeline.
    Returns FileCandidate objects ranked by similarity.

    The query is the full task text (no multi-view needed — Jina v2 base
    handles 8192 tokens and captures whole-file semantics).
    """
    coordinator = app_ctx.coordinator

    # Use task text as-is for file-level query (no view engineering)
    query_text = parsed.query_text or parsed.raw

    file_results = coordinator.query_file_embeddings(query_text, top_k=top_k)

    candidates: list[FileCandidate] = []
    for path, sim in file_results:
        cand = FileCandidate(
            path=path,
            similarity=sim,
            combined_score=sim,  # initial score — enriched later
            artifact_kind=_classify_artifact(path),
        )
        candidates.append(cand)

    log.debug(
        "recon.harvest.file_embedding",
        count=len(candidates),
        top5=[(c.path, round(c.similarity, 3)) for c in candidates[:5]],
    )
    return candidates


# ===================================================================
# Harvester B: Term match (SQL LIKE)
# ===================================================================


async def _harvest_term_match(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester B: DefFact term matching via SQL LIKE with IDF weighting.

    Terms that match many definitions get lower weight (low IDF), while
    specific terms that match few definitions get higher weight.
    """
    import math

    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    all_terms = parsed.primary_terms + parsed.secondary_terms
    if not all_terms:
        return candidates

    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for term in all_terms:
            matching_defs = fq.find_defs_matching_term(term, limit=200)
            # IDF weight: rare terms score higher, ubiquitous terms score lower
            idf = 1.0 / math.log1p(len(matching_defs)) if matching_defs else 0.0
            for d in matching_defs:
                uid = d.def_uid
                if uid not in candidates:
                    candidates[uid] = HarvestCandidate(
                        def_uid=uid,
                        def_fact=d,
                        from_term_match=True,
                        term_idf_score=idf,
                    )
                else:
                    candidates[uid].from_term_match = True
                    candidates[uid].term_idf_score += idf
                    if candidates[uid].def_fact is None:
                        candidates[uid].def_fact = d
                candidates[uid].matched_terms.add(term)
                candidates[uid].evidence.append(
                    EvidenceRecord(
                        category="term_match",
                        detail=f"name matches term '{term}'",
                        score=0.5 * idf,
                    )
                )

    log.debug(
        "recon.harvest.term_match",
        count=len(candidates),
        terms=len(all_terms),
    )
    return candidates


# ===================================================================
# Harvester C: Lexical (Tantivy full-text search)
# ===================================================================


async def _harvest_lexical(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester C: Tantivy full-text search -> map hits to containing DefFact.

    Searches file content via Tantivy, then maps each line hit to the
    DefFact whose span contains that line.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    terms = parsed.primary_terms[:16]  # Use more terms; Tantivy handles multi-term well
    if not terms:
        return candidates

    if coordinator._lexical is None:
        return candidates

    query = " ".join(terms)
    search_results = coordinator._lexical.search(query, limit=500)

    if not search_results.results:
        return candidates

    # Group hits by file path
    file_hits: dict[str, list[int]] = {}
    for hit in search_results.results:
        if hit.file_path not in file_hits:
            file_hits[hit.file_path] = []
        file_hits[hit.file_path].append(hit.line)

    # Map line hits to containing DefFacts
    with coordinator.db.session() as session:
        fq = FactQueries(session)

        for file_path, lines in list(file_hits.items())[:50]:
            frec = fq.get_file_by_path(file_path)
            if frec is None or frec.id is None:
                continue

            defs_in_file = fq.list_defs_in_file(frec.id, limit=200)
            if not defs_in_file:
                continue

            for line in lines:
                for d in defs_in_file:
                    if d.start_line <= line <= d.end_line:
                        uid = d.def_uid
                        if uid not in candidates:
                            candidates[uid] = HarvestCandidate(
                                def_uid=uid,
                                def_fact=d,
                                from_lexical=True,
                                lexical_hit_count=1,
                                evidence=[
                                    EvidenceRecord(
                                        category="lexical",
                                        detail=f"full-text hit in {file_path}:{line}",
                                        score=0.4,
                                    )
                                ],
                            )
                        else:
                            candidates[uid].from_lexical = True
                            candidates[uid].lexical_hit_count += 1
                            if candidates[uid].def_fact is None:
                                candidates[uid].def_fact = d
                        break

    log.debug(
        "recon.harvest.lexical",
        count=len(candidates),
        files_searched=len(file_hits),
    )
    return candidates


# ===================================================================
# Harvester D: Explicit mentions (paths + symbols from task text)
# ===================================================================


async def _harvest_explicit(
    app_ctx: AppContext,
    parsed: ParsedTask,
    explicit_seeds: list[str] | None = None,
    auto_seeds: list[str] | None = None,
) -> dict[str, HarvestCandidate]:
    """Harvester D: Explicit mentions (paths + symbols from task text).

    Resolves file paths to defs and symbol names to DefFacts.
    Agent-provided seeds bypass the dual-signal gate (trusted input).
    Auto-seeds (inferred from embedding top files) get lower confidence
    and do NOT set from_explicit — they contribute to graph expansion
    but don't inflate file-level explicit scores.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    # D0: Auto-seed names (inferred, lower confidence)
    #     from_explicit=False — they won't get the explicit RRF boost.
    #     score=0.5 — weaker evidence than agent-provided seeds.
    #     Still enter merged pool so graph harvester can expand from them.
    if auto_seeds:
        for name in auto_seeds:
            d = await coordinator.get_def(name)
            if d is not None and d.def_uid not in candidates:
                candidates[d.def_uid] = HarvestCandidate(
                    def_uid=d.def_uid,
                    def_fact=d,
                    from_explicit=False,
                    from_term_match=True,  # counts as a term-match signal
                    evidence=[
                        EvidenceRecord(
                            category="auto_seed",
                            detail=f"auto-seed '{name}' (hub-ranked)",
                            score=0.5,
                        )
                    ],
                )

    # D1: Explicit seed names provided by the agent
    if explicit_seeds:
        for name in explicit_seeds:
            d = await coordinator.get_def(name)
            if d is not None:
                candidates[d.def_uid] = HarvestCandidate(
                    def_uid=d.def_uid,
                    def_fact=d,
                    from_explicit=True,
                    evidence=[
                        EvidenceRecord(
                            category="explicit",
                            detail=f"agent-provided seed '{name}'",
                            score=1.0,
                        )
                    ],
                )

    # D2: File paths mentioned in the task text
    if parsed.explicit_paths:
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for epath in parsed.explicit_paths:
                frec = fq.get_file_by_path(epath)
                if frec is None or frec.id is None:
                    continue
                defs_in = fq.list_defs_in_file(frec.id, limit=50)
                def_scored = []
                for d in defs_in:
                    hub = min(fq.count_callers(d.def_uid), 30)
                    def_scored.append((d, hub))
                def_scored.sort(key=lambda x: (-x[1], x[0].def_uid))
                for d, _hub in def_scored[:5]:
                    if d.def_uid not in candidates:
                        candidates[d.def_uid] = HarvestCandidate(
                            def_uid=d.def_uid,
                            def_fact=d,
                            from_explicit=True,
                            evidence=[
                                EvidenceRecord(
                                    category="explicit",
                                    detail=f"in mentioned path '{epath}'",
                                    score=0.9,
                                )
                            ],
                        )
                    else:
                        candidates[d.def_uid].from_explicit = True

    # D3: Index-validated symbol extraction from task text.
    #
    # Prior version set from_explicit=True on raw regex matches, which
    # bypassed the dual-signal gate and caused hub-file pollution (50-80%
    # of queries).  This version validates each regex-extracted symbol
    # against the index (coordinator.get_def) — only real definitions
    # pass.  Validated symbols use from_explicit=True but a lower evidence
    # score (0.7) than agent-provided seeds (1.0), reflecting lower
    # confidence from automated extraction vs intentional agent input.
    if parsed.explicit_symbols:
        d3_count = 0
        for sym in parsed.explicit_symbols:
            if sym in {c.def_fact.name for c in candidates.values() if c.def_fact}:
                continue  # Already found via D1 or D2
            d = await coordinator.get_def(sym)
            if d is not None and d.def_uid not in candidates:
                candidates[d.def_uid] = HarvestCandidate(
                    def_uid=d.def_uid,
                    def_fact=d,
                    from_explicit=True,
                    evidence=[
                        EvidenceRecord(
                            category="explicit",
                            detail=f"task-extracted symbol '{sym}'",
                            score=0.7,
                        )
                    ],
                )
                d3_count += 1
        if d3_count:
            log.debug("recon.harvest.explicit.d3", validated=d3_count)

    log.debug(
        "recon.harvest.explicit",
        count=len(candidates),
        paths=len(parsed.explicit_paths),
        symbols=len(parsed.explicit_symbols),
    )
    return candidates


# ===================================================================
# Merge & Enrich — combine harvester outputs + resolve DefFacts
# ===================================================================


def _merge_candidates(
    *harvests: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Merge candidates from multiple harvesters, accumulating evidence."""
    merged: dict[str, HarvestCandidate] = {}

    for harvest in harvests:
        for uid, cand in harvest.items():
            if uid not in merged:
                merged[uid] = cand
            else:
                existing = merged[uid]
                existing.from_term_match = existing.from_term_match or cand.from_term_match
                existing.from_lexical = existing.from_lexical or cand.from_lexical
                existing.from_explicit = existing.from_explicit or cand.from_explicit
                existing.from_graph = existing.from_graph or cand.from_graph
                existing.matched_terms |= cand.matched_terms
                existing.lexical_hit_count += cand.lexical_hit_count
                existing.evidence.extend(cand.evidence)
                if existing.def_fact is None and cand.def_fact is not None:
                    existing.def_fact = cand.def_fact

    return merged


async def _enrich_candidates(
    app_ctx: AppContext,
    candidates: dict[str, HarvestCandidate],
) -> None:
    """Resolve missing DefFact objects and populate structural metadata.

    Mutates candidates in-place.

    Performance: Uses batch file path resolution and hub score caching
    to minimize repeated queries (Section 8).
    """
    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.models import File as FileModel

    coordinator = app_ctx.coordinator

    # Resolve missing DefFacts in one session
    missing_uids = [uid for uid, c in candidates.items() if c.def_fact is None]
    if missing_uids:
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for uid in missing_uids:
                d = fq.get_def(uid)
                if d is not None:
                    candidates[uid].def_fact = d

    # Remove candidates that still lack a DefFact
    dead = [uid for uid, c in candidates.items() if c.def_fact is None]
    for uid in dead:
        del candidates[uid]

    # Populate structural metadata with caching
    fid_path_cache: dict[int, str] = {}
    hub_score_cache: dict[str, int] = {}  # Cache within this recon call

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        # Batch resolve all unique file_ids to paths
        unique_fids = {c.def_fact.file_id for c in candidates.values() if c.def_fact}
        for fid in unique_fids:
            if fid not in fid_path_cache:
                frec = session.get(FileModel, fid)
                fid_path_cache[fid] = frec.path if frec else ""

        for uid, cand in list(candidates.items()):
            if cand.def_fact is None:
                continue
            d = cand.def_fact

            # Hub score with caching (Section 8)
            if uid not in hub_score_cache:
                hub_score_cache[uid] = fq.count_callers(uid)
            cand.hub_score = hub_score_cache[uid]

            cand.file_path = fid_path_cache.get(d.file_id, "")
            cand.is_test = _is_test_file(cand.file_path)
            cand.is_barrel = _is_barrel_file(cand.file_path)
            cand.artifact_kind = _classify_artifact(cand.file_path)

    # --- Populate structural link fields ---
    # Identify "anchor" candidates: explicit mentions
    anchor_uids: set[str] = set()
    anchor_file_ids: set[int] = set()
    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue
        if cand.from_explicit:
            anchor_uids.add(uid)
            anchor_file_ids.add(cand.def_fact.file_id)

    if anchor_uids:
        # shares_file_with_seed: candidate is in the same file as an anchor
        for uid, cand in candidates.items():
            if uid in anchor_uids or cand.def_fact is None:
                continue
            if cand.def_fact.file_id in anchor_file_ids:
                cand.shares_file_with_seed = True

        # is_callee_of_top / is_imported_by_top: traverse graph edges
        with coordinator.db.session() as session:
            fq = FactQueries(session)

            # Collect callees of anchors
            anchor_callee_uids: set[str] = set()
            for anchor_uid in anchor_uids:
                anchor_cand = candidates[anchor_uid]
                if anchor_cand.def_fact is None:
                    continue
                callees = fq.list_callees_in_scope(
                    anchor_cand.def_fact.file_id,
                    anchor_cand.def_fact.start_line,
                    anchor_cand.def_fact.end_line,
                    limit=50,
                )
                for c in callees:
                    anchor_callee_uids.add(c.def_uid)

            # Collect defs from files imported by anchor files
            anchor_import_uids: set[str] = set()
            seen_import_files: set[str] = set()
            for anchor_uid in anchor_uids:
                anchor_cand = candidates[anchor_uid]
                if anchor_cand.def_fact is None:
                    continue
                anchor_path = fid_path_cache.get(anchor_cand.def_fact.file_id, "")
                if not anchor_path or anchor_path in seen_import_files:
                    continue
                seen_import_files.add(anchor_path)
                imports = fq.list_imports(anchor_cand.def_fact.file_id, limit=50)
                for imp in imports:
                    if imp.resolved_path:
                        imp_file = fq.get_file_by_path(imp.resolved_path)
                        if imp_file is not None and imp_file.id is not None:
                            imp_defs = fq.list_defs_in_file(imp_file.id, limit=50)
                            for idef in imp_defs:
                                anchor_import_uids.add(idef.def_uid)

            # Apply to candidates
            for uid, cand in candidates.items():
                if uid in anchor_uids:
                    continue
                if uid in anchor_callee_uids:
                    cand.is_callee_of_top = True
                if uid in anchor_import_uids:
                    cand.is_imported_by_top = True


# ===================================================================
# Harvester E: Graph walk (structural adjacency from top candidates)
# ===================================================================

# Edge weights for graph quality scoring — callee > caller > sibling.
# Quality = edge_weight / seed_rank, so seed #1 callee = 1.0, seed #2 caller = 0.425, etc.
_EDGE_WEIGHT_CALLEE = 1.0
_EDGE_WEIGHT_CALLER = 0.85
_EDGE_WEIGHT_SIBLING = 0.7

# Single performance budget — how many graph-discovered candidates to keep.
_GRAPH_BUDGET = 60


async def _harvest_graph(
    app_ctx: AppContext,
    merged: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester E: Walk 1-hop graph edges from top merged candidates.

    Takes the strongest merged candidates (by evidence count and embedding
    similarity) as graph seeds, then discovers structurally adjacent defs
    via callees, callers (as references), and same-file siblings.

    All discovered edges compete in a single ranked pool scored by
    edge_type × (1 / seed_position).  The top ``_GRAPH_BUDGET`` edges
    are kept — no per-category caps.  High-degree hub symbols naturally
    produce many low-quality edges that fall below the cutoff.

    Runs AFTER merge of A-D harvesters but BEFORE enrichment and filtering.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    if not merged:
        return candidates

    # Select graph seeds: top candidates by evidence axes + embedding sim
    seed_uids = _select_graph_seeds(merged)
    if not seed_uids:
        return candidates

    # Resolve DefFacts for seeds that need them
    seeds_with_facts: list[tuple[str, HarvestCandidate]] = []
    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for uid in seed_uids:
            cand = merged[uid]
            if cand.def_fact is None:
                d = fq.get_def(uid)
                if d is not None:
                    cand.def_fact = d
            if cand.def_fact is not None:
                seeds_with_facts.append((uid, cand))

    if not seeds_with_facts:
        return candidates

    # Collect all edges into one ranked pool: (def_uid, def_fact, quality, edge_type, detail)
    EdgeInfo = tuple[str, object, float, str, str]  # uid, DefFact|None, quality, type, detail
    raw_edges: list[EdgeInfo] = []

    with coordinator.db.session() as session:
        fq = FactQueries(session)
        from codeplane.index.models import File as FileModel

        for seed_idx, (seed_uid, seed_cand) in enumerate(seeds_with_facts, 1):
            seed_def = seed_cand.def_fact
            assert seed_def is not None

            # (a) Callees: defs referenced within this seed's body
            callees = fq.list_callees_in_scope(
                seed_def.file_id,
                seed_def.start_line,
                seed_def.end_line,
                limit=30,
            )
            for callee in callees:
                if callee.def_uid == seed_uid:
                    continue
                quality = _EDGE_WEIGHT_CALLEE / seed_idx
                raw_edges.append(
                    (
                        callee.def_uid,
                        callee,
                        quality,
                        "graph",
                        f"callee of {seed_def.name}",
                    )
                )

            # (b) Callers: defs that reference this seed (via RefFact)
            refs = fq.list_refs_by_def_uid(seed_uid, limit=30)
            caller_file_ids: set[int] = set()
            for ref in refs:
                if ref.file_id == seed_def.file_id:
                    continue
                if ref.file_id in caller_file_ids:
                    continue
                caller_file_ids.add(ref.file_id)
                caller_defs = fq.list_defs_in_file(ref.file_id, limit=200)
                for cd in caller_defs:
                    if (
                        ref.start_line is not None
                        and cd.start_line <= ref.start_line <= cd.end_line
                    ):
                        quality = _EDGE_WEIGHT_CALLER / seed_idx
                        raw_edges.append(
                            (
                                cd.def_uid,
                                cd,
                                quality,
                                "graph",
                                f"caller of {seed_def.name}",
                            )
                        )
                        break

            # (c) Same-file siblings: other key defs in seed's file
            frec = session.get(FileModel, seed_def.file_id)
            if frec is not None and frec.id is not None:
                sibling_defs = fq.list_defs_in_file(frec.id, limit=50)
                for sd in sibling_defs:
                    if sd.def_uid == seed_uid:
                        continue
                    if sd.kind not in ("function", "method", "class"):
                        continue
                    quality = _EDGE_WEIGHT_SIBLING / seed_idx
                    raw_edges.append(
                        (
                            sd.def_uid,
                            sd,
                            quality,
                            "graph",
                            f"sibling of {seed_def.name} in {frec.path}",
                        )
                    )

    # Deduplicate: keep max quality per uid
    best_edges: dict[str, EdgeInfo] = {}
    for edge in raw_edges:
        uid = edge[0]
        if uid not in best_edges or edge[2] > best_edges[uid][2]:
            best_edges[uid] = edge

    # Sort by quality, take top BUDGET
    ranked = sorted(best_edges.values(), key=lambda e: -e[2])[:_GRAPH_BUDGET]

    # Convert to HarvestCandidate or reinforce merged
    for uid, def_fact, quality, category, detail in ranked:
        if uid in merged:
            existing = merged[uid]
            existing.from_graph = True
            existing.graph_quality = max(existing.graph_quality, quality)
            # Only add evidence once
            if not any(e.category == "graph" for e in existing.evidence):
                existing.evidence.append(
                    EvidenceRecord(category=category, detail=detail, score=quality)
                )
            continue
        if uid in candidates:
            # Keep the higher quality
            if quality > candidates[uid].graph_quality:
                candidates[uid].graph_quality = quality
            continue
        candidates[uid] = HarvestCandidate(
            def_uid=uid,
            def_fact=def_fact,  # type: ignore[arg-type]
            from_graph=True,
            graph_quality=quality,
            evidence=[EvidenceRecord(category=category, detail=detail, score=quality)],
        )

    log.debug(
        "recon.harvest.graph",
        count=len(candidates),
        seeds_used=len(seeds_with_facts),
    )
    return candidates


# ===================================================================
# Harvester F: Import-chain discovery (dependency + dependent tracing)
# ===================================================================

# Budget constants for import harvester
_IMPORT_MAX_SEEDS = 15
_IMPORT_MAX_DEPS_PER_SEED = 10  # Forward imports (dependencies)
_IMPORT_MAX_DEPENDENTS_PER_SEED = 8  # Reverse imports (files importing seed)
_IMPORT_MAX_TOTAL = 80


async def _harvest_imports(
    app_ctx: AppContext,
    merged: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester F: Import-chain discovery from top merged candidates.

    Traces *resolved* import edges in both directions from seed files:

    (a) Forward deps  — files that the seed file imports
    (b) Reverse deps  — files whose ``resolved_path`` points at the seed
    (c) ``__init__.py`` barrels in each seed's package directory
    (d) Test file pattern matching (``src/X.py`` → ``tests/test_X.py``)

    These candidates capture the "structural neighbourhood" that embedding
    search and term-match cannot reach — configuration files, re-export
    barrels, and cross-cut infrastructure modules.

    Runs AFTER graph harvester (E) so that callee / caller edges are already
    covered; this harvester fills the remaining import-only gaps.
    """
    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.models import File as FileModel
    from codeplane.index.models import ImportFact

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    if not merged:
        return candidates

    # Select seeds: top candidates by score (reuse graph-seed logic)
    seed_uids = _select_graph_seeds(merged)[:_IMPORT_MAX_SEEDS]
    if not seed_uids:
        return candidates

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        # Resolve seed file paths
        seed_file_paths: dict[int, str] = {}  # file_id → path
        seed_file_ids: set[int] = set()
        for uid in seed_uids:
            cand = merged[uid]
            if cand.def_fact is None:
                d = fq.get_def(uid)
                if d is not None:
                    cand.def_fact = d
            if cand.def_fact is None:
                continue
            fid = cand.def_fact.file_id
            if fid not in seed_file_paths:
                frec = session.get(FileModel, fid)
                if frec is not None:
                    seed_file_paths[fid] = frec.path
                    seed_file_ids.add(fid)

        if not seed_file_ids:
            return candidates

        # Collect unique seed file paths for reverse lookup
        seed_paths_set = set(seed_file_paths.values())

        # (a) Forward deps: files imported by seed files
        seen_import_fids: set[int] = set()
        for fid in seed_file_ids:
            if len(candidates) >= _IMPORT_MAX_TOTAL:
                break
            imports = fq.list_imports(fid, limit=_IMPORT_MAX_DEPS_PER_SEED)
            for imp in imports:
                if not imp.resolved_path:
                    continue
                imp_file = fq.get_file_by_path(imp.resolved_path)
                if imp_file is None or imp_file.id is None:
                    continue
                if imp_file.id in seed_file_ids or imp_file.id in seen_import_fids:
                    continue
                seen_import_fids.add(imp_file.id)
                _add_file_defs_as_candidates(
                    fq,
                    imp_file,
                    candidates,
                    merged,
                    category="import_forward",
                    detail=f"imported by {seed_file_paths.get(fid, '?')}",
                    score=0.45,
                    graph_quality=0.5,
                    limit=5,
                )

        # (b) Reverse deps: files that import seed files
        if seed_paths_set:
            from sqlmodel import col, select

            reverse_stmt = (
                select(ImportFact.file_id)
                .where(col(ImportFact.resolved_path).in_(list(seed_paths_set)))
                .distinct()
            )
            reverse_fids = list(session.exec(reverse_stmt).all())
            for rfid in reverse_fids[: _IMPORT_MAX_DEPENDENTS_PER_SEED * len(seed_file_ids)]:
                if len(candidates) >= _IMPORT_MAX_TOTAL:
                    break
                if rfid in seed_file_ids:
                    continue
                rfile = session.get(FileModel, rfid)
                if rfile is None:
                    continue
                _add_file_defs_as_candidates(
                    fq,
                    rfile,
                    candidates,
                    merged,
                    category="import_reverse",
                    detail=f"imports a seed file ({rfile.path})",
                    score=0.40,
                    graph_quality=0.4,
                    limit=3,
                )

        # (c) __init__.py barrels + conftest.py in seed directories
        seen_dirs: set[str] = set()
        for seed_path in seed_paths_set:
            import os

            dir_path = os.path.dirname(seed_path)
            if not dir_path or dir_path in seen_dirs:
                continue
            seen_dirs.add(dir_path)
            for special_name in ("__init__.py", "conftest.py"):
                barrel_path = f"{dir_path}/{special_name}"
                if barrel_path in seed_paths_set:
                    continue
                barrel_file = fq.get_file_by_path(barrel_path)
                if barrel_file is None or barrel_file.id is None:
                    continue
                _add_file_defs_as_candidates(
                    fq,
                    barrel_file,
                    candidates,
                    merged,
                    category="import_barrel",
                    detail=f"package init/conftest in {dir_path}",
                    score=0.35,
                    graph_quality=0.3,
                    limit=3,
                )

        # (d) Test file pattern matching
        for seed_path in seed_paths_set:
            if len(candidates) >= _IMPORT_MAX_TOTAL:
                break
            test_paths = _infer_test_paths(seed_path)
            for tp in test_paths:
                tf = fq.get_file_by_path(tp)
                if tf is not None and tf.id is not None:
                    _add_file_defs_as_candidates(
                        fq,
                        tf,
                        candidates,
                        merged,
                        category="import_test",
                        detail=f"test file for {seed_path}",
                        score=0.35,
                        graph_quality=0.35,
                        limit=3,
                    )

    log.debug(
        "recon.harvest.imports",
        count=len(candidates),
        seed_files=len(seed_file_ids),
    )
    return candidates


def _add_file_defs_as_candidates(
    fq: object,  # FactQueries
    file_rec: object,  # File model
    candidates: dict[str, HarvestCandidate],
    merged: dict[str, HarvestCandidate],
    *,
    category: str,
    detail: str,
    score: float,
    graph_quality: float = 0.0,
    limit: int = 5,
) -> None:
    """Add top defs from a file as import-discovered candidates."""
    from codeplane.index._internal.indexing.graph import FactQueries as _FQ

    fq_typed: _FQ = fq  # type: ignore[assignment]
    file_id = getattr(file_rec, "id", None)
    if file_id is None:
        return

    defs = fq_typed.list_defs_in_file(file_id, limit=50)
    # Prefer functions/classes/methods over variables
    important = [d for d in defs if d.kind in ("function", "method", "class", "module")]
    rest = [d for d in defs if d.kind not in ("function", "method", "class", "module")]
    selected = (important + rest)[:limit]

    for d in selected:
        if d.def_uid in merged:
            # Reinforce existing candidate
            existing = merged[d.def_uid]
            if not existing.from_graph:
                existing.from_graph = True
                existing.graph_quality = max(existing.graph_quality, graph_quality)
                existing.evidence.append(
                    EvidenceRecord(
                        category=category,
                        detail=detail,
                        score=score,
                    )
                )
            continue
        if d.def_uid in candidates:
            # Keep higher quality
            candidates[d.def_uid].graph_quality = max(
                candidates[d.def_uid].graph_quality, graph_quality
            )
            continue
        candidates[d.def_uid] = HarvestCandidate(
            def_uid=d.def_uid,
            def_fact=d,
            from_graph=True,
            graph_quality=graph_quality,
            evidence=[
                EvidenceRecord(
                    category=category,
                    detail=detail,
                    score=score,
                )
            ],
        )


def _infer_test_paths(source_path: str) -> list[str]:
    """Infer candidate test file paths from a source file path.

    Handles common patterns:
    - src/foo/bar.py → tests/foo/test_bar.py
    - src/foo/bar.py → tests/test_bar.py
    - lib/foo.py → tests/test_foo.py
    """
    import os

    parts = source_path.split("/")
    basename = parts[-1]

    # Skip if already a test file
    if basename.startswith("test_") or basename == "conftest.py":
        return []

    # Strip .py extension
    if not basename.endswith(".py"):
        return []
    name_stem = basename[:-3]
    test_name = f"test_{name_stem}.py"

    candidates: list[str] = []

    # Pattern 1: mirror path under tests/
    # src/foo/bar.py → tests/foo/test_bar.py
    if len(parts) >= 2:
        # Try dropping first dir (src/) and placing under tests/
        sub_path = "/".join(parts[1:-1])
        if sub_path:
            candidates.append(f"tests/{sub_path}/{test_name}")
        candidates.append(f"tests/{test_name}")

    # Pattern 2: same directory
    dir_path = os.path.dirname(source_path)
    if dir_path:
        candidates.append(f"{dir_path}/{test_name}")

    return candidates


def _select_graph_seeds(
    merged: dict[str, HarvestCandidate],
) -> list[str]:
    """Select the best candidates from merged pool to use as graph seeds.

    Returns up to 15 seeds.  This limits DB query count (performance),
    not semantic quality — the single ``_GRAPH_BUDGET`` controls output.

    Prioritizes candidates with:
    1. Multiple evidence axes (found by multiple harvesters)
    2. Explicit mentions
    """
    scored: list[tuple[str, float]] = []
    for uid, cand in merged.items():
        # Score: axes * 2 + explicit_bonus
        score = cand.evidence_axes * 2.0 + (2.0 if cand.from_explicit else 0.0)
        scored.append((uid, score))

    scored.sort(key=lambda x: -x[1])
    return [uid for uid, _ in scored[:15]]


# ===================================================================
# File-level enrichment — combine file candidates with def-level signals
# ===================================================================


def _enrich_file_candidates(
    file_candidates: list[FileCandidate],
    def_candidates: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> list[FileCandidate]:
    """Combine file-level embedding with def-level signals via Reciprocal Rank Fusion.

    Instead of additive bonuses on top of the embedding score, each signal
    source produces an independent ranked list.  RRF fuses them so that
    a file ranked high by *any* source gets a fair shot, and files found
    by multiple sources are naturally boosted.

    RRF(d) = sum over sources of weight_s / (k + rank_s(d))

    where k=60 (standard constant, dampens the influence of very high
    individual ranks).

    Sources (with weights):
      1. Embedding  — file_candidates ranked by similarity (weight 2.0×)
      2. Term match — files ranked by IDF-weighted term score
      3. Lexical    — files ranked by aggregated lexical hit count
      4. Graph      — files ranked by graded edge quality
      5. Explicit   — agent-mentioned paths (always rank 1)
      6. Path token — files ranked by query-term overlap with path components

    Embedding gets 2× weight because it is the only true semantic signal —
    it compares meaning, not just lexical overlap.

    Graph is graded (ranked by edge quality = edge_type × 1/seed_rank)
    rather than binary (all graph files at rank 1).

    Term ranking uses IDF-weighted scores so that rare, specific terms
    rank higher than ubiquitous terms like "config" or "test".

    Mutates candidates in-place (sets combined_score, signal fields).
    Also appends files found only by secondary sources.
    """
    _RRF_K = 60
    _EMBEDDING_WEIGHT = 2.0

    # ── Aggregate def-level signals by file path ──
    path_signals: dict[str, dict[str, Any]] = {}
    for _uid, cand in def_candidates.items():
        fp = cand.file_path
        if not fp:
            continue
        if fp not in path_signals:
            path_signals[fp] = {
                "term_count": 0,
                "term_idf": 0.0,
                "lex_count": 0,
                "explicit": False,
                "graph": False,
                "graph_quality": 0.0,
            }
        sig = path_signals[fp]
        sig["term_count"] += len(cand.matched_terms)
        sig["term_idf"] += cand.term_idf_score
        sig["lex_count"] += cand.lexical_hit_count
        sig["explicit"] = sig["explicit"] or cand.from_explicit
        sig["graph"] = sig["graph"] or cand.from_graph
        sig["graph_quality"] = max(sig["graph_quality"], cand.graph_quality)

    explicit_paths = set(parsed.explicit_paths or [])

    # ── Populate signal fields on existing file candidates ──
    for fc in file_candidates:
        sig = path_signals.get(fc.path, {})
        fc.term_match_count = sig.get("term_count", 0)
        fc.lexical_hit_count = sig.get("lex_count", 0)
        fc.has_explicit_mention = sig.get("explicit", False) or fc.path in explicit_paths
        fc.graph_connected = sig.get("graph", False)
        fc.graph_quality = sig.get("graph_quality", 0.0)

    # ── Append files found ONLY by def-level harvesters ──
    existing_paths = {fc.path for fc in file_candidates}
    for fp, sig in path_signals.items():
        if fp in existing_paths:
            continue
        term_count = sig.get("term_count", 0)
        lex_count = sig.get("lex_count", 0)
        is_explicit = sig.get("explicit", False) or fp in explicit_paths
        is_graph = sig.get("graph", False)
        gq = sig.get("graph_quality", 0.0)

        # Only include if there's a concrete signal (not noise)
        if term_count > 0 or lex_count > 0 or is_explicit or is_graph:
            fc = FileCandidate(
                path=fp,
                similarity=0.0,
                combined_score=0.0,  # will be set by RRF below
                term_match_count=term_count,
                lexical_hit_count=lex_count,
                has_explicit_mention=is_explicit,
                graph_connected=is_graph,
                graph_quality=gq,
                artifact_kind=_classify_artifact(fp),
            )
            file_candidates.append(fc)

    # ── Build per-source ranked lists ──
    # Each list is sorted descending by the source's score.
    # rank is 1-based (rank 1 = best).

    # Source 1: Embedding similarity (weight 2.0×)
    embedding_ranked = sorted(
        [(fc.path, fc.similarity) for fc in file_candidates if fc.similarity > 0],
        key=lambda x: -x[1],
    )
    embedding_rank: dict[str, int] = {
        path: rank for rank, (path, _) in enumerate(embedding_ranked, 1)
    }

    # Source 2: Term match — ranked by IDF-weighted score
    term_idf_by_path: dict[str, float] = {}
    for fp, sig in path_signals.items():
        idf_val = sig.get("term_idf", 0.0)
        if idf_val > 0:
            term_idf_by_path[fp] = idf_val
    term_ranked = sorted(term_idf_by_path.items(), key=lambda x: -x[1])
    term_rank: dict[str, int] = {path: rank for rank, (path, _) in enumerate(term_ranked, 1)}

    # Source 3: Lexical hit count
    lex_ranked = sorted(
        [(fc.path, fc.lexical_hit_count) for fc in file_candidates if fc.lexical_hit_count > 0],
        key=lambda x: -x[1],
    )
    lex_rank: dict[str, int] = {path: rank for rank, (path, _) in enumerate(lex_ranked, 1)}

    # Source 4: Graph — ranked by graded edge quality (not binary)
    graph_ranked = sorted(
        [(fc.path, fc.graph_quality) for fc in file_candidates if fc.graph_quality > 0],
        key=lambda x: -x[1],
    )
    graph_rank: dict[str, int] = {path: rank for rank, (path, _) in enumerate(graph_ranked, 1)}

    # Source 5: Explicit mention (binary — rank 1 for all explicit)
    explicit_all = {fc.path for fc in file_candidates if fc.has_explicit_mention}

    # Source 6: Path-token matching
    # Tokenize file paths and check overlap with query terms.
    # Gives files a signal when their path components match query terms,
    # independent of code content.  Important for graph-island files
    # (e.g. packages/evee-azureml/) where the path is the primary
    # discriminator and code-only harvesters miss them.
    # Only primary terms, stop-word filtered, ≥4 chars — secondary
    # terms like "config", "model" are too common in paths.
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
        }
    )
    path_query_terms: set[str] = set()
    for t in parsed.primary_terms:
        tl = t.lower()
        if len(tl) >= 4 and tl not in _PATH_STOP_TOKENS:
            path_query_terms.add(tl)

    path_match_scores: dict[str, int] = {}
    if path_query_terms:
        for fc in file_candidates:
            path_lower = fc.path.lower()
            path_tokens = set(re.split(r"[/._\-]", path_lower))
            path_tokens = {t for t in path_tokens if len(t) >= 2}

            # Token overlap
            hits = path_query_terms & path_tokens
            # Also substring match for compound terms (e.g. "mlflow" in path)
            if not hits:
                hits = {t for t in path_query_terms if t in path_lower}

            if hits:
                path_match_scores[fc.path] = len(hits)

    path_match_ranked = sorted(path_match_scores.items(), key=lambda x: -x[1])
    path_match_rank: dict[str, int] = {
        path: rank for rank, (path, _) in enumerate(path_match_ranked, 1)
    }

    # ── Compute RRF score for each candidate ──
    for fc in file_candidates:
        rrf = 0.0
        if fc.path in embedding_rank:
            rrf += _EMBEDDING_WEIGHT / (_RRF_K + embedding_rank[fc.path])
        if fc.path in term_rank:
            rrf += 1.0 / (_RRF_K + term_rank[fc.path])
        if fc.path in lex_rank:
            rrf += 1.0 / (_RRF_K + lex_rank[fc.path])
        if fc.path in graph_rank:
            rrf += 1.0 / (_RRF_K + graph_rank[fc.path])
        if fc.path in explicit_all:
            rrf += 1.0 / (_RRF_K + 1)  # rank 1 for all explicit mentions
        if fc.path in path_match_rank:
            rrf += 1.0 / (_RRF_K + path_match_rank[fc.path])
        fc.combined_score = rrf

    return file_candidates
