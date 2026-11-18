"""Job-owned adapter for the production standalone permission authority."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import StateConflictError
from backend.models.events import EventKind, new_event
from backend.services.action_policy.agt_policy import PolicyInput
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.auth.permission_policy import is_hard_gated_command
from backend.services.auth.workspace_paths import workspace_target

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.models.execution_contracts import ExecutionContractPin
    from backend.services.action_policy.authority import PolicyAuthority
    from backend.services.action_policy.checkpoint_service import CheckpointService
    from backend.services.action_policy.classifier import RepoPolicy
    from backend.services.action_policy.session_policy import PermissionInvocation
    from backend.services.events.event_bus import EventBus
    from backend.services.runtime.execution_contracts import ExecutionContracts

log = structlog.get_logger()
_NATIVE_READS = frozenset({"read", "view", "read_file", "glob", "grep", "rg", "ls", "list_dir", "read_files"})


class StandalonePolicyRouter:
    def __init__(
        self,
        *,
        pin: ExecutionContractPin,
        execution_epoch: str,
        policy: RepoPolicy,
        authority: PolicyAuthority,
        contracts: ExecutionContracts,
        events: EventBus,
        checkpoint: CheckpointService,
        job_is_current: Callable[[str, str], Awaitable[bool]],
    ) -> None:
        self.pin = pin
        self.execution_epoch = execution_epoch
        self._policy = policy
        self._authority = authority
        self._contracts = contracts
        self._events = events
        self._checkpoint = checkpoint
        self._job_is_current = job_is_current
        self._closed = False

    def update_policy(self, policy: RepoPolicy) -> None:
        self._policy = policy

    async def authorize_permission(self, invocation: PermissionInvocation) -> bool:
        metadata = invocation.metadata
        native = invocation.native_execution
        if (
            invocation.job_id != self.pin.job_id
            or (native is None and metadata.run_id != invocation.session_id)
            or metadata.execution_epoch != self.execution_epoch
            or not metadata.execution_token
            or metadata.integration_binding_id is not None
            or (
                native is not None
                and (
                    native.job_id != invocation.job_id
                    or native.epoch != metadata.execution_epoch
                    or native.execution_token != metadata.execution_token
                    or not native.binding_id
                    or invocation.session_id != ""
                )
            )
        ):
            log.error(
                "standalone_permission_binding_invalid",
                job_id=invocation.job_id,
                session_id=invocation.session_id,
            )
            return False
        selected_policy = self._policy

        async def current() -> bool:
            return (
                not self._closed
                and self._policy is selected_policy
                and await invocation.is_current()
                and await self._job_is_current(invocation.job_id, invocation.provider)
            )

        if not await current():
            return False
        self._contracts.require_available(self.pin)
        if native is None and invocation.provider != "codeplane.command":
            await self._contracts.bind_handle(
                self.pin,
                sdk=invocation.provider,
                session_id=invocation.session_id,
                live_epoch=self.execution_epoch,
                execution_token=metadata.execution_token,
                attempt_id=metadata.attempt_id,
            )
        request = invocation.request
        paths = tuple(
            dict.fromkeys(path for path in (request.file_name, request.path, *(request.possible_paths or ())) if path)
        )
        contained = bool(paths)
        protected = False
        for path in paths:
            try:
                target = workspace_target(invocation.workspace, path)
                relative = str(target.relative_to(Path(invocation.workspace).resolve())).replace("\\", "/")
            except (OSError, ValueError, RuntimeError):
                contained = False
                relative = path.replace("\\", "/")
            protected |= any(
                fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(path, pattern)
                for pattern in invocation.protected_paths
            )
        command = request.full_command_text or ""
        trusted_read = (
            request.kind == "read"
            and invocation.provider in {"copilot", "claude"}
            and invocation.tool_name.lower() in _NATIVE_READS
            and contained
            and not command
        )
        local = PolicyInput(
            job_id=invocation.job_id,
            preset=selected_policy.preset,
            trusted_file_read=trusted_read,
            hard_gate=invocation.hard_gate or bool(command) and is_hard_gated_command(command),
            protected_write=protected and request.kind != "read",
            local_scope="external" if paths and not contained else None,
        )

        async def savepoint() -> str | None:
            if not await current():
                return None
            return await self._checkpoint.create(
                invocation.job_id,
                f"Before {invocation.tool_name or request.kind}",
                cwd=invocation.workspace,
            )

        authorized = await self._authority.authorize(
            pin=self.pin,
            execution=native
            or ExecutionIdentity(
                invocation.job_id,
                f"{invocation.provider}:{invocation.session_id}",
                self.execution_epoch,
                metadata.execution_token,
            ),
            local=local,
            fields={
                "command": command,
                "arguments": invocation.arguments_json,
                "tool_definition": invocation.tool_definition,
                "context": (
                    f"Native tool: {invocation.tool_name}; permission kind: {request.kind}"
                    + (f"; producer call ID: {invocation.native_tool_call_id}" if native is not None else "")
                ),
            },
            dialect=invocation.dialect,
            metadata=metadata,
            tool_call_id=invocation.tool_call_id,
            is_current=current,
            savepoint=savepoint,
        )
        await self._events.publish(
            new_event(
                invocation.job_id,
                EventKind.action_classified,
                {
                    "recommendedAction": authorized.decision.action,
                    "reasonCode": authorized.decision.reason_code,
                    "riskScore": None,
                    "riskBand": "unknown",
                    "tool_name": invocation.tool_name,
                    "decision_id": authorized.decision_id,
                    "action_digest": authorized.binding.sha256,
                    "checkpoint_ref": authorized.checkpoint_ref,
                    "approval_id": authorized.approval_id,
                    "policy_version": authorized.decision.policy_version,
                },
                metadata=metadata,
            )
        )
        return authorized.proceed and await current()

    async def route(self, action: object, *, cwd: str | None = None) -> None:
        raise StateConflictError("Standalone invocation requires exact execution ownership and immutable tool inputs")

    def cleanup_job(self, job_id: str) -> None:
        if job_id == self.pin.job_id:
            self._closed = True
            self._checkpoint.cleanup_job(job_id)
