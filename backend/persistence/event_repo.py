"""Event persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import delete, func, select, update

from backend.models.db import EventRow
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent, new_event
from backend.models.session_event import EventMetadata
from backend.persistence.repository import BaseRepository


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """A canonical event paired with its storage-local replay cursor."""

    storage_cursor: int
    event: SessionEvent


@dataclass(frozen=True, slots=True)
class ObservationProjectionReference:
    event_id: str
    step_id: str | None
    step_number: int | None
    turn_id: str | None

    @classmethod
    def from_event(cls, event: SessionEvent) -> ObservationProjectionReference:
        return cls(
            event.id, event.payload.get("step_id"), event.payload.get("step_number"), event.payload.get("turn_id")
        )


class EventRepository(BaseRepository):
    """Raw event persistence. Direct consumers: RuntimeService, TrailService,
    RuntimeTelemetry (log lines). All other services must use
    TrailNodeRepository projections. See internal-docs/design/unified-trail-service.md §6."""

    @staticmethod
    def _to_domain(row: EventRow) -> SessionEvent:
        raw_md = row.event_metadata
        metadata = EventMetadata.model_validate(json.loads(raw_md)) if raw_md else None
        return new_event(
            event_id=row.event_id,
            session_id=row.job_id,
            timestamp=row.timestamp.replace(tzinfo=UTC) if row.timestamp.tzinfo is None else row.timestamp,
            kind=row.kind,
            payload=json.loads(row.payload),
            metadata=metadata,
            raw_event=json.loads(row.raw_event) if row.raw_event is not None else None,
        )

    async def append(self, event: SessionEvent) -> int:
        """Persist a domain event. Returns the autoincrement DB id."""
        existing = await self._session.execute(select(EventRow).where(EventRow.event_id == event.id))
        previous = existing.scalar_one_or_none()
        if previous is not None:
            self._verify_identity(previous, event)
            return previous.id
        row = EventRow(
            event_id=event.id,
            job_id=event.session_id or None,
            kind=str(event.kind),
            timestamp=(
                event.timestamp.replace(tzinfo=UTC)
                if event.timestamp.tzinfo is None
                else event.timestamp.astimezone(UTC)
            ),
            payload=json.dumps(dict(event.payload)),
            event_metadata=json.dumps(event.metadata.model_dump(mode="json")),
            raw_event=json.dumps(event.raw_event) if event.raw_event is not None else None,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    @staticmethod
    def _verify_identity(row: EventRow, event: SessionEvent) -> None:
        previous = json.loads(row.payload)
        source = event.payload.get("_codeplane_source")
        same = previous.get("_codeplane_source") == source if source else previous == dict(event.payload)
        raw_matches = row.raw_event is None or json.loads(row.raw_event) == event.raw_event
        if row.job_id != event.session_id or row.kind != str(event.kind) or not same or not raw_matches:
            raise ValueError(f"Conflicting content for event identity {event.id}")

    async def contains(self, event: SessionEvent) -> bool:
        result = await self._session.execute(select(EventRow).where(EventRow.event_id == event.id))
        row = result.scalar_one_or_none()
        if row is None:
            return False
        self._verify_identity(row, event)
        return True

    async def count_native_events(self, job_id: str, framework: str, external_session_id: str) -> int:
        """Count durable projected source events, not physical records or SSE cursors."""
        result = await self._session.execute(
            select(func.count())
            .select_from(EventRow)
            .where(
                EventRow.job_id == job_id,
                func.json_extract(EventRow.payload, "$._codeplane_source.framework") == framework,
                func.json_extract(EventRow.payload, "$._codeplane_source.session_id") == external_session_id,
            )
        )
        return int(result.scalar_one())

    async def get(self, event_id: str) -> SessionEvent | None:
        result = await self._session.execute(select(EventRow).where(EventRow.event_id == event_id))
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row is not None else None

    async def first_observed_completions(
        self, job_id: str, binding_id: str, operation_ids: list[str]
    ) -> dict[str, ObservationProjectionReference]:
        """Bounded lookups over retained canonical history, not a lifetime-sized cache."""
        found = {}
        operation = func.json_extract(EventRow.payload, "$._codeplane_observation.operation_id")
        for offset in range(0, len(operation_ids), 128):
            first = (
                select(func.min(EventRow.id))
                .where(
                    EventRow.job_id == job_id,
                    EventRow.kind.in_((EventKind.tool_call_completed.value, EventKind.tool_call_failed.value)),
                    func.json_extract(EventRow.event_metadata, "$.integration_binding_id") == binding_id,
                    operation.in_(operation_ids[offset : offset + 128]),
                )
                .group_by(operation)
            )
            rows = await self._session.execute(
                select(
                    EventRow.event_id,
                    operation.label("operation"),
                    func.json_extract(EventRow.payload, "$.step_id").label("step_id"),
                    func.json_extract(EventRow.payload, "$.step_number").label("step_number"),
                    func.json_extract(EventRow.payload, "$.turn_id").label("turn_id"),
                ).where(EventRow.id.in_(first))
            )
            for row in rows.mappings():
                found[row["operation"]] = ObservationProjectionReference(
                    row["event_id"],
                    row["step_id"],
                    row["step_number"],
                    row["turn_id"],
                )
        return found

    async def list_after(
        self,
        after_id: int,
        job_id: str | None = None,
        limit: int = 500,
    ) -> list[StoredEvent]:
        """List canonical events with their storage-local cursors."""
        stmt = select(EventRow).where(EventRow.id > after_id).order_by(EventRow.id)
        if job_id is not None:
            stmt = stmt.where(EventRow.job_id == job_id)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [StoredEvent(storage_cursor=row.id, event=self._to_domain(row)) for row in result.scalars().all()]

    async def list_by_job(
        self,
        job_id: str,
        kinds: list[EventKind],
        limit: int = 2000,
    ) -> list[SessionEvent]:
        """List events for a job filtered by kind, ordered by db id."""
        stmt = (
            select(EventRow)
            .where(EventRow.job_id == job_id)
            .where(EventRow.kind.in_([k.value for k in kinds]))
            .order_by(EventRow.id)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def list_all_by_job(
        self,
        job_id: str,
        kinds: list[EventKind],
    ) -> list[SessionEvent]:
        """List all events for a job filtered by kind, without an upper bound."""
        stmt = (
            select(EventRow)
            .where(EventRow.job_id == job_id)
            .where(EventRow.kind.in_([k.value for k in kinds]))
            .order_by(EventRow.id)
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
            .where(EventRow.kind == EventKind.progress_headline.value)
            .group_by(EventRow.job_id)
            .subquery()
        )

        stmt = select(EventRow).join(latest_ids, EventRow.id == latest_ids.c.latest_id)
        result = await self._session.execute(stmt)

        previews: dict[str, tuple[str, str]] = {}
        for row in result.scalars().all():
            job_id = row.job_id
            payload = json.loads(row.payload)
            previews[job_id] = (
                str(payload.get("headline", "")).strip(),
                str(payload.get("summary", "")).strip(),
            )
        return previews

    async def search_transcript(
        self,
        job_id: str,
        query: str,
        kinds: list[str] | None = None,
        step_id: str | None = None,
        limit: int = 50,
    ) -> list[SessionEvent]:
        """Full-text search within a job's transcript events."""
        from sqlalchemy import func, or_

        stmt = select(EventRow).where(
            EventRow.job_id == job_id,
            EventRow.kind.in_([k.value for k in TRANSCRIPT_KINDS]),
        )
        if kinds:
            stmt = stmt.where(EventRow.kind.in_(kinds))
        if step_id:
            stmt = stmt.where(EventRow.payload.contains(f'"step_id": "{step_id}"'))

        # Search only content-bearing fields, not the entire JSON payload.
        # ``tool_display`` lives on the serialized EventMetadata, not the payload.
        like_pattern = f"%{query}%"
        content_field = func.json_extract(EventRow.payload, "$.content")
        tool_name_field = func.json_extract(EventRow.payload, "$.tool_name")
        tool_display_field = func.json_extract(EventRow.event_metadata, "$.tool_display")
        stmt = stmt.where(
            or_(
                content_field.ilike(like_pattern),
                tool_name_field.ilike(like_pattern),
                tool_display_field.ilike(like_pattern),
            )
        )
        stmt = stmt.order_by(EventRow.id).limit(limit)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def list_all_events_by_job(
        self, job_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[SessionEvent]:
        """List events for a job in storage order, with optional pagination."""
        stmt = select(EventRow).where(EventRow.job_id == job_id).order_by(EventRow.id)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def update_metadata(
        self,
        event_id: str,
        metadata: EventMetadata,
    ) -> None:
        """Update the serialised metadata on an existing event row."""
        stmt = (
            update(EventRow)
            .where(EventRow.event_id == event_id)
            .values(
                event_metadata=json.dumps(
                    metadata.model_dump(mode="json"),
                    ensure_ascii=False,
                    default=str,
                )
            )
        )
        await self._session.execute(stmt)

    async def delete_event(self, event_id: str) -> None:
        """Delete a single event by its ID."""
        stmt = delete(EventRow).where(EventRow.event_id == event_id)
        await self._session.execute(stmt)
