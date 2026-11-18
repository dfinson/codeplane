"""Runtime-owned observation admission and subscription-scoped callback drain."""

from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.domain import StateConflictError
from backend.services.adapters.integration_policy import IntegrationDisabledError

from .lifecycle import retain_task

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from concurrent.futures import Future

    from backend.models.session_event import SessionEvent
    from backend.services.adapters.contracts import ExecutionIdentity
    from backend.services.runtime.native_execution import NativeExecution, NativeExecutions

log = structlog.get_logger()
native_execution_context: ContextVar[ExecutionIdentity | None] = ContextVar("native_execution_owner", default=None)


class NativeObservationOwner:
    """Issued by Runtime for one retained execution and exact native source."""

    def __init__(
        self,
        *,
        registry: NativeExecutions,
        execution: NativeExecution,
        source_id: str,
        append: Callable[[Mapping[str, Any]], Awaitable[tuple[SessionEvent, ...]]],
        finish: Callable[[], Awaitable[None]],
    ) -> None:
        from backend.services.runtime.native_execution import NativeExecutions

        if not isinstance(registry, NativeExecutions) or not registry.retains(execution):
            raise StateConflictError("Native observation requires a Runtime-issued retained owner")
        self.execution = execution.identity
        self.family = execution.gates.binding.family
        self.source_id = source_id
        self.loop = execution.gates.binding.loop
        self._registry = registry
        self._execution = execution
        self._append = append
        self._finish = finish
        self._subscription: ObservationSubscription | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def _enabled(self) -> bool:
        return not self._registry.host._config.runtime.copilot_only

    def _retained(self) -> bool:
        return self._registry.retains(self._execution) and self._execution.identity == self.execution

    async def assert_retained(self) -> None:
        if not self._retained():
            raise StateConflictError("Native observation owner has been released or replaced")

    async def require_registration(self, family: str) -> None:
        if not self._enabled():
            await self.close()
            raise IntegrationDisabledError("Native observation is disabled by the application profile")
        await self.assert_retained()
        if self._closed or self.family != family or not await self._execution.gates.binding.current():
            raise StateConflictError("Native observation requires the current admitted execution/source")

    def admits_callback(self) -> bool:
        return (
            not self._closed
            and self._enabled()
            and self._retained()
            and native_execution_context.get() == self.execution
        )

    def attach(self, subscription: ObservationSubscription) -> None:
        if self._closed or self._subscription is not None:
            raise StateConflictError("Native source already has a subscription or has been retired")
        self._subscription = subscription

    async def append(self, raw: Mapping[str, Any]) -> tuple[SessionEvent, ...]:
        await self.assert_retained()
        return await self._append(raw)

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True

            async def close() -> None:
                try:
                    if self._subscription is not None:
                        await self._subscription.detach()
                finally:
                    await self._finish()

            self._close_task = asyncio.create_task(close(), name="native-observation-source-close")
        await retain_task(self._close_task)


class ObservationSubscription:
    blocking = False

    def __init__(
        self,
        owner: NativeObservationOwner,
        export: Callable[[SessionEvent], None] | None = None,
    ) -> None:
        self.owner = owner
        self._export = export
        self._closed = False
        self._remove: Callable[[], None] = lambda: None
        self._pending = 0
        self._lock = threading.Lock()
        self._changed = asyncio.Event()
        self._serial = asyncio.Lock()
        self._errors: list[Exception] = []
        self._detach_task: asyncio.Task[None] | None = None
        owner.attach(self)

    @property
    def active(self) -> bool:
        if not self.owner._enabled():
            self.request_detach()
            return False
        with self._lock:
            active = not self._closed
        return active and self.owner._retained()

    def request_detach(self) -> None:
        with self._lock:
            self._closed = True
        self._remove()

    def capture(self, record: Callable[[], Mapping[str, Any] | None]) -> None:
        if not self.owner._enabled():
            self.request_detach()
            return
        with self._lock:
            if self._closed or not self.owner.admits_callback():
                return
            self._pending += 1
        try:
            captured = record()
            if captured is None:
                self._finish_one(None)
                return
            raw = deepcopy(dict(captured))
            future = asyncio.run_coroutine_threadsafe(self._accept(raw), self.owner.loop)
            future.add_done_callback(self._completed)
        except BaseException as exc:
            self._finish_one(exc)
            raise

    async def _accept(self, raw: Mapping[str, Any]) -> None:
        async with self._serial:
            events = await self.owner.append(raw)
            if self._export is not None and self.owner._enabled():
                for event in events:
                    self._export(event)

    def _completed(self, future: Future[None]) -> None:
        error = (
            RuntimeError("Native observation acceptance was cancelled") if future.cancelled() else future.exception()
        )
        self._finish_one(error)

    def _finish_one(self, error: BaseException | None) -> None:
        with self._lock:
            if error is not None:
                retained = error if isinstance(error, Exception) else RuntimeError(type(error).__name__)
                self._errors.append(retained)
                log.error("native_observation_acceptance_failed", error_type=type(error).__name__)
            self._pending -= 1
        self.owner.loop.call_soon_threadsafe(self._changed.set)

    async def detach(self) -> None:
        self.request_detach()
        if self._detach_task is None:
            self._detach_task = asyncio.create_task(self._drain(), name="native-observation-callback-drain")
        await retain_task(self._detach_task)

    async def _drain(self) -> None:
        while True:
            self._changed.clear()
            with self._lock:
                pending = self._pending
                errors = tuple(self._errors)
            if not pending:
                if errors:
                    raise ExceptionGroup("Native observation acceptance failed", list(errors))
                return
            await self._changed.wait()
