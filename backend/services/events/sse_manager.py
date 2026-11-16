"""SSE connection management."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from backend.models.api_schemas import ApprovalResponse, SnapshotPayload
from backend.models.events import TRANSCRIPT_KINDS, EventKind, SessionEvent
from backend.services.events.observation import allows_observation_notification

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.persistence.approval_repo import ApprovalRepository
    from backend.persistence.event_repo import EventRepository
    from backend.persistence.job_repo import JobRepository

log = structlog.get_logger()

# Dotted TF kinds delivered to the frontend. Kinds NOT in this allowlist are
# internal-only (steps, sidecar internals, workspace prep, dead kinds) and are
# never broadcast. The wire ``event:`` type IS the dotted kind and ``data:`` is
# the TF event serialized as-is — no translation to any legacy wire vocabulary.
# Derived job-state transitions (e.g. permission.requested ⇒ waiting_for_approval)
# are computed by the frontend from these dotted kinds, not synthesized here.
_BROADCAST_KINDS: frozenset[str] = frozenset(
    {
        EventKind.job_created,
        EventKind.tracker_operation_updated,
        EventKind.recipe_updated,
        EventKind.review_recorded,
        EventKind.job_setup_progress,
        EventKind.log_line_emitted,
        EventKind.diff_updated,
        EventKind.approval_requested,
        EventKind.approval_resolved,
        EventKind.batch_approval_requested,
        EventKind.batch_approval_resolved,
        EventKind.job_review,
        EventKind.job_completed,
        EventKind.job_failed,
        EventKind.job_canceled,
        EventKind.job_state_changed,
        EventKind.session_heartbeat,
        EventKind.merge_completed,
        EventKind.merge_conflict,
        EventKind.session_resumed,
        EventKind.job_resolved,
        EventKind.job_archived,
        EventKind.job_title_updated,
        EventKind.model_downgraded,
        EventKind.model_observed,
        EventKind.tool_group_summary,
        EventKind.telemetry_updated,
        EventKind.plan_step_updated,
        EventKind.step_entries_reassigned,
        EventKind.turn_summary,
        EventKind.verification_started,
        EventKind.verification_result,
        EventKind.action_classified,
        EventKind.policy_settings_changed,
        EventKind.stall_detected,
        EventKind.secondary_session_started,
        EventKind.secondary_session_entry,
        EventKind.secondary_session_completed,
        EventKind.context_handoff,
    }
    | TRANSCRIPT_KINDS
)

# High-frequency dotted kinds suppressed in selective mode (>20 active jobs)
_SELECTIVE_SUPPRESSED: frozenset[str] = frozenset(
    {
        EventKind.log_line_emitted,
        EventKind.diff_updated,
        EventKind.session_heartbeat,
    }
    | TRANSCRIPT_KINDS
)

# Dotted kinds delivered only to job-scoped connections, never to global/dashboard.
# These are high-frequency during execution and only relevant to a user viewing
# a specific job's detail panel.
_JOB_SCOPED_ONLY: frozenset[str] = frozenset(
    {
        EventKind.telemetry_updated,
        EventKind.secondary_session_entry,
    }
)

# Replay bounds
MAX_REPLAY_EVENTS = 500
MAX_REPLAY_AGE = timedelta(minutes=5)


class SSEConnection:
    """Represents a single SSE client connection."""

    _QUEUE_WARN_THRESHOLD = 0.8  # 80% of maxsize

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id  # None = all jobs
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1024)
        self.closed = False

    def send(self, data: str) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            # Close the overloaded connection so the client reconnects and
            # gets missed events via replay instead of silently losing them.
            log.warning("sse_queue_full_closing_connection", job_id=self.job_id)
            self.close()

    def close(self) -> None:
        self.closed = True


def _format_sse(event_id: str | None, event_type: str, data: str) -> str:
    """Format a single SSE frame. Omits ``id:`` when *event_id* is ``None``."""
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event_type}")
    parts.append(f"data: {data}")
    return "\n".join(parts) + "\n\n"


# ---------------------------------------------------------------------------
# TraceForge event serialization
# ---------------------------------------------------------------------------


def _serialize_tf_event(event: SessionEvent) -> str:
    """Serialize a traceforge ``SessionEvent`` to the SSE ``data:`` field as-is.

    The wire carries the event's own shape — its dotted ``kind``, ``payload``,
    and ``metadata`` — with no translation to any legacy payload model. The
    frontend consumes this shape directly.
    """
    return event.model_dump_json()


class SSEManager:
    """Manages open SSE connections and broadcasts events to clients.

    Responsibilities:
    - Track active SSE connections (optionally scoped to a job_id)
    - Translate domain events to SSE wire format
    - Broadcast/route events to appropriate connections
    - Support selective streaming when >20 jobs active
    - Handle disconnection cleanup
    """

    def __init__(self) -> None:
        self._connections: list[SSEConnection] = []
        self._active_job_count: int = 0

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def register(self, conn: SSEConnection) -> None:
        """Register a new SSE connection."""
        self._connections.append(conn)
        log.debug("sse_connection_opened", job_id=conn.job_id, total=len(self._connections))

    def unregister(self, conn: SSEConnection) -> None:
        """Remove a connection."""
        conn.close()
        with contextlib.suppress(ValueError):
            self._connections.remove(conn)
        log.debug("sse_connection_closed", job_id=conn.job_id, total=len(self._connections))

    def set_active_job_count(self, count: int) -> None:
        """Update the active job count for selective streaming decisions."""
        self._active_job_count = count

    async def broadcast_domain_event(
        self,
        event: SessionEvent,
        *,
        storage_cursor: int | None = None,
    ) -> None:
        """Event bus subscriber — serialize a TF event to the SSE wire as-is.

        The ``event:`` type is the event's dotted ``kind`` and ``data:`` is the
        TraceForge ``SessionEvent`` serialized verbatim. Kinds outside the
        broadcast allowlist are internal-only and dropped here. The optional SSE
        resume cursor is storage-local and never changes event metadata.
        """
        if event.kind not in _BROADCAST_KINDS or not allows_observation_notification(event):
            return  # internal-only event

        sse_id = str(storage_cursor) if storage_cursor is not None else None
        frame = _format_sse(sse_id, str(event.kind), _serialize_tf_event(event))
        selective = self._active_job_count > 20

        # Prune connections closed since last broadcast
        self._connections = [c for c in self._connections if not c.closed]

        for conn in list(self._connections):
            if conn.closed:
                continue

            # Job-scoped connection: only deliver events for this job
            if conn.job_id is not None:
                if event.session_id != conn.job_id:
                    continue
                # Scoped connections always get full streaming
                conn.send(frame)
                continue

            # Global connections: skip job-scoped-only events entirely
            if event.kind in _JOB_SCOPED_ONLY:
                continue

            # Global connections: apply selective streaming if needed
            if selective and event.kind in _SELECTIVE_SUPPRESSED:
                continue

            conn.send(frame)

    def send_snapshot(self, conn: SSEConnection, snapshot: SnapshotPayload) -> None:
        """Send a snapshot event to a specific connection.

        Snapshot frames omit the ``id:`` field so they don't advance the
        client's ``lastEventId`` cursor — replay IDs stay monotonic with
        the DB autoincrement sequence.
        """
        frame = _format_sse(
            None,
            "snapshot",
            snapshot.model_dump_json(by_alias=True),
        )
        conn.send(frame)

    @staticmethod
    async def _fetch_pending_approvals(
        approval_repo: ApprovalRepository | None,
        job_id: str | None,
    ) -> list[ApprovalResponse]:
        """Fetch pending approvals from the database for snapshot payloads."""
        if approval_repo is None:
            return []

        pending = await approval_repo.list_pending(job_id=job_id)
        return [
            ApprovalResponse(
                id=a.id,
                job_id=a.job_id,
                description=a.description,
                proposed_action=a.proposed_action,
                requested_at=a.requested_at,
                resolved_at=a.resolved_at,
                resolution=a.resolution,
                requires_explicit_approval=a.requires_explicit_approval,
            )
            for a in pending
        ]

    async def replay_events(
        self,
        conn: SSEConnection,
        event_repo: EventRepository,
        job_repo: JobRepository,
        last_event_id: int,
        approval_repo: ApprovalRepository | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Replay missed events to a reconnecting client.

        If the gap is too large or too old, sends a snapshot first then
        recent events within the replay window.
        """
        cutoff = datetime.now(UTC) - MAX_REPLAY_AGE

        stored_events = await event_repo.list_after(
            after_id=last_event_id,
            job_id=conn.job_id,
            limit=MAX_REPLAY_EVENTS + 1,  # +1 to detect overflow
        )

        needs_snapshot = False
        if len(stored_events) > MAX_REPLAY_EVENTS:
            needs_snapshot = True
            stored_events = stored_events[:MAX_REPLAY_EVENTS]

        # Check if oldest event is beyond replay window
        if stored_events and stored_events[0].event.timestamp.replace(tzinfo=UTC) < cutoff:
            needs_snapshot = True

        if needs_snapshot:
            # Build and send snapshot (scoped to conn.job_id if set)
            from backend.models.api_schemas import JobResponse
            from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

            if conn.job_id is not None:
                single = await job_repo.get(conn.job_id)
                fetched_jobs = [single] if single else []
            else:
                fetched_jobs = await job_repo.list_all(include_archived=False)

            job_ids = [j.id for j in fetched_jobs]
            progress_by_job = await event_repo.list_latest_progress_previews(job_ids)
            cost_by_job: dict[str, dict[str, float | int]] = {}
            if session is not None:
                cost_by_job = await TelemetrySummaryRepository(session).batch_cost_tokens(job_ids)

            job_responses = [
                JobResponse.from_domain(
                    j,
                    progress_headline=progress_by_job.get(j.id, (None, None))[0],
                    progress_summary=progress_by_job.get(j.id, (None, None))[1],
                    **{k: v for k, v in cost_by_job.get(j.id, {}).items()},
                )
                for j in fetched_jobs
            ]
            snapshot = SnapshotPayload(
                jobs=job_responses,
                pending_approvals=await self._fetch_pending_approvals(approval_repo, conn.job_id),
            )
            self.send_snapshot(conn, snapshot)

            # Filter events to only those within the replay window
            stored_events = [stored for stored in stored_events if stored.event.timestamp.replace(tzinfo=UTC) >= cutoff]

        # Replay the events
        for stored in stored_events:
            event = stored.event
            if event.kind not in _BROADCAST_KINDS or not allows_observation_notification(event):
                continue
            frame = _format_sse(
                str(stored.storage_cursor),
                str(event.kind),
                _serialize_tf_event(event),
            )
            conn.send(frame)

    async def replay_from_factory(
        self,
        conn: SSEConnection,
        session_factory: async_sessionmaker[AsyncSession],
        last_event_id: int,
    ) -> None:
        """Replay missed events using a session factory.

        This is the preferred entry point from API routes — it keeps
        persistence imports inside the service layer so route modules
        never need to import repository classes directly.
        """
        from backend.persistence.approval_repo import ApprovalRepository
        from backend.persistence.event_repo import EventRepository
        from backend.persistence.job_repo import JobRepository

        async with session_factory() as session:
            event_repo = EventRepository(session)
            job_repo = JobRepository(session)
            approval_repo = ApprovalRepository(session)
            await self.replay_events(
                conn,
                event_repo,
                job_repo,
                last_event_id,
                approval_repo=approval_repo,
                session=session,
            )

    def close_all(self) -> None:
        """Close all connections (used during shutdown)."""
        for conn in list(self._connections):
            conn.close()
        self._connections.clear()
