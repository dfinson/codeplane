"""Recon pipeline — ML-based context retrieval + tool registration.

Composes: raw_signals pipeline → gate → ranker → cutoff → output.
Also registers the ``recon``, ``recon_map``, and optionally
``recon_raw_signals`` MCP tools.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


async def _recon_pipeline(
    app_ctx: AppContext,
    task: str,
    seeds: list[str] | None = None,
    pins: list[str] | None = None,
) -> dict[str, Any]:
    """Run the full recon pipeline: retrieve → gate → rank → cut.

    Returns a dict with gate_label, ranked candidates, and diagnostics.
    """
    from codeplane.mcp.tools.recon.raw_signals import _raw_signals_pipeline
    from codeplane.ranking.cutoff import load_cutoff
    from codeplane.ranking.features import (
        extract_cutoff_features,
        extract_gate_features,
        extract_ranker_features,
    )
    from codeplane.ranking.gate import load_gate
    from codeplane.ranking.models import GateLabel
    from codeplane.ranking.ranker import load_ranker

    t0 = time.monotonic()

    # 1. Get raw signals
    raw = await _raw_signals_pipeline(app_ctx, task, seeds=seeds, pins=pins)
    candidates = raw.get("candidates", [])
    query_features = raw.get("query_features", {})
    repo_features = raw.get("repo_features", {})
    diagnostics = raw.get("diagnostics", {})

    # 2. Gate
    gate = load_gate()
    gate_features = extract_gate_features(candidates, query_features, repo_features)
    gate_label = gate.classify(gate_features)

    if gate_label != GateLabel.OK:
        return {
            "gate_label": gate_label.value,
            "candidates": [],
            "predicted_n": 0,
            "diagnostics": {
                **diagnostics,
                "gate_label": gate_label.value,
                "total_ms": round((time.monotonic() - t0) * 1000),
            },
        }

    # 3. Rank
    ranker = load_ranker()
    ranker_features = extract_ranker_features(candidates, query_features)
    scores = ranker.score(ranker_features)

    # Pair candidates with scores and sort descending
    scored = sorted(
        zip(candidates, scores),
        key=lambda x: -x[1],
    )

    # 4. Cutoff
    cutoff = load_cutoff()
    ranked_for_cutoff = [{**c, "ranker_score": s} for c, s in scored]
    cutoff_features = extract_cutoff_features(
        ranked_for_cutoff, query_features, repo_features,
    )
    predicted_n = cutoff.predict(cutoff_features)

    # 5. Build output — top N ranked DefFacts
    top_n = scored[:predicted_n]
    result_candidates = [
        {
            "def_uid": c["def_uid"],
            "path": c["path"],
            "kind": c["kind"],
            "name": c["name"],
            "lexical_path": c.get("lexical_path", ""),
            "start_line": c["start_line"],
            "end_line": c["end_line"],
            "score": round(s, 6),
        }
        for c, s in top_n
    ]

    diagnostics["gate_label"] = gate_label.value
    diagnostics["predicted_n"] = predicted_n
    diagnostics["total_candidates"] = len(candidates)
    diagnostics["total_ms"] = round((time.monotonic() - t0) * 1000)

    return {
        "gate_label": gate_label.value,
        "candidates": result_candidates,
        "predicted_n": predicted_n,
        "diagnostics": diagnostics,
    }


def register_tools(mcp: FastMCP, app_ctx: AppContext, *, dev_mode: bool = False) -> None:
    """Register recon tools with FastMCP server."""

    # Register raw signals endpoint only in dev mode (ranking training)
    if dev_mode:
        from codeplane.mcp.tools.recon.raw_signals import register_raw_signals_tool

        register_raw_signals_tool(mcp, app_ctx)

    @mcp.tool(
        annotations={
            "title": "Recon: task-aware context retrieval",
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
                "or domain terms when known."
            ),
        ),
        seeds: list[str] = Field(
            default_factory=list,
            description=(
                "Symbol names to seed retrieval with "
                "(e.g., ['IndexCoordinatorEngine', 'FactQueries'])."
            ),
        ),
        pins: list[str] = Field(
            default_factory=list,
            description=(
                "File paths to pin as relevant "
                "(e.g., ['src/core/base_model.py'])."
            ),
        ),
    ) -> dict[str, Any]:
        """Task-aware context retrieval — returns ranked semantic spans.

        Pipeline: retrieve → gate → rank → cutoff → return top N DefFacts.

        Each result includes def_uid, path, name, kind, line range, and
        ranker score. Read files via terminal for full content.
        """
        recon_id = uuid.uuid4().hex[:12]

        result = await _recon_pipeline(
            app_ctx, task,
            seeds=seeds or None,
            pins=pins or None,
        )

        gate_label = result["gate_label"]
        candidates = result["candidates"]
        diagnostics = result.get("diagnostics", {})

        response: dict[str, Any] = {
            "recon_id": recon_id,
            "gate_label": gate_label,
            "candidates": candidates,
            "summary": (
                f"gate={gate_label}, {len(candidates)} relevant spans"
                if gate_label == "OK"
                else f"gate={gate_label} — query not actionable"
            ),
            "diagnostics": diagnostics,
        }

        if gate_label == "OK" and candidates:
            top_paths = list(dict.fromkeys(c["path"] for c in candidates[:8]))
            response["agentic_hint"] = (
                f"Recon found {len(candidates)} relevant spans across "
                f"{len(set(c['path'] for c in candidates))} files. "
                f"Top files: {', '.join(top_paths)}. "
                f"Read files via terminal (cat/head) for full content."
            )
        elif gate_label != "OK":
            hints = {
                "UNSAT": "Query makes wrong assumptions about this repo. Verify and retry.",
                "BROAD": "Task is too broad. Decompose into smaller sub-tasks.",
                "AMBIG": "Query is ambiguous. Specify which subsystem you mean.",
            }
            response["agentic_hint"] = hints.get(gate_label, "Retry with a clearer query.")

        from codeplane.mcp.delivery import wrap_response

        return wrap_response(
            response,
            resource_kind="recon_result",
            session_id=ctx.session_id,
        )

    @mcp.tool(
        annotations={
            "title": "Recon: repository structure map",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_map(
        ctx: Context,  # noqa: ARG001
    ) -> dict[str, Any]:
        """Repository structure map — file tree, languages, entry points.

        Returns the repo's directory structure, language distribution,
        and key entry points. Use this to orient before calling recon.
        """
        repo_map: dict[str, Any] = {}
        try:
            map_result = await app_ctx.coordinator.map_repo(
                include=["structure", "languages", "entry_points"],
                depth=3,
                limit=100,
            )
            from codeplane.mcp.tools.index import _build_overview, _map_repo_sections_to_text

            repo_map = {
                "overview": _build_overview(map_result),
                **_map_repo_sections_to_text(map_result),
            }
        except Exception:  # noqa: BLE001
            log.warning("recon_map.failed", exc_info=True)
            repo_map = {"error": "Failed to build repo map"}

        from codeplane.mcp.delivery import wrap_response

        return wrap_response(
            repo_map,
            resource_kind="repo_map",
            session_id=ctx.session_id,
        )
