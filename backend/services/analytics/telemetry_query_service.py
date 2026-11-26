"""Service layer for job telemetry assembly.

Encapsulates the per-job telemetry query and response construction that
was previously inlined in the ``job_telemetry`` API handler — consistent
with the project convention that route handlers delegate to services.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from backend.models.api_schemas import (
    JobTelemetryResponse,
    SidecarSessionSummary,
    TelemetryCostBucket,
    TelemetryCostDrivers,
    TelemetryFileAccess,
    TelemetryFileEntry,
    TelemetryFileStats,
    TelemetryLatencyBucket,
    TelemetryLatencyDrivers,
    TelemetryLlmCall,
    TelemetryQuotaSnapshot,
    TelemetryReviewComplexity,
    TelemetryReviewSignals,
    TelemetryToolCall,
    TelemetryTurnEconomics,
    TelemetryTurnLatency,
    TurnAction,
)
from backend.models.currency import optional_float
from backend.services.tools.tool_classifier import classify_tool, classify_tool_activity, refine_shell_category

if TYPE_CHECKING:
    from backend.models.domain import (
        CostAttributionRow,
        FileAccessRow,
        FileAccessStatsRow,
        Job,
        LatencyAttributionRow,
        TelemetrySpanRow,
        TelemetrySummaryRow,
    )
    from backend.persistence.cost_attribution_repo import CostAttributionRepository
    from backend.persistence.file_access_repo import FileAccessRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.latency_attribution_repo import LatencyAttributionRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

log = structlog.get_logger()

# Shell tool names that should get enriched display names
_SHELL_TOOL_NAMES = frozenset({"bash", "Bash", "run_in_terminal", "terminal", "exec", "write_bash"})


def _shell_display_name(tool_name: str, tool_args_json: str | None) -> str:
    """Derive a display name like 'pytest' or 'git commit' from shell tool args.

    Falls back to the raw tool name if no command can be extracted.
    """
    if not tool_args_json:
        return tool_name
    try:
        parsed = json.loads(tool_args_json) if isinstance(tool_args_json, str) else tool_args_json
        cmd = (parsed.get("command", "") or parsed.get("cmd", "")).strip()
    except (json.JSONDecodeError, TypeError, AttributeError):
        return tool_name
    if not cmd:
        return tool_name
    # Strip leading 'cd ... &&' prefix
    if cmd.startswith("cd ") and "&&" in cmd:
        cmd = cmd.split("&&", 1)[1].strip()
    # Get the first meaningful token (skip env vars, sudo, etc.)
    parts = cmd.split()
    for part in parts:
        if "=" in part and not part.startswith("-"):
            continue  # env var assignment like FOO=bar
        if part in ("sudo", "env", "nohup", "time"):
            continue
        # Use this as the command name
        # For compound commands like 'git commit', include the subcommand
        base = part.split("/")[-1]  # strip path prefix
        idx = parts.index(part)
        if base in ("git", "npm", "npx", "uv", "cargo", "docker", "kubectl") and idx + 1 < len(parts):
            sub = parts[idx + 1]
            if not sub.startswith("-"):
                return f"{base} {sub}"
        # python -m <module> → show the module name (e.g. "pytest" not "python")
        if base in ("python", "python3") and idx + 2 < len(parts) and parts[idx + 1] == "-m":
            return str(parts[idx + 2])
        return str(base)
    return str(tool_name)


# Review complexity thresholds — calibrated against historical job data:
# >500 diff lines ≈ top-10% by size, >20 turns ≈ extended sessions,
# >15 unique files ≈ cross-cutting changes.
_LARGE_DIFF_LINES = 500
_MANY_TURNS = 20
_MANY_FILES = 15


def _refine_tool_category(tool_name: str, tool_args_json: str | None) -> str:
    """Return the tool category, promoting shell git commands to git_read/git_write."""
    cat = classify_tool(tool_name)
    if cat == "shell":
        refined = refine_shell_category(tool_args_json)
        if refined:
            return refined
    return cat


class TelemetryQueryService:
    """Assembles a ``JobTelemetryResponse`` from the persistence layer."""

    def __init__(
        self,
        cost_repo: CostAttributionRepository,
        file_repo: FileAccessRepository,
        job_repo: JobRepository,
        latency_repo: LatencyAttributionRepository,
        spans_repo: TelemetrySpansRepository,
        summary_repo: TelemetrySummaryRepository,
    ) -> None:
        self._cost_repo = cost_repo
        self._file_repo = file_repo
        self._job_repo = job_repo
        self._latency_repo = latency_repo
        self._spans_repo = spans_repo
        self._summary_repo = summary_repo

    async def get_telemetry(self, job_id: str) -> JobTelemetryResponse:
        """Build the full telemetry response for *job_id*."""
        import asyncio

        summary = await self._summary_repo.get_currency(job_id)
        if summary is None:
            return JobTelemetryResponse(job_id=job_id, available=False)

        # Fetch remaining data in parallel — these are all independent queries
        gather_results = await asyncio.gather(
            self._job_repo.get(job_id),
            self._spans_repo.list_for_job(job_id),
            self._cost_repo.for_job(job_id),
            self._latency_repo.for_job(job_id),
            self._file_repo.reread_stats(job_id),
            self._file_repo.most_accessed_files(job_id=job_id),
            self._summary_repo.list_sidecars(job_id),
        )
        (
            job_row,
            spans,
            attribution_rows,
            latency_rows,
            file_stats,
            top_files,
            sidecar_rows,
        ) = cast(
            "tuple[Job | None, list[TelemetrySpanRow],"
            " list[CostAttributionRow], list[LatencyAttributionRow],"
            " FileAccessStatsRow, list[FileAccessRow], list[TelemetrySummaryRow]]",
            gather_results,
        )
        sdk = job_row.sdk if job_row else ""

        # Parse quota JSON if present
        quota_snapshots_raw: dict[str, dict[str, Any]] | None = None
        quota_json = summary.get("quota_json")
        if quota_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed_quota = json.loads(quota_json)
                if isinstance(parsed_quota, dict):
                    quota_snapshots_raw = {
                        str(resource): snap for resource, snap in parsed_quota.items() if isinstance(snap, dict)
                    }

        # Compute derived fields
        input_tok = summary.get("input_tokens", 0)
        output_tok = summary.get("output_tokens", 0)
        cache_read = summary.get("cache_read_tokens", 0)
        window_size = summary.get("context_window_size", 0)
        current_ctx = summary.get("current_context_tokens", 0)

        # --- Build tool/LLM call lists ---
        tool_calls: list[TelemetryToolCall] = []
        llm_calls: list[TelemetryLlmCall] = []
        for span in spans:
            attrs = span.get("attrs")
            if not isinstance(attrs, dict):
                attrs = {}
            if span.get("span_type") == "tool":
                edit_motivations = None
                if span.get("edit_motivations"):
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        edit_motivations = json.loads(span["edit_motivations"] or "[]")
                tool_name = span["name"]
                tool_args = span.get("tool_args_json")
                # Derive a human-readable display label
                display_label: str | None = None
                if tool_name in _SHELL_TOOL_NAMES:
                    derived = _shell_display_name(tool_name, tool_args)
                    if derived != tool_name:
                        display_label = derived
                elif "_" in tool_name:
                    # Humanize underscore-separated names: git_diff → "git diff"
                    display_label = tool_name.replace("_", " ")
                tool_calls.append(
                    TelemetryToolCall(
                        name=tool_name,
                        display_label=display_label,
                        activity=classify_tool_activity(tool_name, tool_args),
                        tool_category=_refine_tool_category(tool_name, tool_args),
                        duration_ms=float(span.get("duration_ms", 0)),
                        success=attrs.get("success") if isinstance(attrs.get("success"), bool) else None,
                        offset_sec=float(span.get("started_at", 0)),
                        motivation_summary=span.get("motivation_summary"),
                        edit_motivations=edit_motivations,
                    )
                )
            elif span.get("span_type") == "llm":
                llm_calls.append(
                    TelemetryLlmCall(
                        model=span["name"],
                        input_tokens=attrs.get("input_tokens", 0),
                        output_tokens=attrs.get("output_tokens", 0),
                        cache_read_tokens=attrs.get("cache_read_tokens", 0),
                        cache_write_tokens=attrs.get("cache_write_tokens", 0),
                        cost=optional_float(span.get("cost_usd")),
                        duration_ms=float(span.get("duration_ms", 0)),
                        is_subagent=attrs.get("is_subagent", False),
                        offset_sec=float(span.get("started_at", 0)),
                        call_count=attrs.get("num_turns", 1),
                    )
                )

        grouped_dimensions: dict[str, list[TelemetryCostBucket]] = {}
        turn_curve: list[TelemetryCostBucket] = []
        for row in attribution_rows:
            bucket = TelemetryCostBucket(
                dimension=row.get("dimension", "unknown"),
                bucket=row.get("bucket", "unknown"),
                cost_usd=optional_float(row.get("cost_usd")),
                input_tokens=int(row.get("input_tokens", 0)),
                output_tokens=int(row.get("output_tokens", 0)),
                call_count=int(row.get("call_count", 0)),
            )
            dimension = str(row.get("dimension", "unknown"))
            grouped_dimensions.setdefault(dimension, []).append(bucket)
            if dimension == "turn":
                turn_curve.append(bucket)

        turn_curve.sort(key=lambda item: int(item.bucket) if item.bucket.isdigit() else 0)

        # Enrich turn curve with activity + tools from raw spans
        self._enrich_turn_curve(turn_curve, spans)

        # Build latency drivers from latency attribution rows
        latency_grouped: dict[str, list[TelemetryLatencyBucket]] = {}
        latency_turn_curve: list[TelemetryLatencyBucket] = []
        for lat_row in latency_rows:
            lb = TelemetryLatencyBucket(
                dimension=lat_row.get("dimension", "unknown"),
                bucket=lat_row.get("bucket", "unknown"),
                wall_clock_ms=int(lat_row.get("wall_clock_ms", 0)),
                sum_duration_ms=int(lat_row.get("sum_duration_ms", 0)),
                span_count=int(lat_row.get("span_count", 0)),
                p50_ms=int(lat_row.get("p50_ms", 0)),
                p95_ms=int(lat_row.get("p95_ms", 0)),
                max_ms=int(lat_row.get("max_ms", 0)),
                pct_of_total=float(lat_row.get("pct_of_total", 0)),
            )
            dim = str(lat_row.get("dimension", "unknown"))
            latency_grouped.setdefault(dim, []).append(lb)
            if dim == "turn":
                latency_turn_curve.append(lb)
        latency_turn_curve.sort(key=lambda item: int(item.bucket) if item.bucket.isdigit() else 0)

        # For running jobs, compute live duration from created_at instead of
        # the stored 0 which is only finalized when the job completes.
        duration_ms = summary.get("duration_ms", 0)
        if duration_ms == 0 and summary.get("status") == "running" and summary.get("created_at"):
            try:
                created = datetime.fromisoformat(summary["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                duration_ms = int((datetime.now(UTC) - created).total_seconds() * 1000)
            except (ValueError, TypeError):
                log.debug("live_duration_parse_failed", job_id=job_id, exc_info=True)

        # Review signals: test co-modifications
        test_co_mods = await self._spans_repo.test_co_modifications(job_id)

        # Review complexity tier
        signals: list[str] = []
        signal_details: dict[str, dict[str, int | float]] = {}
        diff_lines = int(summary.get("diff_lines_added", 0)) + int(summary.get("diff_lines_removed", 0))
        total_turns = int(summary.get("total_turns", 0))
        unique_files = int(file_stats.get("unique_files", 0))
        if diff_lines > _LARGE_DIFF_LINES:
            signals.append("large_diff")
            signal_details["large_diff"] = {"value": diff_lines, "threshold": _LARGE_DIFF_LINES}
        if total_turns > _MANY_TURNS:
            signals.append("many_turns")
            signal_details["many_turns"] = {"value": total_turns, "threshold": _MANY_TURNS}
        if unique_files > _MANY_FILES:
            signals.append("many_files")
            signal_details["many_files"] = {"value": unique_files, "threshold": _MANY_FILES}
        tier = "quick" if not signals else ("deep" if len(signals) >= 3 else "standard")

        # Build quota snapshots if present
        quota_snapshots = None
        if quota_snapshots_raw is not None:
            quota_snapshots = {
                resource: TelemetryQuotaSnapshot(
                    used_requests=snap.get("used_requests", 0),
                    entitlement_requests=snap.get("entitlement_requests", 0),
                    remaining_percentage=snap.get("remaining_percentage", 0),
                    overage=snap.get("overage", 0),
                    overage_allowed=snap.get("overage_allowed", False),
                    is_unlimited=snap.get("is_unlimited", False),
                    reset_date=snap.get("reset_date", ""),
                )
                for resource, snap in quota_snapshots_raw.items()
            }

        return JobTelemetryResponse(
            cost_usage=(await self._summary_repo.batch_cost_usage([job_id], all_roles=True)).get(job_id, []),
            estimated_cost_usd=optional_float(summary.get("estimated_cost_usd")),
            legacy_cost_unverified=optional_float(summary.get("legacy_cost_unverified")),
            available=True,
            job_id=job_id,
            sdk=sdk,
            model=summary.get("model", ""),
            main_model=summary.get("model", ""),
            duration_ms=duration_ms,
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_tokens=input_tok + output_tok + cache_read,
            cache_read_tokens=cache_read,
            cache_write_tokens=summary.get("cache_write_tokens", 0),
            total_cost=optional_float(summary.get("total_cost_usd")),
            context_window_size=window_size,
            current_context_tokens=current_ctx,
            context_utilization=(current_ctx / window_size) if window_size else 0,
            compactions=summary.get("compactions", 0),
            tokens_compacted=summary.get("tokens_compacted", 0),
            tool_call_count=summary.get("tool_call_count", 0),
            total_tool_duration_ms=summary.get("total_tool_duration_ms", 0),
            tool_calls=tool_calls,
            llm_call_count=summary.get("llm_call_count", 0),
            total_llm_duration_ms=summary.get("total_llm_duration_ms", 0),
            llm_calls=llm_calls,
            approval_count=summary.get("approval_count", 0),
            total_approval_wait_ms=summary.get("approval_wait_ms", 0),
            agent_messages=summary.get("agent_messages", 0),
            operator_messages=summary.get("operator_messages", 0),
            premium_requests=float(summary.get("premium_requests", 0)),
            cost_drivers=TelemetryCostDrivers(
                activity=grouped_dimensions.get("activity", []),
                phase=grouped_dimensions.get("phase", []),
                activity_phase=grouped_dimensions.get("activity_phase", []),
                edit_efficiency=grouped_dimensions.get("edit_efficiency", []),
            ),
            turn_economics=TelemetryTurnEconomics(
                total_turns=int(summary.get("total_turns", 0)),
                peak_turn_cost_usd=optional_float(summary.get("peak_turn_cost_usd")),
                avg_turn_cost_usd=optional_float(summary.get("avg_turn_cost_usd")),
                cost_first_half_usd=optional_float(summary.get("cost_first_half_usd")),
                cost_second_half_usd=optional_float(summary.get("cost_second_half_usd")),
                turn_curve=turn_curve,
            ),
            latency_drivers=TelemetryLatencyDrivers(
                category=latency_grouped.get("category", []),
                activity=latency_grouped.get("activity", []),
                phase=latency_grouped.get("phase", []),
            ),
            turn_latency=TelemetryTurnLatency(
                total_turns=int(summary.get("total_turns", 0)),
                peak_turn_ms=max((b.wall_clock_ms for b in latency_turn_curve), default=0),
                avg_turn_ms=(
                    int(sum(b.wall_clock_ms for b in latency_turn_curve) / len(latency_turn_curve))
                    if latency_turn_curve
                    else 0
                ),
                first_half_ms=sum(b.wall_clock_ms for b in latency_turn_curve[: len(latency_turn_curve) // 2]),
                second_half_ms=sum(b.wall_clock_ms for b in latency_turn_curve[len(latency_turn_curve) // 2 :]),
                turn_curve=latency_turn_curve,
            ),
            parallelism_ratio=float(summary.get("parallelism_ratio", 0)),
            idle_ms=int(summary.get("idle_ms", 0)),
            file_access=TelemetryFileAccess(
                stats=TelemetryFileStats(
                    total_accesses=int(file_stats.get("total_accesses") or 0),
                    unique_files=int(file_stats.get("unique_files") or 0),
                    total_reads=int(file_stats.get("total_reads") or 0),
                    total_writes=int(file_stats.get("total_writes") or 0),
                    reread_count=int(file_stats.get("reread_count") or 0),
                ),
                top_files=[
                    TelemetryFileEntry(
                        file_path=str(row.get("file_path", "")),
                        access_count=int(row.get("access_count", 0)),
                        read_count=int(row.get("read_count", 0)),
                        write_count=int(row.get("write_count", 0)),
                    )
                    for row in top_files
                ],
            ),
            quota_snapshots=quota_snapshots,
            review_signals=TelemetryReviewSignals(test_co_modifications=test_co_mods),
            review_complexity=TelemetryReviewComplexity(tier=tier, signals=signals, signal_details=signal_details),
            sidecar_sessions=[
                SidecarSessionSummary(
                    session_kind=str(sr.get("session_kind", "")),
                    input_tokens=int(sr.get("input_tokens", 0)),
                    output_tokens=int(sr.get("output_tokens", 0)),
                    total_cost_usd=optional_float(sr.get("total_cost_usd")),
                    llm_call_count=int(sr.get("llm_call_count", 0)),
                    tool_call_count=int(sr.get("tool_call_count", 0)),
                )
                for sr in sidecar_rows
            ],
        )

    @staticmethod
    def _enrich_turn_curve(turn_curve: list[TelemetryCostBucket], spans: list[TelemetrySpanRow]) -> None:
        """Annotate each turn bucket with intent and concrete actions."""
        import json as _json
        from collections import Counter

        # Group tool spans by turn
        turns: dict[str, list[TelemetrySpanRow]] = {}
        for span in spans:
            if span.get("span_type") != "tool":
                continue
            turn = str(span.get("turn_number", ""))
            if turn:
                turns.setdefault(turn, []).append(span)

        def _short_path(p: str) -> str:
            """Strip worktree prefix, keep last 2 path segments."""
            if not p:
                return ""
            # Strip common worktree prefixes
            parts = p.replace("\\", "/").split("/")
            # Find last segment after .codeplane-worktrees/<job>/
            try:
                idx = next(i for i, seg in enumerate(parts) if seg == ".codeplane-worktrees")
                parts = parts[idx + 2 :]  # skip worktrees/<job-name>
            except StopIteration:
                pass
            # Keep at most last 2 segments
            if len(parts) > 2:
                parts = parts[-2:]
            return "/".join(parts)

        def _short_cmd(cmd: str) -> str:
            """Extract first meaningful command from a shell command."""
            c = cmd.strip()
            if c.startswith("cd ") and "&&" in c:
                c = c.split("&&", 1)[1].strip()
            parts = c.split()
            if not parts:
                return c
            # Skip env vars, sudo, etc.
            for i, part in enumerate(parts):
                if "=" in part and not part.startswith("-"):
                    continue
                if part in ("sudo", "env", "nohup", "time"):
                    continue
                base = part.split("/")[-1]
                # For compound commands, include the subcommand
                if base in ("git", "npm", "npx", "uv", "cargo", "docker", "kubectl") and i + 1 < len(parts):
                    sub = parts[i + 1]
                    if not sub.startswith("-"):
                        return f"{base} {sub}"
                return base
            return parts[0].split("/")[-1]

        for bucket in turn_curve:
            turn_spans = turns.get(bucket.bucket, [])
            if not turn_spans:
                bucket.activity = "communication"
                bucket.intent = None
                bucket.actions = []
                continue

            # Extract intent from report_intent spans (use last one as most specific)
            intent = None
            actions: list[TurnAction] = []

            for span in turn_spans:
                name = span.get("name", "")
                cat = _refine_tool_category(name, span.get("tool_args_json"))
                args_raw = span.get("tool_args_json")
                args: dict[str, Any] = {}
                if args_raw:
                    with contextlib.suppress(Exception):
                        args = _json.loads(args_raw)
                        if not isinstance(args, dict):
                            args = {}

                tool_activity = classify_tool_activity(name, args_raw)

                if name == "report_intent":
                    i = args.get("intent", "")
                    if i:
                        intent = i
                elif cat == "file_write":
                    path = str(args.get("file_path", args.get("path", span.get("tool_target", ""))) or "")
                    short = _short_path(path)
                    if short:
                        actions.append(TurnAction(text=f"edited {short}", activity=tool_activity))
                elif cat == "file_read":
                    path = str(args.get("file_path", args.get("path", span.get("tool_target", ""))) or "")
                    short = _short_path(path)
                    if short:
                        actions.append(TurnAction(text=f"read {short}", activity=tool_activity))
                elif cat == "file_search":
                    path = str(
                        args.get("file_path", args.get("path", args.get("query", span.get("tool_target", "")))) or ""
                    )
                    short = _short_path(path) if "/" in path or "." in path else path[:40]
                    if short:
                        actions.append(TurnAction(text=f"searched {short}", activity=tool_activity))
                elif cat == "shell":
                    cmd = args.get("command", args.get("cmd", ""))
                    if cmd:
                        short = _short_cmd(cmd)
                        if short:
                            actions.append(TurnAction(text=f"ran {short}", activity=tool_activity))
                elif cat in ("git_read", "git_write"):
                    actions.append(TurnAction(text=name.replace("_", " "), activity=tool_activity))
                elif cat == "agent":
                    actions.append(TurnAction(text=f"delegated to {name}", activity=tool_activity))
                elif cat == "browser":
                    actions.append(TurnAction(text=f"fetched {name}", activity=tool_activity))

            # Deduplicate consecutive identical actions (e.g. multiple reads of same file)
            deduped: list[TurnAction] = []
            seen: dict[str, int] = {}
            for action in actions:
                if action.text in seen:
                    seen[action.text] += 1
                else:
                    seen[action.text] = 1
                    deduped.append(action)
            # Update text with count for repeated actions
            final_actions: list[TurnAction] = []
            for action in deduped:
                count = seen[action.text]
                if count > 1:
                    final_actions.append(TurnAction(text=f"{action.text} ×{count}", activity=action.activity))
                else:
                    final_actions.append(action)

            # Classify turn activity — most common per-tool activity
            tool_activities = [a.activity for a in actions] if actions else []
            if tool_activities:
                activity_counts = Counter(tool_activities)
                bucket.activity = activity_counts.most_common(1)[0][0]
            else:
                bucket.activity = "communication"
            bucket.intent = intent
            bucket.actions = final_actions
