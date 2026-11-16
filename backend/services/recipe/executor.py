"""A Job-local recipe driver using persisted cursors, receipts and runtime slots."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import ApprovalAlreadyResolvedError, ApprovalResolution, JobState, StateConflictError
from backend.models.events import EventKind, EventMetadata, new_event
from backend.models.schemas.recipes import (
    RecipeAgentResult,
    RecipeAgentRunner,
    RecipeAttemptResult,
    RecipeBlockResult,
    RecipeBlockRunner,
    RecipeCodeReference,
    RecipeCommandResult,
    RecipeCommandRunner,
    RecipeDecisionReceipt,
    RecipeGateProposal,
    RecipeHumanResult,
    RecipeHumanRunner,
)
from backend.persistence.database import serialized_write
from backend.persistence.recipe_repo import RecipeRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.recipe.agent_runner import AgentStepRunner, InterruptedAgentError
from backend.services.recipe.command_runner import CommandStepRunner, _settle
from backend.services.recipe.definition_service import RecipeDefinitionService
from backend.services.recipe.run_service import RecipeRunService, resolve_command_arguments
from backend.services.recipe.run_state import completed_run_state, project_cursor
from backend.services.recipe.validation import content_checksum
from backend.services.recipe.workspace import prepare_workspace

if TYPE_CHECKING:
    from pydantic import JsonValue

    from backend.models.domain import Job, SessionConfig
    from backend.models.execution_contracts import ExecutionContractPin
    from backend.models.schemas.recipes import (
        RecipeAttemptResponse,
        RecipeDefinition,
        RecipeEvidence,
        RecipeRunResponse,
        RecipeState,
        RecipeStepInstanceResponse,
    )
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


class RecipeExecutor:
    def __init__(
        self,
        host: RuntimeService,
        job: Job,
        config: SessionConfig,
        *,
        contract_pin: ExecutionContractPin | None,
    ) -> None:
        self._host = host
        self._job = job
        self._config = config
        self._contract_pin = contract_pin
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._resumed_instances: set[str] = set()
        self._wake = asyncio.Event()

    async def _changed(self, run_id: str, step_id: str | None = None) -> None:
        self._wake.set()
        await self._host._event_bus.publish(
            new_event(
                self._job.id,
                EventKind.recipe_updated,
                {"run_id": run_id, "step_instance_id": step_id},
                metadata=EventMetadata(
                    source_framework="codeplane.recipe",
                    recipe_run_id=run_id,
                    step_instance_id=step_id,
                    execution_epoch=self._host.execution_epoch,
                ),
            )
        )

    async def execute(self) -> None:
        git = self._host._git_service
        if git is None:
            raise StateConflictError("Recipe execution requires the owning Job's Git service.")
        async with self._host._session_factory() as session:
            run = await RecipeRunRepository(session).get_for_job(self._job.id)
        if run is None:
            raise StateConflictError("The Job has no pinned recipe run.")
        if run.code_ref is None:
            workspace = self._job.worktree_path or self._job.repo
            code = await git.capture_worktree_commit(
                cwd=workspace,
                snapshot_ref=f"refs/codeplane/recipe-inputs/{run.id}",
            )
            async with serialized_write(self._host._session_factory) as session:
                run = await RecipeRunRepository(session).bind_code(
                    run.id,
                    RecipeCodeReference(workspace=workspace, commit=code),
                )
        try:
            if run.resolved_at is None:
                async with serialized_write(self._host._session_factory) as session:
                    self._resumed_instances = await RecipeRunRepository(session).admit_controller(run.id)
            state = run.state if run.resolved_at is not None else await self._frame(run, "", run.snapshot.definition)
            if run.resolved_at is None:
                async with serialized_write(self._host._session_factory) as session:
                    run = await RecipeRunRepository(session).resolve_run(run.id, state)
            await self._changed(run.id)
            await self._host._finalize_diff_safe(
                self._job.id, self._job.worktree_path or self._job.repo, self._job.base_ref
            )
            if state == "succeeded":
                async with serialized_write(self._host._session_factory) as session:
                    service = self._host._make_job_service(session)
                    job = await service.get_job(self._job.id)
                    if job.state in {JobState.running, JobState.waiting_for_approval}:
                        previous = job.state
                        await service.transition_state(job.id, JobState.review)
                    else:
                        previous = None
                if previous:
                    await self._host._publish_state_event(self._job.id, previous, JobState.review)
            elif state == "failed":
                await self._host._fail_job(self._job.id, run.reason or "The recipe's recorded outcome routes failed.")
        except asyncio.CancelledError:
            if not self._host._shutting_down:
                async with serialized_write(self._host._session_factory) as session:
                    await RecipeRunRepository(session).request_cancel(run.id)
            tasks = list(self._tasks.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not self._host._shutting_down:
                after = ""
                while True:
                    async with self._host._session_factory() as session:
                        repository = RecipeRunRepository(session)
                        page = await repository.list_steps(run.id, after=after)
                    for node in page:
                        if node.state not in {"running", "needs_decision"} or node.definition.runner.kind not in {
                            "human",
                            "block",
                        }:
                            continue
                        async with self._host._session_factory() as session:
                            attempt = (
                                await RecipeRunRepository(session).get_attempt(node.current_attempt_id)
                                if node.current_attempt_id
                                else None
                            )
                        if attempt is not None and attempt.result is None:
                            await self._cancel_claimed(run, node, attempt)
                    if not page:
                        break
                    after = page[-1].step_path
                async with serialized_write(self._host._session_factory) as session:
                    repository = RecipeRunRepository(session)
                    try:
                        await repository.resolve_run(run.id, "canceled", reason="Canceled by the operator.")
                    except StateConflictError as exc:
                        await repository.mark_interrupted(run.id, str(exc))
                        log.warning("recipe_cancel_unresolved", run_id=run.id, reason=str(exc))
            else:
                async with serialized_write(self._host._session_factory) as session:
                    await RecipeRunRepository(session).mark_interrupted(run.id, "Coordinator stopped during execution.")
            await self._changed(run.id)
            if not self._host._shutting_down:
                await self._host._event_bus.publish(
                    new_event(
                        self._job.id,
                        EventKind.job_canceled,
                        {"reason": "Recipe run canceled."},
                        event_id=f"recipe-canceled-{run.id}",
                    )
                )
            raise
        except Exception as exc:
            tasks = list(self._tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            async with serialized_write(self._host._session_factory) as session:
                repository = RecipeRunRepository(session)
                current = await repository.get(run.id)
                if current is not None and current.resolved_at is None:
                    try:
                        await repository.resolve_run(run.id, "failed", reason=str(exc))
                    except StateConflictError:
                        await repository.mark_interrupted(run.id, str(exc))
            raise
        finally:
            tasks = list(self._tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.clear()

    async def _frame(
        self,
        run: RecipeRunResponse,
        scope: str,
        definition: RecipeDefinition,
        parent: RecipeAttemptResponse | None = None,
    ) -> RecipeState:
        async with serialized_write(self._host._session_factory) as session:
            await RecipeRunRepository(session).ensure_scope(run.id, scope, definition)
        while True:
            if self._host._shutting_down:
                raise asyncio.CancelledError
            self._wake.clear()
            async with serialized_write(self._host._session_factory) as session:
                repository = RecipeRunRepository(session)
                current_run = await repository.get(run.id)
                if current_run is None or current_run.cancel_requested_at is not None:
                    raise asyncio.CancelledError
                instances = await repository.list_scope(run.id, scope, definition)
                attempts = {}
                for key, instance in instances.items():
                    if instance.current_attempt_id:
                        attempt = await repository.get_attempt(instance.current_attempt_id)
                        if attempt:
                            attempts[key] = attempt
                decisions = project_cursor(definition, instances, attempts)
                for decision in decisions:
                    instances[decision.step_id] = await repository.project_step(
                        instances[decision.step_id],
                        decision.state,
                        decision.reason,
                    )
                terminal = completed_run_state(definition, instances, attempts)
            if decisions:
                await self._changed(run.id)
            if terminal:
                return terminal
            for key, instance in instances.items():
                if instance.id in self._tasks:
                    continue
                attempt = attempts.get(key)
                if instance.state == "ready" or (
                    instance.state in {"running", "needs_decision", "interrupted"} and attempt is not None
                ):
                    task = asyncio.create_task(
                        self._step(run, instance, attempt, parent),
                        name=f"recipe-step-{instance.id}",
                    )
                    self._tasks[instance.id] = task
            active = {
                instance.id: self._tasks[instance.id] for instance in instances.values() if instance.id in self._tasks
            }
            if not active:
                raise StateConflictError("The recipe has no runnable node or recorded terminal outcome.")
            wake = asyncio.create_task(self._wake.wait())
            try:
                done, _ = await asyncio.wait([*active.values(), wake], return_when=asyncio.FIRST_COMPLETED)
                for key, task in active.items():
                    if task in done:
                        self._tasks.pop(key, None)
                        task.result()
            finally:
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=True)

    async def _admit(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        parent: RecipeAttemptResponse | None,
    ) -> RecipeAttemptResponse:
        git = self._host._git_service
        assert git is not None
        async with self._host._session_factory() as session:
            repository = RecipeRunRepository(session)
            service = RecipeRunService(RecipeDefinitionService(RecipeRepository(session)), repository)
            inputs = await service.resolve_inputs(instance)
        workspace = await prepare_workspace(
            git,
            self._job,
            run,
            instance,
            inputs,
            parent_workspace=parent.workspace if parent else None,
        )
        runner = instance.definition.runner
        if isinstance(runner, RecipeCommandRunner):
            runner = runner.model_copy(update={"argv": resolve_command_arguments(runner, inputs)})
        elif isinstance(runner, RecipeAgentRunner):
            provider = runner.provider or self._config.sdk
            runner = runner.model_copy(
                update={
                    "provider": provider,
                    "model": runner.model
                    if runner.model is not None
                    else (self._config.model if provider == self._config.sdk else None),
                }
            )

        async def persist() -> RecipeAttemptResponse:
            async with serialized_write(self._host._session_factory) as session:
                return await RecipeRunRepository(session).claim_attempt(
                    instance,
                    inputs=inputs,
                    runner=runner,
                    capabilities=instance.definition.capabilities,
                    workspace=workspace,
                )

        admission = asyncio.create_task(persist())
        try:
            return await asyncio.shield(admission)
        except asyncio.CancelledError:
            try:
                admitted = await _settle(admission)
            except StateConflictError:
                raise asyncio.CancelledError from None
            async with self._host._session_factory() as session:
                current = await RecipeRunRepository(session).get_step(instance.id)
            if current and current.current_attempt_id == admitted.id and current.state == "running":
                await self._cancel_claimed(run, current, admitted)
            raise

    async def _step(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        retained: RecipeAttemptResponse | None,
        parent: RecipeAttemptResponse | None,
    ) -> None:
        attempt = retained
        if self._host._shutting_down:
            raise asyncio.CancelledError
        if attempt is not None and instance.state == "interrupted":
            async with serialized_write(self._host._session_factory) as session:
                attempt = await RecipeRunRepository(session).resume_interrupted(instance)
        elif (
            attempt is not None
            and instance.state == "running"
            and attempt.runner.kind in {"agent", "command"}
            and instance.id not in self._resumed_instances
        ):
            async with serialized_write(self._host._session_factory) as session:
                await RecipeRunRepository(session).interrupt_attempt(
                    attempt.id,
                    "The previous execution has no confirmed stop receipt.",
                    stop_confirmed=False,
                    resume_safe=False,
                    execution_epoch=self._host.execution_epoch,
                    expected_execution=attempt.executions[-1].execution_token if attempt.executions else None,
                    expected_instance_version=instance.version,
                )
            raise StateConflictError("The previous execution must be reconciled before it can resume.")
        self._resumed_instances.discard(instance.id)
        if instance.state == "ready":
            attempt = None
        kind = instance.definition.runner.kind
        if kind in {"agent", "command"}:
            async with self._host._recipe_runner_slots:
                attempt = attempt or await self._admit(run, instance, parent)
                await self._run_claimed(run, instance, attempt)
        else:
            attempt = attempt or await self._admit(run, instance, parent)
            await self._run_claimed(run, instance, attempt)

    async def _run_claimed(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        attempt: RecipeAttemptResponse,
    ) -> None:
        runner_started = False
        try:
            async with self._host._session_factory() as session:
                current = await RecipeRunRepository(session).get_step(instance.id)
            if current is None:
                raise StateConflictError("The admitted step is missing.")
            instance = current
            await self._changed(run.id, instance.id)
            runner_started = True
            if attempt.runner.kind == "command":
                result, outputs = await CommandStepRunner(self._host).run(run, instance, attempt)
            elif attempt.runner.kind == "agent":
                result, outputs = await AgentStepRunner(self._host).run(
                    run,
                    instance,
                    attempt,
                    self._config,
                    contract_pin=self._contract_pin,
                )
                git = self._host._git_service
                assert git is not None
                if not isinstance(result.evidence, RecipeAgentResult):
                    raise StateConflictError("Agent completion did not provide agent execution evidence.")
                if not result.evidence.uncertain:
                    code = await git.capture_worktree_commit(
                        cwd=attempt.workspace.path,
                        snapshot_ref=f"refs/codeplane/recipe-inputs/{attempt.id}-result",
                    )
                    for name, port in instance.definition.outputs.items():
                        if port.type == "code_ref":
                            outputs[name] = RecipeCodeReference(
                                workspace=attempt.workspace.path, commit=code
                            ).model_dump(mode="json")
            elif attempt.runner.kind == "human":
                result, outputs = await self._human(run, instance, attempt)
            else:
                result, outputs = await self._block(run, instance, attempt)
        except InterruptedAgentError as stopped:
            if self._host._shutting_down:
                async with serialized_write(self._host._session_factory) as session:
                    await RecipeRunRepository(session).interrupt_attempt(
                        attempt.id,
                        "Agent stopped before the step completed.",
                        stop_confirmed=True,
                        resume_safe=stopped.resume_safe,
                        execution_epoch=self._host.execution_epoch,
                        expected_execution=stopped.execution_token,
                        expected_instance_version=stopped.instance_version,
                    )
            else:
                await self._cancel_claimed(run, instance, attempt, stopped.evidence)
            raise asyncio.CancelledError from None
        except asyncio.CancelledError:
            if not self._host._shutting_down or not runner_started:
                async with self._host._session_factory() as session:
                    current = await RecipeRunRepository(session).get_step(instance.id)
                if current is not None and current.state in {"running", "needs_decision"}:
                    await self._cancel_claimed(run, current, attempt)
            raise

        if (
            self._host._shutting_down
            and isinstance(result.evidence, RecipeCommandResult)
            and not result.evidence.started
        ):
            await self._cancel_claimed(run, instance, attempt)
            raise asyncio.CancelledError

        async def finish() -> None:
            async with serialized_write(self._host._session_factory) as session:
                await RecipeRunRepository(session).finish_attempt(attempt.id, result, outputs)

        await _settle(asyncio.create_task(finish()))
        await self._changed(run.id, instance.id)

    async def _cancel_claimed(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        attempt: RecipeAttemptResponse,
        agent_evidence: RecipeAgentResult | None = None,
    ) -> None:
        if self._host._shutting_down:
            if attempt.runner.kind in {"human", "block"}:
                return
            async with serialized_write(self._host._session_factory) as session:
                await RecipeRunRepository(session).interrupt_attempt(
                    attempt.id,
                    "Stopped before dispatch.",
                    stop_confirmed=True,
                    resume_safe=True,
                    execution_epoch=self._host.execution_epoch,
                    expected_instance_version=instance.version,
                    expected_execution=attempt.executions[-1].execution_token if attempt.executions else None,
                    dispatch_started=False,
                )
            return
        evidence: RecipeEvidence
        if isinstance(attempt.runner, RecipeHumanRunner):
            checksum = content_checksum(
                {name: receipt.model_dump(mode="json", exclude_unset=True) for name, receipt in attempt.inputs.items()}
            )
            async with self._host._session_factory() as session:
                repository = RecipeRunRepository(session)
                binding = await repository.get_gate(attempt.id)
                approval_id = (
                    binding.approval_id
                    if binding
                    else await repository.find_gate_request(
                        RecipeGateProposal(
                            recipe_run_id=run.id,
                            step_instance_id=instance.id,
                            attempt_id=attempt.id,
                            input_checksum=checksum,
                            proposed_action=attempt.runner.proposed_action,
                            next_steps={name: route.next_steps for name, route in instance.definition.routes.items()},
                        )
                    )
                )
            approvals = self._host._approval_service
            if approval_id is not None and approvals is not None:
                approval = await approvals.get(approval_id)
                if approval is not None and approval.resolution is None:
                    try:
                        await approvals.resolve(
                            approval.id,
                            ApprovalResolution.rejected,
                            notes="Recipe run canceled before the decision was consumed.",
                        )
                    except ApprovalAlreadyResolvedError:
                        log.info("recipe_gate_resolved_before_cancel", approval_id=approval.id)
            evidence = RecipeHumanResult(
                approval_id=approval_id,
                decision=None,
                input_checksum=checksum,
            )
        elif isinstance(attempt.runner, RecipeBlockRunner):
            evidence = RecipeBlockResult(revision_id=attempt.runner.revision_id, child_attempt_ids=[])
        elif isinstance(attempt.runner, RecipeCommandRunner):
            evidence = RecipeCommandResult(
                argv=resolve_command_arguments(attempt.runner, attempt.inputs),
                started=False,
                exit_status=None,
                stdout=None,
                stderr=None,
                workspace=attempt.workspace.path,
                start_code_ref=attempt.workspace.code_ref,
                end_code_ref=None,
            )
        else:
            evidence = agent_evidence or RecipeAgentResult(
                session_ids=[execution.session_id for execution in attempt.executions],
                text=None,
            )
        async with serialized_write(self._host._session_factory) as session:
            await RecipeRunRepository(session).finish_attempt(
                attempt.id,
                RecipeAttemptResult(
                    outcome="canceled",
                    evidence=evidence,
                    reason="Canceled by the operator.",
                    completed_at=datetime.now(UTC),
                ),
                {},
            )

    async def _human(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        attempt: RecipeAttemptResponse,
    ) -> tuple[RecipeAttemptResult, dict[str, JsonValue]]:
        approvals = self._host._approval_service
        if approvals is None or not isinstance(attempt.runner, RecipeHumanRunner):
            raise StateConflictError("Canonical human approval is unavailable.")
        async with self._host._session_factory() as session:
            binding = await RecipeRunRepository(session).get_gate(attempt.id)
        checksum = content_checksum(
            {name: receipt.model_dump(mode="json", exclude_unset=True) for name, receipt in attempt.inputs.items()}
        )
        if binding is None:
            proposal = RecipeGateProposal(
                recipe_run_id=run.id,
                step_instance_id=instance.id,
                attempt_id=attempt.id,
                input_checksum=checksum,
                proposed_action=attempt.runner.proposed_action,
                next_steps={name: route.next_steps for name, route in instance.definition.routes.items()},
            )
            async with self._host._session_factory() as session:
                existing_request = await RecipeRunRepository(session).find_gate_request(proposal)
            if existing_request is None:
                created_approval = await approvals.create_request(
                    run.job_id,
                    instance.definition.title,
                    proposal.model_dump_json(),
                    requires_explicit_approval=True,
                )
                existing_request = created_approval.id
            async with serialized_write(self._host._session_factory) as session:
                repository = RecipeRunRepository(session)
                await repository.bind_gate(proposal, existing_request)
                binding = await repository.get_gate(attempt.id)
            await self._changed(run.id, instance.id)
        assert binding is not None
        await approvals.recover_pending_approvals(job_id=run.job_id)
        approval = await approvals.get(binding.approval_id)
        if approval is None:
            raise StateConflictError("The exact human authority record is missing.")
        if approval.resolution is None:
            await self._host._event_bus.publish(
                new_event(
                    run.job_id,
                    EventKind.approval_requested,
                    {
                        "approval_id": approval.id,
                        "description": approval.description,
                        "proposed_action": approval.proposed_action,
                        "requires_explicit_approval": True,
                    },
                    timestamp=approval.requested_at,
                    event_id=f"recipe-gate-request-{approval.id}",
                    metadata=EventMetadata(
                        source_framework="codeplane.recipe",
                        recipe_run_id=run.id,
                        recipe_revision=run.snapshot.revision,
                        step_instance_id=instance.id,
                        attempt_id=attempt.id,
                    ),
                )
            )
            decision = await approvals.wait_for_resolution(binding.approval_id)
            approval = await approvals.get(binding.approval_id)
            if approval is None:
                raise StateConflictError("The resolved human authority record is missing.")
        else:
            decision = approval.resolution
        await self._host._event_bus.publish(
            new_event(
                run.job_id,
                EventKind.approval_resolved,
                {
                    "approval_id": approval.id,
                    "resolution": decision.value,
                    "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
                },
                timestamp=approval.resolved_at,
                event_id=f"recipe-gate-resolution-{approval.id}",
                metadata=EventMetadata(
                    source_framework="codeplane.recipe",
                    recipe_run_id=run.id,
                    recipe_revision=run.snapshot.revision,
                    step_instance_id=instance.id,
                    attempt_id=attempt.id,
                ),
            )
        )
        outputs: dict[str, JsonValue] = {}
        if approval.notes and approval.notes.startswith('{"kind":"recipe_decision"'):
            receipt = RecipeDecisionReceipt.model_validate_json(approval.notes)
            if receipt.attempt_id != attempt.id or receipt.input_checksum != checksum:
                raise StateConflictError("The submitted decision belongs to different inputs or another attempt.")
            outputs = receipt.outputs
        else:
            for name, port in instance.definition.outputs.items():
                if name == "feedback" and port.type == "string":
                    outputs[name] = approval.notes or ""
                elif name == "decision" and port.type == "string":
                    outputs[name] = decision.value
        return RecipeAttemptResult(
            outcome=decision.value,
            evidence=RecipeHumanResult(
                approval_id=approval.id,
                decision=decision.value,
                input_checksum=checksum,
            ),
            reason=None,
            completed_at=datetime.now(UTC),
        ), outputs

    async def _block(
        self,
        run: RecipeRunResponse,
        instance: RecipeStepInstanceResponse,
        attempt: RecipeAttemptResponse,
    ) -> tuple[RecipeAttemptResult, dict[str, JsonValue]]:
        if not isinstance(attempt.runner, RecipeBlockRunner):
            raise StateConflictError("Block execution requires a pinned block revision.")
        block = run.snapshot.blocks[attempt.runner.revision_id]
        scope = f"{instance.step_path}@{attempt.number}"
        state = await self._frame(run, scope, block.definition, attempt)
        outputs: dict[str, JsonValue] = {}
        children: list[str] = []
        async with self._host._session_factory() as session:
            repository = RecipeRunRepository(session)
            instances = await repository.list_scope(run.id, scope, block.definition)
            for child in instances.values():
                if child.current_attempt_id:
                    children.append(child.current_attempt_id)
            for name, export in block.definition.outputs.items():
                child = instances[export.binding.step_id]
                producer = await repository.get_attempt(child.current_attempt_id) if child.current_attempt_id else None
                if producer and child.state == "succeeded":
                    output = next((value for value in producer.outputs if value.name == export.binding.output), None)
                    if output:
                        outputs[name] = output.value
        return RecipeAttemptResult(
            outcome=state,
            evidence=RecipeBlockResult(revision_id=block.revision_id, child_attempt_ids=children),
            reason=None,
            completed_at=datetime.now(UTC),
        ), outputs
