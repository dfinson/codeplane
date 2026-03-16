"""Tests for the SSE manager — connection tracking, broadcast, selective streaming, replay."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from backend.models.api_schemas import SnapshotPayload
from backend.models.domain import Job
from backend.models.events import DomainEvent, DomainEventKind
from backend.services.sse_manager import (
    MAX_REPLAY_AGE,
    MAX_REPLAY_EVENTS,
    SSEConnection,
    SSEManager,
    _build_sse_data,
    _format_sse,
)


def _make_event(
    kind: DomainEventKind = DomainEventKind.job_created,
    job_id: str = "job-1",
    event_id: str = "evt-1",
    payload: dict[str, object] | None = None,
    db_id: int | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        job_id=job_id,
        timestamp=datetime.now(UTC),
        kind=kind,
        payload=payload or {"test": True},
        db_id=db_id,
    )


def _make_job_domain(job_id: str = "job-1", state: str = "running") -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        repo="/repos/test",
        prompt="Fix the bug",
        state=state,
        strategy="single_agent",
        base_ref="main",
        branch="fix/bug",
        worktree_path="/repos/test",
        session_id=None,
        created_at=now,
        updated_at=now,
    )


# --- Unit tests for helper functions ---


class TestFormatSSE:
    def test_basic_format(self) -> None:
        result = _format_sse("42", "job_state_changed", '{"hello":"world"}')
        assert result == 'id: 42\nevent: job_state_changed\ndata: {"hello":"world"}\n\n'

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


class TestBuildSSEData:
    def test_serializes_log_line_camel_case(self) -> None:
        event = _make_event(
            kind=DomainEventKind.log_line_emitted,
            payload={"seq": 1, "message": "hello", "level": "info"},
        )
        result = _build_sse_data(event, "log_line")
        parsed = json.loads(result)
        # CamelModel serialization: keys must be camelCase
        assert "jobId" in parsed
        assert parsed["message"] == "hello"

    def test_serializes_job_state_changed(self) -> None:
        event = _make_event(kind=DomainEventKind.job_succeeded)
        result = _build_sse_data(event, "job_state_changed")
        parsed = json.loads(result)
        assert parsed["newState"] == "succeeded"
        assert "jobId" in parsed

    def test_job_created_maps_to_running(self) -> None:
        event = _make_event(kind=DomainEventKind.job_created)
        result = _build_sse_data(event, "job_state_changed")
        parsed = json.loads(result)
        assert parsed["newState"] == "running"


# --- SSEConnection tests ---


class TestSSEConnection:
    @pytest.mark.asyncio
    async def test_send_enqueues_data(self) -> None:
        conn = SSEConnection()
        await conn.send("hello")
        assert not conn.queue.empty()
        assert conn.queue.get_nowait() == "hello"

    @pytest.mark.asyncio
    async def test_send_on_closed_connection_is_noop(self) -> None:
        conn = SSEConnection()
        conn.close()
        await conn.send("hello")
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
    async def test_handle_event_broadcasts_to_global(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(kind=DomainEventKind.job_created, db_id=42)
        await mgr.handle_event(event)

        data = conn.queue.get_nowait()
        assert "event: job_state_changed" in data
        assert "id: 42\n" in data

    @pytest.mark.asyncio
    async def test_handle_event_routes_to_scoped_connection(self) -> None:
        mgr = SSEManager()
        conn1 = SSEConnection(job_id="job-1")
        conn2 = SSEConnection(job_id="job-2")
        mgr.register(conn1)
        mgr.register(conn2)

        event = _make_event(kind=DomainEventKind.job_created, job_id="job-1", db_id=10)
        await mgr.handle_event(event)

        assert not conn1.queue.empty()
        assert conn2.queue.empty()

    @pytest.mark.asyncio
    async def test_handle_internal_event_skipped(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(kind=DomainEventKind.workspace_prepared)
        await mgr.handle_event(event)

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_handle_agent_session_started_skipped(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(kind=DomainEventKind.agent_session_started)
        await mgr.handle_event(event)

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_streaming_suppresses_high_freq(self) -> None:
        """When >20 active jobs, suppress log/transcript/diff/heartbeat from global connections."""
        mgr = SSEManager()
        mgr.set_active_job_count(25)
        conn = SSEConnection()  # global (no job_id)
        mgr.register(conn)

        for kind in [
            DomainEventKind.log_line_emitted,
            DomainEventKind.transcript_updated,
            DomainEventKind.diff_updated,
            DomainEventKind.session_heartbeat,
        ]:
            await mgr.handle_event(_make_event(kind=kind))

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_streaming_allows_state_events(self) -> None:
        """State change events are always delivered even in selective mode."""
        mgr = SSEManager()
        mgr.set_active_job_count(25)
        conn = SSEConnection()
        mgr.register(conn)

        await mgr.handle_event(_make_event(kind=DomainEventKind.job_succeeded))
        assert not conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_not_applied_to_scoped_connections(self) -> None:
        """Scoped connections always get full streaming."""
        mgr = SSEManager()
        mgr.set_active_job_count(25)
        conn = SSEConnection(job_id="job-1")
        mgr.register(conn)

        await mgr.handle_event(_make_event(kind=DomainEventKind.log_line_emitted, job_id="job-1"))
        assert not conn.queue.empty()

    @pytest.mark.asyncio
    async def test_selective_not_applied_under_threshold(self) -> None:
        """When ≤20 active jobs, no suppression."""
        mgr = SSEManager()
        mgr.set_active_job_count(20)
        conn = SSEConnection()
        mgr.register(conn)

        await mgr.handle_event(_make_event(kind=DomainEventKind.log_line_emitted))
        assert not conn.queue.empty()

    @pytest.mark.asyncio
    async def test_approval_requested_emits_secondary_state_event(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(
            kind=DomainEventKind.approval_requested,
            payload={"approval_id": "apr-1", "description": "approve?"},
        )
        await mgr.handle_event(event)

        # Should have 2 frames: approval_requested + job_state_changed
        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert len(frames) == 2
        assert "event: approval_requested" in frames[0]
        assert "event: job_state_changed" in frames[1]
        assert "waiting_for_approval" in frames[1]
        # Secondary frame must not carry an id: line (would break replay)
        assert "id:" not in frames[1]

    @pytest.mark.asyncio
    async def test_approval_resolved_approved_emits_running(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(
            kind=DomainEventKind.approval_resolved,
            payload={"resolution": "approved"},
        )
        await mgr.handle_event(event)

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert len(frames) == 2
        assert "event: approval_resolved" in frames[0]
        assert "event: job_state_changed" in frames[1]
        assert '"running"' in frames[1] or "running" in frames[1]

    @pytest.mark.asyncio
    async def test_approval_resolved_rejected_emits_failed(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(
            kind=DomainEventKind.approval_resolved,
            payload={"resolution": "rejected"},
        )
        await mgr.handle_event(event)

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert len(frames) == 2
        state_data = frames[1]
        assert "failed" in state_data

    @pytest.mark.asyncio
    async def test_closed_connections_skipped(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)
        conn.close()

        await mgr.handle_event(_make_event())
        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_send_snapshot(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        snapshot = SnapshotPayload(jobs=[], pending_approvals=[])
        await mgr.send_snapshot(conn, snapshot)

        data = conn.queue.get_nowait()
        assert "event: snapshot" in data
        # Snapshot frames must NOT have an id: line (avoids advancing cursor)
        assert "id:" not in data

    @pytest.mark.asyncio
    async def test_close_all(self) -> None:
        mgr = SSEManager()
        c1 = SSEConnection()
        c2 = SSEConnection()
        mgr.register(c1)
        mgr.register(c2)

        await mgr.close_all()
        assert mgr.connection_count == 0
        assert c1.closed
        assert c2.closed

    @pytest.mark.asyncio
    async def test_replay_events_simple(self) -> None:
        """Replay events from the repository to a connection."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        # Create mock repos
        now = datetime.now(UTC)
        events = [
            DomainEvent(
                event_id="evt-1",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.job_created,
                payload={"state": "running"},
                db_id=1,
            ),
            DomainEvent(
                event_id="evt-2",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": 1, "message": "hello"},
                db_id=2,
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        # Should have 2 replayed frames with numeric IDs
        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())
        assert len(frames) == 2
        assert "id: 1\n" in frames[0]
        assert "id: 2\n" in frames[1]

    @pytest.mark.asyncio
    async def test_replay_events_sends_snapshot_on_overflow(self) -> None:
        """When more events than MAX_REPLAY_EVENTS, send snapshot first."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        # Create MAX_REPLAY_EVENTS + 1 events to trigger snapshot
        events = [
            DomainEvent(
                event_id=f"evt-{i}",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

        job_repo = AsyncMock()
        job_repo.list.return_value = [_make_job_domain()]

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        # First frame should be a snapshot
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
            DomainEvent(
                event_id="evt-old",
                job_id="job-1",
                timestamp=old_time,
                kind=DomainEventKind.job_created,
                payload={},
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

        job_repo = AsyncMock()
        job_repo.list.return_value = [_make_job_domain()]

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert any("event: snapshot" in f for f in frames)

    @pytest.mark.asyncio
    async def test_replay_skips_internal_events(self) -> None:
        """Internal events (workspace_prepared) should not be replayed."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            DomainEvent(
                event_id="evt-1",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.workspace_prepared,
                payload={},
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events
        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_all_mappable_event_types(self) -> None:
        """Every domain event kind that maps to an SSE type gets delivered."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        mappable_kinds = [
            DomainEventKind.job_created,
            DomainEventKind.log_line_emitted,
            DomainEventKind.transcript_updated,
            DomainEventKind.diff_updated,
            DomainEventKind.approval_requested,
            DomainEventKind.approval_resolved,
            DomainEventKind.job_succeeded,
            DomainEventKind.job_failed,
            DomainEventKind.job_canceled,
            DomainEventKind.session_heartbeat,
            DomainEventKind.job_resolved,
            DomainEventKind.job_archived,
        ]

        for i, kind in enumerate(mappable_kinds):
            payload: dict[str, object] = {}
            if kind == DomainEventKind.approval_resolved:
                payload = {"resolution": "approved"}
            if kind == DomainEventKind.job_failed:
                payload = {"reason": "test error"}
            if kind == DomainEventKind.job_resolved:
                payload = {"resolution": "merged"}
            await mgr.handle_event(_make_event(kind=kind, event_id=f"evt-{i}", payload=payload))

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        # 12 kinds. approval_requested/resolved produce 2 each (secondary job_state_changed).
        # job_succeeded/job_failed produce 2 each (secondary job_state_changed).
        # That's 12 primary + 4 secondary = 16 total.
        assert len(frames) == 16

    @pytest.mark.asyncio
    async def test_approval_resolved_secondary_frame_has_no_id(self) -> None:
        """Secondary job_state_changed from approval_resolved must omit id: line."""
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        event = _make_event(
            kind=DomainEventKind.approval_resolved,
            payload={"resolution": "approved"},
            db_id=99,
        )
        await mgr.handle_event(event)

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert len(frames) == 2
        # Primary frame has the db_id
        assert "id: 99\n" in frames[0]
        # Secondary frame must NOT have an id: line (same as approval_requested)
        assert "id:" not in frames[1]

    @pytest.mark.asyncio
    async def test_replay_scoped_connection_uses_job_repo_get(self) -> None:
        """Job-scoped replay with snapshot uses job_repo.get() not list()."""
        mgr = SSEManager()
        conn = SSEConnection(job_id="job-1")
        mgr.register(conn)

        now = datetime.now(UTC)
        # Create overflow to trigger snapshot
        events = [
            DomainEvent(
                event_id=f"evt-{i}",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

        job_repo = AsyncMock()
        job_repo.get.return_value = _make_job_domain("job-1")

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        # Must use get() for scoped, not list()
        job_repo.get.assert_called_once_with("job-1")
        job_repo.list.assert_not_called()

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        # First frame is a snapshot
        assert "event: snapshot" in frames[0]
        # Snapshot should contain the scoped job
        assert "job-1" in frames[0]

    @pytest.mark.asyncio
    async def test_replay_scoped_connection_missing_job(self) -> None:
        """Job-scoped replay where job no longer exists sends empty snapshot."""
        mgr = SSEManager()
        conn = SSEConnection(job_id="deleted-job")
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            DomainEvent(
                event_id=f"evt-{i}",
                job_id="deleted-job",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

        job_repo = AsyncMock()
        job_repo.get.return_value = None  # job was deleted

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        # Snapshot with empty jobs list
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
            DomainEvent(
                event_id=f"evt-{i}",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": i},
            )
            for i in range(MAX_REPLAY_EVENTS + 1)
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

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

        frames: list[str] = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert "event: snapshot" in frames[0]
        snapshot_data = json.loads(frames[0].split("data: ", 1)[1].split("\n")[0])
        assert len(snapshot_data["pendingApprovals"]) == 1
        assert snapshot_data["pendingApprovals"][0]["id"] == "apr-1"
        assert snapshot_data["pendingApprovals"][0]["description"] == "Delete file?"
        assert snapshot_data["pendingApprovals"][0]["proposedAction"] == "rm file.txt"
        approval_repo.list_pending.assert_called_once_with(job_id="job-1")

    """Test _build_sse_data for every SSE event type."""

    def test_approval_requested_payload(self) -> None:
        event = _make_event(
            kind=DomainEventKind.approval_requested,
            payload={
                "approval_id": "apr-1",
                "description": "Delete file?",
                "proposed_action": "rm file.txt",
            },
        )
        result = _build_sse_data(event, "approval_requested")
        parsed = json.loads(result)
        assert parsed["jobId"] == "job-1"
        assert parsed["approvalId"] == "apr-1"
        assert parsed["description"] == "Delete file?"
        assert parsed["proposedAction"] == "rm file.txt"
        assert "timestamp" in parsed

    def test_approval_requested_missing_fields_use_defaults(self) -> None:
        event = _make_event(
            kind=DomainEventKind.approval_requested,
            payload={},
        )
        result = _build_sse_data(event, "approval_requested")
        parsed = json.loads(result)
        assert parsed["approvalId"] == ""
        assert parsed["description"] == ""
        assert parsed["proposedAction"] is None

    def test_approval_resolved_payload(self) -> None:
        event = _make_event(
            kind=DomainEventKind.approval_resolved,
            payload={
                "approval_id": "apr-1",
                "resolution": "approved",
            },
        )
        result = _build_sse_data(event, "approval_resolved")
        parsed = json.loads(result)
        assert parsed["approvalId"] == "apr-1"
        assert parsed["resolution"] == "approved"
        assert "timestamp" in parsed

    def test_diff_update_payload(self) -> None:
        event = _make_event(
            kind=DomainEventKind.diff_updated,
            payload={"changed_files": []},
        )
        result = _build_sse_data(event, "diff_update")
        parsed = json.loads(result)
        assert parsed["jobId"] == "job-1"
        assert parsed["changedFiles"] == []

    def test_session_heartbeat_payload(self) -> None:
        event = _make_event(
            kind=DomainEventKind.session_heartbeat,
            payload={"session_id": "sess-1"},
        )
        result = _build_sse_data(event, "session_heartbeat")
        parsed = json.loads(result)
        assert parsed["jobId"] == "job-1"
        assert parsed["sessionId"] == "sess-1"
        assert "timestamp" in parsed

    def test_transcript_update_payload(self) -> None:
        event = _make_event(
            kind=DomainEventKind.transcript_updated,
            payload={"seq": 5, "role": "agent", "content": "I found the bug"},
        )
        result = _build_sse_data(event, "transcript_update")
        parsed = json.loads(result)
        assert parsed["jobId"] == "job-1"
        assert parsed["seq"] == 5
        assert parsed["role"] == "agent"
        assert parsed["content"] == "I found the bug"

    def test_fallback_for_unknown_type(self) -> None:
        event = _make_event(payload={"custom": "data"})
        result = _build_sse_data(event, "unknown_type")
        parsed = json.loads(result)
        assert parsed["custom"] == "data"


class TestReplayDerivedFrames:
    """Replay must emit derived job_state_changed frames for approval events."""

    @pytest.mark.asyncio
    async def test_replay_approval_requested_emits_derived_frame(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            DomainEvent(
                event_id="evt-1",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.approval_requested,
                payload={"approval_id": "apr-1", "description": "ok?"},
                db_id=10,
            ),
        ]
        event_repo = AsyncMock()
        event_repo.list_after.return_value = events
        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames: list[str] = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert len(frames) == 2
        assert "event: approval_requested" in frames[0]
        assert "event: job_state_changed" in frames[1]
        assert "waiting_for_approval" in frames[1]
        # Derived frame reuses the same SSE id (no cursor advancement)
        assert "id: 10\n" in frames[1]

    @pytest.mark.asyncio
    async def test_replay_approval_resolved_emits_derived_frame(self) -> None:
        mgr = SSEManager()
        conn = SSEConnection()
        mgr.register(conn)

        now = datetime.now(UTC)
        events = [
            DomainEvent(
                event_id="evt-2",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.approval_resolved,
                payload={"approval_id": "apr-1", "resolution": "rejected"},
                db_id=20,
            ),
        ]
        event_repo = AsyncMock()
        event_repo.list_after.return_value = events
        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        frames: list[str] = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        assert len(frames) == 2
        assert "event: approval_resolved" in frames[0]
        assert "event: job_state_changed" in frames[1]
        # rejected → failed
        assert '"failed"' in frames[1] or "failed" in frames[1]
        assert "id: 20\n" in frames[1]
