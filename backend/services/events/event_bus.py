"""Internal event bus — async in-process pub/sub."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from backend.models.session_event import SessionEvent

log = structlog.get_logger()

# Subscriber signature: async callable accepting a traceforge SessionEvent
Subscriber = Callable[[SessionEvent], Coroutine[Any, Any, None]]


class EventBus:
    """In-process async pub/sub for canonical ``traceforge.SessionEvent``s.

    Subscribers are async callables. Publishing fans out to all subscribers
    concurrently via ``asyncio.gather``. Subscriber exceptions are logged
    but do not prevent other subscribers from receiving the event.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._required: list[Subscriber] = []
        self._muted = False

    def mute(self) -> None:
        """Stop dispatching events (used during shutdown)."""
        self._muted = True

    def subscribe(self, handler: Subscriber, *, required: bool = False) -> None:
        (self._required if required else self._subscribers).append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        """Remove a previously registered handler (no-op if not found)."""
        with contextlib.suppress(ValueError):
            self._subscribers.remove(handler)
        with contextlib.suppress(ValueError):
            self._required.remove(handler)

    async def publish(self, event: SessionEvent) -> None:
        """Fan-out *event* to every subscriber concurrently."""
        if self._muted:
            return
        for subscriber in tuple(self._required):
            await subscriber(event)
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
                    exc_info=result,
                )
