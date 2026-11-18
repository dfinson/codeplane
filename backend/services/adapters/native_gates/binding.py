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


class NativeBinding:
    """An externally admitted execution. This class never mints ownership.

    `is_current` must check the runtime's actual task/handle, pin, enablement,
    cancellation and replacement generation, not merely return a cached boolean.
    The binding id is a CodePlane native handle, not a fabricated managed session.
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
        self._revoked = threading.Event()
        self.loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._on_result = on_result
        self._authorized: dict[str, NativeCall] = {}

    @property
    def active(self) -> bool:
        return not self._revoked.is_set()

    def revoke(self) -> None:
        """Fence callbacks immediately. This is not a claim that a worker stopped."""
        self._revoked.set()

    async def current(self) -> bool:
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

        async def current() -> bool:
            return attached() and await self.current() and attached() and self.action(call) == action

        if not await current():
            return False
        allowed = await self.policy.authorize_permission(
            PermissionInvocation(
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
        )
        if allowed and await current():
            self._authorized[call.operation_id] = call
            return True
        return False

    async def report(self, call: NativeCall, started: bool | None, succeeded: bool | None) -> None:
        if asyncio.get_running_loop() is not self.loop:
            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(self.report(call, started, succeeded), self.loop)
            )
            return
        if self._authorized.get(call.operation_id) is not call:
            raise StateError("Native result has no exact authorized invocation")
        if self._on_result is not None:
            await self._on_result(call, started, succeeded)
        del self._authorized[call.operation_id]

    def report_sync(self, call: NativeCall, started: bool | None, succeeded: bool | None) -> None:
        if threading.get_ident() == self._loop_thread:
            raise RuntimeError("Synchronous result reporting requires the native runtime worker")
        asyncio.run_coroutine_threadsafe(self.report(call, started, succeeded), self.loop).result()

    async def report_unfinished(self) -> None:
        for call in tuple(self._authorized.values()):
            await self.report(call, None, None)

    def authorize_sync(self, call: NativeCall, attached: Callable[[], bool]) -> bool:
        if threading.get_ident() == self._loop_thread:
            raise RuntimeError("Run synchronous native SDK calls in the owning runtime worker, not the policy loop")
        if not self.loop.is_running() or not self.active or not attached():
            return False
        future = asyncio.run_coroutine_threadsafe(self.authorize(call, attached), self.loop)
        return future.result() and self.active and attached()


class StateError(RuntimeError):
    """A native receipt does not match an actually authorized operation."""
