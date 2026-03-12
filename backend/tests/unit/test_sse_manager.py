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
    _domain_to_sse_data,
    _format_sse,
)


def _make_event(
    kind: DomainEventKind = DomainEventKind.job_created,
    job_id: str = "job-1",
    event_id: str = "evt-1",
    payload: dict[str, object] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        job_id=job_id,
        timestamp=datetime.now(UTC),
        kind=kind,
        payload=payload or {"test": True},
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


class TestDomainToSSEData:
    def test_serializes_payload(self) -> None:
        event = _make_event(payload={"key": "value"})
        result = _domain_to_sse_data(event)
        parsed = json.loads(result)
        assert parsed == {"key": "value"}


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

        event = _make_event(kind=DomainEventKind.job_created)
        await mgr.handle_event(event)

        data = conn.queue.get_nowait()
        assert "event: job_state_changed" in data
        assert "id: evt-1" in data

    @pytest.mark.asyncio
    async def test_handle_event_routes_to_scoped_connection(self) -> None:
        mgr = SSEManager()
        conn1 = SSEConnection(job_id="job-1")
        conn2 = SSEConnection(job_id="job-2")
        mgr.register(conn1)
        mgr.register(conn2)

        event = _make_event(kind=DomainEventKind.job_created, job_id="job-1")
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
            ),
            DomainEvent(
                event_id="evt-2",
                job_id="job-1",
                timestamp=now,
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": 1, "message": "hello"},
            ),
        ]

        event_repo = AsyncMock()
        event_repo.list_after.return_value = events

        job_repo = AsyncMock()

        await mgr.replay_events(conn, event_repo, job_repo, last_event_id=0)

        # Should have 2 replayed frames
        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())
        assert len(frames) == 2

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
        ]

        for i, kind in enumerate(mappable_kinds):
            payload: dict[str, object] = {}
            if kind == DomainEventKind.approval_resolved:
                payload = {"resolution": "approved"}
            await mgr.handle_event(_make_event(kind=kind, event_id=f"evt-{i}", payload=payload))

        frames = []
        while not conn.queue.empty():
            frames.append(conn.queue.get_nowait())

        # Each mappable kind produces at least 1 frame. approval_* produce 2 each.
        # 10 kinds, 2 of which produce secondary frames = 12 total
        assert len(frames) == 12
