"""Internal event bus — async in-process pub/sub."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from backend.models.events import DomainEvent

log = structlog.get_logger()

# Subscriber signature: async callable accepting a DomainEvent
Subscriber = Callable[[DomainEvent], Coroutine[Any, Any, None]]


class EventBus:
    """In-process async pub/sub for domain events.

    Subscribers are async callables. Publishing fans out to all subscribers
    concurrently via ``asyncio.gather``. Subscriber exceptions are logged
    but do not prevent other subscribers from receiving the event.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, handler: Subscriber) -> None:
        """Register *handler* to receive all published events."""
        self._subscribers.append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        """Remove a previously registered handler (no-op if not found)."""
        with contextlib.suppress(ValueError):
            self._subscribers.remove(handler)

    async def publish(self, event: DomainEvent) -> None:
        """Fan-out *event* to every subscriber concurrently."""
        if not self._subscribers:
            return

        results = await asyncio.gather(
            *(sub(event) for sub in self._subscribers),
            return_exceptions=True,
        )
        for idx, result in enumerate(results):
            if isinstance(result, BaseException):
                log.error(
                    "event_bus_subscriber_error",
                    subscriber=str(self._subscribers[idx]),
                    event_kind=event.kind,
                    error=str(result),
                )
