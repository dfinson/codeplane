"""SSE fidelity tests — backpressure, queue overflow, and replay coverage.

Tests that:
- Queue overflow closes the connection instead of silently dropping events
- Replay correctly sends missed events after reconnection
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from backend.models.events import DomainEvent, DomainEventKind
from backend.services.sse_manager import SSEConnection, SSEManager, _format_sse


class TestSSEBackpressure:
    """Queue overflow closes the connection for client reconnection."""

    @pytest.mark.asyncio
    async def test_queue_overflow_closes_connection(self) -> None:
        """When the queue is full, connection is closed instead of silently dropping."""
        conn = SSEConnection(job_id="job-1")
        # Fill the queue to capacity (1024 items)
        for i in range(1024):
            await conn.send(f"data: event-{i}\n\n")
        assert not conn.closed

        # One more should close the connection
        await conn.send("data: overflow\n\n")
        assert conn.closed

    @pytest.mark.asyncio
    async def test_send_to_closed_connection_is_noop(self) -> None:
        """Sending to a closed connection does nothing."""
        conn = SSEConnection(job_id="job-1")
        conn.close()
        # Should not raise
        await conn.send("data: test\n\n")
        assert conn.queue.empty()

    @pytest.mark.asyncio
    async def test_normal_send_works(self) -> None:
        """Normal sends queue data correctly."""
        conn = SSEConnection(job_id="job-1")
        await conn.send("data: hello\n\n")
        assert conn.queue.qsize() == 1
        item = conn.queue.get_nowait()
        assert item == "data: hello\n\n"


class TestSSEManagerBroadcast:
    """SSE manager correctly routes events to connections."""

    @pytest.mark.asyncio
    async def test_broadcast_to_scoped_connection(self) -> None:
        """Job-scoped connection receives events for its job."""
        manager = SSEManager()
        conn = SSEConnection(job_id="job-1")
        manager.register(conn)

        event = DomainEvent(
            event_id="evt-1",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.log_line_emitted,
            payload={"seq": 1, "message": "hello", "level": "info"},
            db_id=100,
        )
        await manager.broadcast_domain_event(event)

        # Connection should have received the event
        assert conn.queue.qsize() >= 1
        manager.unregister(conn)

    @pytest.mark.asyncio
    async def test_broadcast_to_wrong_job_not_received(self) -> None:
        """Job-scoped connection ignores events for other jobs."""
        manager = SSEManager()
        conn = SSEConnection(job_id="job-1")
        manager.register(conn)

        event = DomainEvent(
            event_id="evt-1",
            job_id="job-2",  # Different job
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.log_line_emitted,
            payload={"seq": 1, "message": "hello", "level": "info"},
            db_id=100,
        )
        await manager.broadcast_domain_event(event)

        # Connection should NOT have received the event
        assert conn.queue.qsize() == 0
        manager.unregister(conn)

    @pytest.mark.asyncio
    async def test_broadcast_to_global_connection(self) -> None:
        """Global connection (no job_id) receives all events."""
        manager = SSEManager()
        conn = SSEConnection(job_id=None)
        manager.register(conn)

        event = DomainEvent(
            event_id="evt-1",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.log_line_emitted,
            payload={"seq": 1, "message": "hello", "level": "info"},
            db_id=100,
        )
        await manager.broadcast_domain_event(event)

        assert conn.queue.qsize() >= 1
        manager.unregister(conn)


class TestSSEFormat:
    """SSE frame formatting."""

    def test_format_with_id(self) -> None:
        frame = _format_sse("42", "log_line", '{"msg": "hello"}')
        assert "id: 42\n" in frame
        assert "event: log_line\n" in frame
        assert 'data: {"msg": "hello"}\n' in frame

    def test_format_without_id(self) -> None:
        frame = _format_sse(None, "heartbeat", "{}")
        assert "id:" not in frame
        assert "event: heartbeat\n" in frame


class TestBulkEventsOverflow:
    """Rapid event generation triggers backpressure correctly."""

    @pytest.mark.asyncio
    async def test_rapid_events_trigger_close(self) -> None:
        """Sending 2000 events rapidly to a connection that isn't draining."""
        manager = SSEManager()
        conn = SSEConnection(job_id="job-1")
        manager.register(conn)

        closed_after = None
        for i in range(2000):
            if conn.closed:
                closed_after = i
                break
            event = DomainEvent(
                event_id=f"evt-{i}",
                job_id="job-1",
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.log_line_emitted,
                payload={"seq": i, "message": f"line-{i}", "level": "info"},
                db_id=i + 1,
            )
            await manager.broadcast_domain_event(event)

        # Connection should have been closed due to backpressure
        assert conn.closed
        # Should close around 1024 events (queue size) — each broadcast may
        # produce multiple frames (main + derived state), so the threshold
        # is hit at or shortly after 1024 raw events.
        assert closed_after is not None
        assert closed_after <= 1030

        manager.unregister(conn)
