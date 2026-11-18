"""Per-binding operator input and retained stop authority, distinct from tool grants."""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal

from backend.models.api_schemas import SendMessageRequest
from backend.models.domain import StateConflictError

from .lifecycle import retain_task
from .observation_scope import native_execution_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from backend.services.adapters.contracts import ExecutionIdentity

    from .binding import NativeBinding


@dataclass(frozen=True)
class NativeInput:
    id: str
    execution: ExecutionIdentity
    content: str


@dataclass(frozen=True, eq=False)
class ControlPermit:
    operation_id: str
    execution: ExecutionIdentity


@dataclass(frozen=True)
class StopEvidence:
    status: Literal["acknowledged", "cancel-requested", "delivery-unknown"]
    native_ids: tuple[dict[str, str], ...] = ()
    source: str = ""
    local_error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"acknowledged", "cancel-requested", "delivery-unknown"}:
            raise ValueError("Invalid native stop evidence status")
        if any(not isinstance(value, str) or not value for item in self.native_ids for value in item.values()):
            raise ValueError("Native stop identities must be actual nonempty strings")


class NativeControls:
    def __init__(self, binding: NativeBinding) -> None:
        self.binding = binding
        self._registration: object | None = None
        self._input: Callable[[NativeInput], Awaitable[Any]] | None = None
        self._stop: Callable[[ControlPermit], Awaitable[StopEvidence]] | None = None
        self._queue: asyncio.Queue[NativeInput] = asyncio.Queue()
        self._messages: dict[str, NativeInput] = {}
        self._delivery: ContextVar[tuple[NativeInput, list[bool]] | None] = ContextVar(
            "native_input_delivery", default=None
        )
        self._consume_lock = asyncio.Lock()
        self._permits: set[ControlPermit] = set()
        self._stop_task: asyncio.Task[StopEvidence] | None = None

    @property
    def accepts_input(self) -> bool:
        return self._registration is not None and self._input is not None

    def register(
        self,
        *,
        input_handler: Callable[[NativeInput], Awaitable[Any]] | None = None,
        stop_handler: Callable[[ControlPermit], Awaitable[StopEvidence]] | None = None,
    ) -> Callable[[], None]:
        if self._registration is not None:
            raise StateConflictError("Native binding already has an exact control registration")
        if input_handler is None and stop_handler is None:
            raise ValueError("Native controls require an actual input or stop handler")
        if any(handler is not None and not callable(handler) for handler in (input_handler, stop_handler)):
            raise TypeError("Native control registration requires actual callbacks")
        registration = object()
        self._registration, self._input, self._stop = registration, input_handler, stop_handler

        def remove() -> None:
            if self._registration is not registration:
                return
            if self._messages or (self._stop_task is not None and not self._stop_task.done()):
                raise StateConflictError("Native control work must drain before hook restoration")
            self._registration, self._input, self._stop = None, None, None

        return remove

    async def enqueue(self, content: str, *, message_id: str) -> NativeInput:
        if not self.accepts_input:
            raise StateConflictError("This native target has no declared operator-input boundary")
        if not await self.binding.current():
            raise StateConflictError("Native input belongs to an inactive execution")
        message = NativeInput(message_id, self.binding.execution, SendMessageRequest(content=content).content)
        if message.id in self._messages:
            raise StateConflictError("Native input identity is already pending")
        await self.binding.control_receipt(
            message.id,
            "input_queued",
            {
                "message_id": message.id,
                "content": message.content,
                "admission_status": "accepted",
                "execution_status": "unconfirmed",
                "delivery_status": "queued",
            },
        )
        if not await self.binding.current():
            await self.binding.control_receipt(message.id, "input_undelivered", {"reason": "execution_changed"})
            raise StateConflictError("Native execution changed before input was queued")
        self._messages[message.id] = message
        self._queue.put_nowait(message)
        return message

    async def consume(self) -> Any:
        async with self._consume_lock:
            if not self.accepts_input or not await self.binding.current():
                raise StateConflictError("Native operator-input boundary is unavailable")
            pending = asyncio.create_task(self._queue.get())
            revoked = asyncio.create_task(self.binding.wait_revoked())
            message = None
            delivered: list[bool] = []
            try:
                done, _ = await asyncio.wait((pending, revoked), return_when=asyncio.FIRST_COMPLETED)
                if pending in done:
                    message = pending.result()
                if message is None or not await self.binding.current():
                    raise asyncio.CancelledError
                callback = self._input
                if callback is None:
                    raise StateConflictError("Native input registration was removed")
                token = self._delivery.set((message, delivered))
                try:
                    result = await callback(message)
                    if not delivered:
                        raise StateConflictError("Native input returned without observed consumption")
                    return result
                finally:
                    self._delivery.reset(token)
            except BaseException as exc:
                if message is not None:
                    await self.binding.control_receipt(
                        message.id,
                        "input_processing_failed" if delivered else "input_failed",
                        {
                            "message_id": message.id,
                            "delivery_status": "consumed" if delivered else "unconfirmed",
                            "error_type": type(exc).__name__,
                        },
                    )
                raise
            finally:
                pending.cancel()
                revoked.cancel()
                await asyncio.gather(pending, revoked, return_exceptions=True)
                if message is None and not pending.cancelled() and pending.exception() is None:
                    message = pending.result()
                    await self.binding.control_receipt(
                        message.id,
                        "input_undelivered",
                        {
                            "message_id": message.id,
                            "delivery_status": "undelivered",
                            "reason": "boundary_cancelled",
                        },
                    )
                if message is not None:
                    self._messages.pop(message.id, None)
                    self._queue.task_done()

    async def consumed(self, message: NativeInput, native_ids: Mapping[str, str]) -> None:
        delivery = self._delivery.get()
        if (
            delivery is None
            or delivery[0] is not message
            or self._messages.get(message.id) is not message
            or message.execution != self.binding.execution
        ):
            raise StateConflictError("Native consumption does not own the exact admitted message")
        if delivery[1]:
            return
        await self.binding.control_receipt(
            message.id,
            "input_consumed",
            {
                "message_id": message.id,
                "delivery_status": "consumed",
                "native_ids": dict(native_ids),
            },
        )
        delivery[1].append(True)

    async def require(self, permit: ControlPermit) -> None:
        if permit not in self._permits or permit.execution != self.binding.execution:
            raise StateConflictError("Native stop permit is not owned by this exact binding")
        if not self.binding.control_stop_requested():
            raise StateConflictError("Native stop has no Runtime-owned cancellation request")
        await self.binding.require_control_retained()

    async def stop(self, *, refresh: bool = False) -> StopEvidence | None:
        if self._stop is None:
            return None
        if not self.binding.control_stop_requested():
            raise StateConflictError("Native stop has no Runtime-owned cancellation request")
        if self._stop_task is not None and (not refresh or not self._stop_task.done()):
            return await retain_task(self._stop_task)
        await self.binding.require_control_retained()
        callback = self._stop
        permit = ControlPermit(uuid.uuid4().hex, self.binding.execution)
        self._permits.add(permit)

        async def invoke() -> StopEvidence:
            token = native_execution_context.set(self.binding.execution)
            try:
                await self.require(permit)
                await self.binding.control_receipt(
                    permit.operation_id, "stop_requested", {"status": "cancel-requested"}
                )
                evidence = await callback(permit)
                if not isinstance(evidence, StopEvidence):
                    raise TypeError("Native stop callback must return typed evidence")
                await self.require(permit)
                await self.binding.control_receipt(permit.operation_id, "stop_result", asdict(evidence))
                return evidence
            except Exception as exc:
                await self.binding.control_receipt(
                    permit.operation_id,
                    "stop_failed",
                    {
                        "status": "delivery-unknown",
                        "error_type": type(exc).__name__,
                    },
                )
                raise
            finally:
                native_execution_context.reset(token)
                self._permits.discard(permit)

        self._stop_task = asyncio.create_task(invoke(), name=f"native-stop:{permit.operation_id}")
        return await retain_task(self._stop_task)

    async def drain(self) -> None:
        errors: list[Exception] = []
        if self._stop_task is not None:
            try:
                await retain_task(self._stop_task)
            except Exception as exc:
                errors.append(exc)
        try:
            for message in tuple(self._messages.values()):
                try:
                    await self.binding.control_receipt(
                        message.id,
                        "input_undelivered",
                        {"message_id": message.id, "delivery_status": "undelivered", "reason": "execution_ended"},
                    )
                except Exception as exc:
                    errors.append(exc)
        finally:
            self._messages.clear()
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
        if errors:
            raise ExceptionGroup("Native control drain requires reconciliation", errors)
