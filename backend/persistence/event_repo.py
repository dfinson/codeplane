"""Event persistence."""

from __future__ import annotations

import json
from typing import cast

from sqlalchemy import select

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
            event_id=cast(str, row.event_id),
            job_id=cast(str, row.job_id),
            timestamp=cast("datetime", row.timestamp),
            kind=DomainEventKind(cast(str, row.kind)),
            payload=json.loads(cast(str, row.payload)),
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
        db_id = cast(int, row.id)
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
