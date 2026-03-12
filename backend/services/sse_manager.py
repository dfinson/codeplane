"""SSE connection management."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from backend.models.api_schemas import (
    JobStateChangedPayload,
    SnapshotPayload,
)
from backend.models.events import DomainEvent, DomainEventKind

if TYPE_CHECKING:
    from backend.persistence.event_repo import EventRepository
    from backend.persistence.job_repo import JobRepository

log = structlog.get_logger()

# SSE event type mapping from domain event kinds
_SSE_EVENT_TYPE: dict[DomainEventKind, str | None] = {
    DomainEventKind.job_created: "job_state_changed",
    DomainEventKind.workspace_prepared: None,  # internal only
    DomainEventKind.agent_session_started: None,  # internal only
    DomainEventKind.log_line_emitted: "log_line",
    DomainEventKind.transcript_updated: "transcript_update",
    DomainEventKind.diff_updated: "diff_update",
    DomainEventKind.approval_requested: "approval_requested",
    DomainEventKind.approval_resolved: "approval_resolved",
    DomainEventKind.job_succeeded: "job_state_changed",
    DomainEventKind.job_failed: "job_state_changed",
    DomainEventKind.job_canceled: "job_state_changed",
    DomainEventKind.session_heartbeat: "session_heartbeat",
}

# High-frequency event types suppressed in selective mode (>20 active jobs)
_SELECTIVE_SUPPRESSED: frozenset[str] = frozenset(
    {
        "log_line",
        "transcript_update",
        "diff_update",
        "session_heartbeat",
    }
)

# Replay bounds
MAX_REPLAY_EVENTS = 500
MAX_REPLAY_AGE = timedelta(minutes=5)


class SSEConnection:
    """Represents a single SSE client connection."""

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id  # None = all jobs
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1024)
        self.closed = False

    async def send(self, data: str) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            log.warning("sse_queue_full", job_id=self.job_id)

    def close(self) -> None:
        self.closed = True


def _format_sse(event_id: str, event_type: str, data: str) -> str:
    """Format a single SSE frame."""
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


def _domain_to_sse_data(event: DomainEvent) -> str:
    """Serialize the domain event payload to JSON for SSE data field."""
    return json.dumps(event.payload, default=str)


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
        log.info("sse_connection_opened", job_id=conn.job_id, total=len(self._connections))

    def unregister(self, conn: SSEConnection) -> None:
        """Remove a connection."""
        conn.close()
        with contextlib.suppress(ValueError):
            self._connections.remove(conn)
        log.info("sse_connection_closed", job_id=conn.job_id, total=len(self._connections))

    def set_active_job_count(self, count: int) -> None:
        """Update the active job count for selective streaming decisions."""
        self._active_job_count = count

    async def handle_event(self, event: DomainEvent) -> None:
        """Event bus subscriber — translate and broadcast a domain event."""
        sse_type = _SSE_EVENT_TYPE.get(event.kind)
        if sse_type is None:
            return  # internal-only event

        frame = _format_sse(event.event_id, sse_type, _domain_to_sse_data(event))
        selective = self._active_job_count > 20

        for conn in list(self._connections):
            if conn.closed:
                continue

            # Job-scoped connection: only deliver events for this job
            if conn.job_id is not None:
                if event.job_id != conn.job_id:
                    continue
                # Scoped connections always get full streaming
                await conn.send(frame)
                continue

            # Global connections: apply selective streaming if needed
            if selective and sse_type in _SELECTIVE_SUPPRESSED:
                continue

            await conn.send(frame)

        # Emit secondary SSE events per the mapping in §5.3.1
        if event.kind == DomainEventKind.approval_requested:
            state_payload = JobStateChangedPayload(
                job_id=event.job_id,
                previous_state=event.payload.get("previous_state"),
                new_state="waiting_for_approval",
                timestamp=event.timestamp,
            )
            state_frame = _format_sse(
                f"{event.event_id}-state",
                "job_state_changed",
                state_payload.model_dump_json(by_alias=True),
            )
            await self._broadcast_frame(state_frame, event.job_id)

        elif event.kind == DomainEventKind.approval_resolved:
            new_state = "running" if event.payload.get("resolution") == "approved" else "failed"
            state_payload = JobStateChangedPayload(
                job_id=event.job_id,
                previous_state="waiting_for_approval",
                new_state=new_state,
                timestamp=event.timestamp,
            )
            state_frame = _format_sse(
                f"{event.event_id}-state",
                "job_state_changed",
                state_payload.model_dump_json(by_alias=True),
            )
            await self._broadcast_frame(state_frame, event.job_id)

    async def _broadcast_frame(self, frame: str, job_id: str) -> None:
        """Send a pre-formatted frame to all relevant connections."""
        for conn in list(self._connections):
            if conn.closed:
                continue
            if conn.job_id is not None and conn.job_id != job_id:
                continue
            await conn.send(frame)

    async def send_snapshot(self, conn: SSEConnection, snapshot: SnapshotPayload) -> None:
        """Send a snapshot event to a specific connection."""
        frame = _format_sse(
            "snapshot",
            "snapshot",
            snapshot.model_dump_json(by_alias=True),
        )
        await conn.send(frame)

    async def replay_events(
        self,
        conn: SSEConnection,
        event_repo: EventRepository,
        job_repo: JobRepository,
        last_event_id: int,
    ) -> None:
        """Replay missed events to a reconnecting client.

        If the gap is too large or too old, sends a snapshot first then
        recent events within the replay window.
        """
        cutoff = datetime.now(UTC) - MAX_REPLAY_AGE

        events = await event_repo.list_after(
            after_id=last_event_id,
            job_id=conn.job_id,
            limit=MAX_REPLAY_EVENTS + 1,  # +1 to detect overflow
        )

        needs_snapshot = False
        if len(events) > MAX_REPLAY_EVENTS:
            needs_snapshot = True
            events = events[:MAX_REPLAY_EVENTS]

        # Check if oldest event is beyond replay window
        if events and events[0].timestamp.replace(tzinfo=UTC) < cutoff:
            needs_snapshot = True

        if needs_snapshot:
            # Build and send snapshot
            all_jobs = await job_repo.list(limit=10000)
            from backend.models.api_schemas import JobResponse

            job_responses = [
                JobResponse(
                    id=j.id,
                    repo=j.repo,
                    prompt=j.prompt,
                    state=j.state,
                    strategy=j.strategy,
                    base_ref=j.base_ref,
                    worktree_path=j.worktree_path,
                    branch=j.branch,
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                    completed_at=j.completed_at,
                )
                for j in all_jobs
            ]
            snapshot = SnapshotPayload(jobs=job_responses, pending_approvals=[])
            await self.send_snapshot(conn, snapshot)

            # Filter events to only those within the replay window
            events = [e for e in events if e.timestamp.replace(tzinfo=UTC) >= cutoff]

        # Replay the events
        for event in events:
            sse_type = _SSE_EVENT_TYPE.get(event.kind)
            if sse_type is None:
                continue
            frame = _format_sse(event.event_id, sse_type, _domain_to_sse_data(event))
            await conn.send(frame)

    async def close_all(self) -> None:
        """Close all connections (used during shutdown)."""
        for conn in list(self._connections):
            conn.close()
        self._connections.clear()
