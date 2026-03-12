"""Event persistence."""

from __future__ import annotations

import json

from sqlalchemy import select

from backend.models.db import EventRow
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.repository import BaseRepository


class EventRepository(BaseRepository):
    """Database access for domain event records."""

    @staticmethod
    def _to_domain(row: EventRow) -> DomainEvent:
        return DomainEvent(
            event_id=row.event_id,  # type: ignore[arg-type]
            job_id=row.job_id,  # type: ignore[arg-type]
            timestamp=row.timestamp,  # type: ignore[arg-type]
            kind=DomainEventKind(row.kind),
            payload=json.loads(row.payload),  # type: ignore[arg-type]
        )

    async def append(self, event: DomainEvent) -> None:
        """Persist a domain event."""
        row = EventRow(
            event_id=event.event_id,
            job_id=event.job_id,
            kind=event.kind.value,
            timestamp=event.timestamp,
            payload=json.dumps(event.payload),
        )
        self._session.add(row)
        await self._session.flush()

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
