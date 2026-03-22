"""Event persistence."""

from __future__ import annotations

import json
from datetime import datetime  # noqa: TC003 — used in cast() string arg
from typing import cast

from sqlalchemy import func, select

from backend.models.db import EventRow
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.repository import BaseRepository


class EventRepository(BaseRepository):
    """Database access for domain event records."""

    @staticmethod
    def _to_domain(row: EventRow) -> DomainEvent:
        # SQLAlchemy Column descriptors return Any at the type level;
        # cast() documents the expected runtime type for each field.
        return DomainEvent(
            event_id=cast("str", row.event_id),
            job_id=cast("str", row.job_id),
            timestamp=cast("datetime", row.timestamp),
            kind=DomainEventKind(cast("str", row.kind)),
            payload=json.loads(cast("str", row.payload)),
            db_id=cast("int | None", row.id),
        )

    async def append(self, event: DomainEvent) -> int:
        """Persist a domain event. Returns the autoincrement DB id."""
        row = EventRow(
            event_id=event.event_id,
            job_id=event.job_id,
            kind=event.kind.value,
            timestamp=event.timestamp,
            payload=json.dumps(event.payload),
        )
        self._session.add(row)
        await self._session.flush()
        db_id = cast("int", row.id)
        event.db_id = db_id
        return db_id

    async def list_after(
        self,
        after_id: int,
        job_id: str | None = None,
        limit: int = 500,
    ) -> list[DomainEvent]:
        """List events with auto-increment id > after_id, optionally scoped to a job."""
        stmt = select(EventRow).where(EventRow.id > after_id).order_by(EventRow.id)
        if job_id is not None:
            stmt = stmt.where(EventRow.job_id == job_id)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def list_by_job(
        self,
        job_id: str,
        kinds: list[DomainEventKind],
        limit: int = 2000,
    ) -> list[DomainEvent]:
        """List all events for a job filtered by kind, ordered by db id."""
        stmt = (
            select(EventRow)
            .where(EventRow.job_id == job_id)
            .where(EventRow.kind.in_([k.value for k in kinds]))
            .order_by(EventRow.id)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def get_latest_progress_preview(self, job_id: str) -> tuple[str, str] | None:
        """Return the latest progress headline and summary for a job, if present."""
        previews = await self.list_latest_progress_previews([job_id])
        return previews.get(job_id)

    async def list_latest_progress_previews(self, job_ids: list[str]) -> dict[str, tuple[str, str]]:
        """Return the latest progress headline and summary for each requested job."""
        if not job_ids:
            return {}

        latest_ids = (
            select(
                EventRow.job_id.label("job_id"),
                func.max(EventRow.id).label("latest_id"),
            )
            .where(EventRow.job_id.in_(job_ids))
            .where(EventRow.kind == DomainEventKind.progress_headline.value)
            .group_by(EventRow.job_id)
            .subquery()
        )

        stmt = select(EventRow).join(latest_ids, EventRow.id == latest_ids.c.latest_id)
        result = await self._session.execute(stmt)

        previews: dict[str, tuple[str, str]] = {}
        for row in result.scalars().all():
            job_id = cast("str", row.job_id)
            payload = json.loads(cast("str", row.payload))
            previews[job_id] = (
                str(payload.get("headline", "")).strip(),
                str(payload.get("summary", "")).strip(),
            )
        return previews
