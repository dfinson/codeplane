"""Persistence for per-call telemetry span detail rows.

Append-only: one row per LLM call or tool call.  Used for per-job drill-down
(tool breakdown table, LLM call timeline) and cross-job analytics (tool
failure rates, latency percentiles).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.persistence.repository import BaseRepository


class TelemetrySpansRepo(BaseRepository):
    """Append-only insert of individual LLM/tool call spans."""

    async def insert(
        self,
        *,
        job_id: str,
        span_type: str,
        name: str,
        started_at: float,
        duration_ms: float,
        attrs: dict[str, Any] | None = None,
        tool_category: str | None = None,
        tool_target: str | None = None,
        turn_number: int | None = None,
        execution_phase: str | None = None,
        is_retry: bool = False,
        retries_span_id: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        cost_usd: float | None = None,
        tool_args_json: str | None = None,
        result_size_bytes: int | None = None,
    ) -> int:
        """Record a single LLM or tool call span. Returns the inserted row id."""
        now = datetime.now(UTC).isoformat()
        attrs_json = json.dumps(attrs or {})
        result = await self._session.execute(
            text("""
                INSERT INTO job_telemetry_spans
                    (job_id, span_type, name, started_at, duration_ms, attrs_json,
                     tool_category, tool_target, turn_number, execution_phase,
                     is_retry, retries_span_id,
                     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                     cost_usd, tool_args_json, result_size_bytes,
                     created_at)
                VALUES
                    (:job_id, :span_type, :name, :started_at, :duration_ms, :attrs_json,
                     :tool_category, :tool_target, :turn_number, :execution_phase,
                     :is_retry, :retries_span_id,
                     :input_tokens, :output_tokens, :cache_read_tokens, :cache_write_tokens,
                     :cost_usd, :tool_args_json, :result_size_bytes,
                     :now)
            """),
            {
                "job_id": job_id,
                "span_type": span_type,
                "name": name,
                "started_at": started_at,
                "duration_ms": duration_ms,
                "attrs_json": attrs_json,
                "tool_category": tool_category,
                "tool_target": tool_target,
                "turn_number": turn_number,
                "execution_phase": execution_phase,
                "is_retry": is_retry,
                "retries_span_id": retries_span_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "cost_usd": cost_usd,
                "tool_args_json": tool_args_json,
                "result_size_bytes": result_size_bytes,
                "now": now,
            },
        )
        await self._session.flush()
        return result.lastrowid or 0

    async def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Return all spans for a job, ordered by start time."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, span_type, name, started_at, duration_ms, attrs_json, created_at
                FROM job_telemetry_spans
                WHERE job_id = :job_id
                ORDER BY started_at ASC
            """),
            {"job_id": job_id},
        )
        rows = []
        for r in result.mappings().all():
            row = dict(r)
            row["attrs"] = json.loads(row.pop("attrs_json", "{}"))
            rows.append(row)
        return rows

    async def tool_stats(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Aggregate tool performance stats for analytics."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    name,
                    COUNT(*) as count,
                    AVG(duration_ms) as avg_duration_ms,
                    SUM(duration_ms) as total_duration_ms,
                    SUM(CASE WHEN json_extract(attrs_json, '$.success') = 0
                             OR json_extract(attrs_json, '$.success') = 'false'
                        THEN 1 ELSE 0 END) as failure_count
                FROM job_telemetry_spans
                WHERE span_type = 'tool'
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY name
                ORDER BY count DESC
            """),
        )
        return [dict(r) for r in result.mappings().all()]
