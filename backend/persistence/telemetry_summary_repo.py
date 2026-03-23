"""Persistence for the denormalized job telemetry summary table.

Each adapter ``record_*()`` call triggers an atomic upsert so the row is
always up-to-date.  No timers, no flush intervals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.persistence.repository import BaseRepository


class TelemetrySummaryRepo(BaseRepository):
    """Event-driven upserts into ``job_telemetry_summary``."""

    async def init_job(
        self,
        job_id: str,
        *,
        sdk: str,
        model: str = "",
        repo: str = "",
        branch: str = "",
    ) -> None:
        """Insert the initial summary row when a job starts running."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                INSERT INTO job_telemetry_summary
                    (job_id, sdk, model, repo, branch, status, duration_ms,
                     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                     total_cost_usd, premium_requests,
                     llm_call_count, total_llm_duration_ms,
                     tool_call_count, tool_failure_count, total_tool_duration_ms,
                     compactions, tokens_compacted,
                     approval_count, approval_wait_ms,
                     agent_messages, operator_messages,
                     context_window_size, current_context_tokens,
                     created_at, updated_at)
                VALUES
                    (:job_id, :sdk, :model, :repo, :branch, 'running', 0,
                     0, 0, 0, 0,
                     0.0, 0.0,
                     0, 0,
                     0, 0, 0,
                     0, 0,
                     0, 0,
                     0, 0,
                     0, 0,
                     :now, :now)
                ON CONFLICT(job_id) DO UPDATE SET
                    model = CASE WHEN excluded.model != '' THEN excluded.model ELSE job_telemetry_summary.model END,
                    updated_at = excluded.updated_at
            """),
            {"job_id": job_id, "sdk": sdk, "model": model, "repo": repo, "branch": branch, "now": now},
        )
        await self._session.flush()

    async def increment(
        self,
        job_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        total_cost_usd: float = 0.0,
        premium_requests: float = 0.0,
        llm_call_count: int = 0,
        total_llm_duration_ms: int = 0,
        tool_call_count: int = 0,
        tool_failure_count: int = 0,
        total_tool_duration_ms: int = 0,
        compactions: int = 0,
        tokens_compacted: int = 0,
        approval_count: int = 0,
        approval_wait_ms: int = 0,
        agent_messages: int = 0,
        operator_messages: int = 0,
    ) -> None:
        """Atomically increment counters for a job.  Idempotent per field."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary SET
                    input_tokens          = input_tokens + :input_tokens,
                    output_tokens         = output_tokens + :output_tokens,
                    cache_read_tokens     = cache_read_tokens + :cache_read_tokens,
                    cache_write_tokens    = cache_write_tokens + :cache_write_tokens,
                    total_cost_usd        = total_cost_usd + :total_cost_usd,
                    premium_requests      = premium_requests + :premium_requests,
                    llm_call_count        = llm_call_count + :llm_call_count,
                    total_llm_duration_ms = total_llm_duration_ms + :total_llm_duration_ms,
                    tool_call_count       = tool_call_count + :tool_call_count,
                    tool_failure_count    = tool_failure_count + :tool_failure_count,
                    total_tool_duration_ms= total_tool_duration_ms + :total_tool_duration_ms,
                    compactions           = compactions + :compactions,
                    tokens_compacted      = tokens_compacted + :tokens_compacted,
                    approval_count        = approval_count + :approval_count,
                    approval_wait_ms      = approval_wait_ms + :approval_wait_ms,
                    agent_messages        = agent_messages + :agent_messages,
                    operator_messages     = operator_messages + :operator_messages,
                    updated_at            = :now
                WHERE job_id = :job_id
            """),
            {
                "job_id": job_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "total_cost_usd": total_cost_usd,
                "premium_requests": premium_requests,
                "llm_call_count": llm_call_count,
                "total_llm_duration_ms": total_llm_duration_ms,
                "tool_call_count": tool_call_count,
                "tool_failure_count": tool_failure_count,
                "total_tool_duration_ms": total_tool_duration_ms,
                "compactions": compactions,
                "tokens_compacted": tokens_compacted,
                "approval_count": approval_count,
                "approval_wait_ms": approval_wait_ms,
                "agent_messages": agent_messages,
                "operator_messages": operator_messages,
                "now": now,
            },
        )
        await self._session.flush()

    async def set_model(self, job_id: str, model: str) -> None:
        """Update the model once confirmed by the SDK."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary
                SET model = :model, updated_at = :now
                WHERE job_id = :job_id
            """),
            {"job_id": job_id, "model": model, "now": now},
        )
        await self._session.flush()

    async def set_context(
        self, job_id: str, *, current_tokens: int | None = None, window_size: int | None = None
    ) -> None:
        """Update the point-in-time context window state."""
        parts: list[str] = []
        params: dict[str, Any] = {"job_id": job_id, "now": datetime.now(UTC).isoformat()}
        if current_tokens is not None:
            parts.append("current_context_tokens = :current_tokens")
            params["current_tokens"] = current_tokens
        if window_size is not None:
            parts.append("context_window_size = :window_size")
            params["window_size"] = window_size
        if not parts:
            return
        parts.append("updated_at = :now")
        set_clause = ", ".join(parts)
        await self._session.execute(
            text(f"UPDATE job_telemetry_summary SET {set_clause} WHERE job_id = :job_id"),  # noqa: S608
            params,
        )
        await self._session.flush()

    async def set_quota(self, job_id: str, quota_json: str) -> None:
        """Store latest Copilot quota snapshot as JSON."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary
                SET quota_json = :quota_json, updated_at = :now
                WHERE job_id = :job_id
            """),
            {"job_id": job_id, "quota_json": quota_json, "now": now},
        )
        await self._session.flush()

    async def finalize(self, job_id: str, *, status: str, duration_ms: int) -> None:
        """Set terminal status and completion timestamp."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary
                SET status = :status, completed_at = :now, duration_ms = :duration_ms, updated_at = :now
                WHERE job_id = :job_id
            """),
            {"job_id": job_id, "status": status, "duration_ms": duration_ms, "now": now},
        )
        await self._session.flush()

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Load summary row as a plain dict.  Returns None if not found."""
        result = await self._session.execute(
            text("SELECT * FROM job_telemetry_summary WHERE job_id = :job_id"),
            {"job_id": job_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return dict(row)

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
    ) -> list[dict[str, Any]]:
        """Query summary rows with optional filters."""
        conditions: list[str] = []
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
            text(
                f"SELECT * FROM job_telemetry_summary{where} "  # noqa: S608
                f"ORDER BY {sort_col} {direction} LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        return [dict(r) for r in result.mappings().all()]

    async def aggregate(self, *, period_days: int = 7) -> dict[str, Any]:
        """Return aggregate stats for the analytics overview."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    COUNT(*) as total_jobs,
                    SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) as succeeded,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                    COALESCE(SUM(total_cost_usd), 0) as total_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(SUM(premium_requests), 0) as total_premium_requests,
                    COALESCE(SUM(tool_call_count), 0) as total_tool_calls,
                    COALESCE(SUM(tool_failure_count), 0) as total_tool_failures,
                    COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
            """),
        )
        row = result.mappings().first()
        return dict(row) if row else {}

    async def cost_by_day(self, *, period_days: int = 7) -> list[dict[str, Any]]:
        """Return daily cost breakdown."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    date(created_at) as date,
                    COALESCE(SUM(total_cost_usd), 0) as cost,
                    COUNT(*) as jobs
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY date(created_at)
                ORDER BY date(created_at)
            """),
        )
        return [dict(r) for r in result.mappings().all()]

    async def cost_by_repo(self, *, period_days: int = 7) -> list[dict[str, Any]]:
        """Return per-repo cost / job count / token breakdown."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    repo,
                    COUNT(*) as job_count,
                    SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) as succeeded,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    COALESCE(SUM(total_cost_usd), 0) as total_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                    COALESCE(SUM(tool_call_count), 0) as tool_calls,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(SUM(premium_requests), 0) as premium_requests
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY repo
                ORDER BY total_cost_usd DESC
            """),
        )
        return [dict(r) for r in result.mappings().all()]

    async def cost_by_model(self, *, period_days: int = 7) -> list[dict[str, Any]]:
        """Return per-model cost / job count / token breakdown."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    model,
                    sdk,
                    COUNT(*) as job_count,
                    COALESCE(SUM(total_cost_usd), 0) as total_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COALESCE(SUM(premium_requests), 0) as premium_requests
                FROM job_telemetry_summary
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                    AND model != ''
                GROUP BY model, sdk
                ORDER BY total_cost_usd DESC
            """),
        )
        return [dict(r) for r in result.mappings().all()]
