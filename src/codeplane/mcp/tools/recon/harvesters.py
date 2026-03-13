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



async def _harvest_def_embedding(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Per-def embedding harvest for raw signal collection.

    Returns HarvestCandidate objects keyed by def_uid with embedding
    similarity as evidence. All defs with positive similarity are returned.
    """
    coordinator = app_ctx.coordinator
    query_text = parsed.query_text or parsed.raw
    candidates: dict[str, HarvestCandidate] = {}

    # Per-def embedding results (code defs) — batch lookup
    def_results = coordinator.query_def_embeddings(query_text)
    if def_results:
        uids = [uid for uid, _ in def_results]
        defs_map = coordinator.batch_get_defs(uids)
        sim_map = {uid: sim for uid, sim in def_results}
        for uid, d in defs_map.items():
            candidates[uid] = HarvestCandidate(
                def_uid=uid,
                def_fact=d,
                evidence=[
                    EvidenceRecord(
                        category="embedding",
                        detail=f"def embedding sim={sim_map[uid]:.3f}",
                        score=sim_map[uid],
                    )
                ],
            )

    # File-level embedding results (non-code files) → expand to defs in those files
    file_results = coordinator.query_file_embeddings(query_text)
    if file_results:
        from codeplane.index._internal.indexing.graph import FactQueries

        with coordinator.db.session() as session:
            fq = FactQueries(session)
            paths = [path for path, _ in file_results]
            files_map = fq.batch_get_files_by_paths(paths)
            sim_by_path = {path: sim for path, sim in file_results}
            for path, frec in files_map.items():
                if frec.id is None:
                    continue
                defs_in_file = fq.list_defs_in_file(frec.id)
                sim = sim_by_path[path]
                for d in defs_in_file:
                    if d.def_uid not in candidates:
                        candidates[d.def_uid] = HarvestCandidate(
                            def_uid=d.def_uid,
                            def_fact=d,
                            evidence=[
                                EvidenceRecord(
                                    category="embedding",
                                    detail=f"file embedding sim={sim:.3f} ({path})",
                                    score=sim,
                                )
                            ],
                        )

    log.debug(
        "recon.harvest.def_embedding",
        count=len(candidates),
    )
    return candidates


# ===================================================================
# Harvester B: Term match (SQL LIKE)
# ===================================================================


async def _harvest_term_match(
    app_ctx: AppContext,
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester B: DefFact term matching via SQL LIKE.

    Returns all matching defs with raw match counts.
    No IDF pre-computation — the ranker learns its own weighting.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    all_terms = parsed.primary_terms + parsed.secondary_terms
    if not all_terms:
        return candidates

    with coordinator.db.session() as session:
        fq = FactQueries(session)
        for term in all_terms:
            matching_defs = fq.find_defs_matching_term(term)
            n_matches = len(matching_defs)

            for d in matching_defs:
                uid = d.def_uid
                if uid not in candidates:
                    candidates[uid] = HarvestCandidate(
                        def_uid=uid,
                        def_fact=d,
                        from_term_match=True,
                        term_match_count=1,
                        term_total_matches=n_matches,
                    )
                else:
                    candidates[uid].from_term_match = True
                    candidates[uid].term_match_count += 1
                    candidates[uid].term_total_matches = max(
                        candidates[uid].term_total_matches, n_matches
                    )
                    if candidates[uid].def_fact is None:
                        candidates[uid].def_fact = d
                candidates[uid].matched_terms.add(term)
                candidates[uid].evidence.append(
                    EvidenceRecord(
                        category="term_match",
                        detail=f"name matches term '{term}'",
                        score=1.0,
                    )
                )

    log.debug(
        "recon.harvest.term_match",
        count=len(candidates),
        terms=len(all_terms),
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
                    symbol_source="auto_seed",
                    evidence=[
                        EvidenceRecord(
                            category="auto_seed",
                            detail=f"auto-seed '{name}' (hub-ranked)",
                            score=1.0,
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
                    symbol_source="agent_seed",
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
            files_map = fq.batch_get_files_by_paths(parsed.explicit_paths)
            for epath, frec in files_map.items():
                if frec.id is None:
                    continue
                defs_in = fq.list_defs_in_file(frec.id)
                for d in defs_in:
                    if d.def_uid not in candidates:
                        candidates[d.def_uid] = HarvestCandidate(
                            def_uid=d.def_uid,
                            def_fact=d,
                            from_explicit=True,
                            symbol_source="path_mention",
                            evidence=[
                                EvidenceRecord(
                                    category="explicit",
                                    detail=f"in mentioned path '{epath}'",
                                    score=1.0,
                                )
                            ],
                        )
                    else:
                        candidates[d.def_uid].from_explicit = True
                        if candidates[d.def_uid].symbol_source is None:
                            candidates[d.def_uid].symbol_source = "path_mention"

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
                    symbol_source="task_extracted",
                    evidence=[
                        EvidenceRecord(
                            category="explicit",
                            detail=f"task-extracted symbol '{sym}'",
                            score=1.0,
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


async def _harvest_graph(
    app_ctx: AppContext,
    merged: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester E: Walk 1-hop graph edges from top merged candidates.

    Takes seeds (candidates found by ≥2 retrievers or explicitly mentioned),
    discovers structurally adjacent defs via callees, callers, and siblings.

    Emits raw edge type and seed rank per candidate. No quality scoring,
    no budget cap — the ranker learns relevance from data.
    """
    from codeplane.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}

    if not merged:
        return candidates

    seed_uids = _select_graph_seeds(merged)
    if not seed_uids:
        return candidates

    # Resolve DefFacts for seeds — batch lookup for missing ones
    seeds_with_facts: list[tuple[str, HarvestCandidate]] = []
    missing_uids = [uid for uid in seed_uids if merged[uid].def_fact is None]
    if missing_uids:
        defs_map = coordinator.batch_get_defs(missing_uids)
        for uid, d in defs_map.items():
            merged[uid].def_fact = d
    for uid in seed_uids:
        cand = merged[uid]
        if cand.def_fact is not None:
            seeds_with_facts.append((uid, cand))

    if not seeds_with_facts:
        return candidates

    # Collect all edges: (def_uid, def_fact, edge_type, seed_rank, detail)
    EdgeInfo = tuple[str, object, str, int, str]
    raw_edges: list[EdgeInfo] = []

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        # Pre-resolve all seed file IDs to File objects in one batch
        seed_file_ids = list({sc.def_fact.file_id for _, sc in seeds_with_facts if sc.def_fact})
        files_by_id = fq.batch_get_files(seed_file_ids)

        for seed_idx, (seed_uid, seed_cand) in enumerate(seeds_with_facts, 1):
            seed_def = seed_cand.def_fact
            assert seed_def is not None

            # (a) Callees
            callees = fq.list_callees_in_scope(
                seed_def.file_id,
                seed_def.start_line,
                seed_def.end_line,
            )
            for callee in callees:
                if callee.def_uid == seed_uid:
                    continue
                raw_edges.append((
                    callee.def_uid, callee, "callee", seed_idx,
                    f"callee of {seed_def.name}",
                ))

            # (b) Callers
            refs = fq.list_refs_by_def_uid(seed_uid)
            caller_file_ids: set[int] = set()
            for ref in refs:
                if ref.file_id == seed_def.file_id:
                    continue
                if ref.file_id in caller_file_ids:
                    continue
                caller_file_ids.add(ref.file_id)
                caller_defs = fq.list_defs_in_file(ref.file_id)
                for cd in caller_defs:
                    if (
                        ref.start_line is not None
                        and cd.start_line <= ref.start_line <= cd.end_line
                    ):
                        raw_edges.append((
                            cd.def_uid, cd, "caller", seed_idx,
                            f"caller of {seed_def.name}",
                        ))
                        break

            # (c) Same-file siblings
            frec = files_by_id.get(seed_def.file_id)
            if frec is not None and frec.id is not None:
                sibling_defs = fq.list_defs_in_file(frec.id)
                for sd in sibling_defs:
                    if sd.def_uid == seed_uid:
                        continue
                    if sd.kind not in ("function", "method", "class"):
                        continue
                    raw_edges.append((
                        sd.def_uid, sd, "sibling", seed_idx,
                        f"sibling of {seed_def.name} in {frec.path}",
                    ))

    # Deduplicate: per uid, keep lowest seed_rank (closest to top seed)
    best_edges: dict[str, EdgeInfo] = {}
    for edge in raw_edges:
        uid = edge[0]
        if uid not in best_edges or edge[3] < best_edges[uid][3]:
            best_edges[uid] = edge


    for uid, (_, def_fact, edge_type, seed_rank, detail) in best_edges.items():

        if uid in merged:
            existing = merged[uid]
            existing.from_graph = True
            if existing.graph_edge_type is None:
                existing.graph_edge_type = edge_type
                existing.graph_seed_rank = seed_rank
            if not any(e.category == "graph" for e in existing.evidence):
                existing.evidence.append(
                    EvidenceRecord(category="graph", detail=detail, score=1.0)
                )
            continue
        if uid in candidates:
            candidates[uid].graph_edge_type = edge_type
            candidates[uid].graph_seed_rank = seed_rank
            continue
        candidates[uid] = HarvestCandidate(
            def_uid=uid,
            def_fact=def_fact,  # type: ignore[arg-type]
            from_graph=True,
            graph_edge_type=edge_type,
            graph_seed_rank=seed_rank,
            evidence=[EvidenceRecord(category="graph", detail=detail, score=1.0)],
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
    seed_uids = _select_graph_seeds(merged)
    if not seed_uids:
        return candidates

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        # Resolve seed file paths — batch lookup for missing defs, then batch file resolution
        missing_uids = [uid for uid in seed_uids if merged[uid].def_fact is None]
        if missing_uids:
            defs_map = coordinator.batch_get_defs(missing_uids)
            for uid, d in defs_map.items():
                merged[uid].def_fact = d

        seed_file_ids_list: list[int] = []
        for uid in seed_uids:
            cand = merged[uid]
            if cand.def_fact is not None:
                fid = cand.def_fact.file_id
                if fid not in seed_file_ids_list:
                    seed_file_ids_list.append(fid)

        files_by_id = fq.batch_get_files(seed_file_ids_list)
        seed_file_paths: dict[int, str] = {fid: f.path for fid, f in files_by_id.items()}
        seed_file_ids: set[int] = set(seed_file_paths.keys())

        if not seed_file_ids:
            return candidates

        # Collect unique seed file paths for reverse lookup
        seed_paths_set = set(seed_file_paths.values())

        # (a) Forward deps: files imported by seed files
        # Collect all resolved import paths across all seeds, then batch lookup
        all_import_paths: set[str] = set()
        imports_by_fid: dict[int, list[ImportFact]] = {}
        for fid in seed_file_ids:
            imports = fq.list_imports(fid)
            imports_by_fid[fid] = imports
            for imp in imports:
                if imp.resolved_path:
                    all_import_paths.add(imp.resolved_path)

        if all_import_paths:
            import_files_map = fq.batch_get_files_by_paths(list(all_import_paths))
        else:
            import_files_map = {}

        seen_import_fids: set[int] = set()
        for fid in seed_file_ids:
            for imp in imports_by_fid.get(fid, []):
                if not imp.resolved_path:
                    continue
                imp_file = import_files_map.get(imp.resolved_path)
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
                    score=1.0,
                    import_direction="forward",
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
            # Batch lookup all reverse file IDs
            reverse_fids_to_lookup = [rfid for rfid in reverse_fids if rfid not in seed_file_ids]
            if reverse_fids_to_lookup:
                reverse_files = fq.batch_get_files(reverse_fids_to_lookup)
                for rfid, rfile in reverse_files.items():
                    _add_file_defs_as_candidates(
                        fq,
                        rfile,
                        candidates,
                        merged,
                        category="import_reverse",
                        detail=f"imports a seed file ({rfile.path})",
                        score=1.0,
                        import_direction="reverse",
                    )

        # (c) __init__.py barrels + conftest.py in seed directories
        # Collect all candidate barrel/conftest paths, then batch lookup
        barrel_paths: list[str] = []
        seen_dirs: set[str] = set()
        for seed_path in seed_paths_set:
            import os

            dir_path = os.path.dirname(seed_path)
            if not dir_path or dir_path in seen_dirs:
                continue
            seen_dirs.add(dir_path)
            for special_name in ("__init__.py", "conftest.py"):
                barrel_path = f"{dir_path}/{special_name}"
                if barrel_path not in seed_paths_set:
                    barrel_paths.append(barrel_path)

        # (d) Test file pattern matching — collect all candidate paths
        test_lookup_paths: list[str] = []
        test_path_to_seed: dict[str, str] = {}
        for seed_path in seed_paths_set:
            test_paths = _infer_test_paths(seed_path)
            for tp in test_paths:
                test_lookup_paths.append(tp)
                test_path_to_seed[tp] = seed_path

        # Single batch lookup for all barrel + test paths
        all_lookup_paths = barrel_paths + test_lookup_paths
        if all_lookup_paths:
            lookup_files = fq.batch_get_files_by_paths(all_lookup_paths)
        else:
            lookup_files = {}

        # Process barrel files
        for bp in barrel_paths:
            barrel_file = lookup_files.get(bp)
            if barrel_file is not None and barrel_file.id is not None:
                import os
                dir_path = os.path.dirname(bp)
                _add_file_defs_as_candidates(
                    fq,
                    barrel_file,
                    candidates,
                    merged,
                    category="import_barrel",
                    detail=f"package init/conftest in {dir_path}",
                    score=1.0,
                    import_direction="barrel",
                )

        # Process test files
        for tp in test_lookup_paths:
            tf = lookup_files.get(tp)
            if tf is not None and tf.id is not None:
                _add_file_defs_as_candidates(
                    fq,
                    tf,
                    candidates,
                    merged,
                    category="import_test",
                    detail=f"test file for {test_path_to_seed[tp]}",
                    score=1.0,
                    import_direction="test_pair",
                )

    log.debug(
        "recon.harvest.imports",
        count=len(candidates),
        seed_files=len(seed_file_ids),
    )
    return candidates
