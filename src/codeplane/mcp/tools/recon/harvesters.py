"""Harvesters — independent candidate sources (A-F).

Single Responsibility: Each harvester queries one data source and produces
candidates.  Merge, enrich, and scoring live in separate modules.

Open/Closed: New harvesters can be added without modifying existing ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from codeplane.mcp.tools.recon.models import (
    EvidenceRecord,
    FileCandidate,
    HarvestCandidate,
    _classify_artifact,
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


from codeplane.mcp.tools.recon.merge import (  # noqa: E402
    _add_file_defs_as_candidates,
    _infer_test_paths,
    _select_graph_seeds,
)

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
