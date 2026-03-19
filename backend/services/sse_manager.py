"""SSE connection management."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from backend.models.api_schemas import (
    AgentPlanPayload,
    AgentPlanStep,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    ApprovalResponse,
    DiffUpdatePayload,
    JobArchivedPayload,
    JobFailedPayload,
    JobResolvedPayload,
    JobStateChangedPayload,
    JobSucceededPayload,
    JobTitleUpdatedPayload,
    LogLinePayload,
    MergeCompletedPayload,
    MergeConflictPayload,
    ModelDowngradedPayload,
    ProgressHeadlinePayload,
    SessionHeartbeatPayload,
    SessionResumedPayload,
    SnapshotPayload,
    ToolGroupSummaryPayload,
    TranscriptPayload,
)
from backend.models.domain import JobState, Resolution
from backend.models.events import DomainEvent, DomainEventKind

if TYPE_CHECKING:
    from backend.persistence.approval_repo import ApprovalRepository
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
    DomainEventKind.job_succeeded: "job_succeeded",
    DomainEventKind.job_failed: "job_failed",
    DomainEventKind.job_canceled: "job_state_changed",
    DomainEventKind.job_state_changed: "job_state_changed",
    DomainEventKind.session_heartbeat: "session_heartbeat",
    DomainEventKind.merge_completed: "merge_completed",
    DomainEventKind.merge_conflict: "merge_conflict",
    DomainEventKind.session_resumed: "session_resumed",
    DomainEventKind.job_resolved: "job_resolved",
    DomainEventKind.job_archived: "job_archived",
    DomainEventKind.job_title_updated: "job_title_updated",
    DomainEventKind.progress_headline: "progress_headline",
    DomainEventKind.model_downgraded: "model_downgraded",
    DomainEventKind.tool_group_summary: "tool_group_summary",
    DomainEventKind.agent_plan_updated: "agent_plan_updated",
}

# State implied by each domain event kind (for job_state_changed payloads)
_KIND_TO_STATE: dict[DomainEventKind, str] = {
    DomainEventKind.job_created: JobState.running,
    DomainEventKind.job_succeeded: JobState.succeeded,
    DomainEventKind.job_failed: JobState.failed,
    DomainEventKind.job_canceled: JobState.canceled,
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


def _format_sse(event_id: str | None, event_type: str, data: str) -> str:
    """Format a single SSE frame. Omits ``id:`` when *event_id* is ``None``."""
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event_type}")
    parts.append(f"data: {data}")
    return "\n".join(parts) + "\n\n"


def _build_job_state_changed(event: DomainEvent) -> str:
    new_state = _KIND_TO_STATE.get(event.kind, event.payload.get("state", event.payload.get("new_state", JobState.queued)))
    return JobStateChangedPayload(
        job_id=event.job_id,
        previous_state=event.payload.get("previous_state"),
        new_state=new_state,
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_log_line(event: DomainEvent) -> str:
    return LogLinePayload(
        job_id=event.job_id,
        seq=event.payload.get("seq", 0),
        timestamp=event.payload.get("timestamp", event.timestamp),
        level=event.payload.get("level", "info"),
        message=event.payload.get("message", ""),
        context=event.payload.get("context"),
    ).model_dump_json(by_alias=True)


def _build_transcript_update(event: DomainEvent) -> str:
    return TranscriptPayload(
        job_id=event.job_id,
        seq=event.payload.get("seq", 0),
        timestamp=event.payload.get("timestamp", event.timestamp),
        role=event.payload.get("role", "agent"),
        content=event.payload.get("content", ""),
        title=event.payload.get("title"),
        turn_id=event.payload.get("turn_id"),
        tool_name=event.payload.get("tool_name"),
        tool_args=event.payload.get("tool_args"),
        tool_result=event.payload.get("tool_result"),
        tool_success=event.payload.get("tool_success"),
        tool_issue=event.payload.get("tool_issue"),
        tool_intent=event.payload.get("tool_intent"),
        tool_title=event.payload.get("tool_title"),
        tool_display=event.payload.get("tool_display"),
    ).model_dump_json(by_alias=True)


def _build_diff_update(event: DomainEvent) -> str:
    return DiffUpdatePayload(
        job_id=event.job_id,
        changed_files=event.payload.get("changed_files", []),
    ).model_dump_json(by_alias=True)


def _build_approval_requested(event: DomainEvent) -> str:
    return ApprovalRequestedPayload(
        job_id=event.job_id,
        approval_id=event.payload.get("approval_id", ""),
        description=event.payload.get("description", ""),
        proposed_action=event.payload.get("proposed_action"),
        timestamp=event.payload.get("timestamp", event.timestamp),
    ).model_dump_json(by_alias=True)


def _build_approval_resolved(event: DomainEvent) -> str:
    return ApprovalResolvedPayload(
        job_id=event.job_id,
        approval_id=event.payload.get("approval_id", ""),
        resolution=event.payload.get("resolution", ""),
        timestamp=event.payload.get("timestamp", event.timestamp),
    ).model_dump_json(by_alias=True)


def _build_session_heartbeat(event: DomainEvent) -> str:
    return SessionHeartbeatPayload(
        job_id=event.job_id,
        session_id=event.payload.get("session_id", ""),
        timestamp=event.payload.get("timestamp", event.timestamp),
    ).model_dump_json(by_alias=True)


def _build_merge_completed(event: DomainEvent) -> str:
    return MergeCompletedPayload(
        job_id=event.job_id,
        branch=event.payload.get("branch", ""),
        base_ref=event.payload.get("base_ref", ""),
        strategy=event.payload.get("strategy", ""),
        timestamp=event.payload.get("timestamp", event.timestamp),
    ).model_dump_json(by_alias=True)


def _build_merge_conflict(event: DomainEvent) -> str:
    return MergeConflictPayload(
        job_id=event.job_id,
        branch=event.payload.get("branch", ""),
        base_ref=event.payload.get("base_ref", ""),
        conflict_files=event.payload.get("conflict_files", []),
        fallback=event.payload.get("fallback", "none"),
        pr_url=event.payload.get("pr_url"),
        timestamp=event.payload.get("timestamp", event.timestamp),
    ).model_dump_json(by_alias=True)


def _build_session_resumed(event: DomainEvent) -> str:
    return SessionResumedPayload(
        job_id=event.job_id,
        session_number=event.payload.get("session_number", 1),
        timestamp=event.payload.get("timestamp", event.timestamp),
    ).model_dump_json(by_alias=True)


def _build_job_failed(event: DomainEvent) -> str:
    return JobFailedPayload(
        job_id=event.job_id,
        reason=event.payload.get("reason", "Unknown error"),
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_job_succeeded(event: DomainEvent) -> str:
    return JobSucceededPayload(
        job_id=event.job_id,
        pr_url=event.payload.get("pr_url"),
        merge_status=event.payload.get("merge_status"),
        resolution=event.payload.get("resolution"),
        model_downgraded=bool(event.payload.get("model_downgraded", False)),
        requested_model=event.payload.get("requested_model"),
        actual_model=event.payload.get("actual_model"),
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_job_resolved(event: DomainEvent) -> str:
    return JobResolvedPayload(
        job_id=event.job_id,
        resolution=event.payload.get("resolution", Resolution.unresolved),
        pr_url=event.payload.get("pr_url"),
        conflict_files=event.payload.get("conflict_files"),
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_job_archived(event: DomainEvent) -> str:
    return JobArchivedPayload(
        job_id=event.job_id,
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_job_title_updated(event: DomainEvent) -> str:
    return JobTitleUpdatedPayload(
        job_id=event.job_id,
        title=event.payload.get("title"),
        branch=event.payload.get("branch"),
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_progress_headline(event: DomainEvent) -> str:
    return ProgressHeadlinePayload(
        job_id=event.job_id,
        headline=event.payload.get("headline", ""),
        headline_past=event.payload.get("headline_past", event.payload.get("headline", "")),
        summary=event.payload.get("summary", ""),
        timestamp=event.timestamp,
        replaces_count=event.payload.get("replaces_count", 0),
    ).model_dump_json(by_alias=True)


def _build_model_downgraded(event: DomainEvent) -> str:
    return ModelDowngradedPayload(
        job_id=event.job_id,
        requested_model=event.payload.get("requested_model", ""),
        actual_model=event.payload.get("actual_model", ""),
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_tool_group_summary(event: DomainEvent) -> str:
    return ToolGroupSummaryPayload(
        job_id=event.job_id,
        turn_id=event.payload.get("turn_id", ""),
        summary=event.payload.get("summary", ""),
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


def _build_agent_plan_updated(event: DomainEvent) -> str:
    raw_steps = event.payload.get("steps", [])
    steps = [AgentPlanStep(label=s.get("label", ""), status=s.get("status", "pending")) for s in raw_steps]
    return AgentPlanPayload(
        job_id=event.job_id,
        steps=steps,
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


from collections.abc import Callable

_SSE_BUILDERS: dict[str, Callable[[DomainEvent], str]] = {
    "job_state_changed": _build_job_state_changed,
    "log_line": _build_log_line,
    "transcript_update": _build_transcript_update,
    "diff_update": _build_diff_update,
    "approval_requested": _build_approval_requested,
    "approval_resolved": _build_approval_resolved,
    "session_heartbeat": _build_session_heartbeat,
    "merge_completed": _build_merge_completed,
    "merge_conflict": _build_merge_conflict,
    "session_resumed": _build_session_resumed,
    "job_failed": _build_job_failed,
    "job_succeeded": _build_job_succeeded,
    "job_resolved": _build_job_resolved,
    "job_archived": _build_job_archived,
    "job_title_updated": _build_job_title_updated,
    "progress_headline": _build_progress_headline,
    "model_downgraded": _build_model_downgraded,
    "tool_group_summary": _build_tool_group_summary,
    "agent_plan_updated": _build_agent_plan_updated,
}


def _build_sse_data(event: DomainEvent, sse_type: str) -> str:
    """Serialize the domain event payload via the appropriate Pydantic SSE model.

    This ensures all SSE payloads use **camelCase** keys matching the API contract.
    """
    builder = _SSE_BUILDERS.get(sse_type)
    if builder is not None:
        return builder(event)
    # Fallback (should not happen for known types)
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

    async def broadcast_domain_event(self, event: DomainEvent) -> None:
        """Event bus subscriber — translate and broadcast a domain event."""
        sse_type = _SSE_EVENT_TYPE.get(event.kind)
        if sse_type is None:
            return  # internal-only event

        sse_id = str(event.db_id) if event.db_id is not None else event.event_id
        frame = _format_sse(sse_id, sse_type, _build_sse_data(event, sse_type))
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
                new_state=JobState.waiting_for_approval,
                timestamp=event.timestamp,
            )
            state_frame = _format_sse(
                None,
                "job_state_changed",
                state_payload.model_dump_json(by_alias=True),
            )
            await self._broadcast_frame(state_frame, event.job_id)

        elif event.kind == DomainEventKind.approval_resolved:
            new_state = JobState.running if event.payload.get("resolution") == "approved" else JobState.failed
            state_payload = JobStateChangedPayload(
                job_id=event.job_id,
                previous_state=JobState.waiting_for_approval,
                new_state=new_state,
                timestamp=event.timestamp,
            )
            state_frame = _format_sse(
                None,
                "job_state_changed",
                state_payload.model_dump_json(by_alias=True),
            )
            await self._broadcast_frame(state_frame, event.job_id)

        elif event.kind in (DomainEventKind.job_succeeded, DomainEventKind.job_failed):
            new_state = _KIND_TO_STATE[event.kind]
            state_payload = JobStateChangedPayload(
                job_id=event.job_id,
                previous_state=None,
                new_state=new_state,
                timestamp=event.timestamp,
            )
            state_frame = _format_sse(
                None,
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
        await conn.send(frame)

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
            # Build and send snapshot (scoped to conn.job_id if set)
            from backend.models.api_schemas import JobResponse

            if conn.job_id is not None:
                single = await job_repo.get(conn.job_id)
                fetched_jobs = [single] if single else []
            else:
                fetched_jobs = [j for j in await job_repo.list(limit=10000) if j.archived_at is None]

            job_responses = [
                JobResponse(
                    id=j.id,
                    repo=j.repo,
                    prompt=j.prompt,
                    title=j.title,
                    state=j.state,
                    base_ref=j.base_ref,
                    worktree_path=j.worktree_path,
                    branch=j.branch,
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                    completed_at=j.completed_at,
                    pr_url=j.pr_url,
                    merge_status=j.merge_status,
                    resolution=j.resolution,
                    archived_at=j.archived_at,
                    failure_reason=j.failure_reason,
                    model=j.model,
                )
                for j in fetched_jobs
            ]
            snapshot = SnapshotPayload(
                jobs=job_responses,
                pending_approvals=await self._fetch_pending_approvals(approval_repo, conn.job_id),
            )
            await self.send_snapshot(conn, snapshot)

            # Filter events to only those within the replay window
            events = [e for e in events if e.timestamp.replace(tzinfo=UTC) >= cutoff]

        # Replay the events
        for event in events:
            sse_type = _SSE_EVENT_TYPE.get(event.kind)
            if sse_type is None:
                continue
            sse_id = str(event.db_id) if event.db_id is not None else event.event_id
            frame = _format_sse(sse_id, sse_type, _build_sse_data(event, sse_type))
            await conn.send(frame)

            # Mirror broadcast_domain_event(): approval events emit a derived
            # job_state_changed frame so the client sees the state
            # transition on reconnect.  Reuse the same SSE id so the
            # replay cursor does not advance beyond the underlying event.
            if event.kind == DomainEventKind.approval_requested:
                derived_payload = JobStateChangedPayload(
                    job_id=event.job_id,
                    previous_state=event.payload.get("previous_state"),
                    new_state=JobState.waiting_for_approval,
                    timestamp=event.timestamp,
                )
                await conn.send(
                    _format_sse(
                        sse_id,
                        "job_state_changed",
                        derived_payload.model_dump_json(by_alias=True),
                    )
                )
            elif event.kind == DomainEventKind.approval_resolved:
                new_state = JobState.running if event.payload.get("resolution") == "approved" else JobState.failed
                derived_payload = JobStateChangedPayload(
                    job_id=event.job_id,
                    previous_state=JobState.waiting_for_approval,
                    new_state=new_state,
                    timestamp=event.timestamp,
                )
                await conn.send(
                    _format_sse(
                        sse_id,
                        "job_state_changed",
                        derived_payload.model_dump_json(by_alias=True),
                    )
                )
            elif event.kind in (DomainEventKind.job_succeeded, DomainEventKind.job_failed):
                derived_state = _KIND_TO_STATE[event.kind]
                derived_payload = JobStateChangedPayload(
                    job_id=event.job_id,
                    previous_state=None,
                    new_state=derived_state,
                    timestamp=event.timestamp,
                )
                await conn.send(
                    _format_sse(
                        sse_id,
                        "job_state_changed",
                        derived_payload.model_dump_json(by_alias=True),
                    )
                )

    async def replay_from_factory(
        self,
        conn: SSEConnection,
        session_factory: object,
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

        async with session_factory() as session:  # type: ignore[operator]
            event_repo = EventRepository(session)
            job_repo = JobRepository(session)
            approval_repo = ApprovalRepository(session)
            await self.replay_events(
                conn,
                event_repo,
                job_repo,
                last_event_id,
                approval_repo=approval_repo,
            )

    async def close_all(self) -> None:
        """Close all connections (used during shutdown)."""
        for conn in list(self._connections):
            conn.close()
        self._connections.clear()
