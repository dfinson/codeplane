"""Exact, fail-closed attachment ownership and lazy native hook installation."""

from __future__ import annotations

import threading
from contextvars import ContextVar
from functools import wraps
from importlib import import_module
from inspect import iscoroutine, iscoroutinefunction
from typing import TYPE_CHECKING, Any

from .readiness import UnsupportedNativeHookError
from .selection import require_installation

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .binding import NativeBinding, NativeCall

_MARKER = "_codeplane_native_gate"
_MISSING = object()


class GateAttachment:
    def __init__(self, binding: NativeBinding, target: Any) -> None:
        self.binding = binding
        self.target = target
        self._closed = threading.Event()
        self._undo: list[Callable[[], None]] = []
        self._executing: ContextVar[NativeCall | None] = ContextVar("native_execution", default=None)
        self._tracked: set[tuple[int, str]] = set()

    @property
    def active(self) -> bool:
        return not self._closed.is_set() and self.binding.active

    def claim(self, target: Any) -> None:
        owner = getattr(target, _MARKER, None)
        if owner is self:
            return
        if owner is not None:
            raise ValueError("Native object already belongs to another gate attachment")
        self.patch(target, _MARKER, self)

    def patch(self, target: Any, name: str, replacement: Any) -> None:
        original = vars(target).get(name, _MISSING)
        object.__setattr__(target, name, replacement)

        def restore() -> None:
            # Never unwrap a replacement's hook or overwrite a caller's later edit.
            if getattr(target, name, _MISSING) is replacement:
                if original is _MISSING:
                    object.__delattr__(target, name)
                else:
                    object.__setattr__(target, name, original)

        self._undo.append(restore)

    def track_callable(self, target: Any, name: str) -> bool:
        """Observe actual callable entry, independently of permission wrappers."""
        key = (id(target), name)
        if key in self._tracked:
            return True
        original = getattr(target, name, None)
        if not callable(original):
            return False
        self.claim(target)

        @wraps(original)
        def synchronous(*args: Any, **kwargs: Any) -> Any:
            call = self._executing.get()
            token = self.binding.begin_work(call) if call is not None else None
            try:
                return original(*args, **kwargs)
            finally:
                if token is not None:
                    self.binding.end_work(token)

        @wraps(original)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            call = self._executing.get()
            token = self.binding.begin_work(call) if call is not None else None
            try:
                return await original(*args, **kwargs)
            finally:
                if token is not None:
                    self.binding.end_work(token)

        self.patch(target, name, asynchronous if iscoroutinefunction(original) else synchronous)
        self._tracked.add(key)
        return True

    def cleanup(self, undo: Callable[[], None]) -> None:
        self._undo.append(undo)

    def detach(self) -> None:
        self._closed.set()
        # Keep a failing cleanup registered for an explicit retry. No silent leaks.
        while self._undo:
            self._undo[-1]()
            self._undo.pop()

    async def allow(self, call: NativeCall) -> bool:
        return await self.binding.authorize(call, lambda: self.active)

    def allow_sync(self, call: NativeCall) -> bool:
        return self.binding.authorize_sync(call, lambda: self.active)

    async def complete[T](
        self, call: NativeCall, invoke: Callable[[], Awaitable[T]], *, outcome: Callable[[T], bool | None] | None = None
    ) -> T:
        token = self._executing.set(call)
        try:
            result = await invoke()
        except BaseException as exc:
            started = call.execution_probe(exc) if call.execution_probe is not None else call.evidence.started
            await self.binding.report(call, started, False)
            raise
        finally:
            self._executing.reset(token)
        succeeded = outcome(result) if outcome is not None else True
        started = call.execution_probe(None) if call.execution_probe is not None else call.evidence.started
        await self.binding.report(call, started, succeeded)
        return result

    def complete_sync[T](
        self, call: NativeCall, invoke: Callable[[], T], *, outcome: Callable[[T], bool | None] | None = None
    ) -> T:
        token = self._executing.set(call)
        try:
            result = invoke()
        except BaseException as exc:
            started = call.execution_probe(exc) if call.execution_probe is not None else call.evidence.started
            self.binding.report_sync(call, started, False)
            raise
        finally:
            self._executing.reset(token)
        succeeded = outcome(result) if outcome is not None else True
        started = call.execution_probe(None) if call.execution_probe is not None else call.evidence.started
        self.binding.report_sync(call, started, succeeded)
        return result


class NativeGates:
    """One exact execution's attachments. All SDK imports occur after admission."""

    def __init__(self, binding: NativeBinding) -> None:
        self.binding = binding
        self._attachments: dict[int, GateAttachment] = {}

    async def attach(self, target: Any) -> GateAttachment:
        if not await self.binding.current():
            raise PermissionError("Native execution is not currently admitted")
        previous = self._attachments.get(id(target))
        if previous is not None and previous.active:
            return previous
        selected = require_installation(self.binding.family)
        module = import_module(selected.module)
        install = getattr(module, "install", None)
        if not callable(install) or iscoroutinefunction(install):
            raise UnsupportedNativeHookError("Native installer must implement the synchronous app-owned contract")
        attachment = GateAttachment(self.binding, target)
        try:
            attachment.claim(target)
            result = install(attachment, target)
            if result is not None:
                if iscoroutine(result):
                    result.close()
                raise UnsupportedNativeHookError("Native installer did not complete its synchronous attachment")
        except BaseException:
            attachment.detach()
            raise
        self._attachments[id(target)] = attachment
        return attachment

    def detach(self, target: Any) -> None:
        attachment = self._attachments.get(id(target))
        if attachment is not None:
            attachment.detach()
            del self._attachments[id(target)]

    def cancel(self) -> str:
        self.binding.revoke()
        return "cancel-requested"

    def close(self) -> None:
        self.binding.revoke()
        for target in list(self._attachments):
            self._attachments[target].detach()
            del self._attachments[target]
