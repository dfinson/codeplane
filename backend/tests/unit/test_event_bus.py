"""Tests for the internal event bus (async pub/sub)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from backend.models.events import DomainEvent, DomainEventKind
from backend.services.event_bus import EventBus


def _make_event(kind: DomainEventKind = DomainEventKind.job_created) -> DomainEvent:
    return DomainEvent(
        event_id="evt-1",
        job_id="job-1",
        timestamp=datetime.now(UTC),
        kind=kind,
        payload={"hello": "world"},
    )


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_to_single_subscriber(self) -> None:
        bus = EventBus()
        received: list[DomainEvent] = []

        async def handler(event: DomainEvent) -> None:
            received.append(event)

        bus.subscribe(handler)
        event = _make_event()
        await bus.publish(event)

        assert len(received) == 1
        assert received[0] is event

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self) -> None:
        bus = EventBus()
        received_a: list[DomainEvent] = []
        received_b: list[DomainEvent] = []

        async def handler_a(event: DomainEvent) -> None:
            received_a.append(event)

        async def handler_b(event: DomainEvent) -> None:
            received_b.append(event)

        bus.subscribe(handler_a)
        bus.subscribe(handler_b)
        await bus.publish(_make_event())

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self) -> None:
        bus = EventBus()
        # Should not raise
        await bus.publish(_make_event())

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_handler(self) -> None:
        bus = EventBus()
        received: list[DomainEvent] = []

        async def handler(event: DomainEvent) -> None:
            received.append(event)

        bus.subscribe(handler)
        bus.unsubscribe(handler)
        await bus.publish(_make_event())

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_handler_is_noop(self) -> None:
        bus = EventBus()

        async def handler(event: DomainEvent) -> None:
            pass

        # Should not raise
        bus.unsubscribe(handler)

    @pytest.mark.asyncio
    async def test_subscriber_error_does_not_block_others(self) -> None:
        bus = EventBus()
        received: list[DomainEvent] = []

        async def failing_handler(event: DomainEvent) -> None:
            raise RuntimeError("boom")

        async def good_handler(event: DomainEvent) -> None:
            received.append(event)

        bus.subscribe(failing_handler)
        bus.subscribe(good_handler)
        await bus.publish(_make_event())

        # Good handler still received the event
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_concurrent_delivery(self) -> None:
        """Subscribers run concurrently, not sequentially."""
        bus = EventBus()
        order: list[str] = []

        async def slow_handler(event: DomainEvent) -> None:
            await asyncio.sleep(0.05)
            order.append("slow")

        async def fast_handler(event: DomainEvent) -> None:
            order.append("fast")

        bus.subscribe(slow_handler)
        bus.subscribe(fast_handler)
        await bus.publish(_make_event())

        # fast should finish before slow due to concurrent gather
        assert order == ["fast", "slow"]

    @pytest.mark.asyncio
    async def test_multiple_publishes(self) -> None:
        bus = EventBus()
        count = 0

        async def handler(event: DomainEvent) -> None:
            nonlocal count
            count += 1

        bus.subscribe(handler)
        for _ in range(10):
            await bus.publish(_make_event())

        assert count == 10
