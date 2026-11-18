"""Native invocation inside an existing Runtime task, not another scheduler."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

from backend.config import resolve_protected_paths
from backend.models.domain import ApprovalAlreadyResolvedError, ApprovalResolution, JobState, StateConflictError
from backend.persistence.database import serialized_write
from backend.persistence.job_repo import JobRepository
from backend.persistence.native_execution_repo import NativeExecutionRepository
from backend.persistence.policy_repo import PolicyRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.action_policy.classifier import Preset, RepoPolicy
from backend.services.action_policy.standalone_router import StandalonePolicyRouter
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.integration_policy import IntegrationDisabledError
from backend.services.adapters.native_gates import NativeBinding, NativeGates
from backend.services.adapters.native_gates.readiness import require_ready
from backend.services.adapters.native_gates.usage import result_usage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.models.execution_contracts import ExecutionContractPin
    from backend.models.session_event import EventMetadata
    from backend.services.adapters.native_gates.binding import NativeCall
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


class NativeFamily(StrEnum):
    crewai = "crewai"
    langchain = "langchain"
    langgraph = "langgraph"
    semantic_kernel = "semantic_kernel"
    maf = "maf"
    smolagents = "smolagents"
    pydantic_ai = "pydantic_ai"
    openai_agents = "openai_agents"


@dataclass
class NativeExecution:
    identity: ExecutionIdentity
    pin: ExecutionContractPin
    metadata: EventMetadata
    gates: NativeGates
    owner: asyncio.Task[Any]
    target: object
    cancel_requested: bool = False


class NativeExecutions:
    def __init__(self, host: RuntimeService) -> None:
        self.host = host
        self._live: dict[str, NativeExecution] = {}
        self._repository = NativeExecutionRepository(host._session_factory)

    def for_job(self, job_id: str) -> tuple[NativeExecution, ...]:
        return tuple(item for item in self._live.values() if item.identity.job_id == job_id)

    async def start[T](
        self,
        job_id: str,
        *,
        family: NativeFamily,
        target: object,
        invoke: Callable[[], T | Awaitable[T]],
        synchronous: bool,
    ) -> asyncio.Task[T]:
        """Internal composition entry: claim a queued Job and create its real task.

        A caller must already have persisted the Job and its legacy declaration
        through JobService. No public API accepts Python objects or code.
        """
        host = self.host
        family = NativeFamily(family)
        if host._config.runtime.copilot_only:
            raise IntegrationDisabledError("Native framework execution is disabled by runtime.copilot_only")
        if host._standalone_governance is None or host._execution_contracts is None:
            raise StateConflictError("Native admission requires standalone governance composition")
        require_ready(family.value)
        starting = asyncio.current_task()
        if starting is None:
            raise StateConflictError("Native admission requires an owning caller task")
        async with host._execution_control.hold(job_id):
            if host._shutting_down or job_id in host._tasks or job_id in host._starting_tasks:
                raise StateConflictError("Job already has an execution owner or Runtime is shutting down")
            host._starting_tasks[job_id] = starting
        reserved = claimed = False
        try:
            await host._recipe_runner_slots.acquire()
            reserved = True
            async with host._execution_control.hold(job_id):
                if host._config.runtime.copilot_only or host._shutting_down or starting.cancelling():
                    raise StateConflictError("Native admission was revoked while waiting for capacity")
                async with serialized_write(host._session_factory) as session:
                    job = await JobRepository(session).get(job_id)
                    if job is None or job.observation_only or job.state != JobState.queued:
                        raise StateConflictError("Native start requires a queued, prepared execution Job")
                    if await RecipeRunRepository(session).get_for_job(job_id) is not None:
                        raise StateConflictError("Recipe invocations must use their actual RecipeExecutor task")
                    if not await JobRepository(session).claim_for_start(job_id):
                        raise StateConflictError("Native Job start claim was lost")
                    claimed = True
                await host._prepare_legacy_execution_contract(job_id)
                pin = host._job_contract_pins[job_id]
                async with host._session_factory() as session:
                    stored_policy = await PolicyRepository(session).get_config()
                policy = RepoPolicy(
                    preset=Preset(job.preset or "supervised"),
                    batch_window_seconds=stored_policy["batch_window_seconds"],
                    mcp_configs={},
                )
                host._policy_routers[job_id] = host._standalone_governance.for_job(pin, policy)
                host._repo_policies[job_id] = policy
                await host._publish_state_event(job_id, JobState.queued, JobState.running)

                async def run() -> T:
                    try:
                        result = await self.invoke(
                            job_id,
                            family=family,
                            target=target,
                            invoke=invoke,
                            synchronous=synchronous,
                            attempt_id=None,
                        )
                        async with host._session_factory() as session:
                            await host._make_job_service(session).transition_state(job_id, JobState.review)
                            await session.commit()
                        await host._publish_state_event(job_id, JobState.running, JobState.review)
                        return result
                    except asyncio.CancelledError:
                        if not host._shutting_down:
                            await host._cancel_safety_net(job_id)
                        raise
                    except Exception:
                        log.exception("native_job_failed", job_id=job_id, family=family.value)
                        await host._fail_job(job_id, "Native invocation failed")
                        raise
                    finally:
                        await host._cleanup_job_state(job_id)

                task = asyncio.create_task(run(), name=f"job-{job_id}")
                host._tasks[job_id] = task
                host._legacy_runner_slots.add(job_id)
                reserved = False
                return task
        except BaseException:
            if claimed:
                await host._fail_job(job_id, "Native execution could not be admitted")
                await host._cleanup_job_state(job_id)
            raise
        finally:
            if host._starting_tasks.get(job_id) is starting:
                host._starting_tasks.pop(job_id, None)
            if reserved:
                host._recipe_runner_slots.release()

    async def _retire_approvals(self, execution: NativeExecution) -> None:
        approvals = self.host._approval_service
        if approvals is None:
            return
        async with self.host._execution_control.hold(execution.identity.job_id):
            for approval in await approvals.list_pending(execution.identity.job_id):
                try:
                    proposed = json.loads(approval.proposed_action or "")
                except (ValueError, TypeError):
                    continue
                if (
                    not isinstance(proposed, dict)
                    or proposed.get("kind") != "policy_action"
                    or proposed.get("execution") != asdict(execution.identity)
                    or proposed.get("admission_id") != execution.pin.admission_id
                ):
                    continue
                try:
                    await approvals.resolve(
                        approval.id, ApprovalResolution.rejected, "The exact native execution is no longer live"
                    )
                except ApprovalAlreadyResolvedError:
                    log.info("native_approval_already_resolved", approval_id=approval.id)

    async def cancel_job(self, job_id: str) -> bool:
        executions = self.for_job(job_id)
        # Fence every binding before the first await.
        for execution in executions:
            execution.cancel_requested = True
            execution.gates.cancel()
        for execution in executions:
            await self._retire_approvals(execution)
            await self._repository.record(
                execution.identity,
                execution.pin,
                execution.gates.binding.family,
                "cancel_requested",
                execution.metadata,
            )
        return bool(executions)

    async def detach(self, identity: ExecutionIdentity) -> str:
        execution = self._live.get(identity.execution_token)
        if execution is None or execution.identity != identity:
            raise StateConflictError("Native binding has ended or been replaced")
        execution.cancel_requested = True
        execution.gates.cancel()
        await self._retire_approvals(execution)
        await self._repository.record(
            identity, execution.pin, execution.gates.binding.family, "cancel_requested", execution.metadata
        )
        return "cancel-requested"

    async def invoke[T](
        self,
        job_id: str,
        *,
        family: NativeFamily,
        target: object,
        invoke: Callable[[], T | Awaitable[T]],
        synchronous: bool,
        attempt_id: str | None,
    ) -> T:
        host = self.host
        family = NativeFamily(family)
        if host._config.runtime.copilot_only:
            raise IntegrationDisabledError("Native framework execution is disabled by runtime.copilot_only")
        owner = asyncio.current_task()
        controller = host._recipe_controllers.get(job_id)
        owned = controller.owned_attempt(attempt_id) if controller is not None and attempt_id is not None else None
        expected_owner = owned.task if owned is not None else host._tasks.get(job_id) if attempt_id is None else None
        if owner is None or owner is not expected_owner or owner.cancelling():
            raise StateConflictError("Native invocation must run inside the actual owning Runtime task")
        if any(item.target is target for item in self._live.values()):
            raise StateConflictError("The native object is already executing under another binding")
        pin = host._recipe_contract_pins.get(job_id) or host._job_contract_pins.get(job_id)
        router = host._policy_routers.get(job_id)
        if (
            pin is None
            or host._execution_contracts is None
            or host._approval_service is None
            or not isinstance(router, StandalonePolicyRouter)
            or router.pin != pin
        ):
            raise StateConflictError("Native invocation requires the existing admission-pinned standalone Job policy")
        host._execution_contracts.require_available(pin)
        run_id, step_id = (owned.run_id, owned.step_id) if owned is not None else (None, None)
        admission = await self._repository.admission(pin, attempt_id=attempt_id, run_id=run_id, step_id=step_id)
        identity = ExecutionIdentity(job_id, f"native:{uuid.uuid4().hex}", host.execution_epoch, uuid.uuid4().hex)
        metadata = admission.metadata.model_copy(
            update={
                "source_framework": family.value,
                "execution_epoch": identity.epoch,
                "execution_token": identity.execution_token,
            }
        )
        execution: NativeExecution

        def locally_current() -> bool:
            if (
                self._live.get(identity.execution_token) is not execution
                or execution.cancel_requested
                or owner.done()
                or owner.cancelling()
                or host._shutting_down
                or host._config.runtime.copilot_only
                or host._active_gates.get(job_id)
                or job_id in host._waiting_for_approval
                or host._policy_routers.get(job_id) is not router
                or (host._recipe_contract_pins.get(job_id) or host._job_contract_pins.get(job_id)) != pin
            ):
                return False
            if owned is not None:
                if (
                    controller is None
                    or attempt_id is None
                    or host._recipe_controllers.get(job_id) is not controller
                    or controller.owned_attempt(attempt_id) is not owned
                ):
                    return False
            elif host._tasks.get(job_id) is not owner:
                return False
            return True

        async def current() -> bool:
            if not locally_current():
                return False
            try:
                latest = await self._repository.admission(pin, attempt_id=attempt_id, run_id=run_id, step_id=step_id)
            except StateConflictError:
                return False
            return latest == admission and locally_current()

        async def report(call: NativeCall, started: bool | None, succeeded: bool | None) -> None:
            if self._live.get(identity.execution_token) is not execution:
                raise StateConflictError("Native receipt arrived after its execution was released")
            event = await self._repository.receipt(
                identity,
                pin,
                family.value,
                metadata,
                operation_id=call.operation_id,
                phase="tool_result",
                payload={"native_tool_call_id": call.tool_call_id, "started": started, "succeeded": succeeded},
            )
            await host._event_bus.publish(event)

        gates = NativeGates(
            NativeBinding(
                family=family.value,
                execution=identity,
                policy=router,
                metadata=metadata,
                workspace=admission.workspace,
                protected_paths=tuple(resolve_protected_paths(admission.repo)),
                is_current=current,
                on_result=report,
            )
        )
        execution = NativeExecution(identity, pin, metadata, gates, owner, target)
        self._live[identity.execution_token] = execution
        worker: asyncio.Task[T] | None = None
        try:
            async with host._execution_control.hold(job_id):
                if not await current():
                    raise StateConflictError("Native ownership changed before admission")
                if await host._approval_service.has_blocking_approvals(job_id, attempt_id=attempt_id):
                    raise StateConflictError("Native invocation is blocked by a pending approval")
                require_ready(family.value)
                await self._repository.record(identity, pin, family.value, "bound", metadata)
                await gates.attach(target)
            if not await current():
                raise StateConflictError("Native ownership changed before invocation")
            if synchronous:

                def call() -> T:
                    value = invoke()
                    if inspect.isawaitable(value):
                        if inspect.iscoroutine(value):
                            value.close()
                        raise TypeError("A synchronous native invocation must return a result, not an awaitable")
                    return value

                worker = asyncio.create_task(asyncio.to_thread(call), name=f"native-worker:{identity.execution_token}")
                result = await asyncio.shield(worker)
            else:
                pending = invoke()
                if not inspect.isawaitable(pending):
                    raise TypeError("An asynchronous native invocation must return an awaitable")
                result = await pending
            usage = result_usage(family.value, result, identity)
            if usage:
                event = await self._repository.receipt(
                    identity,
                    pin,
                    family.value,
                    metadata,
                    operation_id="run",
                    phase="usage",
                    payload={"usage": [amount.model_dump(mode="json") for amount in usage]},
                )
                await host._event_bus.publish(event)
            await self._repository.record(identity, pin, family.value, "completed", metadata)
            return result
        except asyncio.CancelledError:
            execution.cancel_requested = True
            gates.cancel()
            await self._retire_approvals(execution)
            await self._repository.record(identity, pin, family.value, "cancel_requested", metadata)
            # Cancelling an asyncio waiter cannot stop a running synchronous tool.
            if worker is not None:
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    try:
                        worker.result()
                    except Exception as exc:
                        log.warning("native_cancelled_worker_failed", job_id=job_id, error_type=type(exc).__name__)
            raise
        finally:
            gates.cancel()
            try:
                await self._retire_approvals(execution)
                await gates.binding.report_unfinished()
            finally:
                gates.close()
                try:
                    await self._repository.record(identity, pin, family.value, "released", metadata)
                finally:
                    self._live.pop(identity.execution_token, None)
