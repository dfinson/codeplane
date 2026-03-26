"""Persistence for cross-job cost observations and anomalies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.persistence.repository import BaseRepository


class ObservationsRepo(BaseRepository):
    """CRUD for the cost_observations table."""

    async def upsert(
        self,
        *,
        category: str,
        severity: str,
        title: str,
        detail: str,
        evidence: dict[str, Any] | list[Any],
        job_count: int = 0,
        total_waste_usd: float = 0.0,
    ) -> None:
        """Insert or update an observation by (category, title)."""
        now = datetime.now(UTC).isoformat()
        evidence_json = json.dumps(evidence)

        existing = await self._session.execute(
            text("""
                SELECT id FROM cost_observations
                WHERE category = :category AND title = :title
                LIMIT 1
            """),
            {"category": category, "title": title},
        )
        row = existing.mappings().first()

        if row:
            await self._session.execute(
                text("""
                    UPDATE cost_observations SET
                        severity = :severity,
                        detail = :detail,
                        evidence_json = :evidence_json,
                        job_count = :job_count,
                        total_waste_usd = :total_waste_usd,
                        last_seen_at = :now
                    WHERE id = :id
                """),
                {
                    "id": row["id"],
                    "severity": severity,
                    "detail": detail,
                    "evidence_json": evidence_json,
                    "job_count": job_count,
                    "total_waste_usd": total_waste_usd,
                    "now": now,
                },
            )
        else:
            await self._session.execute(
                text("""
                    INSERT INTO cost_observations
                        (category, severity, title, detail, evidence_json,
                         job_count, total_waste_usd, first_seen_at, last_seen_at, dismissed)
                    VALUES
                        (:category, :severity, :title, :detail, :evidence_json,
                         :job_count, :total_waste_usd, :now, :now, 0)
                """),
                {
                    "category": category,
                    "severity": severity,
                    "title": title,
                    "detail": detail,
                    "evidence_json": evidence_json,
                    "job_count": job_count,
                    "total_waste_usd": total_waste_usd,
                    "now": now,
                },
            )
        await self._session.flush()

    async def list_active(
        self,
        *,
        category: str | None = None,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List non-dismissed observations, most severe first."""
        conditions = ["dismissed = 0"]
        params: dict[str, Any] = {"limit": limit}
        if category:
            conditions.append("category = :category")
            params["category"] = category
        if severity:
            conditions.append("severity = :severity")
            params["severity"] = severity

        where = " AND ".join(conditions)
        severity_order = "CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"

        result = await self._session.execute(
            text(f"""
                SELECT id, category, severity, title, detail, evidence_json,
                       job_count, total_waste_usd, first_seen_at, last_seen_at
                FROM cost_observations
                WHERE {where}
                ORDER BY {severity_order}, total_waste_usd DESC
                LIMIT :limit
            """),
            params,
        )
        rows = []
        for r in result.mappings().all():
            row = dict(r)
            row["evidence"] = json.loads(row.pop("evidence_json", "{}"))
            rows.append(row)
        return rows

    async def dismiss(self, observation_id: int) -> None:
        """Mark an observation as dismissed."""
        await self._session.execute(
            text("UPDATE cost_observations SET dismissed = 1 WHERE id = :id"),
            {"id": observation_id},
        )
        await self._session.flush()
