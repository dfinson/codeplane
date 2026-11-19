"""Rebuild structural historical metadata without model inference or raw rewrites."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from backend.models.events import EventKind, new_event
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.services.events.tool_pairing import ToolPairing

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.session_event import SessionEvent

log = structlog.get_logger()
_PROJECTION = "cp-history-metadata-v1"
_BATCH_SIZE = 500
_job_locks: dict[str, asyncio.Lock] = {}


async def reenrich_job_events(
    job_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    force: bool = False,
) -> int:
    """Rebuild display/duration from stored events, preserving legacy evidence.

    This does not reclassify risk, rerun titles, emit execution events or alter
    raw records. Legacy enrichment markers remain historical evidence.
    """
    lock = _job_locks.setdefault(job_id, asyncio.Lock())
    if lock.locked():
        log.info("reenrich_already_running", job_id=job_id)
        return 0
    async with lock:
        try:
            return await _reenrich_locked(job_id, session_factory, force=force)
        finally:
            _job_locks.pop(job_id, None)


async def _reenrich_locked(job_id: str, session_factory: async_sessionmaker[AsyncSession], *, force: bool) -> int:
    async with serialized_write(session_factory) as session:
        repo = EventRepository(session)
        markers = await repo.list_by_job(job_id, [EventKind.reenrich_complete])
        current = [event for event in markers if event.payload.get("projection_version") == _PROJECTION]
        if current and not force:
            return 0
        pairing = ToolPairing()
        updated = 0
        offset = 0
        while True:
            batch = await repo.list_all_events_by_job(job_id, limit=_BATCH_SIZE, offset=offset)
            if not batch:
                break
            for event in batch:
                if event.kind == EventKind.reenrich_complete:
                    continue
                enriched = pairing.process(event)
                if enriched is None:
                    continue
                emitted = enriched if isinstance(enriched, list) else [enriched]
                for projected in emitted:
                    # Displaced/orphan starts carry unchanged metadata; do not
                    # replace their raw payload with a new pairing interpretation.
                    original = event if projected.id == event.id else await repo.get(projected.id)
                    if original is None:
                        raise ValueError("Historical pairing produced an unknown event identity")
                    updated += await _persist_metadata(repo, original, projected)
            if len(batch) < _BATCH_SIZE:
                break
            offset += _BATCH_SIZE
        for projected in pairing.flush():
            original = await repo.get(projected.id)
            if original is None:
                raise ValueError("Historical pairing produced an unknown buffered identity")
            updated += await _persist_metadata(repo, original, projected)
        for marker in current:
            await repo.delete_event(marker.id)
        await repo.append(
            new_event(
                job_id,
                EventKind.reenrich_complete,
                {"updated_count": updated, "projection_version": _PROJECTION},
            )
        )
    log.info("reenrich_complete", job_id=job_id, updated=updated, projection_version=_PROJECTION)
    return updated


async def _persist_metadata(repo: EventRepository, original: SessionEvent, projected: SessionEvent) -> int:
    metadata = original.metadata.model_copy(
        update={
            "duration_ms": projected.metadata.duration_ms,
            "tool_display": projected.metadata.tool_display,
        }
    )
    if original.metadata == metadata:
        return 0
    await repo.update_metadata(event_id=original.id, metadata=metadata)
    return 1
