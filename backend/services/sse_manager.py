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


# ---------------------------------------------------------------------------
# Generic field-map builder
# ---------------------------------------------------------------------------
# Sentinels for timestamp handling in field maps
_TS_FALLBACK = object()  # event.payload.get(key, event.timestamp)
_TS_EVENT = object()  # always event.timestamp

# FieldMap: model kwarg → (payload_key, default)
# When default is _TS_FALLBACK, falls back to event.timestamp if missing.
# When default is _TS_EVENT, always uses event.timestamp (payload_key ignored).
FieldMap = dict[str, tuple[str, object]]


def _build_from_fields(event: DomainEvent, model_cls: type, fields: FieldMap) -> str:
    """Build a Pydantic SSE payload from a declarative field map.

    Every model receives ``job_id=event.job_id`` automatically.
    """
    kwargs: dict[str, object] = {"job_id": event.job_id}
    for kwarg_name, (payload_key, default) in fields.items():
        if default is _TS_FALLBACK:
            kwargs[kwarg_name] = event.payload.get(payload_key, event.timestamp)
        elif default is _TS_EVENT:
            kwargs[kwarg_name] = event.timestamp
        else:
            kwargs[kwarg_name] = event.payload.get(payload_key, default)
    return model_cls(**kwargs).model_dump_json(by_alias=True)


# ---------------------------------------------------------------------------
# Custom builders for event types with non-trivial extraction logic
# ---------------------------------------------------------------------------

from collections.abc import Callable

_BuilderFn = Callable[[DomainEvent], str]


def _build_job_state_changed(event: DomainEvent) -> str:
    new_state = _KIND_TO_STATE.get(event.kind, event.payload.get("state", event.payload.get("new_state", JobState.queued)))
    return JobStateChangedPayload(
        job_id=event.job_id,
        previous_state=event.payload.get("previous_state"),
        new_state=new_state,
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


def _build_progress_headline(event: DomainEvent) -> str:
    return ProgressHeadlinePayload(
        job_id=event.job_id,
        headline=event.payload.get("headline", ""),
        headline_past=event.payload.get("headline_past", event.payload.get("headline", "")),
        summary=event.payload.get("summary", ""),
        timestamp=event.timestamp,
        replaces_count=event.payload.get("replaces_count", 0),
    ).model_dump_json(by_alias=True)


def _build_agent_plan_updated(event: DomainEvent) -> str:
    raw_steps = event.payload.get("steps", [])
    steps = [AgentPlanStep(label=s.get("label", ""), status=s.get("status", "pending")) for s in raw_steps]
    return AgentPlanPayload(
        job_id=event.job_id,
        steps=steps,
        timestamp=event.timestamp,
    ).model_dump_json(by_alias=True)


# ---------------------------------------------------------------------------
# Unified SSE payload registry
# ---------------------------------------------------------------------------
# Each entry is either a (ModelClass, FieldMap) tuple handled generically by
# ``_build_from_fields``, or a custom callable for event types that need
# non-trivial extraction logic.

_SSE_PAYLOAD_REGISTRY: dict[str, tuple[type, FieldMap] | _BuilderFn] = {
    # --- Custom builders (non-trivial extraction) ---
    "job_state_changed": _build_job_state_changed,
    "job_succeeded": _build_job_succeeded,
    "progress_headline": _build_progress_headline,
    "agent_plan_updated": _build_agent_plan_updated,
    # --- Field-map builders (declarative) ---
    "log_line": (LogLinePayload, {
        "seq": ("seq", 0),
        "timestamp": ("timestamp", _TS_FALLBACK),
        "level": ("level", "info"),
        "message": ("message", ""),
        "context": ("context", None),
    }),
    "transcript_update": (TranscriptPayload, {
        "seq": ("seq", 0),
        "timestamp": ("timestamp", _TS_FALLBACK),
        "role": ("role", "agent"),
        "content": ("content", ""),
        "title": ("title", None),
        "turn_id": ("turn_id", None),
        "tool_name": ("tool_name", None),
        "tool_args": ("tool_args", None),
        "tool_result": ("tool_result", None),
        "tool_success": ("tool_success", None),
        "tool_issue": ("tool_issue", None),
        "tool_intent": ("tool_intent", None),
        "tool_title": ("tool_title", None),
        "tool_display": ("tool_display", None),
    }),
    "diff_update": (DiffUpdatePayload, {
        "changed_files": ("changed_files", []),
    }),
    "approval_requested": (ApprovalRequestedPayload, {
        "approval_id": ("approval_id", ""),
        "description": ("description", ""),
        "proposed_action": ("proposed_action", None),
        "timestamp": ("timestamp", _TS_FALLBACK),
    }),
    "approval_resolved": (ApprovalResolvedPayload, {
        "approval_id": ("approval_id", ""),
        "resolution": ("resolution", ""),
        "timestamp": ("timestamp", _TS_FALLBACK),
    }),
    "session_heartbeat": (SessionHeartbeatPayload, {
        "session_id": ("session_id", ""),
        "timestamp": ("timestamp", _TS_FALLBACK),
    }),
    "merge_completed": (MergeCompletedPayload, {
        "branch": ("branch", ""),
        "base_ref": ("base_ref", ""),
        "strategy": ("strategy", ""),
        "timestamp": ("timestamp", _TS_FALLBACK),
    }),
    "merge_conflict": (MergeConflictPayload, {
        "branch": ("branch", ""),
        "base_ref": ("base_ref", ""),
        "conflict_files": ("conflict_files", []),
        "fallback": ("fallback", "none"),
        "pr_url": ("pr_url", None),
        "timestamp": ("timestamp", _TS_FALLBACK),
    }),
    "session_resumed": (SessionResumedPayload, {
        "session_number": ("session_number", 1),
        "timestamp": ("timestamp", _TS_FALLBACK),
    }),
    "job_failed": (JobFailedPayload, {
        "reason": ("reason", "Unknown error"),
        "timestamp": ("timestamp", _TS_EVENT),
    }),
    "job_resolved": (JobResolvedPayload, {
        "resolution": ("resolution", Resolution.unresolved),
        "pr_url": ("pr_url", None),
        "conflict_files": ("conflict_files", None),
        "timestamp": ("timestamp", _TS_EVENT),
    }),
    "job_archived": (JobArchivedPayload, {
        "timestamp": ("timestamp", _TS_EVENT),
    }),
    "job_title_updated": (JobTitleUpdatedPayload, {
        "title": ("title", None),
        "branch": ("branch", None),
        "timestamp": ("timestamp", _TS_EVENT),
    }),
    "model_downgraded": (ModelDowngradedPayload, {
        "requested_model": ("requested_model", ""),
        "actual_model": ("actual_model", ""),
        "timestamp": ("timestamp", _TS_EVENT),
    }),
    "tool_group_summary": (ToolGroupSummaryPayload, {
        "turn_id": ("turn_id", ""),
        "summary": ("summary", ""),
        "timestamp": ("timestamp", _TS_EVENT),
    }),
}


def _build_sse_data(event: DomainEvent, sse_type: str) -> str:
    """Serialize the domain event payload via the appropriate Pydantic SSE model.

    This ensures all SSE payloads use **camelCase** keys matching the API contract.
    """
    spec = _SSE_PAYLOAD_REGISTRY.get(sse_type)
    if spec is None:
        # Fallback (should not happen for known types)
        return json.dumps(event.payload, default=str)
    if callable(spec):
        return spec(event)
    model_cls, fields = spec
    return _build_from_fields(event, model_cls, fields)


def _build_derived_state_frame(event: DomainEvent, sse_id: str | None) -> str | None:
    """Build a derived ``job_state_changed`` SSE frame for events that imply a state transition.

    Returns ``None`` when *event* does not trigger a secondary frame.
    """
    if event.kind == DomainEventKind.approval_requested:
        payload = JobStateChangedPayload(
            job_id=event.job_id,
            previous_state=event.payload.get("previous_state"),
            new_state=JobState.waiting_for_approval,
            timestamp=event.timestamp,
        )
    elif event.kind == DomainEventKind.approval_resolved:
        new_state = JobState.running if event.payload.get("resolution") == "approved" else JobState.failed
        payload = JobStateChangedPayload(
            job_id=event.job_id,
            previous_state=JobState.waiting_for_approval,
            new_state=new_state,
            timestamp=event.timestamp,
        )
    elif event.kind in (DomainEventKind.job_succeeded, DomainEventKind.job_failed):
        payload = JobStateChangedPayload(
            job_id=event.job_id,
            previous_state=None,
            new_state=_KIND_TO_STATE[event.kind],
            timestamp=event.timestamp,
        )
    else:
        return None
    return _format_sse(sse_id, "job_state_changed", payload.model_dump_json(by_alias=True))


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
        derived = _build_derived_state_frame(event, sse_id=None)
        if derived is not None:
            await self._broadcast_frame(derived, event.job_id)

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

            # Mirror broadcast_domain_event(): emit a derived
            # job_state_changed frame so the client sees the state
            # transition on reconnect.  Reuse the same SSE id so the
            # replay cursor does not advance beyond the underlying event.
            derived = _build_derived_state_frame(event, sse_id=sse_id)
            if derived is not None:
                await conn.send(derived)

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
