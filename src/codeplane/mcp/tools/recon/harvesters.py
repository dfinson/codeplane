"""Harvesters — four independent candidate sources + merge + enrich.

Single Responsibility: Each harvester queries one data source and produces
``HarvestCandidate`` dicts.  ``_merge_candidates`` combines them.
``_enrich_candidates`` resolves missing DefFacts and populates structural
metadata.

Open/Closed: New harvesters can be added without modifying existing ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from codeplane.mcp.tools.recon.models import (
    EvidenceRecord,
    HarvestCandidate,
    _classify_artifact,
    _is_barrel_file,
    _is_test_file,
)
from codeplane.mcp.tools.recon.parsing import (
    _build_query_views,
)

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext
    from codeplane.mcp.tools.recon.models import ParsedTask

log = structlog.get_logger(__name__)


# ===================================================================
# Harvester A: Embedding (dense vector similarity)
# ===================================================================


async def _harvest_embedding(
    app_ctx: AppContext,
    parsed: ParsedTask,
    *,
    top_k: int = 200,
) -> dict[str, HarvestCandidate]:
    """Harvester A: Multi-view dense vector similarity search.

    Builds multiple query views (natural-language, code-style,
    keyword-focused) from the parsed task and uses the evidence-record
    multiview retrieval pipeline (SPEC §16.4).  The embedding index
    handles ratio gate, per-record→per-uid aggregation, and tiered
    acceptance — no external threshold or merge needed.
    """
    coordinator = app_ctx.coordinator

    views = _build_query_views(parsed)
    similar = coordinator.query_similar_defs_multiview(views, top_k=top_k)

    candidates: dict[str, HarvestCandidate] = {}
    for uid, sim in similar:
        candidates[uid] = HarvestCandidate(
            def_uid=uid,
            from_embedding=True,
            embedding_similarity=sim,
            evidence=[
                EvidenceRecord(
                    category="embedding",
                    detail=f"semantic similarity {sim:.3f} (multiview-fused)",
                    score=min(sim, 1.0),
                )
            ],
        )

    log.debug(
        "recon.harvest.embedding",
        count=len(candidates),
        views=len(views),
        top5=[(uid.split("::")[-1], round(s, 3)) for uid, s in similar[:5]],
    )
    return candidates


# ===================================================================
# Harvester B: Term match (SQL LIKE)
# ===================================================================


async def _harvest_term_match(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester B: DefFact term matching via SQL LIKE."""
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
            for d in matching_defs:
                uid = d.def_uid
                if uid not in candidates:
                    candidates[uid] = HarvestCandidate(
                        def_uid=uid,
                        def_fact=d,
                        from_term_match=True,
                    )
                else:
                    candidates[uid].from_term_match = True
                    if candidates[uid].def_fact is None:
                        candidates[uid].def_fact = d
                candidates[uid].matched_terms.add(term)
                candidates[uid].evidence.append(
                    EvidenceRecord(
                        category="term_match",
                        detail=f"name matches term '{term}'",
                        score=0.5,
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
) -> dict[str, HarvestCandidate]:
    """Harvester D: Explicit mentions (paths + symbols from task text).

    Resolves file paths to defs and symbol names to DefFacts.
    These bypass the dual-signal gate (trusted input).
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

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

    # D3: Symbol names mentioned in the task text
    if parsed.explicit_symbols:
        for sym in parsed.explicit_symbols:
            # Exact match first via symbol table (Section 1: use the index)
            exact_defs = await coordinator.get_all_defs(sym, limit=10)
            for d in exact_defs:
                if d.def_uid not in candidates:
                    candidates[d.def_uid] = HarvestCandidate(
                        def_uid=d.def_uid,
                        def_fact=d,
                        from_explicit=True,
                        evidence=[
                            EvidenceRecord(
                                category="explicit",
                                detail=f"exact symbol match '{sym}'",
                                score=1.0,
                            )
                        ],
                    )
                else:
                    candidates[d.def_uid].from_explicit = True

            # LIKE fallback only for symbols not found by exact match
            if not exact_defs:
                with coordinator.db.session() as session:
                    fq = FactQueries(session)
                    matching = fq.find_defs_matching_term(sym, limit=10)
                    for d in matching:
                        if sym.lower() in d.name.lower() or (
                            d.qualified_name and sym.lower() in d.qualified_name.lower()
                        ):
                            if d.def_uid not in candidates:
                                candidates[d.def_uid] = HarvestCandidate(
                                    def_uid=d.def_uid,
                                    def_fact=d,
                                    from_explicit=True,
                                    evidence=[
                                        EvidenceRecord(
                                            category="explicit",
                                            detail=f"fuzzy symbol match '{sym}'",
                                            score=0.7,
                                        )
                                    ],
                                )
                            else:
                                candidates[d.def_uid].from_explicit = True

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
                existing.from_embedding = existing.from_embedding or cand.from_embedding
                existing.from_term_match = existing.from_term_match or cand.from_term_match
                existing.from_lexical = existing.from_lexical or cand.from_lexical
                existing.from_explicit = existing.from_explicit or cand.from_explicit
                existing.from_graph = existing.from_graph or cand.from_graph
                existing.embedding_similarity = max(
                    existing.embedding_similarity, cand.embedding_similarity
                )
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
    # Identify "anchor" candidates: explicit mentions or strong embedding
    anchor_uids: set[str] = set()
    anchor_file_ids: set[int] = set()
    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue
        if cand.from_explicit or (cand.from_embedding and cand.embedding_similarity >= 0.5):
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

# Budget constants for graph harvester
_GRAPH_MAX_SEEDS = 10  # Max candidates to use as graph seeds
_GRAPH_MAX_CALLEES_PER_SEED = 8
_GRAPH_MAX_CALLERS_PER_SEED = 5
_GRAPH_MAX_SIBLINGS_PER_SEED = 5
_GRAPH_MAX_TOTAL = 100  # Hard cap on graph-discovered candidates


async def _harvest_graph(
    app_ctx: AppContext,
    merged: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester E: Walk 1-hop graph edges from top merged candidates.

    Takes the strongest merged candidates (by evidence count and embedding
    similarity) as graph seeds, then discovers structurally adjacent defs
    via callees, callers (as references), and same-file siblings.

    These candidates are new — they were NOT found by embedding, term,
    lexical, or explicit search.  Graph-discovered candidates get moderate
    base evidence and rely on the filter/scoring pipeline for final selection.

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

    # Walk graph edges from each seed
    with coordinator.db.session() as session:
        fq = FactQueries(session)
        from codeplane.index.models import File as FileModel

        for seed_uid, seed_cand in seeds_with_facts:
            if len(candidates) >= _GRAPH_MAX_TOTAL:
                break
            seed_def = seed_cand.def_fact
            assert seed_def is not None

            # (a) Callees: defs referenced within this seed's body
            callees = fq.list_callees_in_scope(
                seed_def.file_id,
                seed_def.start_line,
                seed_def.end_line,
                limit=_GRAPH_MAX_CALLEES_PER_SEED,
            )
            for callee in callees:
                if callee.def_uid == seed_uid:
                    continue
                # If callee is already merged, reinforce with graph evidence
                if callee.def_uid in merged:
                    existing = merged[callee.def_uid]
                    if not existing.from_graph:
                        existing.from_graph = True
                        existing.evidence.append(
                            EvidenceRecord(
                                category="graph",
                                detail=f"callee of {seed_def.name}",
                                score=0.4,
                            )
                        )
                    continue
                if callee.def_uid in candidates:
                    continue
                candidates[callee.def_uid] = HarvestCandidate(
                    def_uid=callee.def_uid,
                    def_fact=callee,
                    from_graph=True,
                    evidence=[
                        EvidenceRecord(
                            category="graph",
                            detail=f"callee of {seed_def.name}",
                            score=0.4,
                        )
                    ],
                )

            # (b) Callers: defs that reference this seed (via RefFact)
            refs = fq.list_refs_by_def_uid(seed_uid, limit=_GRAPH_MAX_CALLERS_PER_SEED * 3)
            caller_file_ids: set[int] = set()
            for ref in refs:
                if len(caller_file_ids) >= _GRAPH_MAX_CALLERS_PER_SEED:
                    break
                if ref.file_id == seed_def.file_id:
                    continue
                if ref.file_id in caller_file_ids:
                    continue
                caller_file_ids.add(ref.file_id)
                # Find the enclosing def at the reference site
                caller_defs = fq.list_defs_in_file(ref.file_id, limit=200)
                for cd in caller_defs:
                    if (
                        ref.start_line is not None
                        and cd.start_line <= ref.start_line <= cd.end_line
                    ):
                        # If caller is already merged, reinforce with graph evidence
                        if cd.def_uid in merged:
                            existing = merged[cd.def_uid]
                            if not existing.from_graph:
                                existing.from_graph = True
                                existing.evidence.append(
                                    EvidenceRecord(
                                        category="graph",
                                        detail=f"caller of {seed_def.name}",
                                        score=0.35,
                                    )
                                )
                            break
                        if cd.def_uid in candidates:
                            break
                        candidates[cd.def_uid] = HarvestCandidate(
                            def_uid=cd.def_uid,
                            def_fact=cd,
                            from_graph=True,
                            evidence=[
                                EvidenceRecord(
                                    category="graph",
                                    detail=f"caller of {seed_def.name}",
                                    score=0.35,
                                )
                            ],
                        )
                        break

            # (c) Same-file siblings: other key defs in seed's file
            frec = session.get(FileModel, seed_def.file_id)
            if frec is not None and frec.id is not None:
                sibling_defs = fq.list_defs_in_file(frec.id, limit=50)
                sib_count = 0
                for sd in sibling_defs:
                    if sib_count >= _GRAPH_MAX_SIBLINGS_PER_SEED:
                        break
                    if sd.def_uid == seed_uid:
                        continue
                    if sd.kind not in ("function", "method", "class"):
                        continue
                    # If sibling is already merged, reinforce with graph evidence
                    if sd.def_uid in merged:
                        existing = merged[sd.def_uid]
                        if not existing.from_graph:
                            existing.from_graph = True
                            existing.evidence.append(
                                EvidenceRecord(
                                    category="graph",
                                    detail=f"sibling of {seed_def.name} in {frec.path}",
                                    score=0.3,
                                )
                            )
                        sib_count += 1
                        continue
                    if sd.def_uid in candidates:
                        continue
                    candidates[sd.def_uid] = HarvestCandidate(
                        def_uid=sd.def_uid,
                        def_fact=sd,
                        from_graph=True,
                        evidence=[
                            EvidenceRecord(
                                category="graph",
                                detail=f"sibling of {seed_def.name} in {frec.path}",
                                score=0.3,
                            )
                        ],
                    )
                    sib_count += 1

    log.debug(
        "recon.harvest.graph",
        count=len(candidates),
        seeds_used=len(seeds_with_facts),
    )
    return candidates


def _select_graph_seeds(
    merged: dict[str, HarvestCandidate],
) -> list[str]:
    """Select the best candidates from merged pool to use as graph seeds.

    Prioritizes candidates with:
    1. Multiple evidence axes (found by multiple harvesters)
    2. High embedding similarity
    3. Explicit mentions
    """
    scored: list[tuple[str, float]] = []
    for uid, cand in merged.items():
        # Score: axes * 2 + embedding_sim + explicit_bonus
        score = (
            cand.evidence_axes * 2.0
            + cand.embedding_similarity
            + (2.0 if cand.from_explicit else 0.0)
        )
        scored.append((uid, score))

    scored.sort(key=lambda x: -x[1])
    return [uid for uid, _ in scored[:_GRAPH_MAX_SEEDS]]
