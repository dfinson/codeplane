"""Tests for the SSE manager — connection tracking, broadcast, selective streaming, replay.

Post-nuke contract: the SSE wire carries the ``traceforge.SessionEvent`` as-is —
``event:`` is the dotted ``kind`` and ``data:`` is the TF event serialized verbatim
(snake_case ``session_id`` / ``payload`` / ``metadata``). There is no translation to any
legacy payload model, and no derived ``job_state_changed`` frames (the frontend derives
job state from the dotted kinds).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from backend.models.api_schemas import SnapshotPayload
from backend.models.domain import Job
from backend.models.events import EventKind, SessionEvent, new_event
from backend.persistence.event_repo import StoredEvent
from backend.services.events.sse_manager import (
    MAX_REPLAY_AGE,
    MAX_REPLAY_EVENTS,
    SSEConnection,
    SSEManager,
    _format_sse,
    _serialize_tf_event,
)


def _make_event(
    kind: EventKind = EventKind.job_created,
    job_id: str = "job-1",
    event_id: str = "evt-1",
    payload: dict[str, object] | None = None,
    sequence: int | None = None,
) -> SessionEvent:
    return new_event(
        event_id=event_id,
        session_id=job_id,
        timestamp=datetime.now(UTC),
        kind=kind,
        payload=payload or {"test": True},
        sequence=sequence,
    )


def _make_job_domain(job_id: str = "job-1", state: str = "running") -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        project_id="proj-1",
        repo="/repos/test",
        prompt="Fix the bug",
        state=state,
        base_ref="main",
        branch="fix/bug",
        worktree_path="/repos/test",
        session_id=None,
        created_at=now,
        updated_at=now,
    )


def _frames(conn: SSEConnection) -> list[str]:
    out: list[str] = []
    while not conn.queue.empty():
        out.append(conn.queue.get_nowait())
    return out


def _stored(events: list[SessionEvent]) -> list[StoredEvent]:
    return [StoredEvent(storage_cursor=index, event=event) for index, event in enumerate(events, start=1)]


@pytest.mark.parametrize("active_jobs", [0, 21])
async def test_tracker_operation_update_routes_global_matching_job_and_orphans(active_jobs: int) -> None:
    manager = SSEManager()
    manager.set_active_job_count(active_jobs)
    global_connection = SSEConnection()
    matching = SSEConnection(job_id="job-1")
    unrelated = SSEConnection(job_id="job-other")
    for connection in (global_connection, matching, unrelated):
        manager.register(connection)
    payload = {"operation": {"id": "operation", "version": 4, "state": "uncertain", "jobRef": "job-1"}}
    await manager.broadcast_domain_event(
        new_event(session_id="job-1", kind="tracker.operation_updated", payload=payload),
        storage_cursor=23,
    )
    global_frames, matching_frames = _frames(global_connection), _frames(matching)
    assert len(global_frames) == len(matching_frames) == 1
    assert global_frames == matching_frames
    assert "event: tracker.operation_updated\n" in global_frames[0]
    assert "id: 23\n" in global_frames[0]
    body = json.loads(next(line[6:] for line in global_frames[0].splitlines() if line.startswith("data: ")))
    assert body["payload"] == payload
    assert _frames(unrelated) == []
    await manager.broadcast_domain_event(
        new_event(session_id="", kind="tracker.operation_updated", payload=payload),
    )
    orphan_frames = _frames(global_connection)
    assert len(orphan_frames) == 1
    assert not orphan_frames[0].startswith("id:")
    assert _frames(matching) == _frames(unrelated) == []


# --- Unit tests for helper functions ---


class TestFormatSSE:
    def test_basic_format(self) -> None:
        result = _format_sse("42", "job.created", '{"hello":"world"}')
        assert result == 'id: 42\nevent: job.created\ndata: {"hello":"world"}\n\n'

    def test_json_data(self) -> None:
        data = json.dumps({"job_id": "job-1", "state": "running"})
        result = _format_sse("1", "test", data)
        assert "id: 1\n" in result
        assert "event: test\n" in result
        assert f"data: {data}\n" in result

    def test_none_id_omits_id_line(self) -> None:
        result = _format_sse(None, "snapshot", '{"jobs":[]}')
        assert "id:" not in result
        assert "event: snapshot\n" in result
        assert 'data: {"jobs":[]}\n' in result


class TestSerializeTFEvent:
    def test_serializes_dotted_kind_and_payload(self) -> None:
        event = _make_event(
            kind=EventKind.log_line_emitted,
            payload={"seq": 1, "message": "hello", "level": "info"},
        )
        parsed = json.loads(_serialize_tf_event(event))
        # Wire carries the TF event as-is: dotted kind + verbatim snake_case payload.
        assert parsed["kind"] == "log"
        assert parsed["session_id"] == "job-1"
        assert parsed["payload"]["message"] == "hello"
        assert parsed["payload"]["level"] == "info"

    def test_carries_metadata_sequence(self) -> None:
        event = _make_event(kind=EventKind.job_created, sequence=7)
        parsed = json.loads(_serialize_tf_event(event))
        assert parsed["metadata"]["sequence"] == 7

    def test_transcript_payload_is_verbatim(self) -> None:
        event = _make_event(
            kind=EventKind.tool_call_completed,
            payload={
                "seq": 3,
                "role": "tool_call",
                "content": "replace_string_in_file",
                "tool_name": "replace_string_in_file",
                "tool_success": False,
                "tool_issue": "oldString not found",
            },
        )
        parsed = json.loads(_serialize_tf_event(event))
        assert parsed["kind"] == "tool.call.completed"
        # No camelCase remapping — payload keys are exactly as authored.
        assert parsed["payload"]["tool_success"] is False
        assert parsed["payload"]["tool_issue"] == "oldString not found"
        assert parsed["payload"]["role"] == "tool_call"


# --- SSEConnection tests ---


class TestSSEConnection:
    def test_send_enqueues_data(self) -> None:
        conn = SSEConnection()
        conn.send("hello")
        assert not conn.queue.empty()
        assert conn.queue.get_nowait() == "hello"

    def test_send_on_closed_connection_is_noop(self) -> None:
        conn = SSEConnection()
        conn.close()
        conn.send("hello")
        assert conn.queue.empty()

    def test_job_id_scoping(self) -> None:
        conn = SSEConnection(job_id="job-1")
        assert conn.job_id == "job-1"

    def test_default_no_job_scope(self) -> None:
        conn = SSEConnection()
        assert conn.job_id is None

    def test_close_sets_flag(self) -> None:
        conn = SSEConnection()
        assert not conn.closed
        conn.close()
        assert conn.closed


# --- SSEManager tests ---


class TestSSEManager:
    def test_register_and_unregister(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)
        assert mgr.connection_count == 1

        mgr.unregister(conn)
        assert mgr.connection_count == 0
        assert conn.closed

    def test_unregister_unknown_is_noop(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.unregister(conn)  # should not raise
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_domain_event_broadcasts_to_global(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(kind=EventKind.job_created, sequence=9001)
        await mgr.broadcast_domain_event(event, storage_cursor=42)

        data = conn.queue.get_nowait()
        # The SSE replay cursor is storage-local; canonical sequence is unchanged.
        assert "event: job.created" in data
        assert "id: 42\n" in data
        assert json.loads(data.split("data: ", maxsplit=1)[1])["metadata"]["sequence"] == 9001

    @pytest.mark.asyncio
    async def test_broadcast_emits_single_frame(self) -> None:
        """No derived secondary frames — exactly one frame per broadcast event."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(
            kind=EventKind.approval_requested,
            payload={"approval_id": "apr-1", "description": "approve?"},
            sequence=9001,
        )
        await mgr.broadcast_domain_event(event, storage_cursor=5)

        frames = _frames(conn)
        assert len(frames) == 1
        assert "event: permission.requested" in frames[0]
        assert "id: 5\n" in frames[0]

    @pytest.mark.asyncio
    async def test_broadcast_domain_event_routes_to_scoped_connection(self) -> None:
        mgr = SSEManager()
        conn1 = SSEConnection(job_id="job-1")
        conn2 = SSEConnection(job_id="job-2")
        mgr.register(conn1)
        mgr.register(conn2)

        event = _make_event(kind=EventKind.job_created, job_id="job-1", sequence=9001)
        await mgr.broadcast_domain_event(event, storage_cursor=10)

        assert not conn1.queue.empty()
        assert conn2.queue.empty()

    @pytest.mark.asyncio
    async def test_handle_internal_event_skipped(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(kind=EventKind.workspace_prepared)
        await mgr.broadcast_domain_event(event)

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_handle_agent_session_started_skipped(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(kind=EventKind.agent_session_started)
        await mgr.broadcast_domain_event(event)

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_step_events_are_internal(self) -> None:
        """Step-system kinds stay internal — never broadcast to the frontend."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        for kind in (EventKind.step_started, EventKind.step_completed, EventKind.agent_plan_updated):
            await mgr.broadcast_domain_event(_make_event(kind=kind))

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_streaming_suppresses_high_freq(self) -> None:
        """When >20 active jobs, suppress log/transcript/diff/heartbeat from global connections."""
        mgr = SSEManager()
        mgr.set_active_job_count(25)
        conn = SSEConnection()  # global (no job_id)
        mgr.register(conn)

        for kind in [
            EventKind.log_line_emitted,
            EventKind.message_assistant,
            EventKind.tool_call_completed,
            EventKind.diff_updated,
            EventKind.session_heartbeat,
        ]:
            await mgr.broadcast_domain_event(_make_event(kind=kind))

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_streaming_allows_state_events(self) -> None:
        """State change events are always delivered even in selective mode."""
        mgr = SSEManager()
        mgr.set_active_job_count(25)
        conn = SSEConnection()
        mgr.register(conn)

        await mgr.broadcast_domain_event(_make_event(kind=EventKind.job_review))
        assert not conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_not_applied_to_scoped_connections(self) -> None:
        """Scoped connections always get full streaming."""
        mgr = SSEManager()
        mgr.set_active_job_count(25)
        conn = SSEConnection(job_id="job-1")
        mgr.register(conn)

        await mgr.broadcast_domain_event(_make_event(kind=EventKind.log_line_emitted, job_id="job-1"))
        assert not conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_not_applied_under_threshold(self) -> None:
        """When ≤20 active jobs, no suppression."""
        mgr = SSEManager()
        mgr.set_active_job_count(20)
        conn = SSEConnection()
        mgr.register(conn)

        await mgr.broadcast_domain_event(_make_event(kind=EventKind.log_line_emitted))
        assert not conn.queue.empty()

    @pytest.mark.asyncio
    async def test_job_scoped_only_kinds_skip_global(self) -> None:
        """telemetry.updated / secondary_session.entry only reach job-scoped connections."""
        mgr = SSEManager()
        global_conn = SSEConnection()
        scoped_conn = SSEConnection(job_id="job-1")
        mgr.register(global_conn)
        mgr.register(scoped_conn)

        await mgr.broadcast_domain_event(_make_event(kind=EventKind.telemetry_updated, job_id="job-1"))

        assert global_conn.queue.empty()
        assert not scoped_conn.queue.empty()

    @pytest.mark.asyncio
    async def test_closed_connections_skipped(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)
        conn.close()

        await mgr.broadcast_domain_event(_make_event())
        assert conn.queue.empty()

    def test_send_snapshot(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        snapshot = SnapshotPayload(jobs=[], pending_approvals=[])
        mgr.send_snapshot(conn, snapshot)

        data = conn.queue.get_nowait()
        assert "event: snapshot" in data
        # Snapshot frames must NOT have an id: line (avoids advancing cursor)
        assert "id:" not in data

    def test_close_all(self) -> None:
        mgr = SSEManager()
        c1 = SSEConnection()
        c2 = SSEConnection()
        mgr.register(c1)
        mgr.register(c2)

        mgr.close_all()
        assert mgr.connection_count == 0
        assert c1.closed
        assert c2.closed

    @pytest.mark.asyncio
    async def test_replay_events_simple(self) -> None:
        """Replay events from the repository to a connection."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            new_event(
                event_id="evt-1",
                session_id="job-1",
                timestamp=now,
                kind=EventKind.job_created,
                payload={"state": "running"},
                sequence=9001,
            ),
            new_event(
                event_id="evt-2",
                session_id="job-1",
                timestamp=now,
                kind=EventKind.log_line_emitted,
                payload={"seq": 1, "message": "hello"},
                sequence=9002,
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)

        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        # Should have 2 replayed frames with numeric IDs and dotted event types.
        frames = _frames(conn)
        assert len(frames) == 2
        assert "id: 1\n" in frames[0]
        assert "event: job.created" in frames[0]
        assert '"sequence":9001' in frames[0]
        assert "id: 2\n" in frames[1]
        assert "event: log" in frames[1]
        assert '"sequence":9002' in frames[1]

    @pytest.mark.asyncio
    async def test_replay_events_sends_snapshot_on_overflow(self) -> None:
        """When more events than MAX_REPLAY_EVENTS, send snapshot first."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            new_event(
                event_id=f"evt-{i}",
                session_id="job-1",
                timestamp=now,
                kind=EventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)
        event_repo.list_latest_progress_previews.return_value = {}

        job_repo = AsyncMock()
        job_repo.list.return_value = [_make_job_domain()]

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames = _frames(conn)
        assert len(frames) > 0
        assert "event: snapshot" in frames[0]

    @pytest.mark.asyncio
    async def test_replay_events_sends_snapshot_on_old_events(self) -> None:
        """When oldest event is beyond replay window, send snapshot."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        old_time = datetime.now(UTC) - MAX_REPLAY_AGE - timedelta(minutes=1)
        events = [
            new_event(
                event_id="evt-old", session_id="job-1", timestamp=old_time, kind=EventKind.job_created, payload={}
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)
        event_repo.list_latest_progress_previews.return_value = {}

        job_repo = AsyncMock()
        job_repo.list.return_value = [_make_job_domain()]

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames = _frames(conn)
        assert any("event: snapshot" in f for f in frames)

    @pytest.mark.asyncio
    async def test_replay_skips_internal_events(self) -> None:
        """Internal events (workspace_prepared) should not be replayed."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            new_event(
                event_id="evt-1", session_id="job-1", timestamp=now, kind=EventKind.workspace_prepared, payload={}
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)
        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_all_broadcast_kinds_deliver_one_frame_each(self) -> None:
        """Every allowlisted kind is delivered as exactly one frame (no secondaries)."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        kinds = [
            EventKind.job_created,
            EventKind.log_line_emitted,
            EventKind.message_assistant,
            EventKind.diff_updated,
            EventKind.approval_requested,
            EventKind.approval_resolved,
            EventKind.job_review,
            EventKind.job_completed,
            EventKind.job_failed,
            EventKind.job_canceled,
            EventKind.session_heartbeat,
            EventKind.job_resolved,
            EventKind.job_archived,
        ]

        for i, kind in enumerate(kinds):
            await mgr.broadcast_domain_event(_make_event(kind=kind, event_id=f"evt-{i}"))

        frames = _frames(conn)
        assert len(frames) == len(kinds)

    @pytest.mark.asyncio
    async def test_replay_scoped_connection_uses_job_repo_get(self) -> None:
        """Job-scoped replay with snapshot uses job_repo.get() not list()."""
        mgr = SSEManager()
        conn = SSEConnection(job_id="job-1")
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            new_event(
                event_id=f"evt-{i}",
                session_id="job-1",
                timestamp=now,
                kind=EventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)
        event_repo.list_latest_progress_previews.return_value = {}

        job_repo = AsyncMock()
        job_repo.get.return_value = _make_job_domain("job-1")

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        # Must use get() for scoped, not list()
        job_repo.get.assert_called_once_with("job-1")
        job_repo.list.assert_not_called()

        frames = _frames(conn)
        assert "event: snapshot" in frames[0]
        assert "job-1" in frames[0]

    @pytest.mark.asyncio
    async def test_replay_scoped_connection_missing_job(self) -> None:
        """Job-scoped replay where job no longer exists sends empty snapshot."""
        mgr = SSEManager()
        conn = SSEConnection(job_id="deleted-job")
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            new_event(
                event_id=f"evt-{i}",
                session_id="deleted-job",
                timestamp=now,
                kind=EventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)
        event_repo.list_latest_progress_previews.return_value = {}

        job_repo = AsyncMock()
        job_repo.get.return_value = None  # job was deleted

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames = _frames(conn)
        assert "event: snapshot" in frames[0]
        assert '"jobs": []' in frames[0] or '"jobs":[]' in frames[0]

    @pytest.mark.asyncio
    async def test_replay_snapshot_includes_pending_approvals(self) -> None:
        """Snapshot sent to a reconnecting client includes pending approvals."""
        from backend.models.domain import Approval

        mgr = SSEManager()
        conn = SSEConnection(job_id="job-1")
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            new_event(
                event_id=f"evt-{i}",
                session_id="job-1",
                timestamp=now,
                kind=EventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = _stored(events)
        event_repo.list_latest_progress_previews.return_value = {}

        job_repo = AsyncMock()
        job_repo.get.return_value = _make_job_domain("job-1")

        approval_repo = AsyncMock()
        approval_repo.list_pending.return_value = [
            Approval(
                id="apr-1",
                job_id="job-1",
                description="Delete file?",
                proposed_action="rm file.txt",
                requested_at=now,
            ),
        ]

        await mgr.replay_events(
            conn,
            event_repo,
            job_repo,
            last_event_id=0,
            approval_repo=approval_repo,
        )

        frames = _frames(conn)
        assert "event: snapshot" in frames[0]
        snapshot_data = json.loads(frames[0].split("data: ", 1)[1].split("\n")[0])
        assert len(snapshot_data["pendingApprovals"]) == 1
        assert snapshot_data["pendingApprovals"][0]["id"] == "apr-1"
        assert snapshot_data["pendingApprovals"][0]["description"] == "Delete file?"
        assert snapshot_data["pendingApprovals"][0]["proposedAction"] == "rm file.txt"
        approval_repo.list_pending.assert_called_once_with(job_id="job-1")
