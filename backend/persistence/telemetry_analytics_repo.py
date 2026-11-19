"""Read-only analytics queries on the job_telemetry_summary table.

Split from TelemetrySummaryRepository to separate write-path (event-driven
upserts) from read-path (analytics queries, scorecards, comparisons).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import text

from backend.models.currency import divide_known, optional_float, sum_known
from backend.models.domain import (
    AggregateStats,
    CostByDayRow,
    CostByModelRow,
    CostByRepoRow,
    ModelComparisonRow,
    TelemetrySummaryRow,
)
from backend.persistence.currency_projection import currency_query
from backend.persistence.repository import BaseRepository


class TelemetryAnalyticsRepository(BaseRepository):
    """Read-only analytics queries on job_telemetry_summary.

    Fleet-level analytics filter to ``session_kind = 'job'`` by default
    so sidecar session costs (preflight, memory, etc.) don't inflate
    job-level metrics.  Use ``session_kind=None`` to query all kinds.
    """

    # Base filter applied to fleet-level aggregates.  Single-job lookups
    # by job_id don't need this because a job's main row always has 'job'.
    _JOB_ONLY = "session_kind = 'job'"

    async def get(self, job_id: str) -> TelemetrySummaryRow | None:
        """Load summary row as a plain dict.  Returns None if not found."""
        result = await self._session.execute(
            currency_query("SELECT * FROM job_telemetry_summary WHERE job_id = :job_id AND session_kind = 'job'"),
            {"job_id": job_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return TelemetrySummaryRow(**dict(row))  # type: ignore[typeddict-item]

    async def query(
        self,
        *,
        period_days: int | None = None,
        sdk: str | None = None,
        model: str | None = None,
        status: str | None = None,
        repo: str | None = None,
        sort: str = "completed_at",
        desc: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TelemetrySummaryRow]:
        """Query summary rows with optional filters."""
        conditions: list[str] = [self._JOB_ONLY]
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if period_days is not None:
            conditions.append(f"created_at >= datetime('now', '-{int(period_days)} days')")
        if sdk:
            conditions.append("sdk = :sdk")
            params["sdk"] = sdk
        if model:
            conditions.append("model = :model")
            params["model"] = model
        if status:
            conditions.append("status = :status")
            params["status"] = status
        if repo:
            conditions.append("repo = :repo")
            params["repo"] = repo

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        allowed_sorts = {"completed_at", "created_at", "total_cost_usd", "duration_ms", "input_tokens"}
        sort_col = sort if sort in allowed_sorts else "completed_at"
        direction = "DESC" if desc else "ASC"

        result = await self._session.execute(
            currency_query(
                f"SELECT * FROM job_telemetry_summary{where} "  # noqa: S608
                f"ORDER BY {sort_col} {direction} LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        return [cast("TelemetrySummaryRow", dict(r)) for r in result.mappings().all()]

    async def aggregate(self, *, period_days: int = 7) -> AggregateStats:
        """Return aggregate stats for the analytics overview."""
        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    COUNT(*) as total_jobs,
                    COALESCE(SUM(CASE WHEN status = 'review' THEN 1 ELSE 0 END), 0) as review,
                    COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) as completed,
                    COALESCE(SUM(CASE WHEN status IN ('review', 'completed') THEN 1 ELSE 0 END), 0) as succeeded,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed,
                    COALESCE(SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END), 0) as cancelled,
                    COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0) as running,
                    SUM(total_cost_usd) as total_cost_usd,
                    SUM(estimated_cost_usd) as estimated_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(SUM(premium_requests), 0) as total_premium_requests,
                    COALESCE(SUM(tool_call_count), 0) as total_tool_calls,
                    COALESCE(SUM(tool_failure_count), 0) as total_tool_failures,
                    COALESCE(SUM((
                        SELECT COUNT(*) FROM job_telemetry_spans AS observed
                        WHERE observed.job_id = job_telemetry_summary.job_id
                          AND observed.session_kind = 'job' AND observed.span_type = 'tool'
                          AND COALESCE(json_type(observed.attrs_json, '$.success'), 'null') = 'null'
                    )), 0) as total_tool_unknown,
                    COALESCE(SUM(agent_error_count), 0) as total_agent_errors,
                    COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                    SUM(subagent_cost_usd) as total_subagent_cost_usd,
                    SUM(retry_cost_usd) as total_retry_cost_usd,
                    COALESCE(SUM(retry_count), 0) as total_retry_count
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                    AND session_kind = 'job'
            """),
        )
        row = result.mappings().first()
        # COUNT/SUM without GROUP BY always returns a row, but guard defensively
        if not row:
            return AggregateStats()
        return cast("AggregateStats", dict(row))

    async def cost_by_day(self, *, period_days: int = 7) -> list[CostByDayRow]:
        """Return daily cost breakdown."""
        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    date(created_at) as date,
                    SUM(total_cost_usd) as cost,
                    COUNT(*) as jobs
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                    AND session_kind = 'job'
                GROUP BY date(created_at)
                ORDER BY date(created_at)
            """),
        )
        return cast("list[CostByDayRow]", [dict(r) for r in result.mappings().all()])

    async def cost_by_repo(self, *, period_days: int = 7) -> list[CostByRepoRow]:
        """Return per-repo cost / job count / token breakdown."""
        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    repo,
                    COUNT(*) as job_count,
                    SUM(CASE WHEN status IN ('review', 'completed') THEN 1 ELSE 0 END) as succeeded,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(total_cost_usd) as total_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                    COALESCE(SUM(tool_call_count), 0) as tool_calls,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(SUM(premium_requests), 0) as premium_requests
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                    AND session_kind = 'job'
                GROUP BY repo
                ORDER BY total_cost_usd DESC
            """),
        )
        return cast("list[CostByRepoRow]", [dict(r) for r in result.mappings().all()])

    async def cost_by_model(self, *, period_days: int = 7) -> list[CostByModelRow]:
        """Return per-model cost / job count / token breakdown with normalized metrics."""
        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    model,
                    sdk,
                    COUNT(*) as job_count,
                    SUM(total_cost_usd) as total_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(SUM(premium_requests), 0) as premium_requests,
                    COALESCE(SUM(total_turns), 0) as total_turns,
                    COALESCE(SUM(tool_call_count), 0) as total_tool_calls,
                    COALESCE(SUM(diff_lines_added + diff_lines_removed), 0) as total_diff_lines,
                    -- Normalized metrics
                    CASE WHEN COUNT(*) > 0
                        THEN SUM(total_cost_usd) / COUNT(*)
                        ELSE NULL END as cost_per_job,
                    CASE WHEN SUM(duration_ms) > 0
                        THEN SUM(total_cost_usd) / (SUM(duration_ms) / 60000.0)
                        ELSE NULL END as cost_per_minute,
                    CASE WHEN SUM(total_turns) > 0
                        THEN SUM(total_cost_usd) / SUM(total_turns)
                        ELSE NULL END as cost_per_turn,
                    CASE WHEN SUM(tool_call_count) > 0
                        THEN SUM(total_cost_usd) / SUM(tool_call_count)
                        ELSE NULL END as cost_per_tool_call,
                    CASE WHEN SUM(diff_lines_added + diff_lines_removed) > 0
                        THEN SUM(total_cost_usd) / SUM(diff_lines_added + diff_lines_removed)
                        ELSE NULL END as cost_per_diff_line,
                    CASE WHEN SUM(input_tokens + output_tokens) > 0
                        THEN SUM(total_cost_usd) / (SUM(input_tokens + output_tokens) / 1000000.0)
                        ELSE NULL END as cost_per_mtok,
                    CASE WHEN SUM(total_cost_usd) > 0
                        THEN COALESCE(SUM(cache_read_tokens), 0) * 1.0 / NULLIF(SUM(input_tokens), 0)
                        ELSE 0 END as cache_hit_rate
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                    AND model != ''
                    AND session_kind = 'job'
                GROUP BY model, sdk
                ORDER BY total_cost_usd DESC
            """),
        )
        return cast("list[CostByModelRow]", [dict(r) for r in result.mappings().all()])

    # ------------------------------------------------------------------
    # Scorecard / resolution-joined queries
    # ------------------------------------------------------------------

    async def scorecard(self, *, period_days: int = 7) -> dict[str, Any]:
        """Budget per SDK, activity with resolution, quota, cost trend.

        Joins ``jobs`` table for resolution data that telemetry_summary lacks.
        """
        activity = await self._session.execute(
            text(f"""
                SELECT
                    COUNT(*) as total_jobs,
                    COALESCE(SUM(CASE WHEN j.state = 'running' THEN 1 ELSE 0 END), 0) as running,
                    COALESCE(SUM(CASE WHEN j.state = 'review' THEN 1 ELSE 0 END), 0) as in_review,
                    COALESCE(SUM(CASE WHEN j.resolution = 'merged' THEN 1 ELSE 0 END), 0) as merged,
                    COALESCE(SUM(CASE WHEN j.resolution = 'pr_created' THEN 1 ELSE 0 END), 0) as pr_created,
                    COALESCE(SUM(CASE WHEN j.resolution = 'discarded' THEN 1 ELSE 0 END), 0) as discarded,
                    COALESCE(SUM(CASE WHEN j.state = 'failed' THEN 1 ELSE 0 END), 0) as failed,
                    COALESCE(SUM(CASE WHEN j.state = 'canceled' THEN 1 ELSE 0 END), 0) as cancelled
                FROM jobs j
                WHERE j.created_at >= datetime('now', '-{int(period_days)} days')
            """),
        )
        activity_row = dict(activity.mappings().first() or {})

        budget = await self._session.execute(
            currency_query(f"""
                SELECT
                    t.sdk,
                    SUM(t.total_cost_usd) as total_cost_usd,
                    COALESCE(SUM(t.premium_requests), 0) as premium_requests,
                    COUNT(*) as job_count,
                    AVG(t.total_cost_usd) as avg_cost_per_job,
                    COALESCE(AVG(t.duration_ms), 0) as avg_duration_ms
                FROM job_telemetry_summary t
                WHERE t.created_at >= datetime('now', '-{int(period_days)} days')
                    AND t.session_kind = 'job'
                GROUP BY t.sdk
            """),
        )
        budget_rows = [dict(r) for r in budget.mappings().all()]

        quota_row = await self._session.execute(
            text("""
                SELECT quota_json
                FROM job_telemetry_summary
                WHERE sdk = 'copilot' AND quota_json IS NOT NULL AND quota_json != ''
                    AND session_kind = 'job'
                ORDER BY updated_at DESC
                LIMIT 1
            """),
        )
        quota_json_raw = None
        qr = quota_row.mappings().first()
        if qr:
            quota_json_raw = qr.get("quota_json")

        cost_trend = await self.cost_by_day(period_days=period_days)

        return {
            "activity": activity_row,
            "budget": budget_rows,
            "quotaJson": quota_json_raw,
            "costTrend": cost_trend,
        }

    async def model_comparison(self, *, period_days: int = 30, repo: str | None = None) -> list[ModelComparisonRow]:
        """Per-model stats joined with resolution data from jobs table."""
        repo_filter = ""
        params: dict[str, Any] = {}
        if repo:
            repo_filter = "AND j.repo = :repo"
            params["repo"] = repo

        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    t.model,
                    t.sdk,
                    COUNT(*) as job_count,
                    AVG(t.total_cost_usd) as avg_cost,
                    COALESCE(AVG(t.duration_ms), 0) as avg_duration_ms,
                    SUM(t.total_cost_usd) as total_cost_usd,
                    COALESCE(SUM(t.premium_requests), 0) as premium_requests,
                    SUM(CASE WHEN j.resolution = 'merged' THEN 1 ELSE 0 END) as merged,
                    SUM(CASE WHEN j.resolution = 'pr_created' THEN 1 ELSE 0 END) as pr_created,
                    SUM(CASE WHEN j.resolution = 'discarded' THEN 1 ELSE 0 END) as discarded,
                    SUM(CASE WHEN j.state = 'failed' THEN 1 ELSE 0 END) as failed,
                    AVG(CASE WHEN j.verify = 1 THEN t.total_turns ELSE NULL END) as avg_verify_turns,
                    SUM(CASE WHEN j.verify = 1 THEN 1 ELSE 0 END) as verify_job_count,
                    COALESCE(AVG(t.diff_lines_added + t.diff_lines_removed), 0) as avg_diff_lines,
                    CASE WHEN SUM(t.input_tokens) > 0
                        THEN COALESCE(SUM(t.cache_read_tokens), 0) * 1.0 / SUM(t.input_tokens)
                        ELSE 0 END as cache_hit_rate,
                    CASE WHEN COUNT(*) > 0
                        THEN SUM(t.total_cost_usd) / COUNT(*)
                        ELSE NULL END as cost_per_job,
                    CASE WHEN SUM(t.duration_ms) > 0
                        THEN SUM(t.total_cost_usd) / (SUM(t.duration_ms) / 60000.0)
                        ELSE NULL END as cost_per_minute,
                    CASE WHEN SUM(t.total_turns) > 0
                        THEN SUM(t.total_cost_usd) / SUM(t.total_turns)
                        ELSE NULL END as cost_per_turn,
                    CASE WHEN SUM(t.tool_call_count) > 0
                        THEN SUM(t.total_cost_usd) / SUM(t.tool_call_count)
                        ELSE NULL END as cost_per_tool_call,
                    CASE WHEN SUM(t.diff_lines_added + t.diff_lines_removed) > 0
                        THEN SUM(t.total_cost_usd) / SUM(t.diff_lines_added + t.diff_lines_removed)
                        ELSE NULL END as cost_per_diff_line
                FROM job_telemetry_summary t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.created_at >= datetime('now', '-{int(period_days)} days')
                    AND t.model != ''
                    AND t.session_kind = 'job'
                    {repo_filter}
                GROUP BY t.model, t.sdk
                ORDER BY COUNT(*) DESC
            """),
            params,
        )
        return cast("list[ModelComparisonRow]", [dict(r) for r in result.mappings().all()])

    async def job_context(self, job_id: str) -> dict[str, Any] | None:
        """Job telemetry plus comparison against repo averages."""
        job_row = await self.get(job_id)
        if not job_row:
            return None

        repo = job_row.get("repo", "")
        repo_avg = await self._session.execute(
            currency_query("""
                SELECT
                    COUNT(*) as job_count,
                    AVG(total_cost_usd) as avg_cost,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(AVG(diff_lines_added + diff_lines_removed), 0) as avg_diff_lines
                FROM job_telemetry_summary
                WHERE repo = :repo
                    AND job_id != :job_id
                    AND status = 'completed'
                    AND session_kind = 'job'
            """),
            {"repo": repo, "job_id": job_id},
        )
        avg_row = dict(repo_avg.mappings().first() or {})

        flags: list[dict[str, str]] = []
        cost_first = job_row.get("cost_first_half_usd")
        cost_second = job_row.get("cost_second_half_usd")
        total_cost = sum_known((cost_first, cost_second))
        # Only flag escalation when the job spent enough for it to matter
        # and the 2nd half is significantly worse than the 1st
        if (
            total_cost is not None
            and total_cost >= 0.50
            and cost_first is not None
            and cost_first > 0
            and cost_second is not None
            and cost_second > 2.0 * cost_first
        ):
            pct = round(cost_second / total_cost * 100)
            msg = f"Cost escalation: {pct}% of spend in second half of turns"
            flags.append({"type": "turn_escalation", "message": msg})

        reread_count = job_row.get("file_reread_count") or 0
        if reread_count > 50:
            flags.append({"type": "high_rereads", "message": f"High file re-reads: {reread_count} re-reads detected"})

        tool_failures = job_row.get("tool_failure_count") or 0
        if tool_failures >= 5:
            suffix = "s" if tool_failures > 1 else ""
            flags.append({"type": "tool_failures", "message": f"{tool_failures} tool failure{suffix} during this job"})

        return {
            "job": {
                "cost": optional_float(job_row.get("total_cost_usd")),
                "durationMs": float(job_row.get("duration_ms") or 0),
                "diffLinesAdded": int(job_row.get("diff_lines_added") or 0),
                "diffLinesRemoved": int(job_row.get("diff_lines_removed") or 0),
                "sdk": job_row.get("sdk") or "",
                "model": job_row.get("model") or "",
                "totalTurns": int(job_row.get("total_turns") or 0),
                "peakTurnCostUsd": optional_float(job_row.get("peak_turn_cost_usd")),
                "avgTurnCostUsd": optional_float(job_row.get("avg_turn_cost_usd")),
                "costFirstHalfUsd": cost_first,
                "costSecondHalfUsd": cost_second,
            },
            "repoAvg": {
                "jobCount": int(avg_row.get("job_count") or 0),
                "avgCost": optional_float(avg_row.get("avg_cost")),
                "avgDurationMs": float(avg_row.get("avg_duration_ms") or 0),
                "avgDiffLines": float(avg_row.get("avg_diff_lines") or 0),
            }
            if (avg_row.get("job_count") or 0) >= 3
            else None,
            "flags": flags,
        }

    async def turn_escalation_jobs(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Find jobs where cost/turn escalates significantly in the second half."""
        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    job_id,
                    total_turns,
                    cost_first_half_usd,
                    cost_second_half_usd,
                    total_cost_usd
                FROM job_telemetry_summary
                WHERE total_turns >= 6
                    AND cost_second_half_usd > 0
                    AND cost_first_half_usd > 0
                    AND cost_second_half_usd >= 0.50
                    AND (cost_second_half_usd / cost_first_half_usd) >= 2.0
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                    AND session_kind = 'job'
                ORDER BY (cost_second_half_usd - cost_first_half_usd) DESC
                LIMIT 20
            """)
        )
        return [dict(r) for r in result.mappings().all()]

    async def compaction_storm_jobs(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Find jobs with excessive context compactions."""
        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    job_id,
                    compactions,
                    tokens_compacted,
                    total_cost_usd,
                    total_turns
                FROM job_telemetry_summary
                WHERE compactions >= 5
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                    AND status IN ('completed', 'failed')
                    AND session_kind = 'job'
                ORDER BY compactions DESC
                LIMIT 20
            """)
        )
        return [dict(r) for r in result.mappings().all()]

    async def cache_efficiency_periods(self) -> dict[str, Any]:
        """Compare cache hit rates between recent and prior 7-day periods."""
        result = await self._session.execute(
            text("""
                SELECT
                    SUM(CASE WHEN created_at >= datetime('now', '-7 days')
                        THEN cache_read_tokens ELSE 0 END) as recent_cache,
                    SUM(CASE WHEN created_at >= datetime('now', '-7 days')
                        THEN input_tokens ELSE 0 END) as recent_input,
                    SUM(CASE WHEN created_at < datetime('now', '-7 days')
                             AND created_at >= datetime('now', '-14 days')
                        THEN cache_read_tokens ELSE 0 END) as prior_cache,
                    SUM(CASE WHEN created_at < datetime('now', '-7 days')
                             AND created_at >= datetime('now', '-14 days')
                        THEN input_tokens ELSE 0 END) as prior_input,
                    COUNT(CASE WHEN created_at >= datetime('now', '-7 days')
                        THEN 1 END) as recent_jobs,
                    COUNT(CASE WHEN created_at < datetime('now', '-7 days')
                             AND created_at >= datetime('now', '-14 days')
                        THEN 1 END) as prior_jobs
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-14 days')
                    AND session_kind = 'job'
            """)
        )
        row = result.mappings().first()
        return dict(row) if row else {}

    async def yield_summary(
        self,
        *,
        period_days: int,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Yield/ROI: job cost by resolution outcome."""
        repo_filter = ""
        params: dict[str, Any] = {"offset": f"-{int(period_days)} days"}
        if repo:
            repo_filter = "AND j.repo = :repo"
            params["repo"] = repo

        result = await self._session.execute(
            currency_query(f"""
                SELECT
                    CASE
                        WHEN j.resolution IN ('merged', 'pr_created') THEN 'productive'
                        WHEN j.resolution = 'discarded' THEN 'abandoned'
                        WHEN j.state = 'failed' THEN 'failed'
                        ELSE 'cancelled'
                    END AS category,
                    COUNT(*) AS job_count,
                    SUM(t.total_cost_usd) AS total_cost_usd,
                    AVG(t.total_cost_usd) AS avg_cost_usd
                FROM jobs j
                JOIN job_telemetry_summary t ON t.job_id = j.id
                WHERE t.created_at >= datetime('now', :offset)
                    AND j.state IN ('completed', 'failed', 'canceled')
                    AND t.session_kind = 'job'
                    {repo_filter}
                GROUP BY category
            """),
            params,
        )
        rows = [dict(r) for r in result.mappings().all()]

        total_cost = sum_known(r["total_cost_usd"] for r in rows)
        total_jobs = sum(r["job_count"] for r in rows)

        # Compute pct_of_total for each category
        categories = []
        for r in rows:
            r["pct_of_total"] = divide_known(r["total_cost_usd"], total_cost or 0)
            categories.append(r)

        # Cost per merge
        productive = next((r for r in rows if r["category"] == "productive"), None)
        merged_cost = productive["total_cost_usd"] if productive else None
        merged_count = productive["job_count"] if productive else 0
        cost_per_merge = divide_known(merged_cost, merged_count)

        return {
            "period": period_days,
            "categories": categories,
            "cost_per_merge_usd": cost_per_merge,
            "total_cost_usd": total_cost,
            "total_jobs": total_jobs,
        }

    async def monthly_burn(self) -> dict[str, Any]:
        """Current month spend, daily average, and projected month-end total."""
        result = await self._session.execute(
            currency_query("""
                SELECT
                    SUM(total_cost_usd) AS month_spend,
                    COUNT(DISTINCT date(created_at)) AS active_days,
                    julianday('now') - julianday(
                        strftime('%Y-%m-01', 'now')
                    ) + 1 AS days_elapsed
                FROM job_telemetry_summary
                WHERE created_at >= strftime('%Y-%m-01', 'now')
                    AND session_kind = 'job'
            """),
        )
        row = result.mappings().first()
        if not row:
            return {
                "month_spend_usd": None,
                "days_elapsed": 1,
                "days_in_month": 30,
                "daily_avg_usd": None,
                "projected_month_end_usd": None,
            }
        month_spend = optional_float(row["month_spend"])
        days_elapsed = max(float(row["days_elapsed"] or 1), 1)

        days_in_month_result = await self._session.execute(
            text("""
                SELECT julianday(
                    strftime('%Y-%m-01', 'now', '+1 month')
                ) - julianday(
                    strftime('%Y-%m-01', 'now')
                ) AS days_in_month
            """),
        )
        dim_row = days_in_month_result.mappings().first()
        days_in_month = float(dim_row["days_in_month"]) if dim_row else 30.0
        daily_avg = divide_known(month_spend, days_elapsed)
        projected = daily_avg * days_in_month if daily_avg is not None else None

        return {
            "month_spend_usd": month_spend,
            "days_elapsed": int(days_elapsed),
            "days_in_month": int(days_in_month),
            "daily_avg_usd": daily_avg,
            "projected_month_end_usd": projected,
        }

    async def high_delegation_jobs(
        self,
        *,
        period_days: int = 14,
    ) -> list[dict[str, Any]]:
        """Jobs where sub-agent cost exceeds the parent's direct cost."""
        result = await self._session.execute(
            currency_query("""
                SELECT
                    t.job_id,
                    t.subagent_cost_usd,
                    t.total_cost_usd - t.subagent_cost_usd AS direct_cost_usd,
                    t.total_cost_usd
                FROM job_telemetry_summary t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.created_at >= datetime('now', '-' || :days || ' days')
                    AND t.subagent_cost_usd > 0
                    AND t.subagent_cost_usd
                        > t.total_cost_usd - t.subagent_cost_usd
                    AND t.session_kind = 'job'
                ORDER BY t.subagent_cost_usd DESC
            """),
            {"days": int(period_days)},
        )
        return [dict(r) for r in result.mappings().all()]

    async def sum_compacted_tokens(self, period_days: int) -> int:
        """Total tokens compacted (re-ingested) across all jobs in the period (Item 13)."""
        result = await self._session.execute(
            text("""
                SELECT COALESCE(SUM(t.tokens_compacted), 0) AS total
                FROM job_telemetry_summary t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.created_at >= datetime('now', :offset)
                    AND t.session_kind = 'job'
            """),
            {"offset": f"-{int(period_days)} days"},
        )
        return int(result.scalar() or 0)

    async def fleet_waste_metrics(self, *, period_days: int = 30) -> dict[str, float | None]:
        """Aggregate waste-related metrics across the fleet (Item 18)."""
        result = await self._session.execute(
            currency_query("""
                SELECT
                    SUM(t.retry_cost_usd) AS total_retry_cost_usd,
                    SUM(
                        CASE WHEN j.resolution IN ('failed', 'discarded')
                        THEN t.total_cost_usd END
                    ) AS failed_discarded_cost_usd,
                    COALESCE(SUM(t.tokens_compacted), 0) AS total_tokens_compacted,
                    COALESCE(SUM(
                        CASE WHEN t.file_reread_count > t.unique_files_read
                        THEN t.file_reread_count - t.unique_files_read ELSE 0 END
                    ), 0) AS excess_rereads
                FROM job_telemetry_summary t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.created_at >= datetime('now', :offset)
                    AND t.session_kind = 'job'
            """),
            {"offset": f"-{int(period_days)} days"},
        )
        row: Any = result.mappings().first() or {}
        # Estimate compaction cost: re-ingesting tokens at conservative input rate
        compaction_tokens = int(row.get("total_tokens_compacted", 0))
        avg_input_rate = 0.000003  # ~$3/1M tokens — conservative Claude Sonnet-class rate
        compaction_cost = compaction_tokens * avg_input_rate
        # Estimate re-read cost: each excess re-read wastes marginal overhead
        excess_rereads = int(row.get("excess_rereads", 0))
        reread_cost = excess_rereads * 0.001

        return {
            "total_retry_cost_usd": optional_float(row.get("total_retry_cost_usd", 0)),
            "failed_discarded_cost_usd": optional_float(row.get("failed_discarded_cost_usd", 0)),
            "compaction_cost_estimate_usd": compaction_cost,
            "reread_cost_estimate_usd": reread_cost,
        }

    async def sidecar_cost_breakdown(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Cost breakdown by session_kind for sidecar sessions.

        Returns one row per session_kind (excluding 'job') with totals.
        """
        result = await self._session.execute(
            currency_query("""
                SELECT
                    session_kind,
                    COUNT(*) AS session_count,
                    SUM(total_cost_usd) AS total_cost_usd,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(llm_call_count), 0) AS llm_call_count,
                    COALESCE(SUM(tool_call_count), 0) AS tool_call_count
                FROM job_telemetry_summary
                WHERE session_kind != 'job'
                    AND created_at >= datetime('now', :offset)
                GROUP BY session_kind
                ORDER BY total_cost_usd DESC
            """),
            {"offset": f"-{int(period_days)} days"},
        )
        return [dict(r) for r in result.mappings().all()]
