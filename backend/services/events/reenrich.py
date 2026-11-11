"""Idempotent re-enrichment of persisted events through TraceForge.

Replays a job's stored ``SessionEvent`` values through a fresh
``traceforge.Enricher`` instance in temporal order so that
classification, visibility, phases, duration_ms, risk scoring, and
tool_display are backfilled on events that predate the inline
enrichment wiring.

The path is bounded (batched pagination, not full memory load),
deterministic (same enricher, same event order → same output),
concurrency-safe (per-job asyncio lock prevents duplicate replay),
and durable-no-repeat (a marker event prevents double processing;
force deletes then re-inserts the marker).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from traceforge.enricher import Enricher as TFEnricher

from backend.models.events import EventKind, EventMetadata, SessionEvent, new_event

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.persistence.event_repo import EventRepository

log = structlog.get_logger()

# Marker event kind stored after a successful re-enrichment pass.
_REENRICH_MARKER_KIND = EventKind.reenrich_complete

# Per-job concurrency exclusion — prevents duplicate concurrent re-enrichment.
# Bounded: locks are removed after the reenrich completes (see finally block).
_job_locks: dict[str, asyncio.Lock] = {}

# Batch size for paginated event loading.
_BATCH_SIZE = 500


def _canonical_metadata(metadata: EventMetadata | None) -> str | None:
    """Serialize event metadata to a stable, order-independent string.

    Used to detect whether re-enrichment actually changed an event's metadata.
    Snapshotting the *serialized* form **before** ``Enricher.process`` makes the
    change check robust even if a future TraceForge were to mutate the event's
    metadata object in place (sharing it with the returned event) instead of
    returning a fresh copy: a plain ``!=`` on the live objects would then always
    compare equal and silently skip the write, marking backfill complete with no
    persisted changes. TF 0.1.5 returns copies (frozen models), but comparing
    pre-captured snapshots keeps the guarantee independent of that.
    """
    if metadata is None:
        return None
    return json.dumps(
        metadata.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


async def _persist_if_changed(
    repo: EventRepository,
    event: SessionEvent,
    baseline: str | None,
) -> bool:
    """Persist *event*'s metadata iff it differs from the *baseline* snapshot.

    ``baseline`` is the canonical serialization of the event's metadata captured
    before enrichment. Returns ``True`` when a write occurred (so callers can
    keep an accurate updated count) and ``False`` otherwise — avoiding redundant
    writes when enrichment produced no net change.
    """
    after = _canonical_metadata(event.metadata)
    if after is None or after == baseline:
        return False
    await repo.update_metadata(event_id=event.id, metadata=event.metadata)
    return True


def _pair_key(event: SessionEvent) -> tuple[str, str] | None:
    """The ``(session_id, tool_call_id)`` key TraceForge pairs a
    ``tool_call_started`` and its ``tool_call_completed`` on.

    TraceForge 0.1.5 correlates a start with its completion purely on the
    canonical ``payload['tool_call_id']`` — and only when it is a **non-empty
    string** (its private ``_extract_tool_call_id`` is exactly
    ``value if isinstance(value, str) and value else None``). This mirrors that
    public contract directly: both CodePlane adapters write ``tool_call_id`` and
    the ingest-equivalence contract asserts it is identical across a
    started/completed pair. A missing, empty, or non-string id is unpaired — TF
    emits such a start immediately rather than buffering it — so it yields no
    pair key here.
    """
    tool_call_id = (event.payload or {}).get("tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return None
    return (event.session_id, tool_call_id)


class _StartBaselineTracker:
    """Bounded store of pre-enrichment metadata snapshots for buffered tool-starts.

    TraceForge buffers a ``tool_call_started`` until its ``tool_call_completed``
    arrives, then emits the *completion* (a distinct event id) and silently drops
    the paired start — the start is never re-emitted. Keying snapshots only by the
    emitted id therefore never retires a paired start, so the store would grow
    with every paired call (a real leak on long jobs).

    This tracker keeps a start's baseline keyed by the start's own id (so an
    orphan — same id — finds its baseline even across the duplicate/displaced
    case) and additionally indexes the currently-buffered start id by
    TraceForge's ``(session_id, tool_call_id)`` pair key. A start's baseline is
    retired the moment its completion resolves the pair (``retire_completion``)
    or it is emitted as an orphan (``retire_emitted``), so the store stays bounded
    by the enricher's live pending-start buffer — not the total number of calls.
    Truly unpaired starts are retained until ``flush`` so their orphans can still
    be compared against their own baseline.
    """

    def __init__(self) -> None:
        # start event id -> original serialized metadata (baseline)
        self._baseline_by_id: dict[str, str | None] = {}
        # (session_id, tool_call_id) -> id of the currently-buffered start
        self._id_by_pair: dict[tuple[str, str], str] = {}
        # Largest number of retained baselines seen — asserted bounded in tests.
        self.peak_size = 0

    def __len__(self) -> int:
        return len(self._baseline_by_id)

    def record_start(self, event: SessionEvent, baseline: str | None) -> None:
        """Retain *event*'s baseline; index it under TF's pair key."""
        self._baseline_by_id[event.id] = baseline
        pair = _pair_key(event)
        if pair is not None:
            self._id_by_pair[pair] = event.id
        self.peak_size = max(self.peak_size, len(self._baseline_by_id))

    def retire_completion(self, event: SessionEvent) -> None:
        """Retire the buffered start a completion pairs with (TF consumes it)."""
        pair = _pair_key(event)
        if pair is None:
            return
        start_id = self._id_by_pair.pop(pair, None)
        if start_id is not None:
            self._baseline_by_id.pop(start_id, None)

    def baseline_for(self, event: SessionEvent) -> str | None:
        """Baseline for an emitted orphan (looked up by its own id)."""
        return self._baseline_by_id.get(event.id)

    def retire_emitted(self, event: SessionEvent) -> None:
        """Retire a start once it has been emitted as an orphan (or rolled back)."""
        self._baseline_by_id.pop(event.id, None)
        pair = _pair_key(event)
        if pair is not None and self._id_by_pair.get(pair) == event.id:
            self._id_by_pair.pop(pair, None)


def _get_job_lock(job_id: str) -> asyncio.Lock:
    """Get or create the per-job asyncio lock."""
    if job_id not in _job_locks:
        _job_locks[job_id] = asyncio.Lock()
    return _job_locks[job_id]


async def reenrich_job_events(
    job_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    force: bool = False,
) -> int:
    """Re-enrich all persisted events for *job_id* through TraceForge.

    Returns the number of events whose metadata was updated, or 0 if the
    job was already re-enriched (unless *force* is True).

    Concurrency-safe: per-job lock prevents duplicate concurrent replay.
    Bounded: events are loaded in batches of ``_BATCH_SIZE``.
    Durable: force=True deletes the old marker and inserts a fresh one.
    """
    lock = _get_job_lock(job_id)
    if lock.locked():
        log.info("reenrich_already_running", job_id=job_id)
        return 0

    async with lock:
        try:
            return await _reenrich_locked(job_id, session_factory, force=force)
        finally:
            # Remove lock from dict to prevent unbounded accumulation.
            # If another call races after removal, _get_job_lock creates a new one.
            _job_locks.pop(job_id, None)


async def _reenrich_locked(
    job_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    force: bool = False,
) -> int:
    """Core re-enrichment logic (must be called under the per-job lock)."""
    from backend.persistence.event_repo import EventRepository

    async with session_factory() as session:
        repo = EventRepository(session)

        # Check for existing marker
        markers = await repo.list_by_job(job_id, [_REENRICH_MARKER_KIND], limit=1)
        if markers and not force:
            log.info("reenrich_already_complete", job_id=job_id)
            return 0

        # If force, delete existing marker(s) first — clean slate
        if markers and force:
            for m in markers:
                await repo.delete_event(m.id)

        # Re-enrich through a fresh TF Enricher in batches
        enricher = TFEnricher(flush_on_session_end=True)
        updated = 0
        failed = 0
        offset = 0

        # Pre-enrichment metadata snapshots for buffered tool-starts, so an
        # orphaned start is compared against its *own* original (not the
        # unrelated event currently being processed). Bounded: a start's baseline
        # is retired the instant its completion resolves the pair or it is
        # emitted as an orphan, so this never grows with total paired calls.
        tracker = _StartBaselineTracker()

        while True:
            batch = await repo.list_all_events_by_job(job_id, limit=_BATCH_SIZE, offset=offset)
            if not batch:
                break

            for event in batch:
                # Skip marker events — they are internal, not replay targets
                if event.kind == _REENRICH_MARKER_KIND:
                    continue

                # Snapshot the ORIGINAL metadata *before* enrichment so the
                # change check can't be defeated by in-place mutation.
                original = _canonical_metadata(event.metadata)
                if event.kind == EventKind.tool_call_started:
                    tracker.record_start(event, original)

                try:
                    enriched = enricher.process(event)
                except Exception:
                    failed += 1
                    log.warning(
                        "reenrich_event_failed",
                        job_id=job_id,
                        event_id=event.id,
                        exc_info=True,
                    )
                    if event.kind == EventKind.tool_call_started:
                        tracker.retire_emitted(event)  # roll back this start
                    continue

                # A completion consumes its buffered start (TF drops it from the
                # pending buffer and never re-emits it) — retire that baseline now
                # so paired starts do not accumulate.
                if event.kind == EventKind.tool_call_completed:
                    tracker.retire_completion(event)

                if enriched is None:
                    # Buffered unpaired tool-start: its baseline stays in the
                    # tracker for the eventual orphan flush.
                    continue

                events_to_update = enriched if isinstance(enriched, list) else [enriched]
                for e in events_to_update:
                    baseline = original if e.id == event.id else tracker.baseline_for(e)
                    if await _persist_if_changed(repo, e, baseline):
                        updated += 1
                    # Retire the baseline of any emitted START now that it is
                    # persisted — whether it is a displaced/evicted orphan (a
                    # different id) OR this very event emitted immediately under
                    # its own id because it has no valid tool_call_id and so was
                    # never buffered. Keying retirement on ``e.id != event.id``
                    # missed the latter, leaking one baseline per id-less start.
                    # ``retire_emitted`` only drops the pair index when it still
                    # points at this id, preserving displaced-orphan cleanup.
                    if e.kind == EventKind.tool_call_started:
                        tracker.retire_emitted(e)

            if len(batch) < _BATCH_SIZE:
                break
            offset += _BATCH_SIZE

        # Flush any remaining buffered events (truly unpaired tool-starts), each
        # compared against its own pre-enrichment snapshot.
        for orphan in enricher.flush():
            baseline = tracker.baseline_for(orphan)
            tracker.retire_emitted(orphan)
            if await _persist_if_changed(repo, orphan, baseline):
                updated += 1

        # Insert fresh marker event (old one deleted above if force)
        if failed:
            raise RuntimeError(f"Re-enrichment failed for {failed} events. No completion marker was written.")
        marker = new_event(
            session_id=job_id,
            timestamp=datetime.now(UTC),
            kind=_REENRICH_MARKER_KIND,
            payload={"updated_count": updated},
        )
        await _append_marker(session, job_id, marker)

        await session.commit()
        log.info("reenrich_complete", job_id=job_id, updated=updated)
        return updated


async def _append_marker(
    session: AsyncSession,
    job_id: str,
    marker: SessionEvent,
) -> None:
    """Persist the re-enrichment marker event."""
    from backend.models.db import EventRow

    row = EventRow(
        event_id=marker.id,
        job_id=job_id,
        kind=str(marker.kind),
        timestamp=marker.timestamp,
        payload=json.dumps(marker.payload, ensure_ascii=False, default=str),
        event_metadata=json.dumps(
            marker.metadata.model_dump(mode="json") if marker.metadata else {},
            ensure_ascii=False,
            default=str,
        ),
    )
    session.add(row)
