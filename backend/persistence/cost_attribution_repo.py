"""Persistence for per-job cost attribution breakdown.

Each row represents one slice of a job's cost — by phase, tool category,
or other dimension — enabling cross-job analysis of what drives cost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.persistence.repository import BaseRepository


class CostAttributionRepo(BaseRepository):
    """Read/write for job_cost_attribution rows."""

    async def insert(
        self,
        *,
        job_id: str,
        dimension: str,
        bucket: str,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        call_count: int = 0,
    ) -> None:
        """Insert a single attribution row."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                INSERT INTO job_cost_attribution
                    (job_id, dimension, bucket, cost_usd, input_tokens, output_tokens, call_count, created_at)
                VALUES
                    (:job_id, :dimension, :bucket, :cost_usd, :input_tokens, :output_tokens, :call_count, :now)
            """),
            {
                "job_id": job_id,
                "dimension": dimension,
                "bucket": bucket,
                "cost_usd": cost_usd,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "call_count": call_count,
                "now": now,
            },
        )
        await self._session.flush()

    async def insert_batch(
        self,
        *,
        job_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Insert multiple attribution rows for a job."""
        if not rows:
            return
        now = datetime.now(UTC).isoformat()
        for row in rows:
            await self._session.execute(
                text("""
                    INSERT INTO job_cost_attribution
                        (job_id, dimension, bucket, cost_usd, input_tokens, output_tokens, call_count, created_at)
                    VALUES
                        (:job_id, :dimension, :bucket, :cost_usd, :input_tokens, :output_tokens, :call_count, :now)
                """),
                {
                    "job_id": job_id,
                    "dimension": row.get("dimension", ""),
                    "bucket": row.get("bucket", ""),
                    "cost_usd": row.get("cost_usd", 0.0),
                    "input_tokens": row.get("input_tokens", 0),
                    "output_tokens": row.get("output_tokens", 0),
                    "call_count": row.get("call_count", 0),
                    "now": now,
                },
            )
        await self._session.flush()

    async def for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Fetch all attribution rows for a job."""
        result = await self._session.execute(
            text("""
                SELECT id, job_id, dimension, bucket, cost_usd,
                       input_tokens, output_tokens, call_count, created_at
                FROM job_cost_attribution
                WHERE job_id = :job_id
                ORDER BY dimension, cost_usd DESC
            """),
            {"job_id": job_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def by_dimension(
        self,
        dimension: str,
        *,
        period_days: int = 30,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Aggregate attribution across jobs for a given dimension."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    bucket,
                    SUM(cost_usd) as cost_usd,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(call_count) as call_count,
                    COUNT(DISTINCT job_id) as job_count
                FROM job_cost_attribution
                WHERE dimension = :dimension
                    AND created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY bucket
                ORDER BY cost_usd DESC
                LIMIT :limit
            """),
            {"dimension": dimension, "limit": limit},
        )
        return [dict(r) for r in result.mappings().all()]

    async def fleet_summary(self, *, period_days: int = 30) -> list[dict[str, Any]]:
        """Cross-job summary: top cost buckets across all dimensions."""
        result = await self._session.execute(
            text(f"""
                SELECT
                    dimension,
                    bucket,
                    SUM(cost_usd) as cost_usd,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(call_count) as call_count,
                    COUNT(DISTINCT job_id) as job_count,
                    AVG(cost_usd) as avg_cost_per_job
                FROM job_cost_attribution
                WHERE created_at >= datetime('now', '-{int(period_days)} days')
                GROUP BY dimension, bucket
                ORDER BY cost_usd DESC
                LIMIT 100
            """),
        )
        return [dict(r) for r in result.mappings().all()]
