"""Raw signal endpoint for ranking model training data collection.

Returns per-DefFact candidate pools with per-retriever scores — the raw
inputs needed for the ranker/cutoff/gate training datasets.

Does NOT run any model (ranker, cutoff, gate). Does not filter, sort,
or truncate. Returns the raw union of all retriever outputs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from codeplane.mcp.tools.recon.harvesters import (
    _harvest_def_embedding,
    _harvest_explicit,
    _harvest_graph,
    _harvest_lexical,
    _harvest_term_match,
)
from codeplane.mcp.tools.recon.merge import (
    _enrich_candidates,
    _merge_candidates,
)
from codeplane.mcp.tools.recon.models import HarvestCandidate
from codeplane.mcp.tools.recon.parsing import parse_task

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


async def _raw_signals_pipeline(
    app_ctx: AppContext,
    query: str,
    seeds: list[str] | None = None,
    pins: list[str] | None = None,
) -> dict[str, Any]:
    """Run all retrievers and return raw per-def signals without model inference.

    Returns a dict with query features, repo features, and a list of
    candidate defs with per-retriever scores and ranks.
    """
    t0 = time.monotonic()

    # Parse query
    parsed = parse_task(query)

    # Run all harvesters in parallel-safe order
    # A: Embedding (per-def + per-file, both indices)
    emb_candidates = await _harvest_def_embedding(app_ctx, parsed, top_k=200)

    # B: Term match
    term_candidates = await _harvest_term_match(app_ctx, parsed)

    # C: Lexical
    lex_candidates = await _harvest_lexical(app_ctx, parsed)

    # D: Explicit (symbols/paths from query text + agent seeds)
    explicit_candidates = await _harvest_explicit(
        app_ctx, parsed, explicit_seeds=seeds or None,
    )

    # Merge A-D
    merged = _merge_candidates(emb_candidates, term_candidates, lex_candidates, explicit_candidates)

    # D2: Pin injection — add all defs from pinned files
    if pins:
        from codeplane.index._internal.indexing.graph import FactQueries
        from codeplane.mcp.tools.recon.models import EvidenceRecord

        coordinator = app_ctx.coordinator
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for pin_path in pins:
                frec = fq.get_file_by_path(pin_path)
                if frec is None or frec.id is None:
                    continue
                defs_in = fq.list_defs_in_file(frec.id, limit=200)
                for d in defs_in:
                    if d.def_uid not in merged:
                        merged[d.def_uid] = HarvestCandidate(
                            def_uid=d.def_uid,
                            def_fact=d,
                            from_explicit=True,
                            symbol_source="pin",
                            evidence=[
                                EvidenceRecord(
                                    category="explicit",
                                    detail=f"pinned path '{pin_path}'",
                                    score=1.0,
                                )
                            ],
                        )
                    else:
                        existing = merged[d.def_uid]
                        existing.from_explicit = True
                        if existing.symbol_source is None:
                            existing.symbol_source = "pin"

    # E: Graph walk from top merged candidates
    graph_candidates = await _harvest_graph(app_ctx, merged, parsed)

    # Final merge with graph
    merged = _merge_candidates(merged, graph_candidates)

    # Enrich: resolve missing DefFacts, populate metadata
    await _enrich_candidates(app_ctx, merged)

    # Build per-retriever score/rank lists
    # Embedding scores from evidence
    emb_scores: dict[str, float] = {}
    for uid, cand in emb_candidates.items():
        for ev in cand.evidence:
            if ev.category == "embedding":
                emb_scores[uid] = ev.score
                break

    # Compute ranks for embedding only (other signals use categorical features)
    def _compute_ranks(scores: dict[str, float]) -> dict[str, int]:
        sorted_uids = sorted(scores.keys(), key=lambda u: -scores[u])
        return {uid: rank for rank, uid in enumerate(sorted_uids, 1)}

    emb_ranks = _compute_ranks(emb_scores)

    # Build candidate list
    candidates_out: list[dict[str, Any]] = []
    for uid, cand in merged.items():
        if cand.def_fact is None:
            continue

        d = cand.def_fact

        # Count retrievers that found this def
        retriever_hits = sum([
            uid in emb_scores,
            cand.from_term_match,
            cand.from_lexical,
            cand.from_graph,
            cand.from_explicit,
            cand.import_direction is not None,
        ])

        # Path tokenization
        path_parts = cand.file_path.rsplit("/", 1)
        parent_dir = path_parts[0] if len(path_parts) > 1 else ""
        path_depth = cand.file_path.count("/")

        # Nesting depth from lexical_path
        nesting_depth = d.lexical_path.count(".") if d.lexical_path else 0

        candidates_out.append({
            # Identity
            "def_uid": uid,
            "path": cand.file_path,
            "kind": d.kind,
            "name": d.name,
            "lexical_path": d.lexical_path,
            "qualified_name": d.qualified_name,
            # Span
            "start_line": d.start_line,
            "end_line": d.end_line,
            "object_size_lines": d.end_line - d.start_line + 1,
            # Path features
            "file_ext": "." + cand.file_path.rsplit(".", 1)[-1] if "." in cand.file_path else "",
            "parent_dir": parent_dir,
            "path_depth": path_depth,
            # Structural metadata from index
            "has_docstring": d.docstring is not None and len(d.docstring) > 0,
            "has_decorators": d.decorators_json is not None and d.decorators_json != "[]",
            "has_return_type": d.return_type is not None,
            "signature_text": d.signature_text,
            "namespace": d.namespace,
            "nesting_depth": nesting_depth,
            "has_parent_scope": nesting_depth > 0,
            "hub_score": cand.hub_score,
            "is_test": cand.is_test,
            # Embedding signal (continuous)
            "emb_score": emb_scores.get(uid),
            "emb_rank": emb_ranks.get(uid),
            # Term match signal (raw counts)
            "term_match_count": cand.term_match_count if cand.from_term_match else None,
            "term_total_matches": cand.term_total_matches if cand.from_term_match else None,
            # Lexical signal (raw count)
            "lex_hit_count": cand.lexical_hit_count if cand.from_lexical else None,
            # Graph signal (categorical)
            "graph_edge_type": cand.graph_edge_type,
            "graph_seed_rank": cand.graph_seed_rank,
            # Symbol/explicit signal (categorical)
            "symbol_source": cand.symbol_source,
            # Import signal (categorical)
            "import_direction": cand.import_direction,
            # Retriever agreement
            "retriever_hits": retriever_hits,
        })

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    # Query features
    query_features = {
        "query_len": len(query),
        "has_identifier": bool(parsed.explicit_symbols),
        "has_path": bool(parsed.explicit_paths),
        "identifier_density": (
            len(parsed.explicit_symbols) / max(len(query.split()), 1)
        ),
        "has_numbers": any(c.isdigit() for c in query),
        "has_quoted_strings": '"' in query or "'" in query,
        "term_count": len(parsed.primary_terms) + len(parsed.secondary_terms),
    }

    # Repo features
    coordinator = app_ctx.coordinator
    repo_features: dict[str, Any] = {}
    try:
        with coordinator.db.session() as session:
            from sqlmodel import func, select

            from codeplane.index.models import DefFact, File

            def_count = session.exec(select(func.count()).select_from(DefFact)).one()
            file_count = session.exec(select(func.count()).select_from(File)).one()
            repo_features = {
                "object_count": def_count,
                "file_count": file_count,
            }
    except Exception:  # noqa: BLE001
        repo_features = {"object_count": 0, "file_count": 0}

    return {
        "query_features": query_features,
        "repo_features": repo_features,
        "candidates": candidates_out,
        "diagnostics": {
            "elapsed_ms": elapsed_ms,
            "candidate_count": len(candidates_out),
            "emb_hits": len(emb_scores),
            "term_hits": sum(1 for c in merged.values() if c.from_term_match),
            "lex_hits": sum(1 for c in merged.values() if c.from_lexical),
            "graph_hits": sum(1 for c in merged.values() if c.from_graph),
            "symbol_hits": sum(1 for c in merged.values() if c.from_explicit),
        },
    }


def register_raw_signals_tool(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register the recon_raw_signals tool with FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Recon: raw retrieval signals for ranking training",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_raw_signals(
        ctx: Context,  # noqa: ARG001
        query: str = Field(
            description=(
                "A single query string to evaluate against the current index. "
                "Returns raw per-def retrieval signals from all retrievers "
                "without any model inference, filtering, or ranking. "
                "Used for ranking model training data collection."
            ),
        ),
        seeds: list[str] = Field(
            default_factory=list,
            description="Symbol names to inject as explicit seed candidates.",
        ),
        pins: list[str] = Field(
            default_factory=list,
            description="File paths to inject as pinned candidates (all defs in each file).",
        ),
    ) -> dict[str, Any]:
        """Raw retrieval signals for ranking model training.

        Runs all retrievers (embedding, lexical, term match, graph,
        symbol) against the current index and returns the unfiltered
        candidate pool with per-retriever scores and ranks per DefFact.

        Does NOT run any model (ranker, cutoff, gate).
        Does NOT filter, sort, or truncate the candidate pool.
        Returns the raw union of all retriever outputs.
        """
        result = await _raw_signals_pipeline(
            app_ctx, query,
            seeds=seeds or None,
            pins=pins or None,
        )

        from codeplane.mcp.delivery import wrap_response

        return wrap_response(
            result,
            resource_kind="raw_signals",
            session_id=ctx.session_id,
        )
