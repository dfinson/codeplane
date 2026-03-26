"""Persistence for file access tracking.

Records every file read/write by tool calls for redundant I/O analysis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.persistence.repository import BaseRepository


class FileAccessRepo(BaseRepository):
    """Append-only log of file reads and writes per job."""

    async def record(
        self,
        *,
        job_id: str,
        file_path: str,
        access_type: str,
        turn_number: int | None = None,
        span_id: int | None = None,
        byte_count: int | None = None,
    ) -> None:
        """Insert a single file access event."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                INSERT INTO job_file_access_log
                    (job_id, file_path, access_type, turn_number, span_id, byte_count, created_at)
                VALUES
                    (:job_id, :file_path, :access_type, :turn_number, :span_id, :byte_count, :now)
            """),
            {
                "job_id": job_id,
                "file_path": file_path,
                "access_type": access_type,
                "turn_number": turn_number,
                "span_id": span_id,
                "byte_count": byte_count,
                "now": now,
            },
        )
        await self._session.flush()

    async def record_batch(
        self,
        *,
        job_id: str,
        entries: list[dict[str, Any]],
    ) -> None:
        """Insert multiple file access events in a single batch."""
        if not entries:
            return
        now = datetime.now(UTC).isoformat()
        for entry in entries:
            await self._session.execute(
                text("""
                    INSERT INTO job_file_access_log
                        (job_id, file_path, access_type, turn_number, span_id, byte_count, created_at)
                    VALUES
                        (:job_id, :file_path, :access_type, :turn_number, :span_id, :byte_count, :now)
                """),
                {
                    "job_id": job_id,
                    "file_path": entry.get("file_path", ""),
                    "access_type": entry.get("access_type", "read"),
                    "turn_number": entry.get("turn_number"),
                    "span_id": entry.get("span_id"),
                    "byte_count": entry.get("byte_count"),
                    "now": now,
                },
            )
        await self._session.flush()

    async def reread_stats(self, job_id: str) -> dict[str, Any]:
        """Compute file reread statistics for a job."""
        result = await self._session.execute(
            text("""
                SELECT
                    COUNT(*) as total_accesses,
                    COUNT(DISTINCT file_path) as unique_files,
                    SUM(CASE WHEN access_type = 'read' THEN 1 ELSE 0 END) as total_reads,
                    SUM(CASE WHEN access_type = 'write' THEN 1 ELSE 0 END) as total_writes
                FROM job_file_access_log
                WHERE job_id = :job_id
            """),
            {"job_id": job_id},
        )
        row = result.mappings().first()
        if not row:
            return {
                "total_accesses": 0,
                "unique_files": 0,
                "total_reads": 0,
                "total_writes": 0,
                "reread_count": 0,
            }

        stats = dict(row)

        # Count rereads: files read more than once
        reread_result = await self._session.execute(
            text("""
                SELECT COUNT(*) - COUNT(DISTINCT file_path) as reread_count
                FROM job_file_access_log
                WHERE job_id = :job_id AND access_type = 'read'
            """),
            {"job_id": job_id},
        )
        reread_row = reread_result.mappings().first()
        stats["reread_count"] = reread_row["reread_count"] if reread_row else 0
        return stats

    async def most_accessed_files(
        self, *, job_id: str | None = None, period_days: int = 30, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get most-accessed files, optionally filtered by job."""
        where = "WHERE 1=1"
        params: dict[str, Any] = {}
        if job_id:
            where += " AND job_id = :job_id"
            params["job_id"] = job_id
        else:
            where += f" AND created_at >= datetime('now', '-{int(period_days)} days')"

        result = await self._session.execute(
            text(f"""
                SELECT
                    file_path,
                    COUNT(*) as access_count,
                    SUM(CASE WHEN access_type = 'read' THEN 1 ELSE 0 END) as read_count,
                    SUM(CASE WHEN access_type = 'write' THEN 1 ELSE 0 END) as write_count,
                    COUNT(DISTINCT job_id) as job_count
                FROM job_file_access_log
                {where}
                GROUP BY file_path
                ORDER BY access_count DESC
                LIMIT :limit
            """),
            {**params, "limit": limit},
        )
        return [dict(r) for r in result.mappings().all()]
