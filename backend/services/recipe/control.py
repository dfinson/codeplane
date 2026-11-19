"""Strict selected-attempt message admission, never implicit primary execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.models.domain import ApprovalResolution, StateConflictError
from backend.models.events import EventKind, EventMetadata, new_event
from backend.models.schemas.recipes import (
    RecipeAttemptControl,
    RecipeControlAction,
    RecipeDecisionReceipt,
    RecipeGateDecisionRequest,
    RecipeMessageReceipt,
    RecipeOperationReceipt,
    RecipeReconcileRequest,
)
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.adapters.integration_policy import IntegrationDisabledError, IntegrationPolicy
from backend.services.recipe.validation import value_matches
from backend.services.runtime.attempt_bindings import AttemptBindingConflictError

if TYPE_CHECKING:
    from backend.models.schemas.recipes import RecipeMessageRequest
    from backend.services.recipe.inspection import RecipeInspectionService
    from backend.services.runtime.service import RuntimeService


class RecipeControlService:
    def __init__(self, inspection: RecipeInspectionService, runtime: RuntimeService) -> None:
        self._inspection = inspection
        self._runtime = runtime

    async def context(self, job_id: str, run_id: str, step_id: str, attempt_id: str) -> RecipeAttemptControl:
        async with self._runtime._execution_control.hold(job_id):
            return await self._context(job_id, run_id, step_id, attempt_id)

    async def _context(self, job_id: str, run_id: str, step_id: str, attempt_id: str) -> RecipeAttemptControl:
        inspection = self._inspection
        run = await inspection.run(job_id, run_id)
        step = await inspection._step(job_id, run_id, step_id)
        attempt = (await inspection.attempt(job_id, run_id, step_id, attempt_id)).attempt
        current = await inspection.current(job_id)
        reason = None
        binding = self._runtime._attempt_bindings.get(job_id, attempt_id)
        controller = self._runtime._recipe_controllers.get(job_id)
        owned = controller.owned_attempt(attempt_id) if controller else None
        can_stop = (
            owned is not None
            and owned.run_id == run_id
            and owned.step_id == step_id
            and step.current_attempt_id == attempt_id
            and run.resolved_at is None
            and attempt.result is None
            and attempt.runner.kind in {"agent", "command"}
            and run.cancel_requested_at is None
        )
        can_resume = (
            controller is not None and owned is None and await inspection._runs.safe_resume_available(attempt_id)
        )
        resume_reason = "Resume requires an unfinished attempt with a confirmed-safe stop and an active Job controller."
        if can_resume:
            try:
                await self._runtime._require_attempt_job(job_id, attempt_id)
                if attempt.runner.kind == "agent":
                    IntegrationPolicy(self._runtime._config).require_enabled(
                        attempt.runner.provider or self._runtime._config.runtime.default_sdk
                    )
            except (AttemptBindingConflictError, IntegrationDisabledError) as exc:
                can_resume = False
                resume_reason = str(exc)
        if current.run is None or current.run.id != run_id or run.resolved_at is not None:
            reason = "This is a historical run. Use explicit Job Resume for new work."
        elif step.current_attempt_id != attempt_id or attempt.result is not None:
            reason = "This attempt is no longer the current unfinished execution."
        elif run.cancel_requested_at is not None:
            reason = "The recipe run is canceled. No instruction will be dispatched."
        elif attempt.runner.kind != "agent":
            reason = f"A {attempt.runner.kind} step has no agent message channel."
        elif step.state != "running":
            reason = f"The selected step is {step.state.replace('_', ' ')}."
        elif binding is None or binding.cancel_requested:
            reason = "No live execution is owned for this attempt."
        elif not attempt.executions or (
            attempt.executions[-1].execution_token != binding.token
            or attempt.executions[-1].session_id != binding.session_id
        ):
            reason = "The live execution has not completed durable attempt admission."
        else:
            try:
                await self._runtime._require_attempt_job(job_id, attempt_id)
                IntegrationPolicy(self._runtime._config).require_enabled(binding.sdk)
            except (AttemptBindingConflictError, IntegrationDisabledError) as exc:
                reason = str(exc)
        return RecipeAttemptControl(
            job_id=job_id,
            run_id=run_id,
            step_instance_id=step_id,
            attempt_id=attempt_id,
            can_message=reason is None,
            message_disabled_reason=reason,
            execution_token=binding.token if binding and not binding.cancel_requested else None,
            native_session_id=binding.session_id if binding else None,
            execution_epoch=self._runtime.execution_epoch,
            stop=RecipeControlAction(
                enabled=can_stop,
                disabled_reason=None if can_stop else "No current owned agent or command execution can be stopped.",
                expected_token=owned.token if can_stop and owned else None,
            ),
            resume=RecipeControlAction(
                enabled=can_resume,
                disabled_reason=None if can_resume else resume_reason,
                expected_version=step.version,
            ),
            reconcile=RecipeControlAction(
                enabled=(
                    owned is None
                    and binding is None
                    and step.current_attempt_id == attempt_id
                    and step.state in {"interrupted", "canceled"}
                    and run.resolved_at is None
                ),
                disabled_reason="Reconciliation requires a stopped current attempt with no live execution owner.",
                expected_version=step.version,
            ),
        )

    async def message(
        self, job_id: str, run_id: str, step_id: str, attempt_id: str, request: RecipeMessageRequest
    ) -> RecipeMessageReceipt:
        if not request.content.strip():
            raise StateConflictError("An operator instruction must not be blank.")
        async with self._runtime._execution_control.hold(job_id):
            context = await self._context(job_id, run_id, step_id, attempt_id)
            if not context.can_message or context.execution_token != request.expected_execution:
                return RecipeMessageReceipt(
                    job_id=job_id,
                    run_id=run_id,
                    step_instance_id=step_id,
                    attempt_id=attempt_id,
                    execution_token=context.execution_token,
                    status="conflict",
                    reason=context.message_disabled_reason
                    or "The selected execution was replaced. Refresh its identity.",
                )
            result = await self._runtime.dispatch_current_execution(
                job_id, request.expected_execution, request.content, attempt_id=attempt_id
            )
        return RecipeMessageReceipt.model_validate(
            {
                "job_id": job_id,
                "run_id": run_id,
                "step_instance_id": step_id,
                "attempt_id": attempt_id,
                "execution_token": result.execution_token,
                "status": result.status,
                "reason": result.reason,
                "message_event_id": result.message_event_id,
            }
        )

    async def stop(
        self, job_id: str, run_id: str, step_id: str, attempt_id: str, expected_token: str
    ) -> RecipeOperationReceipt:
        host = self._runtime
        async with host._execution_control.hold(job_id):
            context = await self._context(job_id, run_id, step_id, attempt_id)
            controller = host._recipe_controllers.get(job_id)
            if (
                controller is None
                or context.stop is None
                or not context.stop.enabled
                or context.stop.expected_token != expected_token
            ):
                return RecipeOperationReceipt(
                    attempt_id=attempt_id,
                    status="conflict",
                    reason="The selected stop target is no longer current.",
                )
            async with serialized_write(host._session_factory) as session:
                await EventRepository(session).append(
                    new_event(
                        job_id,
                        "recipe.attempt.stop_requested",
                        {"attempt_id": attempt_id, "control_token": expected_token},
                        metadata=EventMetadata(
                            source_framework="codeplane.recipe",
                            recipe_run_id=run_id,
                            step_instance_id=step_id,
                            attempt_id=attempt_id,
                            execution_epoch=host.execution_epoch,
                        ),
                    )
                )
            task = controller.request_attempt_stop(attempt_id, expected_token)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=35)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
            return RecipeOperationReceipt(
                attempt_id=attempt_id,
                status="uncertain",
                reason="The execution owner was canceled before a stop result was confirmed.",
                requires_reconciliation=True,
            )
        except TimeoutError:
            return RecipeOperationReceipt(
                attempt_id=attempt_id,
                status="uncertain",
                reason="Stop was requested, but owned runner teardown is not confirmed.",
                requires_reconciliation=True,
            )
        async with host._session_factory() as session:
            repository = RecipeRunRepository(session)
            attempt = await repository.get_attempt(attempt_id)
            safe = await repository.safe_resume_available(attempt_id)
        unresolved = not safe and (
            attempt is None or attempt.result is None or getattr(attempt.result.evidence, "uncertain", False)
        )
        return RecipeOperationReceipt(
            attempt_id=attempt_id,
            status="stopped",
            reason="Selected execution stopped. Its recorded outcome and effect certainty remain separate.",
            requires_reconciliation=unresolved,
        )

    async def resume(
        self, job_id: str, run_id: str, step_id: str, attempt_id: str, expected_version: int
    ) -> RecipeOperationReceipt:
        host = self._runtime
        async with host._execution_control.hold(job_id):
            context = await self._context(job_id, run_id, step_id, attempt_id)
            controller = host._recipe_controllers.get(job_id)
            if (
                controller is None
                or context.resume is None
                or not context.resume.enabled
                or context.resume.expected_version != expected_version
            ):
                return RecipeOperationReceipt(
                    attempt_id=attempt_id,
                    status="conflict",
                    reason="The selected attempt is not safely resumable.",
                )
            async with serialized_write(host._session_factory) as session:
                repository = RecipeRunRepository(session)
                step = await repository.get_step(step_id)
                if step is None or step.version != expected_version or step.current_attempt_id != attempt_id:
                    raise StateConflictError("The selected attempt changed before Resume.")
                await repository.resume_interrupted(step, explicit=True)
            controller.wake_resumed(step_id)
        await host._event_bus.publish(
            new_event(
                job_id,
                EventKind.recipe_updated,
                {"run_id": run_id, "step_instance_id": step_id},
                metadata=EventMetadata(
                    source_framework="codeplane.recipe", recipe_run_id=run_id, step_instance_id=step_id
                ),
            )
        )
        return RecipeOperationReceipt(
            attempt_id=attempt_id,
            status="resumed",
            reason="The unfinished attempt was explicitly readmitted without creating another attempt.",
        )

    async def reconcile(
        self, job_id: str, run_id: str, step_id: str, attempt_id: str, request: RecipeReconcileRequest
    ) -> RecipeOperationReceipt:
        host = self._runtime
        if not request.evidence.strip():
            raise StateConflictError("Describe how the stopped effects were inspected and reconciled.")
        async with host._execution_control.hold(job_id):
            context = await self._context(job_id, run_id, step_id, attempt_id)
            if (
                context.reconcile is None
                or not context.reconcile.enabled
                or context.reconcile.expected_version != request.expected_version
            ):
                return RecipeOperationReceipt(
                    attempt_id=attempt_id, status="conflict", reason="The reconciliation target changed."
                )
            attempt = (await self._inspection.attempt(job_id, run_id, step_id, attempt_id)).attempt
            git = host._git_service
            if git is None or request.code.workspace != attempt.workspace.path:
                raise StateConflictError("Reconciliation requires the exact attempt workspace and current code.")
            captured = await git.capture_worktree_commit(
                cwd=attempt.workspace.path,
                snapshot_ref=f"refs/codeplane/recipe-inputs/reconcile-{uuid4().hex}",
            )
            if await git.rev_parse(f"{captured}^{{tree}}", cwd=attempt.workspace.path) != await git.rev_parse(
                f"{request.code.commit}^{{tree}}",
                cwd=attempt.workspace.path,
            ):
                raise StateConflictError("Current code differs from the reconciliation evidence.")
            async with serialized_write(host._session_factory) as session:
                repository = RecipeRunRepository(session)
                step = await repository.get_step(step_id)
                if step is None or step.current_attempt_id != attempt_id or step.version != request.expected_version:
                    raise StateConflictError("The selected attempt changed before reconciliation.")
                await repository.reconcile_stopped(
                    step,
                    evidence=request.evidence,
                    code=request.code,
                    execution_epoch=host.execution_epoch,
                )
            controller = host._recipe_controllers.get(job_id)
            if controller is not None:
                controller.wake_resumed(step_id)
        await host._event_bus.publish(
            new_event(
                job_id,
                EventKind.recipe_updated,
                {"run_id": run_id, "step_instance_id": step_id},
                metadata=EventMetadata(
                    source_framework="codeplane.recipe", recipe_run_id=run_id, step_instance_id=step_id
                ),
            )
        )
        return RecipeOperationReceipt(
            attempt_id=attempt_id,
            status="reconciled",
            reason=(
                "Stopped effects reconciled. Original results remain immutable; "
                "no success or retry approval was granted."
            ),
        )

    async def decide(
        self, job_id: str, run_id: str, step_id: str, attempt_id: str, request: RecipeGateDecisionRequest
    ) -> RecipeOperationReceipt:
        host = self._runtime
        async with host._execution_control.hold(job_id):
            run = await self._inspection.run(job_id, run_id)
            step = await self._inspection._step(job_id, run_id, step_id)
            detail = await self._inspection.attempt(job_id, run_id, step_id, attempt_id)
            attempt, gate = detail.attempt, detail.gate
            job = await host._get_job(job_id)
            if (
                job is None
                or job.state not in {"running", "waiting_for_approval"}
                or run.resolved_at is not None
                or run.cancel_requested_at is not None
                or step.version != request.expected_version
                or step.current_attempt_id != attempt_id
                or step.state != "needs_decision"
                or attempt.result is not None
                or attempt.runner.kind != "human"
                or request.decision not in attempt.runner.decisions
                or gate is None
                or gate.resolution is not None
                or gate.binding.approval_id != request.approval_id
                or gate.binding.input_checksum != request.input_checksum
            ):
                return RecipeOperationReceipt(
                    attempt_id=attempt_id,
                    status="conflict",
                    reason="The exact gate proposal or decision target changed.",
                )
            if set(request.outputs) != set(step.definition.outputs) or any(
                not value_matches(value, step.definition.outputs[name].type) for name, value in request.outputs.items()
            ):
                raise StateConflictError("Decision outputs must match the pinned output names and types.")
            if host._approval_service is None:
                raise StateConflictError("Canonical approval decisions are unavailable.")
            receipt = RecipeDecisionReceipt(
                attempt_id=attempt_id,
                input_checksum=request.input_checksum,
                outputs=request.outputs,
                notes=request.notes,
            )
            await host._approval_service.resolve(
                request.approval_id,
                ApprovalResolution(request.decision),
                notes=receipt.model_dump_json(),
            )
        return RecipeOperationReceipt(
            attempt_id=attempt_id,
            status="decided",
            reason="The exact proposal decision was recorded. It does not approve any other attempt or delivery.",
        )
