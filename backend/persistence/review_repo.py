"""Canonical append-only review event reads with a frozen storage boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models.db import EventRow
from backend.models.domain import StateConflictError
from backend.models.schemas.reviews import ReviewHistoryPage, ReviewRecord
from backend.persistence.event_repo import EventRepository
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from backend.models.events import SessionEvent


class ReviewRepository(BaseRepository):
    async def event_high_water(self, job_id: str) -> int:
        return await self._session.scalar(select(func.max(EventRow.id)).where(EventRow.job_id == job_id)) or 0

    async def get(self, job_id: str, event_id: str) -> ReviewRecord | None:
        event = await EventRepository(self._session).get(event_id)
        if event is None:
            return None
        if (
            event.session_id != job_id
            or str(event.kind) != "review.recorded"
            or event.metadata.source_framework != "codeplane"
            or event.metadata.namespace != ("control", "review")
            or event.metadata.integration_binding_id is not None
        ):
            raise StateConflictError("The review identity belongs to a different canonical record.")
        return ReviewRecord.model_validate(event.payload)

    async def selected_event(self, job_id: str, event_id: str, high_water: int) -> SessionEvent | None:
        row = await self._session.scalar(
            select(EventRow).where(
                EventRow.job_id == job_id,
                EventRow.event_id == event_id,
                EventRow.id <= high_water,
            )
        )
        return EventRepository._to_domain(row) if row is not None else None

    async def history(
        self, job_id: str, *, after: int = 0, high_water: int | None = None, limit: int = 20
    ) -> ReviewHistoryPage:
        if after < 0 or not 1 <= limit <= 100 or (high_water is not None and high_water < after):
            raise StateConflictError("Invalid review history cursor.")
        maximum = high_water if high_water is not None else await self.event_high_water(job_id)
        rows = list(
            await self._session.scalars(
                select(EventRow)
                .where(
                    EventRow.job_id == job_id,
                    EventRow.kind == "review.recorded",
                    EventRow.id > after,
                    EventRow.id <= maximum,
                    func.json_extract(EventRow.event_metadata, "$.source_framework") == "codeplane",
                    func.json_extract(EventRow.event_metadata, "$.namespace") == '["control","review"]',
                    func.json_extract(EventRow.event_metadata, "$.integration_binding_id").is_(None),
                )
                .order_by(EventRow.id)
                .limit(limit + 1)
            )
        )
        return ReviewHistoryPage(
            items=[ReviewRecord.model_validate_json(row.payload) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            high_water=maximum,
        )
