"""Blocking-only external permissions attached to genuine retained Runtime work."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from backend.config import resolve_protected_paths
from backend.models.domain import ApprovalAlreadyResolvedError, ApprovalResolution, StateConflictError
from backend.models.events import new_event
from backend.models.schemas.external_gate import (
    ExternalGateConsume,
    ExternalGateDecision,
    ExternalGatePhase,
    ExternalGatePreflight,
    ExternalGateReport,
    ExternalGateSourceRequest,
    ExternalGateSourceResponse,
)
from backend.models.session_event import EventMetadata
from backend.persistence.external_gate_repo import ExternalGateRepository, correlation
from backend.persistence.native_execution_repo import NativeExecutionRepository
from backend.services.action_policy.agt_policy import POLICY_VERSION
from backend.services.action_policy.standalone_router import StandalonePolicyRouter
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.integration_policy import IntegrationDisabledError
from backend.services.adapters.native_gates.binding import ExecutionEvidence, NativeBinding, NativeCall
from backend.services.adapters.native_gates.lifecycle import retain_task

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.models.execution_contracts import ExecutionContractPin
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


class ExternalGateAuthenticationError(PermissionError):
    pass


def _credential_hash(value: str) -> str:
    return hashlib.sha256(b"codeplane-external-gate-control-v1\0" + value.encode()).hexdigest()


@dataclass
class _Owner:
    execution: ExecutionIdentity
    pin: ExecutionContractPin
    metadata: EventMetadata
    policy: StandalonePolicyRouter
    workspace: str
    protected_paths: tuple[str, ...]
    task: asyncio.Task[Any]
    current: Callable[[], Awaitable[bool]]
    inline: Callable[[], bool]
    native_binding: NativeBinding | None = None


@dataclass
class _Request:
    action: ExternalGatePreflight
    call: NativeCall
    digest: str
    binding: NativeBinding
    metadata: EventMetadata
    done: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = True
    task: asyncio.Task[None] | None = None
    hold: object | None = None


@dataclass
class _Source:
    id: str
    credential_hash: str
    owner: _Owner
    external_session_id: str | None
    active: bool = True
    requests: dict[str, _Request] = field(default_factory=dict)
    close_task: asyncio.Task[None] | None = None
    watcher: asyncio.Task[None] | None = None
    admission_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class ExternalGates:
    def __init__(self, host: RuntimeService) -> None:
        self.host = host
        self.repo = ExternalGateRepository(host._session_factory)
        self._sources: dict[str, _Source] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: int | None = None
        self._reconciled = False
        self._watchers: set[asyncio.Task[None]] = set()

    async def _owner(self, job_id: str, request: ExternalGateSourceRequest) -> _Owner:
        host = self.host
        if host._config.runtime.copilot_only:
            raise IntegrationDisabledError("External gates are disabled by runtime.copilot_only")
        await host.require_governance_admission()
        pin = host._recipe_contract_pins.get(job_id) or host._job_contract_pins.get(job_id)
        policy = host._policy_routers.get(job_id)
        if (
            pin is None
            or pin.contract.policy.id != POLICY_VERSION
            or not isinstance(policy, StandalonePolicyRouter)
            or policy.pin != pin
            or host._execution_contracts is None
            or host._approval_service is None
        ):
            raise StateConflictError("External gates require the current admission-pinned CodePlane authority")
        host._execution_contracts.require_available(pin)
        native = next(
            (
                item
                for item in host._native_executions.for_job(job_id)
                if item.identity.execution_token == request.execution_token
            ),
            None,
        )
        if native is not None:
            if request.attempt_id != native.metadata.attempt_id or not await native.gates.binding.current():
                raise StateConflictError("External gate execution changed")

            async def current_native() -> bool:
                return await native.gates.binding.current()

            def inline_native() -> bool:
                return (
                    host._native_executions.retains(native)
                    and native.gates.binding.active
                    and not native.cancel_requested
                    and not native.owner.done()
                    and not native.owner.cancelling()
                    and host._policy_routers.get(job_id) is policy
                    and not host._config.runtime.copilot_only
                    and (host._recipe_contract_pins.get(job_id) or host._job_contract_pins.get(job_id)) == pin
                    and host.execution_epoch == native.identity.epoch
                )

            owner = _Owner(
                native.identity,
                pin,
                native.metadata,
                policy,
                native.gates.binding.workspace,
                native.gates.binding.protected_paths,
                native.owner,
                current_native,
                inline_native,
                native.gates.binding,
            )
            native_mode, sdk, session_id = True, native.gates.binding.family, None
        else:
            job = await host._get_job(job_id)
            if job is None or job.observation_only or job.state != "running":
                raise StateConflictError("External gate has no live execution owner")
            controller = host._recipe_controllers.get(job_id)
            owned = controller.owned_attempt(request.attempt_id) if controller and request.attempt_id else None
            attempt_handle = host._attempt_bindings.get(job_id, request.attempt_id) if request.attempt_id else None
            handle = attempt_handle if request.attempt_id else host._agent_sessions.get(job_id)
            task = owned.task if owned is not None else host._tasks.get(job_id)
            session_id = handle.session_id if handle is not None else None
            token = getattr(handle, "token" if request.attempt_id else "execution_token", None)
            sdk = attempt_handle.sdk if attempt_handle is not None else str(job.sdk)
            if handle is None or task is None or not session_id or token != request.execution_token:
                raise StateConflictError(
                    "External gate requires an existing Runtime SDK handle, not a supplied identity"
                )
            if request.attempt_id and owned is None:
                raise StateConflictError("External gate attempt has no actual owning Runtime task")
            identity = ExecutionIdentity(job_id, f"{sdk}:{session_id}", host.execution_epoch, token)
            admission = await NativeExecutionRepository(host._session_factory).admission(
                pin,
                attempt_id=request.attempt_id,
                run_id=owned.run_id if owned else None,
                step_id=owned.step_id if owned else None,
            )
            metadata = admission.metadata.model_copy(
                update={
                    "run_id": session_id,
                    "execution_epoch": identity.epoch,
                    "execution_token": identity.execution_token,
                }
            )

            def inline_managed() -> bool:
                selected = (
                    host._attempt_bindings.get(job_id, request.attempt_id)
                    if request.attempt_id
                    else host._agent_sessions.get(job_id)
                )
                return bool(
                    selected is handle
                    and handle.session_id == session_id
                    and getattr(handle, "token" if request.attempt_id else "execution_token", None)
                    == identity.execution_token
                    and not task.done()
                    and not task.cancelling()
                    and not getattr(handle, "cancel_requested", False)
                    and host._policy_routers.get(job_id) is policy
                    and not host._shutting_down
                    and not host._config.runtime.copilot_only
                    and not host._active_gates.get(job_id)
                    and (host._recipe_contract_pins.get(job_id) or host._job_contract_pins.get(job_id)) == pin
                    and host.execution_epoch == identity.epoch
                    and (request.attempt_id is not None or host._tasks.get(job_id) is task)
                    and (
                        not request.attempt_id
                        or controller is not None
                        and controller.owned_attempt(request.attempt_id) is owned
                    )
                )

            async def current_managed() -> bool:
                latest = await host._get_job(job_id)
                return bool(
                    inline_managed()
                    and latest is not None
                    and latest.state == "running"
                    and await self.repo.owner_bound(
                        identity, pin, native=False, sdk=sdk, session_id=session_id, attempt_id=request.attempt_id
                    )
                    and inline_managed()
                )

            owner = _Owner(
                identity,
                pin,
                metadata,
                policy,
                admission.workspace,
                tuple(resolve_protected_paths(admission.repo)),
                task,
                current_managed,
                inline_managed,
            )
            native_mode = False
        if (
            not await self.repo.owner_bound(
                owner.execution, pin, native=native_mode, sdk=sdk, session_id=session_id, attempt_id=request.attempt_id
            )
            or not await owner.current()
        ):
            raise StateConflictError("External gate owner has no exact durable handle admission")
        return owner

    async def issue(self, job_id: str, request: ExternalGateSourceRequest) -> ExternalGateSourceResponse:
        await self.reconcile_stale()
        async with self.host._execution_control.hold(job_id):
            owner = await self._owner(job_id, request)
            source_id, secret = f"gate-{uuid.uuid4().hex}", secrets.token_urlsafe(32)
            digest = _credential_hash(secret)
            await self.repo.bind(source_id, digest, owner.execution, owner.pin, request.external_session_id)
            if not await owner.current():
                await self.repo.disable(source_id)
                raise StateConflictError("External gate owner changed during source binding")
            source = _Source(source_id, digest, owner, request.external_session_id)
            self._sources[source_id] = source
            self._loop, self._thread = asyncio.get_running_loop(), threading.get_ident()
            source.watcher = asyncio.create_task(self._watch(source), name=f"external-gate-owner:{source_id}")
            self._watchers.add(source.watcher)

            def watched(task: asyncio.Task[None]) -> None:
                self._watchers.discard(task)
                if not task.cancelled() and (error := task.exception()) is not None:
                    log.error("external_gate_retirement_failed", source_id=source_id, error_type=type(error).__name__)

            source.watcher.add_done_callback(watched)
        return ExternalGateSourceResponse(
            source_id=source_id,
            credential=secret,
            job_id=job_id,
            execution_binding_id=owner.execution.binding_id,
            execution_epoch=owner.execution.epoch,
            execution_token=owner.execution.execution_token,
        )

    async def authenticate(self, source_id: str, authorization: str | None) -> _Source:
        source = self._sources.get(source_id)
        scheme, _, secret = (authorization or "").partition(" ")
        if (
            source is None
            or scheme.lower() != "bearer"
            or not secret
            or len(secret) > 512
            or not hmac.compare_digest(source.credential_hash, _credential_hash(secret))
        ):
            raise ExternalGateAuthenticationError("Invalid external gate credential")
        if not await self._current(source):
            await self.close_source(source, "owner_unavailable")
            raise ExternalGateAuthenticationError("External gate source is no longer live")
        return source

    async def _current(self, source: _Source) -> bool:
        return bool(
            self._sources.get(source.id) is source
            and source.active
            and not self.host._config.runtime.copilot_only
            and await source.owner.current()
            and await self.repo.active(source.id, source.credential_hash, source.owner.execution, source.owner.pin)
            and source.active
            and source.owner.inline()
        )

    def _call(self, source: _Source, action: ExternalGatePreflight) -> NativeCall:
        supplied = action.model_dump(exclude={"action_digest"})
        return NativeCall(
            action.tool_name,
            {"source_binding_id": source.id, "action": json.loads(json.dumps(supplied, allow_nan=False))},
            tool_call_id=action.tool_call_id,
            run_id=source.owner.metadata.run_id,
            operation_id=correlation(source.id, action.request_id),
            definition="External preflight only. Consumed permission is not proof of tool execution.",
            evidence=ExecutionEvidence(False),
        )

    async def preflight(self, source: _Source, action: ExternalGatePreflight) -> ExternalGateDecision:
        async def admit() -> ExternalGateDecision:
            async with source.admission_lock:
                return await self._preflight(source, action)

        task = asyncio.create_task(admit(), name=f"external-gate-admit:{source.id}:{action.request_id}")
        try:
            return await retain_task(task)
        except asyncio.CancelledError:
            if task.done() and not task.cancelled() and task.exception() is None:
                cleanup = asyncio.create_task(self.retire(source, action.request_id, "caller_cancelled"))
                await retain_task(cleanup)
            raise

    async def _preflight(self, source: _Source, action: ExternalGatePreflight) -> ExternalGateDecision:
        existing = source.requests.get(action.request_id)
        if source.external_session_id is not None and action.session_id != source.external_session_id:
            if existing is not None:
                await self.retire(source, action.request_id, "source_mutated")
            raise StateConflictError("External hook session differs from its bound source")
        call = self._call(source, action)
        if existing is not None:
            if existing.binding.action(call).action_digest != existing.digest:
                await self.retire(source, action.request_id, "action_mutated")
                raise StateConflictError("External gate action changed")
            return await self.status(source, action.request_id)
        async with self.host._execution_control.hold(source.owner.execution.job_id):
            if not await self._current(source):
                raise StateConflictError("External gate owner is no longer live")
            metadata = source.owner.metadata.model_copy(
                update={
                    "source_framework": "codeplane",
                    "namespace": ("control", "external-gate"),
                    "correlation_id": correlation(source.id, action.request_id),
                }
            )
            pending: _Request
            policy_snapshot = source.owner.policy._policy

            async def current() -> bool:
                return (
                    pending.active
                    and source.owner.policy._policy is policy_snapshot
                    and await self._current(source)
                    and pending.active
                    and source.owner.policy._policy is policy_snapshot
                )

            binding = NativeBinding(
                family="external-gate",
                execution=source.owner.execution,
                policy=source.owner.policy,
                metadata=metadata,
                workspace=source.owner.workspace,
                protected_paths=source.owner.protected_paths,
                is_current=current,
                is_current_inline=lambda: (
                    pending.active
                    and source.active
                    and source.owner.inline()
                    and source.owner.policy._policy is policy_snapshot
                ),
            )
            digest = binding.action(call).action_digest
            state = await self.repo.requested(source.id, action.request_id, digest, source.owner.execution, metadata)
            if state.phase != ExternalGatePhase.pending:
                return state
            if not await self._current(source):
                state, _ = await self.repo.finish(
                    source.id,
                    action.request_id,
                    consume=False,
                    digest=digest,
                    reason="owner_unavailable",
                    metadata=metadata,
                )
                return state
            pending = _Request(action.model_copy(deep=True), call, digest, binding, metadata)
            if source.owner.native_binding is not None:
                pending.hold = source.owner.native_binding.begin_work(None)
            source.requests[action.request_id] = pending
            pending.task = asyncio.create_task(self._decide(source, pending), name=f"external-gate:{call.operation_id}")
            return state

    async def _decide(self, source: _Source, pending: _Request) -> None:
        try:
            allowed = await pending.binding.authorize(pending.call, lambda: source.active and pending.active)
            async with self.host._execution_control.hold(source.owner.execution.job_id):
                allowed = allowed and pending.active and await self._current(source)
                await self.repo.decided(source.id, pending.action.request_id, allowed, pending.metadata)
            if not allowed:
                pending.active = False
                self._release(source, pending)
        except BaseException:
            pending.active = False
            pending.binding.revoke()
            await self.repo.finish(
                source.id,
                pending.action.request_id,
                consume=False,
                digest=pending.digest,
                reason="decision_failed",
                metadata=pending.metadata,
            )
            raise
        finally:
            if not pending.active:
                self._release(source, pending)
            pending.done.set()

    def _release(self, source: _Source, pending: _Request) -> None:
        if pending.hold is not None and source.owner.native_binding is not None:
            source.owner.native_binding.end_work(pending.hold)
            pending.hold = None

    async def status(self, source: _Source, request_id: str) -> ExternalGateDecision:
        pending = source.requests.get(request_id)
        if not await self._current(source) or (
            pending is not None and pending.active and not await pending.binding.current()
        ):
            await self.retire(source, request_id, "owner_unavailable")
        if pending is not None and pending.task is not None and pending.task.done() and not pending.task.cancelled():
            pending.task.result()
        return await self.repo.state(source.id, request_id)

    async def wait(self, source: _Source, request_id: str, seconds: float = 25) -> ExternalGateDecision:
        pending = source.requests.get(request_id)
        if pending is None:
            return await self.status(source, request_id)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(pending.done.wait(), timeout=seconds)
        return await self.status(source, request_id)

    async def consume(self, source: _Source, request_id: str, action: ExternalGateConsume) -> ExternalGateDecision:
        pending = source.requests.get(request_id)
        if pending is None:
            raise StateConflictError("External gate request has no retained owner")
        changed = (
            action.request_id != request_id
            or action.action_digest != pending.digest
            or pending.binding.action(self._call(source, action)).action_digest != pending.digest
        )
        if changed:
            return await self.retire(source, request_id, "action_mutated")
        async with self.host._execution_control.hold(source.owner.execution.job_id):
            previous = await self.repo.state(source.id, request_id)
            if previous.phase != ExternalGatePhase.allowed:
                return previous
            if not await self._current(source) or not await pending.binding.entry_current(pending.call):
                consume = False
            else:
                consume = True
            state, inserted = await self.repo.finish(
                source.id,
                request_id,
                consume=consume,
                digest=pending.digest,
                reason="single_use" if consume else "owner_unavailable",
                metadata=pending.metadata,
            )
            allowed = consume and inserted and await self._current(source)
            if inserted:
                pending.active = False
                pending.binding.revoke()
                await pending.binding.report_unfinished()
                self._release(source, pending)
        if not consume:
            return await self.retire(source, request_id, "owner_unavailable")
        return state.model_copy(update={"decision": "allow" if allowed else "deny"})

    async def retire(self, source: _Source, request_id: str, reason: str) -> ExternalGateDecision:
        pending = source.requests.get(request_id)
        if pending is not None:
            pending.active = False
            pending.binding.revoke()
        task = asyncio.create_task(
            self._retire(source, request_id, reason), name=f"external-gate-request-retire:{request_id}"
        )
        return await retain_task(task)

    async def _retire(self, source: _Source, request_id: str, reason: str) -> ExternalGateDecision:
        pending = source.requests.get(request_id)
        if pending is not None:
            pending.active = False
            pending.binding.revoke()
        metadata = (
            pending.metadata
            if pending
            else source.owner.metadata.model_copy(
                update={
                    "source_framework": "codeplane",
                    "namespace": ("control", "external-gate"),
                    "correlation_id": correlation(source.id, request_id),
                }
            )
        )
        errors: list[Exception] = []
        try:
            state = await self.repo.state(source.id, request_id)
            await self.repo.finish(
                source.id, request_id, consume=False, digest=state.action_digest, reason=reason, metadata=metadata
            )
        except Exception as exc:
            errors.append(exc)
        try:
            if pending is not None and pending.task is not None:
                results = await asyncio.gather(pending.task, return_exceptions=True)
                errors.extend(value for value in results if isinstance(value, Exception))
            if self.host._approval_service is not None:
                for approval_id in await self.repo.pending_approvals(source.id, request_id):
                    try:
                        await self.host._approval_service.resolve(approval_id, ApprovalResolution.rejected, reason)
                    except ApprovalAlreadyResolvedError:
                        log.info("external_gate_approval_already_resolved", approval_id=approval_id)
            if pending is not None:
                await pending.binding.report_unfinished()
        except Exception as exc:
            errors.append(exc)
        finally:
            if pending is not None:
                pending.done.set()
                self._release(source, pending)
        if errors:
            raise ExceptionGroup("External gate request requires reconciliation", errors)
        return await self.repo.state(source.id, request_id)

    async def before_call(self, source_id: str, credential: str, action: ExternalGatePreflight) -> ExternalGateDecision:
        source = await self.authenticate(source_id, f"Bearer {credential}")
        state = await self.preflight(source, action)
        try:
            while state.phase == ExternalGatePhase.pending:
                state = await self.wait(source, action.request_id)
            if state.phase != ExternalGatePhase.allowed:
                return state
            return await self.consume(
                source,
                action.request_id,
                ExternalGateConsume(
                    **action.model_dump(),
                    action_digest=state.action_digest,
                ),
            )
        except BaseException:
            cleanup = asyncio.create_task(self.retire(source, action.request_id, "caller_cancelled"))
            await retain_task(cleanup)
            raise

    def before_call_sync(self, source_id: str, credential: str, action: ExternalGatePreflight) -> ExternalGateDecision:
        if self._loop is None or threading.get_ident() == self._thread:
            raise RuntimeError("Synchronous external callbacks require an owning worker, not the server event loop")
        return asyncio.run_coroutine_threadsafe(self.before_call(source_id, credential, action), self._loop).result()

    async def reported(self, source: _Source, request_id: str, report: ExternalGateReport) -> None:
        state = await self.repo.state(source.id, request_id)
        if state.phase != ExternalGatePhase.consumed or not await self._current(source):
            raise StateConflictError("Execution reports require the same live source and a consumed decision")
        event = new_event(
            source.owner.execution.job_id,
            "external.gate.execution_reported",
            {
                "source_id": source.id,
                "request_id": request_id,
                "action_digest": state.action_digest,
                "phase": report.phase,
                "reported": report.details,
            },
            event_id=f"{correlation(source.id, request_id)}:report:{report.report_id}",
            raw_event=report.model_dump(mode="json"),
            metadata=source.owner.metadata.model_copy(
                update={
                    "source_framework": "external-gate",
                    "integration_binding_id": source.id,
                    "namespace": ("observation-source", "external-gate"),
                }
            ),
        )
        await self.repo.report(event)
        await self.host._event_bus.publish(event)

    async def _watch(self, source: _Source) -> None:
        ended = asyncio.Event()

        def finished(task: asyncio.Task[Any]) -> None:
            ended.set()

        source.owner.task.add_done_callback(finished)
        done = asyncio.create_task(ended.wait())
        closed = asyncio.create_task(source.closed.wait())
        revoked = (
            asyncio.create_task(source.owner.native_binding.wait_revoked()) if source.owner.native_binding else None
        )
        try:
            await asyncio.wait(
                (done, closed, revoked) if revoked else (done, closed), return_when=asyncio.FIRST_COMPLETED
            )
            await self.close_source(source, "execution_ended")
        finally:
            source.owner.task.remove_done_callback(finished)
            done.cancel()
            closed.cancel()
            if revoked is not None:
                revoked.cancel()
            await asyncio.gather(
                *(item for item in (done, closed, revoked) if item is not None), return_exceptions=True
            )

    def revoke_job(self, job_id: str) -> None:
        for source in tuple(self._sources.values()):
            if source.owner.execution.job_id == job_id:
                source.active = False
                for pending in source.requests.values():
                    pending.active = False
                    pending.binding.revoke()

    def revoke_all(self) -> None:
        for source in tuple(self._sources.values()):
            self.revoke_job(source.owner.execution.job_id)

    async def retire_job(self, job_id: str) -> None:
        self.revoke_job(job_id)
        await asyncio.gather(
            *(
                self.close_source(source, "execution_cancelled")
                for source in tuple(self._sources.values())
                if source.owner.execution.job_id == job_id
            )
        )

    async def close_source(self, source: _Source, reason: str) -> None:
        source.active = False
        source.closed.set()
        for pending in source.requests.values():
            pending.active = False
            pending.binding.revoke()
        if source.close_task is None:

            async def close() -> None:
                try:
                    results = await asyncio.gather(
                        *(self.retire(source, request_id, reason) for request_id in tuple(source.requests)),
                        return_exceptions=True,
                    )
                    errors = [value for value in results if isinstance(value, Exception)]
                    if errors:
                        raise ExceptionGroup("External gate retirement failed", errors)
                finally:
                    await self.repo.disable(source.id)
                    if self._sources.get(source.id) is source:
                        self._sources.pop(source.id)

            source.close_task = asyncio.create_task(close(), name=f"external-gate-retire:{source.id}")
        await retain_task(source.close_task)

    async def close(self) -> None:
        sources = tuple(self._sources.values())
        for source in sources:
            self.revoke_job(source.owner.execution.job_id)
        results = await asyncio.gather(
            *(self.close_source(source, "shutdown") for source in sources), return_exceptions=True
        )
        if self._watchers:
            await asyncio.gather(*tuple(self._watchers), return_exceptions=True)
        errors = [value for value in results if isinstance(value, Exception)]
        if errors:
            raise ExceptionGroup("External gates require reconciliation", errors)

    async def reconcile_stale(self) -> None:
        if self._reconciled:
            return
        for source_id, job_id, request_ids in await self.repo.stale_sources(self.host.execution_epoch):
            for request_id in request_ids:
                state = await self.repo.state(source_id, request_id)
                await self.repo.finish(
                    source_id,
                    request_id,
                    consume=False,
                    digest=state.action_digest,
                    reason="stale_epoch",
                    metadata=EventMetadata(source_framework="codeplane", namespace=("control", "external-gate")),
                )
                if self.host._approval_service is not None:
                    for approval_id in await self.repo.pending_approvals(source_id, request_id):
                        try:
                            await self.host._approval_service.resolve(
                                approval_id, ApprovalResolution.rejected, "stale_epoch"
                            )
                        except ApprovalAlreadyResolvedError:
                            log.info("external_gate_stale_approval_resolved", job_id=job_id, approval_id=approval_id)
            await self.repo.disable(source_id)
        self._reconciled = True
