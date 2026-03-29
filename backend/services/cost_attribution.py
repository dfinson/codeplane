"""Post-job cost attribution pipeline.

Runs after a job completes to compute cost breakdowns by dimension
(phase, tool category, turn) and write them to the attribution table.
Also computes derived summary stats (turn economics, file I/O waste).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.api_schemas import ExecutionPhase
from backend.services.tool_classifier import classify_tool

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from backend.persistence.cost_attribution_repo import CostAttributionRepo
from backend.persistence.file_access_repo import FileAccessRepo
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

log = structlog.get_logger()

_TOOL_CATEGORY_ACTIVITY = {
    "file_write": "code_changes",
    "git": "code_changes",
    "file_read": "code_reading",
    "file_search": "search_discovery",
    "browser": "search_discovery",
    "shell": "command_execution",
    "agent": "delegation",
    "system": "reasoning",
    "other": "command_execution",
}


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
    by_activity: dict[str, dict[str, Any]] = defaultdict(lambda: _zero_bucket())
    by_turn: dict[int, dict[str, Any]] = defaultdict(lambda: _zero_bucket())
    turn_contexts: dict[int, dict[str, Any]] = defaultdict(_zero_turn_context)
    normalized_phases = _infer_execution_phases(spans)
    spans_missing_phase = 0

    for span, phase in zip(spans, normalized_phases, strict=False):
        attrs = span.get("attrs", {})
        cost = span.get("cost_usd") or attrs.get("cost", 0.0)
        in_tok = span.get("input_tokens") or attrs.get("input_tokens", 0)
        out_tok = span.get("output_tokens") or attrs.get("output_tokens", 0)

        if phase is not None:
            turn = span.get("turn_number")
            if turn is not None:
                turn_contexts[int(turn)]["phase"] = phase
        else:
            spans_missing_phase += 1

        if span.get("span_type") == "tool":
            cat = classify_tool(span.get("name") or "") or "other"
            turn = span.get("turn_number")
            if turn is not None:
                turn_contexts[int(turn)]["tool_categories"].append(cat)

        # Turn dimension (LLM spans carry the cost)
        turn = span.get("turn_number")
        if turn is not None and span.get("span_type") == "llm":
            _accumulate(by_turn[turn], cost, in_tok, out_tok)
            turn_contexts[int(turn)]["cost_usd"] += float(cost or 0)
            turn_contexts[int(turn)]["input_tokens"] += int(in_tok or 0)
            turn_contexts[int(turn)]["output_tokens"] += int(out_tok or 0)

    for _turn_num, context in turn_contexts.items():
        weights = _derive_activity_weights(
            phase=context.get("phase"),
            tool_categories=context.get("tool_categories", []),
        )
        if not weights:
            continue

        allocations = _allocate_weighted_totals(
            weights=weights,
            cost_usd=float(context.get("cost_usd", 0.0) or 0.0),
            input_tokens=int(context.get("input_tokens", 0) or 0),
            output_tokens=int(context.get("output_tokens", 0) or 0),
        )
        for bucket, allocated in allocations.items():
            _accumulate(
                by_activity[bucket],
                float(allocated["cost_usd"]),
                int(allocated["input_tokens"]),
                int(allocated["output_tokens"]),
                call_count=1,
            )

    # --- Write attribution rows ---
    rows: list[dict[str, Any]] = []
    for bucket, data in by_activity.items():
        rows.append({"dimension": "activity", "bucket": bucket, **data})
    for turn_num, data in sorted(by_turn.items()):
        rows.append({"dimension": "turn", "bucket": str(turn_num), **data})

    await attr_repo.insert_batch(job_id=job_id, rows=rows)
    log.info(
        "cost_attribution_written",
        job_id=job_id,
        activity_buckets=len(by_activity),
        turn_buckets=len(by_turn),
        spans_missing_phase=spans_missing_phase,
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

    # --- Diff line counts from the latest diff snapshot ---
    diff_added = 0
    diff_removed = 0
    try:
        from backend.persistence.event_repo import EventRepo
        from backend.models.events import DomainEventKind

        event_repo = EventRepo(session)
        diff_events = await event_repo.list_by_job(
            job_id, kinds=[DomainEventKind.diff_updated], limit=100,
        )
        if diff_events:
            changed_files = diff_events[-1].payload.get("changed_files", [])
            for f in changed_files:
                diff_added += f.get("additions", 0)
                diff_removed += f.get("deletions", 0)
    except Exception:
        log.debug("diff_lines_extraction_failed", job_id=job_id, exc_info=True)

    await summary_repo.set_turn_stats(
        job_id,
        unique_files_read=file_stats.get("unique_files", 0),
        file_reread_count=file_stats.get("reread_count", 0),
        peak_turn_cost_usd=peak,
        avg_turn_cost_usd=avg,
        cost_first_half_usd=first_half,
        cost_second_half_usd=second_half,
        diff_lines_added=diff_added,
        diff_lines_removed=diff_removed,
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


def _zero_turn_context() -> dict[str, Any]:
    return {"phase": None, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "tool_categories": []}


def _infer_execution_phases(spans: list[dict[str, Any]]) -> list[str | None]:
    valid_phases = {phase.value for phase in ExecutionPhase}
    inferred: list[str | None] = []

    last_known: str | None = None
    for span in spans:
        raw_phase = span.get("execution_phase")
        phase = raw_phase if raw_phase in valid_phases else None
        if phase is None:
            phase = last_known
        else:
            last_known = phase
        inferred.append(phase)

    next_known: str | None = None
    for index in range(len(spans) - 1, -1, -1):
        raw_phase = spans[index].get("execution_phase")
        if raw_phase in valid_phases:
            next_known = raw_phase
        elif inferred[index] is None and next_known is not None:
            inferred[index] = next_known

    return inferred


def _derive_activity_weights(*, phase: str | None, tool_categories: list[str]) -> dict[str, int]:
    if phase == ExecutionPhase.verification.value:
        return {"verification": 1}
    if phase == ExecutionPhase.environment_setup.value:
        return {"setup": 1}
    if phase in {ExecutionPhase.finalization.value, ExecutionPhase.post_completion.value}:
        return {"wrap_up": 1}

    weights: dict[str, int] = {}
    for category in tool_categories:
        activity = _TOOL_CATEGORY_ACTIVITY.get(category, "other_tools")
        weights[activity] = weights.get(activity, 0) + 1

    if not weights:
        return {"reasoning": 1}
    return weights


def _allocate_weighted_totals(
    *,
    weights: dict[str, int],
    cost_usd: float,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, dict[str, float | int]]:
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {}

    allocations: dict[str, dict[str, float | int]] = {}
    remaining_cost = float(cost_usd)
    remaining_input = int(input_tokens)
    remaining_output = int(output_tokens)
    items = list(weights.items())
    for index, (bucket, weight) in enumerate(items):
        is_last = index == len(items) - 1
        if is_last:
            alloc_cost = remaining_cost
            alloc_input = remaining_input
            alloc_output = remaining_output
        else:
            share = weight / total_weight
            alloc_cost = cost_usd * share
            alloc_input = int(input_tokens * share)
            alloc_output = int(output_tokens * share)
            remaining_cost -= alloc_cost
            remaining_input -= alloc_input
            remaining_output -= alloc_output
        allocations[bucket] = {
            "cost_usd": alloc_cost,
            "input_tokens": alloc_input,
            "output_tokens": alloc_output,
        }

    return allocations


def _accumulate(bucket: dict[str, Any], cost: float, in_tok: int, out_tok: int, *, call_count: int = 1) -> None:
    bucket["cost_usd"] += float(cost or 0)
    bucket["input_tokens"] += int(in_tok or 0)
    bucket["output_tokens"] += int(out_tok or 0)
    bucket["call_count"] += int(call_count or 0)
