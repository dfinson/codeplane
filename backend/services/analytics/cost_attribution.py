"""Post-job cost attribution pipeline.

Runs after a job completes to compute cost breakdowns by dimension
(phase, tool category, turn) and write them to the attribution table.
Also computes derived summary stats (turn economics, file I/O waste,
intent-refined activity classification, and edit one-shot rate).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

import structlog
from sqlalchemy.exc import DBAPIError

from backend.models.api_schemas import ExecutionPhase
from backend.services.tools.tool_classifier import (
    classify_action_from_tools,
    classify_shell_command,
    classify_tool,
    classify_tool_activity,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import TelemetrySpanRow
    from backend.persistence.cost_attribution_repo import CostAttributionRepository
    from backend.persistence.file_access_repo import FileAccessRepository
    from backend.persistence.file_cost_repo import FileCostRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
    from backend.persistence.trail_repo import TrailNodeRepository

log = structlog.get_logger()


class CostBucket(TypedDict):
    """Aggregated cost metrics for a single attribution dimension."""

    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    call_count: int


class TurnContext(TypedDict):
    """Per-turn cost context including phase and tool breakdown."""

    phase: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    tool_categories: list[str]
    shell_commands: list[str]
    tool_activity_weights: NotRequired[list[tuple[str, int]]]
    is_subagent: NotRequired[bool]


# ---------------------------------------------------------------------------
# Intent-based turn classification
#
# Each turn gets ONE activity label based on its highest-priority action.
# Priority: implementation/debugging > verification > git_ops > setup >
#           investigation > overhead > reasoning > communication
#
# 9 canonical categories:
#   implementation, debugging, investigation, verification, git_ops,
#   communication, setup, reasoning, overhead
#
# Shell commands are classified by their actual content, not the job prompt.
# ---------------------------------------------------------------------------

# Categories that represent file-write actions
_WRITE_TOOL_CATEGORIES = {"file_write", "git_write"}


def _classify_turn_intent(
    context: TurnContext,
) -> str:
    """Assign a single dominant activity to a turn based on its tools.

    Uses a priority ladder: the highest-value action wins the whole turn.

    8 canonical categories:
        implementation, investigation, verification,
        git_ops, communication, setup, reasoning, overhead
    """
    cats = set(context.get("tool_categories", []))
    shell_cmds = context.get("shell_commands", [])

    # Classify each shell command individually
    shell_intents: set[str] = set()
    for cmd in shell_cmds:
        shell_intents.add(classify_shell_command(cmd))

    has_writes = bool(cats & {"file_write"})
    has_git_write = bool(cats & {"git_write"})
    has_git_read = bool(cats & {"git_read"})
    has_reads = bool(cats & {"file_read"})
    has_search = bool(cats & {"file_search", "browser"})
    has_bookkeeping = "bookkeeping" in cats
    has_thinking = "thinking" in cats
    has_agents = "agent" in cats

    # Priority 1: If the agent edited files — implementation
    if has_writes:
        return "implementation"

    # Priority 1b: Shell commands that modify files (sed, rm, mv, cp, etc.)
    if "implementation" in shell_intents:
        return "implementation"

    # Priority 2: If the agent ran tests, this is verification
    if "verification" in shell_intents:
        return "verification"

    # Priority 3: Git write operations (commit, push, merge — dedicated tools or shell)
    if "git_ops" in shell_intents or has_git_write:
        return "git_ops"

    # Priority 4: Setup/install commands
    if "setup" in shell_intents:
        return "setup"

    # Priority 5: Investigation — reading, searching, browsing, git reads
    if has_reads or has_search or has_git_read or "investigation" in shell_intents:
        return "investigation"

    # Priority 5b: Pure delegation — only agent tools, no other content tools.
    # The per-tool weighted path resolves this to the sub-agent's actual activity;
    # this fallback only fires when tool_activity_weights is empty.
    # Conservative: if we can't resolve sub-agent activity, classify as investigation.
    if has_agents:
        return "investigation"

    # Priority 6: Unclassified shell commands (arbitrary bash)
    if "shell_other" in shell_intents:
        return "investigation"  # conservative: unknown bash is probably exploration

    # Priority 7: Pure overhead — only bookkeeping tools, no real work
    if has_bookkeeping:
        return "overhead"

    # Priority 8: Reasoning — only Think tool
    if has_thinking:
        return "reasoning"

    # No tools at all — user communication or reasoning
    out_tok = context.get("output_tokens", 0) or 0
    if out_tok > 0:
        return "communication"
    return "reasoning"


# ---------------------------------------------------------------------------
# Sub-agent activity propagation
#
# Associates invoking turns (those with "agent" tool category) with the
# sub-agent turns that follow them.  Computes the activity distribution
# of the sub-agent turns so the invoking turn's cost can be attributed
# to what the sub-agent actually did instead of a blanket "investigation".
# ---------------------------------------------------------------------------


def _compute_subagent_distributions(
    turn_contexts: dict[int, TurnContext],
) -> dict[int, dict[str, float]]:
    """Map invoking turn numbers → activity distributions of their sub-agent turns.

    Walks turns in order.  Sub-agent turns (is_subagent=True on their LLM span)
    are grouped under the most recent preceding non-sub-agent turn that has an
    "agent" tool category.  The activity distribution is derived from the
    sub-agent turns' own tool classifications.

    Returns {invoking_turn: {"implementation": 0.7, "investigation": 0.3, ...}}
    """
    sorted_turns = sorted(turn_contexts.keys())
    if not sorted_turns:
        return {}

    # Identify invoking turns and sub-agent turns
    invoking_turns: list[int] = []
    subagent_turns: set[int] = set()
    for t in sorted_turns:
        ctx = turn_contexts[t]
        if ctx.get("is_subagent"):
            subagent_turns.add(t)
        elif "agent" in (ctx.get("tool_categories") or []):
            invoking_turns.append(t)

    if not invoking_turns or not subagent_turns:
        return {}

    # Associate sub-agent turns with their closest preceding invoking turn
    # Each sub-agent turn belongs to the most recent invoking turn before it
    invoking_to_subagent: dict[int, list[int]] = defaultdict(list)
    for sa_turn in sorted(subagent_turns):
        # Find the closest invoking turn that precedes this sub-agent turn
        owner = None
        for inv_turn in reversed(invoking_turns):
            if inv_turn < sa_turn:
                owner = inv_turn
                break
        if owner is not None:
            invoking_to_subagent[owner].append(sa_turn)

    # For each invoking turn, compute the weighted activity distribution
    # of its associated sub-agent turns
    result: dict[int, dict[str, float]] = {}
    for inv_turn, sa_turns in invoking_to_subagent.items():
        activity_costs: dict[str, float] = defaultdict(float)
        total_cost = 0.0

        for sa_t in sa_turns:
            sa_ctx = turn_contexts[sa_t]
            sa_cost = float(sa_ctx.get("cost_usd", 0.0) or 0.0)
            if sa_cost <= 0:
                sa_cost = 1.0  # uniform weight if no cost info

            # Classify the sub-agent turn by its own tools
            sa_weights: list[tuple[str, int]] = sa_ctx.get("tool_activity_weights", [])
            if sa_weights:
                # Use per-tool activity classification (skip _delegation sentinels
                # from nested sub-agent calls — fall back to investigation for those)
                weight_total = sum(w for _, w in sa_weights)
                for act, w in sa_weights:
                    resolved_act = act if act != "_delegation" else "investigation"
                    fraction = w / weight_total if weight_total > 0 else 1 / len(sa_weights)
                    activity_costs[resolved_act] += sa_cost * fraction
            else:
                # No tools — use intent classifier
                intent = _classify_turn_intent(sa_ctx)
                activity_costs[intent] += sa_cost
            total_cost += sa_cost

        if total_cost > 0:
            result[inv_turn] = {act: cost / total_cost for act, cost in activity_costs.items()}

    return result


def _classify_motivation(
    turn_num: int,
    trail_nodes: list[dict[str, Any]],
    turn_context: TurnContext,
) -> str:
    """Classify a turn's motivation from trail node metadata (Item 17)."""
    nodes = [n for n in trail_nodes if n.get("turn_number") == turn_num]

    # Priority 1: Error recovery — is_retry or error_kind present
    if any(n.get("is_retry") or n.get("error_kind") for n in nodes):
        return "error_recovery"

    # Priority 2: Test-driven — shell commands include test runners
    shell_cmds = turn_context.get("shell_commands", [])
    if any(classify_shell_command(cmd) == "verification" for cmd in shell_cmds):
        return "test_driven_iteration"

    # Priority 3: Plan execution — trail node has plan_item_id
    if any(n.get("plan_item_id") for n in nodes):
        return "plan_execution"

    # Priority 4: User-directed — first turn or immediately after user message
    if turn_num <= 1:
        return "user_directed"

    # Priority 5: Context gathering — turn is pure reads, no writes
    cats = set(turn_context.get("tool_categories", []))
    if cats and not (cats & {"file_write", "git_write"}):
        return "context_gathering"

    # Default: agent exploration
    return "agent_exploration"


async def compute_attribution(
    session: AsyncSession,
    job_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Compute and store cost attribution for a completed job.

    Reads all spans for the job, aggregates by dimension, writes
    attribution rows, and updates summary turn stats.  Uses keyword-based
    intent analysis on the job prompt to refine coarse activity buckets
    (e.g. ``code_changes`` → ``debugging``, ``refactoring``, ``feature_dev``).
    Also detects edit→shell→edit retry loops for one-shot rate computation.
    """
    from backend.persistence.cost_attribution_repo import CostAttributionRepository
    from backend.persistence.file_access_repo import FileAccessRepository
    from backend.persistence.file_cost_repo import FileCostRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

    trail_repo = None
    if session_factory is not None:
        from backend.persistence.trail_repo import TrailNodeRepository

        trail_repo = TrailNodeRepository(session_factory)

    await _compute_attribution(
        job_id=job_id,
        spans_repo=TelemetrySpansRepository(session),
        attr_repo=CostAttributionRepository(session),
        summary_repo=TelemetrySummaryRepository(session),
        file_repo=FileAccessRepository(session),
        file_cost_repo=FileCostRepository(session),
        session=session,
        trail_repo=trail_repo,
    )


async def _compute_attribution(
    *,
    job_id: str,
    spans_repo: TelemetrySpansRepository,
    attr_repo: CostAttributionRepository,
    summary_repo: TelemetrySummaryRepository,
    file_repo: FileAccessRepository,
    file_cost_repo: FileCostRepository,
    session: AsyncSession,
    trail_repo: TrailNodeRepository | None = None,
) -> None:
    """Core attribution logic with explicit dependencies."""

    spans = await spans_repo.list_for_job(job_id)
    if not spans:
        log.info("cost_attribution_skip_no_spans", job_id=job_id)
        return
    llm_spans = [span for span in spans if span.get("span_type") == "llm"]
    currency_qualified = bool(llm_spans) and all(span.get("cost_usd") is not None for span in llm_spans)

    # --- Load job metadata for sub-classification and model tagging ---
    from sqlalchemy import text as sa_text

    job_meta = await session.execute(
        sa_text(
            "SELECT j.description, j.prompt, "
            "COALESCE(t.model, '') AS model "
            "FROM jobs j "
            "LEFT JOIN job_telemetry_summary t ON t.job_id = j.id AND t.session_kind = 'job' "
            "WHERE j.id = :jid"
        ),
        {"jid": job_id},
    )
    job_row = job_meta.mappings().first()
    job_row_dict: dict[str, Any] = dict(job_row) if job_row else {}
    job_model = job_row_dict.get("model", "") or ""

    # --- Aggregate by dimension ---
    by_turn: dict[int, CostBucket] = defaultdict(lambda: _zero_bucket())
    by_phase: dict[str, CostBucket] = defaultdict(lambda: _zero_bucket())
    turn_contexts: dict[int, TurnContext] = defaultdict(_zero_turn_context)
    normalized_phases = _infer_execution_phases(spans)
    spans_missing_phase = 0

    for span, phase in zip(spans, normalized_phases, strict=False):
        attrs = span.get("attrs", {})
        cost = span.get("cost_usd")
        in_tok = span.get("input_tokens") or attrs.get("input_tokens", 0)
        out_tok = span.get("output_tokens") or attrs.get("output_tokens", 0)
        cache_r = span.get("cache_read_tokens") or 0
        cache_w = span.get("cache_write_tokens") or 0

        if phase is not None:
            turn = span.get("turn_number")
            if turn is not None:
                turn_contexts[int(turn)]["phase"] = phase
        else:
            spans_missing_phase += 1

        if span.get("span_type") == "tool":
            cat = classify_tool(span.get("name") or "") or "other"
            tool_args_raw = span.get("tool_args_json")
            tool_activity = classify_tool_activity(span.get("name") or "", tool_args_raw)
            # Weight by serialised args length — direct proxy for output tokens
            # the LLM spent generating this tool call's arguments.
            args_weight = len(tool_args_raw) if isinstance(tool_args_raw, str) else 1
            turn = span.get("turn_number")
            if turn is not None:
                turn_contexts[int(turn)]["tool_categories"].append(cat)
                turn_contexts[int(turn)].setdefault("tool_activity_weights", []).append((tool_activity, args_weight))
                # Collect shell command text for intent classification
                if cat == "shell":
                    tool_args = tool_args_raw
                    if isinstance(tool_args, str):
                        try:
                            import json as _json

                            parsed = _json.loads(tool_args)
                            cmd = parsed.get("command", "") or parsed.get("cmd", "")
                        except (ValueError, TypeError):
                            cmd = ""
                    elif isinstance(tool_args, dict):
                        cmd = tool_args.get("command", "") or tool_args.get("cmd", "")
                    else:
                        cmd = ""
                    if cmd:
                        turn_contexts[int(turn)]["shell_commands"].append(str(cmd))

        # Turn dimension (LLM spans carry the cost)
        turn = span.get("turn_number")
        if turn is not None and span.get("span_type") == "llm":
            _accumulate(by_turn[turn], cost, in_tok, out_tok, cache_read=cache_r, cache_write=cache_w)
            turn_contexts[int(turn)]["cost_usd"] += float(cost or 0)
            turn_contexts[int(turn)]["input_tokens"] += int(in_tok or 0)
            turn_contexts[int(turn)]["output_tokens"] += int(out_tok or 0)
            turn_contexts[int(turn)]["cache_read_tokens"] += int(cache_r or 0)
            turn_contexts[int(turn)]["cache_write_tokens"] += int(cache_w or 0)
            # Track sub-agent status from LLM span attrs
            if attrs.get("is_subagent"):
                turn_contexts[int(turn)]["is_subagent"] = True

    # --- One-shot rate tracking ---
    # Track edit→shell→edit retry patterns per turn, aggregated by action.
    one_shot_by_action: dict[str, dict[str, int]] = defaultdict(
        lambda: {"edit_turns": 0, "one_shot_turns": 0, "retries": 0}
    )

    # --- Load trail nodes for purpose attribution ---
    trail_list: list[dict[str, Any]] = []
    try:
        if trail_repo is not None:
            trail_nodes = await trail_repo.get_by_job(job_id, limit=1000)
            trail_list = [
                {
                    "turn_number": getattr(n, "turn_number", None) or getattr(n, "anchor_seq", None),
                    "purpose": getattr(n, "purpose", None),
                }
                for n in trail_nodes
            ]
    except Exception:
        log.debug("cost_attribution_trail_fetch_failed", job_id=job_id, exc_info=True)

    # Build turn→purpose lookup from trail nodes
    turn_purpose: dict[int, str | None] = {}
    for tn in trail_list:
        t = tn.get("turn_number")
        if t is not None and tn.get("purpose"):
            turn_purpose[int(t)] = tn["purpose"]

    by_action: dict[str, CostBucket] = defaultdict(lambda: _zero_bucket())
    by_activity: dict[str, CostBucket] = defaultdict(lambda: _zero_bucket())
    by_purpose: dict[str, CostBucket] = defaultdict(lambda: _zero_bucket())
    by_action_purpose: dict[str, CostBucket] = defaultdict(lambda: _zero_bucket())

    # --- Sub-agent activity propagation (Option 3) ---
    # Compute what each sub-agent range actually did so we can attribute
    # the invoking turn's delegation cost to the correct activities.
    subagent_distributions = _compute_subagent_distributions(turn_contexts)

    for turn_num_a, context in turn_contexts.items():
        # Deterministic action from tool categories
        action = classify_action_from_tools(
            context.get("tool_categories", []),
            shell_commands=context.get("shell_commands") or None,
        )

        turn_cost = float(context.get("cost_usd", 0.0) or 0.0)
        turn_in = int(context.get("input_tokens", 0) or 0)
        turn_out = int(context.get("output_tokens", 0) or 0)
        turn_cache_r = int(context.get("cache_read_tokens", 0) or 0)
        turn_cache_w = int(context.get("cache_write_tokens", 0) or 0)

        # Action dimension (internal — feeds edit efficiency and matrix views)
        _accumulate(
            by_action[action],
            turn_cost,
            turn_in,
            turn_out,
            cache_read=turn_cache_r,
            cache_write=turn_cache_w,
            call_count=1,
        )

        # Activity dimension (user-facing cost breakdown)
        # Per-tool classification: split cost weighted by tool_args_json length
        # (proxy for output tokens the LLM spent generating each tool call)
        tool_activity_weights = context.get("tool_activity_weights", [])
        if tool_activity_weights:
            # Group weights by activity
            activity_weight_sums: dict[str, int] = defaultdict(int)
            activity_call_counts: dict[str, int] = defaultdict(int)
            total_weight = 0
            for activity, weight in tool_activity_weights:
                activity_weight_sums[activity] += weight
                activity_call_counts[activity] += 1
                total_weight += weight

            # Resolve _delegation sentinel using sub-agent's actual activity
            delegation_weight = activity_weight_sums.pop("_delegation", 0)
            delegation_calls = activity_call_counts.pop("_delegation", 0)
            if delegation_weight > 0:
                dist = subagent_distributions.get(turn_num_a)
                if dist:
                    # Redistribute delegation weight proportionally to sub-agent activities
                    for act, frac in dist.items():
                        activity_weight_sums[act] += int(delegation_weight * frac)
                        activity_call_counts[act] += max(1, int(delegation_calls * frac))
                else:
                    # No sub-agent turns found — redistribute to other tools in this turn,
                    # or fall back to "investigation" if this is a pure delegation turn
                    if activity_weight_sums:
                        # Redistribute proportionally to existing activities
                        existing_total = sum(activity_weight_sums.values())
                        for act in list(activity_weight_sums.keys()):
                            share = activity_weight_sums[act] / existing_total if existing_total > 0 else 1
                            activity_weight_sums[act] += int(delegation_weight * share)
                    else:
                        activity_weight_sums["investigation"] = delegation_weight
                        activity_call_counts["investigation"] = delegation_calls

            for activity, weight_sum in activity_weight_sums.items():
                fraction = weight_sum / total_weight if total_weight > 0 else 1 / len(activity_weight_sums)
                _accumulate(
                    by_activity[activity],
                    turn_cost * fraction,
                    int(turn_in * fraction),
                    int(turn_out * fraction),
                    cache_read=int(turn_cache_r * fraction),
                    cache_write=int(turn_cache_w * fraction),
                    call_count=activity_call_counts.get(activity, 1),
                )
        else:
            # No tools — use turn-level fallback (reasoning / communication)
            activity = _classify_turn_intent(context)
            _accumulate(
                by_activity[activity],
                turn_cost,
                turn_in,
                turn_out,
                cache_read=turn_cache_r,
                cache_write=turn_cache_w,
                call_count=1,
            )

        # Purpose dimension (nullable — only if enriched)
        purpose = turn_purpose.get(turn_num_a)
        if purpose:
            _accumulate(
                by_purpose[purpose],
                turn_cost,
                turn_in,
                turn_out,
                cache_read=turn_cache_r,
                cache_write=turn_cache_w,
                call_count=1,
            )
            # Action×Purpose compound dimension
            compound = f"{action}:{purpose}"
            _accumulate(
                by_action_purpose[compound],
                turn_cost,
                turn_in,
                turn_out,
                cache_read=turn_cache_r,
                cache_write=turn_cache_w,
                call_count=1,
            )

        # Phase dimension — aggregate by execution phase
        phase = context.get("phase")
        if phase:
            _accumulate(
                by_phase[phase],
                turn_cost,
                turn_in,
                turn_out,
                cache_read=turn_cache_r,
                cache_write=turn_cache_w,
            )

        # One-shot detection: does this turn have file_write tools?
        tool_cats = context.get("tool_categories", [])
        has_edits = any(c in _WRITE_TOOL_CATEGORIES for c in tool_cats)
        if has_edits:
            retries = _count_edit_retries(tool_cats)
            one_shot_by_action[action]["edit_turns"] += 1
            one_shot_by_action[action]["retries"] += retries
            if retries == 0:
                one_shot_by_action[action]["one_shot_turns"] += 1

    # --- Write attribution rows ---
    rows: list[dict[str, Any]] = []
    for bucket, data in by_activity.items():
        rows.append({"dimension": "activity", "bucket": bucket, "model": job_model, **data})
    for bucket, data in by_action.items():
        rows.append({"dimension": "action", "bucket": bucket, "model": job_model, **data})
    for bucket, data in by_purpose.items():
        rows.append({"dimension": "purpose", "bucket": bucket, "model": job_model, **data})
    for bucket, data in by_action_purpose.items():
        rows.append({"dimension": "action_purpose", "bucket": bucket, "model": job_model, **data})
    for turn_num, data in sorted(by_turn.items()):
        rows.append({"dimension": "turn", "bucket": str(turn_num), "model": job_model, **data})
    for phase_name, data in by_phase.items():
        rows.append({"dimension": "phase", "bucket": phase_name, "model": job_model, **data})

    await attr_repo.insert_batch(job_id=job_id, rows=rows, currency_qualified=currency_qualified)
    log.info(
        "cost_attribution_written",
        job_id=job_id,
        activity_buckets=len(by_activity),
        action_buckets=len(by_action),
        purpose_buckets=len(by_purpose),
        action_purpose_buckets=len(by_action_purpose),
        turn_buckets=len(by_turn),
        phase_buckets=len(by_phase),
        spans_missing_phase=spans_missing_phase,
    )

    # --- File-centric cost attribution (Item 14) ---
    try:
        file_access_rows = await file_repo.raw_accesses_for_job(job_id)
        if file_access_rows:
            files_by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for fa in file_access_rows:
                turn = fa.get("turn_number")
                if turn is not None:
                    files_by_turn[int(turn)].append(fa)

            file_costs: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"cost_usd": 0.0, "read_cost": 0.0, "write_cost": 0.0, "turn_count": 0}
            )
            for turn_num_f, turn_files in files_by_turn.items():
                turn_cost_f = float(by_turn.get(turn_num_f, _zero_bucket())["cost_usd"])
                if turn_cost_f <= 0 or not turn_files:
                    continue
                unique_files = set(f["file_path"] for f in turn_files)
                share = turn_cost_f / len(unique_files)
                seen_files: set[str] = set()
                for fa in turn_files:
                    fp = fa["file_path"]
                    if fp not in seen_files:
                        seen_files.add(fp)
                        file_costs[fp]["cost_usd"] += share
                        if fa.get("access_type") == "read":
                            file_costs[fp]["read_cost"] += share
                        else:
                            file_costs[fp]["write_cost"] += share
                        file_costs[fp]["turn_count"] += 1

            if file_costs:
                await file_cost_repo.insert_batch(
                    job_id=job_id,
                    rows=[{"file_path": fp, **data} for fp, data in file_costs.items()],
                    currency_qualified=currency_qualified,
                )
    except Exception:
        log.debug("file_cost_attribution_failed", job_id=job_id, exc_info=True)

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

    # --- Diff line counts from trail nodes ---
    diff_added = 0
    diff_removed = 0
    try:
        if trail_repo is not None:
            diff_added, diff_removed = await trail_repo.get_diff_line_counts(job_id)
        else:
            from sqlalchemy import text as sa_text

            result = await session.execute(
                sa_text(
                    "SELECT COALESCE(SUM(diff_additions), 0) AS added, "
                    "COALESCE(SUM(diff_deletions), 0) AS removed "
                    "FROM trail_nodes WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )
            row = result.mappings().first()
            if row:
                diff_added = row["added"]
                diff_removed = row["removed"]
    except (DBAPIError, KeyError):
        log.debug("cost_attribution_diff_stats_failed", job_id=job_id, exc_info=True)

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
        derived_currency_qualified=currency_qualified,
    )

    log.info(
        "cost_attribution_summary_updated",
        job_id=job_id,
        total_turns=total_turns,
        peak_turn_cost=round(peak, 6),
        rerereads=file_stats.get("reread_count", 0),
    )


def _zero_bucket() -> CostBucket:
    return {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "call_count": 0,
    }


def _zero_turn_context() -> TurnContext:
    return {
        "phase": None,
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_categories": [],
        "shell_commands": [],
    }


def _infer_execution_phases(spans: list[dict[str, Any]] | list[TelemetrySpanRow]) -> list[str | None]:
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


def _accumulate(
    bucket: CostBucket,
    cost: float | None,
    in_tok: int,
    out_tok: int,
    *,
    cache_read: int = 0,
    cache_write: int = 0,
    call_count: int = 1,
) -> None:
    bucket["cost_usd"] += float(cost or 0)
    bucket["input_tokens"] += int(in_tok or 0)
    bucket["output_tokens"] += int(out_tok or 0)
    bucket["cache_read_tokens"] += int(cache_read or 0)
    bucket["cache_write_tokens"] += int(cache_write or 0)
    bucket["call_count"] += int(call_count or 0)


def _count_edit_retries(tool_categories: list[str]) -> int:
    """Detect edit→shell→edit retry loops in a turn's tool sequence.

    Walks the tool category sequence looking for the pattern:
    file_write → shell → file_write (agent edited, ran test/build, had to edit again).
    Each occurrence of this pattern counts as one retry.

    Adapted from CodeBurn's ``countRetries`` (MIT license).
    """
    saw_edit = False
    saw_shell_after_edit = False
    retries = 0

    for cat in tool_categories:
        is_edit = cat in _WRITE_TOOL_CATEGORIES
        is_shell = cat == "shell"

        if is_edit:
            if saw_shell_after_edit:
                retries += 1
            saw_edit = True
            saw_shell_after_edit = False
        if is_shell and saw_edit:
            saw_shell_after_edit = True

    return retries
