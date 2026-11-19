"""File-centric cost attribution repository (Item 14)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.persistence.currency_projection import currency_query
from backend.persistence.repository import BaseRepository


class FileCostRepository(BaseRepository):
    """Read/write for per-job file cost attribution."""

    async def insert_batch(self, *, job_id: str, rows: list[dict[str, Any]], currency_qualified: bool = False) -> None:
        """Replace file cost rows for a job."""
        await self._session.execute(
            text("DELETE FROM job_file_cost WHERE job_id = :job_id"),
            {"job_id": job_id},
        )
        now = datetime.now(UTC).isoformat()
        for row in rows:
            await self._session.execute(
                text("""
                    INSERT INTO job_file_cost
                        (job_id, file_path, cost_usd, read_cost, write_cost,
                         turn_count, created_at, currency_qualified)
                    VALUES
                        (:job_id, :file_path, :cost_usd, :read_cost, :write_cost,
                         :turn_count, :now, :currency_qualified)
                """),
                {
                    "job_id": job_id,
                    "file_path": row.get("file_path", ""),
                    "cost_usd": row.get("cost_usd", 0.0),
                    "read_cost": row.get("read_cost", 0.0),
                    "write_cost": row.get("write_cost", 0.0),
                    "turn_count": row.get("turn_count", 0),
                    "now": now,
                    "currency_qualified": currency_qualified,
                },
            )
        await self._session.flush()

    async def for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Fetch file cost breakdown for a job."""
        result = await self._session.execute(
            currency_query("""
                SELECT file_path, cost_usd, read_cost, write_cost, turn_count
                FROM job_file_cost
                WHERE job_id = :job_id
                ORDER BY cost_usd DESC
            """),
            {"job_id": job_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def fleet_top_files(
        self,
        *,
        period_days: int = 30,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Most expensive files across all jobs in the period."""
        result = await self._session.execute(
            currency_query("""
                SELECT
                    fc.file_path,
                    SUM(fc.cost_usd) AS total_cost_usd,
                    SUM(fc.read_cost) AS total_read_cost,
                    SUM(fc.write_cost) AS total_write_cost,
                    SUM(fc.turn_count) AS total_turns,
                    COUNT(DISTINCT fc.job_id) AS job_count
                FROM job_file_cost fc
                JOIN jobs j ON j.id = fc.job_id
                WHERE j.created_at >= datetime('now', :offset)
                GROUP BY fc.file_path
                ORDER BY total_cost_usd DESC
                LIMIT :limit
            """),
            {"offset": f"-{int(period_days)} days", "limit": limit},
        )
        return [dict(r) for r in result.mappings().all()]
