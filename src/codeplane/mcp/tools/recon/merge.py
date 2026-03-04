"""Candidate merge, enrichment, and graph-seed selection.

Single Responsibility: Combine outputs from multiple harvesters and resolve
structural metadata.  No harvesting logic — just merge + enrich.
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

if TYPE_CHECKING:
    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


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
    to minimize repeated queries.
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
    hub_score_cache: dict[str, int] = {}

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

            if uid not in hub_score_cache:
                hub_score_cache[uid] = fq.count_callers(uid)
            cand.hub_score = hub_score_cache[uid]

            cand.file_path = fid_path_cache.get(d.file_id, "")
            cand.is_test = _is_test_file(cand.file_path)
            cand.is_barrel = _is_barrel_file(cand.file_path)
            cand.artifact_kind = _classify_artifact(cand.file_path)

    # --- Populate structural link fields ---
    anchor_uids: set[str] = set()
    anchor_file_ids: set[int] = set()
    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue
        if cand.from_explicit:
            anchor_uids.add(uid)
            anchor_file_ids.add(cand.def_fact.file_id)

    if anchor_uids:
        for uid, cand in candidates.items():
            if uid in anchor_uids or cand.def_fact is None:
                continue
            if cand.def_fact.file_id in anchor_file_ids:
                cand.shares_file_with_seed = True

        with coordinator.db.session() as session:
            fq = FactQueries(session)

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

            for uid, cand in candidates.items():
                if uid in anchor_uids:
                    continue
                if uid in anchor_callee_uids:
                    cand.is_callee_of_top = True
                if uid in anchor_import_uids:
                    cand.is_imported_by_top = True


def _select_graph_seeds(
    merged: dict[str, HarvestCandidate],
) -> list[str]:
    """Select the best candidates from merged pool to use as graph seeds.

    Returns up to 15 seeds.  Prioritizes candidates with multiple
    evidence axes and explicit mentions.
    """
    scored: list[tuple[str, float]] = []
    for uid, cand in merged.items():
        score = cand.evidence_axes * 2.0 + (2.0 if cand.from_explicit else 0.0)
        scored.append((uid, score))

    scored.sort(key=lambda x: -x[1])
    return [uid for uid, _ in scored[:15]]


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
    important = [d for d in defs if d.kind in ("function", "method", "class", "module")]
    rest = [d for d in defs if d.kind not in ("function", "method", "class", "module")]
    selected = (important + rest)[:limit]

    for d in selected:
        if d.def_uid in merged:
            existing = merged[d.def_uid]
            if not existing.from_graph:
                existing.from_graph = True
                existing.graph_quality = max(existing.graph_quality, graph_quality)
                existing.evidence.append(
                    EvidenceRecord(category=category, detail=detail, score=score)
                )
            continue
        if d.def_uid in candidates:
            candidates[d.def_uid].graph_quality = max(
                candidates[d.def_uid].graph_quality, graph_quality
            )
            continue
        candidates[d.def_uid] = HarvestCandidate(
            def_uid=d.def_uid,
            def_fact=d,
            from_graph=True,
            graph_quality=graph_quality,
            evidence=[EvidenceRecord(category=category, detail=detail, score=score)],
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

    if basename.startswith("test_") or basename == "conftest.py":
        return []
    if not basename.endswith(".py"):
        return []

    name_stem = basename[:-3]
    test_name = f"test_{name_stem}.py"

    candidates: list[str] = []
    if len(parts) >= 2:
        sub_path = "/".join(parts[1:-1])
        if sub_path:
            candidates.append(f"tests/{sub_path}/{test_name}")
        candidates.append(f"tests/{test_name}")

    dir_path = os.path.dirname(source_path)
    if dir_path:
        candidates.append(f"{dir_path}/{test_name}")

    return candidates
