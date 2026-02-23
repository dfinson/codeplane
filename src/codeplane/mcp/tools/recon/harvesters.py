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
    _merge_multi_view_results,
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
    keyword-focused) from the parsed task and embeds them in a single
    batch call.  Results are merged by max-similarity per def_uid,
    improving recall without extra latency.
    """
    coordinator = app_ctx.coordinator

    views = _build_query_views(parsed)
    per_view = coordinator.query_similar_defs_batch(views, top_k=top_k)
    similar = _merge_multi_view_results(per_view)

    candidates: dict[str, HarvestCandidate] = {}
    for uid, sim in similar:
        if sim < 0.15:
            continue
        candidates[uid] = HarvestCandidate(
            def_uid=uid,
            from_embedding=True,
            embedding_similarity=sim,
            evidence=[EvidenceRecord(
                category="embedding",
                detail=f"semantic similarity {sim:.3f} (multi-view)",
                score=min(sim, 1.0),
            )],
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
                candidates[uid].evidence.append(EvidenceRecord(
                    category="term_match",
                    detail=f"name matches term '{term}'",
                    score=0.5,
                ))

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

    terms = parsed.primary_terms[:8]
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
                                evidence=[EvidenceRecord(
                                    category="lexical",
                                    detail=f"full-text hit in {file_path}:{line}",
                                    score=0.4,
                                )],
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
                    evidence=[EvidenceRecord(
                        category="explicit",
                        detail=f"agent-provided seed '{name}'",
                        score=1.0,
                    )],
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
                            evidence=[EvidenceRecord(
                                category="explicit",
                                detail=f"in mentioned path '{epath}'",
                                score=0.9,
                            )],
                        )
                    else:
                        candidates[d.def_uid].from_explicit = True

    # D3: Symbol names mentioned in the task text
    if parsed.explicit_symbols:
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for sym in parsed.explicit_symbols:
                matching = fq.find_defs_matching_term(sym, limit=10)
                for d in matching:
                    if sym.lower() in d.name.lower() or (
                        d.qualified_name
                        and sym.lower() in d.qualified_name.lower()
                    ):
                        if d.def_uid not in candidates:
                            candidates[d.def_uid] = HarvestCandidate(
                                def_uid=d.def_uid,
                                def_fact=d,
                                from_explicit=True,
                                evidence=[EvidenceRecord(
                                    category="explicit",
                                    detail=f"name matches symbol '{sym}'",
                                    score=0.8,
                                )],
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
                existing.from_embedding = (
                    existing.from_embedding or cand.from_embedding
                )
                existing.from_term_match = (
                    existing.from_term_match or cand.from_term_match
                )
                existing.from_lexical = (
                    existing.from_lexical or cand.from_lexical
                )
                existing.from_explicit = (
                    existing.from_explicit or cand.from_explicit
                )
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
    """
    from codeplane.index._internal.indexing.graph import FactQueries
    from codeplane.index.models import File as FileModel

    coordinator = app_ctx.coordinator

    # Resolve missing DefFacts
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

    # Populate structural metadata
    fid_path_cache: dict[int, str] = {}
    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for uid, cand in list(candidates.items()):
            if cand.def_fact is None:
                continue
            d = cand.def_fact

            cand.hub_score = fq.count_callers(uid)

            if d.file_id not in fid_path_cache:
                frec = session.get(FileModel, d.file_id)
                fid_path_cache[d.file_id] = frec.path if frec else ""
            cand.file_path = fid_path_cache[d.file_id]

            cand.is_test = _is_test_file(cand.file_path)
            cand.is_barrel = _is_barrel_file(cand.file_path)
            cand.artifact_kind = _classify_artifact(cand.file_path)
