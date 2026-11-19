"""Persistence for per-call telemetry span detail rows.

One row per LLM call or tool operation. Observed operation revisions update this
read projection while their canonical source events remain immutable. Used for per-job drill-down
(tool breakdown table, LLM call timeline) and cross-job analytics (tool
failure rates, latency percentiles).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import text

from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from backend.models.domain import (
        FileChurnRow,
        RetryCostSummary,
        ShellCommandRow,
        TelemetrySpanRow,
        ToolStatsRow,
    )


class TelemetrySpansRepository(BaseRepository):
    """Individual call spans and source-qualified observed-operation projections."""

    async def observed_tool(self, job_id: str, operation_id: str) -> dict[str, Any] | None:
        result = await self._session.execute(
            text("""
                SELECT id, attrs_json, duration_ms FROM job_telemetry_spans
                WHERE job_id = :job_id AND session_kind = 'job' AND span_type = 'tool'
                  AND json_extract(attrs_json, '$.observation_operation_id') = :operation_id
                LIMIT 1
            """),
            {"job_id": job_id, "operation_id": operation_id},
        )
        row = result.mappings().first()
        return {**row, "attrs": json.loads(row["attrs_json"])} if row is not None else None

    async def update_observed_tool(self, span_id: int, *, attrs: dict[str, Any], duration_ms: float) -> None:
        await self._session.execute(
            text("UPDATE job_telemetry_spans SET attrs_json = :attrs, duration_ms = :duration WHERE id = :id"),
            {"id": span_id, "attrs": json.dumps(attrs), "duration": duration_ms},
        )
        await self._session.flush()

    async def insert(
        self,
        *,
        job_id: str,
        session_kind: str = "job",
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
        error_kind: str | None = None,
        turn_id: str | None = None,
        preceding_context: str | None = None,
        motivation_summary: str | None = None,
    ) -> int:
        """Record a single LLM or tool call span. Returns the inserted row id."""
        now = datetime.now(UTC).isoformat()
        attrs_json = json.dumps(attrs or {})
        result = await self._session.execute(
            text("""
                INSERT INTO job_telemetry_spans
                    (job_id, session_kind, span_type, name, started_at, duration_ms, attrs_json,
                     tool_category, tool_target, turn_number, execution_phase,
                     is_retry, retries_span_id,
                     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                     cost_usd, tool_args_json, result_size_bytes, error_kind,
                     turn_id, preceding_context, motivation_summary, created_at)
                VALUES
                    (:job_id, :session_kind, :span_type, :name, :started_at, :duration_ms, :attrs_json,
                     :tool_category, :tool_target, :turn_number, :execution_phase,
                     :is_retry, :retries_span_id,
                     :input_tokens, :output_tokens, :cache_read_tokens, :cache_write_tokens,
                     :cost_usd, :tool_args_json, :result_size_bytes, :error_kind,
                     :turn_id, :preceding_context, :motivation_summary, :now)
            """),
            {
                "job_id": job_id,
                "session_kind": session_kind,
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
                "error_kind": error_kind,
                "turn_id": turn_id,
                "preceding_context": preceding_context,
                "motivation_summary": motivation_summary,
                "now": now,
            },
        )
        await self._session.flush()
        inserted_id = getattr(result, "lastrowid", None)
        return int(inserted_id or 0)

    async def list_for_job(self, job_id: str) -> list[TelemetrySpanRow]:
        """Return all spans for a job, ordered by start time."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, span_type, name, started_at, duration_ms, attrs_json,
                       tool_category, tool_target, turn_number, execution_phase,
                       is_retry, retries_span_id,
                       input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                       cost_usd, tool_args_json, result_size_bytes, error_kind,
                       turn_id, preceding_context, motivation_summary,
                       edit_motivations, created_at
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
            if row.get("is_retry") is not None:
                row["is_retry"] = bool(row["is_retry"])
            rows.append(row)
        return cast("list[TelemetrySpanRow]", rows)

    async def set_motivation_summary(self, span_id: int, summary: str) -> None:
        """Update the motivation_summary for a span that has been summarized."""
        await self._session.execute(
            text("UPDATE job_telemetry_spans SET motivation_summary = :summary WHERE id = :span_id"),
            {"span_id": span_id, "summary": summary},
        )
        await self._session.flush()

    async def unsummarized_spans(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return spans that have preceding_context but no motivation_summary yet."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, name, tool_args_json, preceding_context
                FROM job_telemetry_spans
                WHERE preceding_context IS NOT NULL
                  AND motivation_summary IS NULL
                ORDER BY id ASC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        return [dict(r) for r in result.mappings().all()]

    async def unenriched_edit_spans(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return file_write spans that have motivation_summary but no edit_motivations."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, name, tool_args_json, tool_target,
                       preceding_context, motivation_summary
                FROM job_telemetry_spans
                WHERE preceding_context IS NOT NULL
                  AND motivation_summary IS NOT NULL
                  AND edit_motivations IS NULL
                  AND tool_category = 'file_write'
                ORDER BY id ASC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        return [dict(r) for r in result.mappings().all()]

    async def set_edit_motivations(self, span_id: int, edit_motivations_json: str) -> None:
        """Store per-edit motivations JSON for a span."""
        await self._session.execute(
            text("UPDATE job_telemetry_spans SET edit_motivations = :em WHERE id = :span_id"),
            {"span_id": span_id, "em": edit_motivations_json},
        )
        await self._session.flush()

    async def file_write_spans_for_step(
        self,
        *,
        job_id: str,
        turn_id: str,
    ) -> list[TelemetrySpanRow]:
        """Return file_write spans with motivation data for a specific step (by turn_id)."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, name, tool_target, tool_args_json,
                       motivation_summary, edit_motivations, turn_id,
                       is_retry, error_kind, preceding_context, started_at
                FROM job_telemetry_spans
                WHERE job_id = :job_id
                  AND turn_id = :turn_id
                  AND tool_category = 'file_write'
                ORDER BY started_at ASC
            """),
            {"job_id": job_id, "turn_id": turn_id},
        )
        return cast("list[TelemetrySpanRow]", [dict(r) for r in result.mappings().all()])

    async def motivated_spans_for_job(
        self,
        *,
        job_id: str,
    ) -> list[TelemetrySpanRow]:
        """Return all file_write spans with motivation data for a job."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, name, tool_target, tool_args_json,
                       motivation_summary, edit_motivations, turn_id
                FROM job_telemetry_spans
                WHERE job_id = :job_id
                  AND tool_category = 'file_write'
                  AND motivation_summary IS NOT NULL
                ORDER BY started_at ASC
            """),
            {"job_id": job_id},
        )
        return cast("list[TelemetrySpanRow]", [dict(r) for r in result.mappings().all()])

    async def tool_stats(self, *, period_days: int = 30) -> list[ToolStatsRow]:
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
                        THEN 1 ELSE 0 END) as failure_count,
                    SUM(CASE WHEN COALESCE(json_type(attrs_json, '$.success'), 'null') = 'null'
                        THEN 1 ELSE 0 END) as unknown_count
                FROM job_telemetry_spans
                WHERE span_type = 'tool'
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY name
                ORDER BY count DESC
            """),
        )
        rows = [dict(r) for r in result.mappings().all()]

        # Compute percentiles per tool via ordered subqueries (SQLite compatible).
        for row in rows:
            pct_result = await self._session.execute(
                text(f"""
                    SELECT duration_ms
                    FROM job_telemetry_spans
                    WHERE span_type = 'tool'
                        AND name = :name
                        AND created_at >= datetime('now', '-{int(period_days)} days')
                    ORDER BY duration_ms
                """),
                {"name": row["name"]},
            )
            durations = [r[0] for r in pct_result.fetchall() if r[0] is not None]
            if durations:
                n = len(durations)
                row["p50_duration_ms"] = durations[int(n * 0.50)] if n > 0 else 0
                row["p95_duration_ms"] = durations[min(int(n * 0.95), n - 1)] if n > 0 else 0
                row["p99_duration_ms"] = durations[min(int(n * 0.99), n - 1)] if n > 0 else 0
            else:
                row["p50_duration_ms"] = 0
                row["p95_duration_ms"] = 0
                row["p99_duration_ms"] = 0

        return cast("list[ToolStatsRow]", rows)

    async def shell_command_breakdown(self, *, period_days: int = 30, limit: int = 30) -> list[ShellCommandRow]:
        """Aggregate shell commands by tool_target (first word of command).

        Groups shell-category tool spans by their extracted command name
        (stored in ``tool_target``), returning call counts and total cost.
        """
        result = await self._session.execute(
            text(f"""
                SELECT
                    tool_target as command,
                    COUNT(*) as call_count,
                    COALESCE(SUM(cost_usd), 0) as total_cost_usd,
                    COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
                    COUNT(DISTINCT job_id) as job_count
                FROM job_telemetry_spans
                WHERE span_type = 'tool'
                    AND tool_category = 'shell'
                    AND tool_target IS NOT NULL
                    AND tool_target != ''
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY tool_target
                ORDER BY call_count DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        return cast("list[ShellCommandRow]", [dict(r) for r in result.mappings().all()])

    async def file_write_churn(self, job_id: str) -> list[FileChurnRow]:
        """Per-file write count and retry count for a job."""
        result = await self._session.execute(
            text("""
                SELECT tool_target,
                       COUNT(*) as write_count,
                       SUM(CASE WHEN is_retry = 1 THEN 1 ELSE 0 END) as retry_count
                FROM job_telemetry_spans
                WHERE job_id = :jid AND tool_category = 'file_write'
                GROUP BY tool_target
            """),
            {"jid": job_id},
        )
        return cast("list[FileChurnRow]", [dict(r) for r in result.mappings().all()])

    async def test_co_modifications(self, job_id: str) -> list[dict[str, Any]]:
        """Find steps where both test and source files were written."""
        result = await self._session.execute(
            text("""
                SELECT s.turn_id, st.step_number, st.title AS step_title,
                       GROUP_CONCAT(s.tool_target) AS files
                FROM job_telemetry_spans s
                LEFT JOIN steps st ON st.job_id = s.job_id AND st.turn_id = s.turn_id
                WHERE s.job_id = :jid AND s.tool_category = 'file_write'
                GROUP BY s.turn_id
                HAVING COUNT(DISTINCT s.tool_target) > 1
            """),
            {"jid": job_id},
        )
        import re

        test_re = re.compile(
            r"(^|/)tests?/|test_[^/]+\.py$|_test\.py$|\.(?:test|spec)\.(?:ts|tsx|js|jsx)$|__tests__/",
        )
        hits: list[dict[str, Any]] = []
        for row in result.mappings():
            files = (row["files"] or "").split(",")
            test_files = [f for f in files if test_re.search(f)]
            source_files = [f for f in files if not test_re.search(f)]
            if test_files and source_files:
                hits.append(
                    {
                        "turnId": row["turn_id"],
                        "stepNumber": row["step_number"],
                        "stepTitle": row["step_title"],
                        "testFiles": test_files,
                        "sourceFiles": source_files,
                    }
                )
        return hits

    async def retry_cost_summary(self, *, period_days: int = 30) -> RetryCostSummary:
        """Compute total cost and count of retry spans fleet-wide."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    COALESCE(SUM(CASE WHEN is_retry = 1 THEN cost_usd ELSE 0 END), 0) as retry_cost_usd,
                    SUM(CASE WHEN is_retry = 1 THEN 1 ELSE 0 END) as retry_count,
                    COUNT(*) as total_spans,
                    COALESCE(SUM(cost_usd), 0) as total_cost_usd
                FROM job_telemetry_spans
                WHERE span_type IN ('llm', 'tool')
                    AND created_at >= datetime('now', '-{int(period_days)} days')
            """),
        )
        row = result.mappings().first()
        if row:
            return cast("RetryCostSummary", dict(row))
        return {"retry_cost_usd": 0, "retry_count": 0, "total_spans": 0, "total_cost_usd": 0}

    async def tool_failure_hotspots(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Find tools with high failure rates (for statistical analysis)."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    name,
                    COUNT(*) as total_calls,
                    SUM(CASE WHEN json_extract(attrs_json, '$.success') = 0
                             OR json_extract(attrs_json, '$.success') = 'false'
                        THEN 1 ELSE 0 END) as failures,
                    COUNT(DISTINCT job_id) as job_count
                FROM job_telemetry_spans
                WHERE span_type = 'tool'
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY name
                HAVING total_calls >= 10
                    AND CAST(failures AS FLOAT) / total_calls >= 0.2
                ORDER BY failures DESC
                LIMIT 20
            """)
        )
        return [dict(r) for r in result.mappings().all()]

    async def retry_hotspots(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Find tools with frequent retries (for statistical analysis)."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    name as tool_name,
                    SUM(CASE WHEN is_retry = 1 THEN 1 ELSE 0 END) as retry_count,
                    COUNT(*) as total_calls,
                    COUNT(DISTINCT job_id) as job_count
                FROM job_telemetry_spans
                WHERE span_type = 'tool'
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY name
                HAVING retry_count >= 5
                ORDER BY retry_count DESC
                LIMIT 20
            """)
        )
        return [dict(r) for r in result.mappings().all()]
