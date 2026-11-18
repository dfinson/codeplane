"""Exact, fail-closed attachment ownership and lazy native hook installation."""

from __future__ import annotations

import threading
from importlib import import_module
from typing import TYPE_CHECKING, Any

from .readiness import require_ready

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

    @property
    def active(self) -> bool:
        return not self._closed.is_set() and self.binding.active

    def claim(self, target: Any) -> None:
        owner = getattr(target, _MARKER, None)
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
        try:
            result = await invoke()
        except BaseException:
            await self.binding.report(call, True, False)
            raise
        succeeded = outcome(result) if outcome is not None else True
        await self.binding.report(call, True if succeeded is True else None, succeeded)
        return result

    def complete_sync[T](
        self, call: NativeCall, invoke: Callable[[], T], *, outcome: Callable[[T], bool | None] | None = None
    ) -> T:
        try:
            result = invoke()
        except BaseException:
            self.binding.report_sync(call, True, False)
            raise
        succeeded = outcome(result) if outcome is not None else True
        self.binding.report_sync(call, True if succeeded is True else None, succeeded)
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
        require_ready(self.binding.family)
        module = import_module(f"{__package__}.{self.binding.family}")
        attachment = GateAttachment(self.binding, target)
        try:
            attachment.claim(target)
            module.install(attachment, target)
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
