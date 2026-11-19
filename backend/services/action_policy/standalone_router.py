"""Job-owned adapter for the production standalone permission authority."""

from __future__ import annotations

import asyncio
import fnmatch
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import StateConflictError
from backend.models.events import EventKind, new_event
from backend.services.action_policy.agt_policy import PolicyInput
from backend.services.action_policy.assessment import DECISION_SECONDS, MAX_INPUT_BYTES
from backend.services.adapters.capability_guard import has_explicit_file_target, invocation_permission_request
from backend.services.adapters.contracts import CapabilityDeniedError, ExecutionIdentity
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
    from backend.services.git.git_service import GitService
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
        git: GitService,
        job_is_current: Callable[[str, str], Awaitable[bool]],
    ) -> None:
        self.pin = pin
        self.execution_epoch = execution_epoch
        self._policy = policy
        self._authority = authority
        self._contracts = contracts
        self._events = events
        self._checkpoint = checkpoint
        self._git = git
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
                and await self._job_is_current(invocation.job_id, invocation.provider)
                and await invocation.is_current()
                and not self._closed
                and self._policy is selected_policy
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
        decision_deadline = asyncio.get_running_loop().time() + DECISION_SECONDS
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
        known_write = (
            request.kind == "write"
            and invocation.provider in {"copilot", "claude"}
            and invocation.tool_name.lower() not in {"str_replace_editor", "str_replace_based_edit_tool"}
            and not command
        )
        recovery = None
        recovery_paths: tuple[str, ...] = ()
        if (
            known_write
            and has_explicit_file_target(invocation.tool_name)
            and contained
            and not protected
            and not invocation.hard_gate
        ):
            try:
                if (
                    len(invocation.arguments_json) > MAX_INPUT_BYTES
                    or len(invocation.arguments_json.encode("utf-8")) > MAX_INPUT_BYTES
                ):
                    raise ValueError("Native write arguments exceed the input bound")
                arguments = json.loads(invocation.arguments_json)
                if not isinstance(arguments, dict):
                    raise ValueError("Native write arguments must be an object")
                declared = invocation_permission_request(invocation.workspace, invocation.tool_name, arguments)
                candidates = tuple(dict.fromkeys(declared.possible_paths or ()))
                if len(candidates) != 1 or {workspace_target(invocation.workspace, path) for path in candidates} != {
                    workspace_target(invocation.workspace, path) for path in paths
                }:
                    raise ValueError("Native write target is missing, ambiguous or differs from permission paths")
                recovery_paths = candidates
            except (CapabilityDeniedError, OSError, ValueError, RuntimeError) as exc:
                log.warning("permission_recovery_target_unavailable", job_id=invocation.job_id, reason=str(exc))
            if recovery_paths:
                recovery = await self._git.recovery_witness(
                    cwd=invocation.workspace,
                    paths=recovery_paths,
                    deadline=decision_deadline,
                    max_input_bytes=MAX_INPUT_BYTES,
                )
        recovery_deadline = decision_deadline

        async def recovery_current(deadline: float) -> bool:
            nonlocal recovery_deadline
            recovery_deadline = deadline
            if recovery is None:
                return True
            refreshed = await self._git.recovery_witness(
                cwd=invocation.workspace,
                paths=recovery_paths,
                deadline=deadline,
                max_input_bytes=MAX_INPUT_BYTES,
            )
            if refreshed != recovery:
                log.warning(
                    "permission_recovery_changed", job_id=invocation.job_id, tool_call_id=invocation.tool_call_id
                )
                return False
            return True

        local = PolicyInput(
            job_id=invocation.job_id,
            preset=selected_policy.preset,
            trusted_file_read=trusted_read,
            hard_gate=invocation.hard_gate or bool(command) and is_hard_gated_command(command),
            protected_write=protected and request.kind != "read",
            local_scope="external" if paths and not contained else None,
            local_effect="mutating" if known_write else None,
            local_reversibility="git" if recovery is not None else "unknown" if known_write else None,
            git_recovery=recovery,
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
                    + (
                        "; Application-verified complete file target set and current-byte recovery: "
                        + json.dumps(asdict(recovery), sort_keys=True)
                        + ". Each blob equals the current unfiltered file bytes in the retained commit. "
                        "A null blob means a new, nonignored target absent in both the workspace and that commit."
                        if recovery is not None
                        else ""
                    )
                ),
            },
            dialect=invocation.dialect,
            metadata=metadata,
            approval_callback=invocation.approval_callback,
            tool_call_id=invocation.tool_call_id,
            is_current=current,
            decision_deadline=decision_deadline,
            recovery_is_current=recovery_current if recovery is not None else None,
            savepoint=savepoint,
        )
        await self._events.publish(
            new_event(
                invocation.job_id,
                EventKind.action_classified,
                {
                    "recommendedAction": authorized.decision.action,
                    "reasonCode": authorized.decision.reason_code,
                    "riskScore": authorized.decision.risk_score,
                    "riskBand": authorized.decision.risk_band,
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
        return authorized.proceed and await recovery_current(recovery_deadline) and await current()

    async def route(self, action: object, *, cwd: str | None = None) -> None:
        raise StateConflictError("Standalone invocation requires exact execution ownership and immutable tool inputs")

    def cleanup_job(self, job_id: str) -> None:
        if job_id == self.pin.job_id:
            self._closed = True
            self._checkpoint.cleanup_job(job_id)
