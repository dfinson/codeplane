"""Bridge native callbacks to the existing, admission-pinned Job policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.services.action_policy.session_policy import PermissionInvocation
from backend.services.adapters.contracts import ExecutionIdentity, GateAction
from backend.services.auth.permission_policy import PermissionRequest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from backend.models.session_event import EventMetadata
    from backend.services.action_policy.session_policy import SessionPolicy


class NativeGateDeniedError(PermissionError):
    """The exact native tool invocation was not authorized."""


def encode_arguments(arguments: object) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class NativeCall:
    name: str
    arguments: Mapping[str, Any]
    tool_call_id: str | None = None
    run_id: str | None = None
    parent_id: str | None = None
    definition: str = ""
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    fingerprint: Callable[[], str] | None = None
    evidence: ExecutionEvidence = field(default_factory=lambda: ExecutionEvidence())
    execution_probe: Callable[[BaseException | None], bool | None] | None = None
    entry_check: Callable[[tuple[Any, ...], dict[str, Any]], bool] | None = None


@dataclass
class ExecutionEvidence:
    started: bool | None = None


class NativeBinding:
    """An externally admitted execution. This class never mints ownership.

    `is_current` must check the runtime's actual task/handle, pin, enablement,
    cancellation and replacement generation, not merely return a cached boolean.
    The binding id is a CodePlane native handle, not a fabricated managed session.
    `is_current_inline` is Runtime's immediate ownership predicate for synchronous
    SDK bodies on the policy loop. Worker entries also await the full check.
    """

    def __init__(
        self,
        *,
        family: str,
        execution: ExecutionIdentity,
        policy: SessionPolicy,
        metadata: EventMetadata,
        workspace: str,
        protected_paths: tuple[str, ...],
        is_current: Callable[[], Awaitable[bool]],
        is_current_inline: Callable[[], bool] | None = None,
        on_result: Callable[[NativeCall, bool | None, bool | None], Awaitable[None]] | None = None,
    ) -> None:
        if (
            not all((execution.job_id, execution.binding_id, execution.epoch, execution.execution_token, workspace))
            or metadata.execution_epoch != execution.epoch
            or metadata.execution_token != execution.execution_token
            or metadata.integration_binding_id is not None
            or policy.execution_epoch != execution.epoch
        ):
            raise ValueError("Native gating requires one exact admitted Job/session/epoch/token and Job policy")
        self.family = family
        self.execution = execution
        self.policy = policy
        self.metadata = metadata
        self.workspace = workspace
        self.protected_paths = protected_paths
        self._is_current = is_current
        self._is_current_inline = is_current_inline
        self._revoked = threading.Event()
        self._revoked_async = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._on_result = on_result
        self._authorized: dict[str, NativeCall] = {}
        self._entry_checks: dict[str, Callable[[], bool]] = {}
        self._permissions: set[asyncio.Task[bool]] = set()
        self._results: dict[str, tuple[bool | None, bool | None]] = {}
        self._work: dict[object, str] = {}
        self._work_lock = threading.Lock()
        self._work_changed = asyncio.Event()
        self._observation: Callable[[Mapping[str, Any]], None] | None = None
        self._usage: Callable[[str, object], Awaitable[None]] | None = None

    def observe(self, record: Mapping[str, Any]) -> None:
        if self._observation is None:
            raise StateError("Native profile observation has no Runtime-owned source")
        self._observation(record)

    async def supplied_usage(self, operation_id: str, result: object) -> None:
        if asyncio.get_running_loop() is not self.loop:
            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(self.supplied_usage(operation_id, result), self.loop)
            )
            return
        if self._usage is None:
            raise StateError("Native profile usage has no Runtime-owned receipt path")
        await self._usage(operation_id, result)

    @property
    def active(self) -> bool:
        return not self._revoked.is_set()

    def revoke(self) -> None:
        """Fence callbacks immediately. This is not a claim that a worker stopped."""
        self._revoked.set()
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._revoked_async.set)

    async def wait_revoked(self) -> None:
        """Observe app revocation, not a native stop acknowledgment."""
        if asyncio.get_running_loop() is not self.loop:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.wait_revoked(), self.loop))
            return
        await self._revoked_async.wait()

    def begin_work(self, call: NativeCall | None) -> object:
        token = object()
        with self._work_lock:
            self._work[token] = call.operation_id if call is not None else ""
        if call is not None:
            call.evidence.started = True
        return token

    def end_work(self, token: object) -> None:
        with self._work_lock:
            del self._work[token]
        self.loop.call_soon_threadsafe(self._work_changed.set)

    async def drain_work(self, operation_id: str | None = None) -> None:
        while True:
            self._work_changed.clear()
            with self._work_lock:
                pending = bool(self._work) if operation_id is None else operation_id in self._work.values()
            if not pending:
                return
            await self._work_changed.wait()

    async def drain_permissions(self) -> None:
        while self._permissions:
            await asyncio.gather(*tuple(self._permissions), return_exceptions=True)

    async def current(self) -> bool:
        if asyncio.get_running_loop() is not self.loop:
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.current(), self.loop))
        return self.active and await self._is_current() and self.active

    def action(self, call: NativeCall) -> GateAction:
        encoded = encode_arguments(call.arguments)
        digest = hashlib.sha256(
            encode_arguments(
                {
                    "execution": {
                        "job_id": self.execution.job_id,
                        "binding_id": self.execution.binding_id,
                        "epoch": self.execution.epoch,
                        "execution_token": self.execution.execution_token,
                    },
                    "name": call.name,
                    "arguments": json.loads(encoded),
                    "tool_call_id": call.tool_call_id,
                    "run_id": call.run_id,
                    "parent_id": call.parent_id,
                    "definition": call.definition,
                    "operation_id": call.operation_id,
                }
            ).encode()
        ).hexdigest()
        # App receipt identity is distinct from the possibly absent producer ID.
        return GateAction(self.execution, call.operation_id, digest, call.name, json.loads(encoded))

    async def authorize(self, call: NativeCall, attached: Callable[[], bool]) -> bool:
        if asyncio.get_running_loop() is not self.loop:
            future = asyncio.run_coroutine_threadsafe(self._authorize(call, attached), self.loop)
            return await asyncio.wrap_future(future)
        return await self._authorize(call, attached)

    async def _authorize(self, call: NativeCall, attached: Callable[[], bool]) -> bool:
        action = self.action(call)
        fingerprint = call.fingerprint() if call.fingerprint is not None else None

        def unchanged() -> bool:
            return (
                attached()
                and self.action(call) == action
                and (call.fingerprint is None or call.fingerprint() == fingerprint)
            )

        async def current() -> bool:
            return unchanged() and await self.current() and unchanged()

        if not await current():
            return False
        invocation = PermissionInvocation(
            job_id=self.execution.job_id,
            session_id="",
            provider=self.family,
            metadata=self.metadata.model_copy(update={"parent_id": call.parent_id, "run_id": call.run_id}),
            workspace=self.workspace,
            protected_paths=self.protected_paths,
            # Arbitrary framework tools are not trusted file reads or automatic grants.
            request=PermissionRequest(kind="custom-tool", workspace_path=self.workspace),
            tool_name=call.name,
            arguments_json=encode_arguments(action.arguments),
            tool_definition=call.definition,
            tool_call_id=call.operation_id,
            is_current=current,
            native_execution=self.execution,
            native_tool_call_id=call.tool_call_id,
        )
        decision = asyncio.create_task(self.policy.authorize_permission(invocation))
        self._permissions.add(decision)
        decision.add_done_callback(self._permissions.discard)
        revoked = asyncio.create_task(self._revoked_async.wait())
        try:
            await asyncio.wait((decision, revoked), return_when=asyncio.FIRST_COMPLETED)
            if self._revoked.is_set():
                decision.cancel()
                await asyncio.gather(decision, return_exceptions=True)
                return False
            allowed = await decision
        finally:
            revoked.cancel()
            if not decision.done():
                decision.cancel()
            await asyncio.gather(decision, revoked, return_exceptions=True)
            self._permissions.discard(decision)
        if allowed and await current():
            self._authorized[call.operation_id] = call
            self._entry_checks[call.operation_id] = unchanged
            return True
        return False

    def _entry_unchanged(self, call: NativeCall) -> bool:
        check = self._entry_checks.get(call.operation_id)
        return self.active and self._authorized.get(call.operation_id) is call and check is not None and check()

    async def entry_current(self, call: NativeCall) -> bool:
        """Recheck the original grant at the leaf, without requesting another grant."""
        if asyncio.get_running_loop() is not self.loop:
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.entry_current(call), self.loop))
        return self._entry_unchanged(call) and await self.current() and self._entry_unchanged(call)

    def entry_current_sync(self, call: NativeCall) -> bool:
        if threading.get_ident() == self._loop_thread:
            if self._is_current_inline is None:
                raise StateError("Inline native entry requires the Runtime's non-awaiting ownership fence")
            return self._entry_unchanged(call) and self._is_current_inline() and self._entry_unchanged(call)
        if not self._entry_unchanged(call):
            return False
        current = asyncio.run_coroutine_threadsafe(self.entry_current(call), self.loop).result()
        return (
            current and (self._is_current_inline is None or self._is_current_inline()) and self._entry_unchanged(call)
        )

    async def report(self, call: NativeCall, started: bool | None, succeeded: bool | None) -> None:
        if asyncio.get_running_loop() is not self.loop:
            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(self.report(call, started, succeeded), self.loop)
            )
            return
        if self._authorized.get(call.operation_id) is not call:
            raise StateError("Native result has no exact authorized invocation")
        await self.drain_work(call.operation_id)
        self._results[call.operation_id] = (started, succeeded)
        if self._on_result is not None:
            await self._on_result(call, started, succeeded)
        del self._authorized[call.operation_id]
        self._entry_checks.pop(call.operation_id, None)
        self._results.pop(call.operation_id, None)

    def report_sync(self, call: NativeCall, started: bool | None, succeeded: bool | None) -> None:
        if threading.get_ident() == self._loop_thread:
            raise RuntimeError("Synchronous result reporting requires the native runtime worker")
        asyncio.run_coroutine_threadsafe(self.report(call, started, succeeded), self.loop).result()

    async def report_unfinished(self) -> None:
        for call in tuple(self._authorized.values()):
            started, succeeded = self._results.get(call.operation_id, (call.evidence.started, None))
            await self.report(call, started, succeeded)

    def authorize_sync(self, call: NativeCall, attached: Callable[[], bool]) -> bool:
        if threading.get_ident() == self._loop_thread:
            raise RuntimeError("Run synchronous native SDK calls in the owning runtime worker, not the policy loop")
        if not self.loop.is_running() or not self.active or not attached():
            return False
        future = asyncio.run_coroutine_threadsafe(self.authorize(call, attached), self.loop)
        return future.result() and self.active and attached()


class StateError(RuntimeError):
    """A native receipt does not match an actually authorized operation."""
