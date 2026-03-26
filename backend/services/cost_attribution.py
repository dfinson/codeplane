"""Post-job cost attribution pipeline.

Runs after a job completes to compute cost breakdowns by dimension
(phase, tool category, turn) and write them to the attribution table.
Also computes derived summary stats (turn economics, file I/O waste).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from backend.persistence.cost_attribution_repo import CostAttributionRepo
from backend.persistence.file_access_repo import FileAccessRepo
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

log = structlog.get_logger()


async def compute_attribution(session: AsyncSession, job_id: str) -> None:
    """Compute and store cost attribution for a completed job.

    Reads all spans for the job, aggregates by dimension, writes
    attribution rows, and updates summary turn stats.
    """
    spans_repo = TelemetrySpansRepo(session)
    attr_repo = CostAttributionRepo(session)
    summary_repo = TelemetrySummaryRepo(session)
    file_repo = FileAccessRepo(session)

    spans = await spans_repo.list_for_job(job_id)
    if not spans:
        log.info("cost_attribution_skip_no_spans", job_id=job_id)
        return

    # --- Aggregate by dimension ---
    by_phase: dict[str, dict[str, Any]] = defaultdict(lambda: _zero_bucket())
    by_category: dict[str, dict[str, Any]] = defaultdict(lambda: _zero_bucket())
    by_turn: dict[int, dict[str, Any]] = defaultdict(lambda: _zero_bucket())

    for span in spans:
        attrs = span.get("attrs", {})
        cost = span.get("cost_usd") or attrs.get("cost", 0.0)
        in_tok = span.get("input_tokens") or attrs.get("input_tokens", 0)
        out_tok = span.get("output_tokens") or attrs.get("output_tokens", 0)

        # Phase dimension
        phase = span.get("execution_phase") or "unknown"
        _accumulate(by_phase[phase], cost, in_tok, out_tok)

        # Tool category dimension (tool spans only)
        if span.get("span_type") == "tool":
            cat = span.get("tool_category") or "other"
            _accumulate(by_category[cat], cost, in_tok, out_tok)

        # Turn dimension (LLM spans carry the cost)
        turn = span.get("turn_number")
        if turn is not None and span.get("span_type") == "llm":
            _accumulate(by_turn[turn], cost, in_tok, out_tok)

    # --- Write attribution rows ---
    rows: list[dict[str, Any]] = []
    for bucket, data in by_phase.items():
        rows.append({"dimension": "phase", "bucket": bucket, **data})
    for bucket, data in by_category.items():
        rows.append({"dimension": "tool_category", "bucket": bucket, **data})
    for turn_num, data in sorted(by_turn.items()):
        rows.append({"dimension": "turn", "bucket": str(turn_num), **data})

    await attr_repo.insert_batch(job_id=job_id, rows=rows)
    log.info(
        "cost_attribution_written",
        job_id=job_id,
        phase_buckets=len(by_phase),
        category_buckets=len(by_category),
        turn_buckets=len(by_turn),
    )

    # --- Compute turn economics for summary ---
    turn_costs = [d["cost_usd"] for d in by_turn.values()]
    total_turns = len(turn_costs)
    if total_turns > 0:
        peak = max(turn_costs)
        avg = sum(turn_costs) / total_turns
        sorted_turns = sorted(by_turn.keys())
        mid = total_turns // 2
        first_half = sum(by_turn[t]["cost_usd"] for t in sorted_turns[:mid])
        second_half = sum(by_turn[t]["cost_usd"] for t in sorted_turns[mid:])
    else:
        peak = avg = first_half = second_half = 0.0

    # --- File I/O stats ---
    file_stats = await file_repo.reread_stats(job_id)

    await summary_repo.set_turn_stats(
        job_id,
        unique_files_read=file_stats.get("unique_files", 0),
        file_reread_count=file_stats.get("reread_count", 0),
        peak_turn_cost_usd=peak,
        avg_turn_cost_usd=avg,
        cost_first_half_usd=first_half,
        cost_second_half_usd=second_half,
    )

    log.info(
        "cost_attribution_summary_updated",
        job_id=job_id,
        total_turns=total_turns,
        peak_turn_cost=round(peak, 6),
        rerereads=file_stats.get("reread_count", 0),
    )


def _zero_bucket() -> dict[str, Any]:
    return {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "call_count": 0}


def _accumulate(bucket: dict[str, Any], cost: float, in_tok: int, out_tok: int) -> None:
    bucket["cost_usd"] += float(cost or 0)
    bucket["input_tokens"] += int(in_tok or 0)
    bucket["output_tokens"] += int(out_tok or 0)
    bucket["call_count"] += 1
