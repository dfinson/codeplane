"""Atomic recipe admission and immutable execution receipts.

Callers commit short transactions before starting any runner.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.sqlite import insert

from backend.models.db import (
    ApprovalRow,
    JobRow,
    RecipeAttemptExecutionRow,
    RecipeAttemptResultRow,
    RecipeAttemptRow,
    RecipeGateBindingRow,
    RecipeOutputReceiptRow,
    RecipeRunRow,
    RecipeStepInstanceRow,
    RecipeWorkspaceLeaseRow,
)
from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import (
    RecipeAttemptExecution,
    RecipeAttemptResponse,
    RecipeAttemptResult,
    RecipeCommandResult,
    RecipeGateProposal,
    RecipeHumanResult,
    RecipeHumanRunner,
    RecipeInputReceipt,
    RecipeLiteralBinding,
    RecipeOutputBinding,
    RecipeOutputReceipt,
    RecipeParameterBinding,
    RecipeResolvedWorkspace,
    RecipeRevisionResponse,
    RecipeRunner,
    RecipeRunResponse,
    RecipeStepInstanceResponse,
)
from backend.persistence.repository import BaseRepository
from backend.services.project.repo_membership import normalize_repo_path
from backend.services.recipe.validation import content_checksum, value_matches

if TYPE_CHECKING:
    from pydantic import JsonValue

    from backend.models.schemas.recipes import RecipeCapability, RecipeState

_INPUTS = TypeAdapter(dict[str, RecipeInputReceipt])
_RUNNER: TypeAdapter[RecipeRunner] = TypeAdapter(RecipeRunner)
_ACTIVE_RUN_STATES = ("blocked", "ready", "running", "needs_decision")


class RecipeRunRepository(BaseRepository):
    @staticmethod
    def _run(row: RecipeRunRow) -> RecipeRunResponse:
        return RecipeRunResponse.model_validate(
            {
                "id": row.id,
                "job_id": row.job_id,
                "revision_id": row.revision_id,
                "snapshot": json.loads(row.snapshot_json),
                "snapshot_checksum": row.snapshot_checksum,
                "inputs": json.loads(row.inputs_json),
                "state": row.state,
                "version": row.version,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "cancel_requested_at": row.cancel_requested_at,
                "completed_at": row.completed_at,
            }
        )

    @staticmethod
    def _step(row: RecipeStepInstanceRow) -> RecipeStepInstanceResponse:
        return RecipeStepInstanceResponse.model_validate(
            {
                "id": row.id,
                "run_id": row.run_id,
                "step_path": row.step_path,
                "definition": json.loads(row.definition_json),
                "state": row.state,
                "reason": row.reason,
                "current_attempt_id": row.current_attempt_id,
                "version": row.version,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _output(row: RecipeOutputReceiptRow) -> RecipeOutputReceipt:
        return RecipeOutputReceipt.model_validate(
            {
                "id": row.id,
                "attempt_id": row.attempt_id,
                "name": row.name,
                "type": row.value_type,
                "value": json.loads(row.content_json),
                "checksum": row.checksum,
                "created_at": row.created_at,
            }
        )

    async def get_for_job(self, job_id: str) -> RecipeRunResponse | None:
        row = await self._session.scalar(
            select(RecipeRunRow).where(RecipeRunRow.job_id == job_id).execution_options(populate_existing=True)
        )
        return self._run(row) if row else None

    async def get(self, run_id: str) -> RecipeRunResponse | None:
        row = await self._session.scalar(
            select(RecipeRunRow).where(RecipeRunRow.id == run_id).execution_options(populate_existing=True)
        )
        return self._run(row) if row else None

    async def create(
        self, job_id: str, snapshot: RecipeRevisionResponse, inputs: dict[str, JsonValue]
    ) -> RecipeRunResponse:
        now = datetime.now(UTC)
        checksum = content_checksum(snapshot.model_dump(mode="json", exclude_unset=True))
        run_id = str(uuid4())
        created = await self._session.scalar(
            insert(RecipeRunRow)
            .values(
                id=run_id,
                job_id=job_id,
                revision_id=snapshot.id,
                snapshot_json=snapshot.model_dump_json(exclude_unset=True),
                snapshot_checksum=checksum,
                inputs_json=json.dumps(inputs, allow_nan=False),
                state="ready",
                version=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["job_id"])
            .returning(RecipeRunRow.id)
        )
        if created is not None:
            for step in snapshot.definition.steps:
                self._session.add(
                    RecipeStepInstanceRow(
                        id=str(uuid4()),
                        run_id=run_id,
                        step_path=step.id,
                        definition_json=step.model_dump_json(),
                        state="blocked",
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await self._session.flush()
        result = await self.get_for_job(job_id)
        assert result is not None
        if result.snapshot_checksum != checksum or result.inputs != inputs:
            raise StateConflictError("This Job already owns a different pinned recipe run.")
        return result

    async def get_step(self, step_instance_id: str) -> RecipeStepInstanceResponse | None:
        row = await self._session.scalar(
            select(RecipeStepInstanceRow)
            .where(RecipeStepInstanceRow.id == step_instance_id)
            .execution_options(populate_existing=True)
        )
        return self._step(row) if row else None

    async def get_step_by_path(self, run_id: str, path: str) -> RecipeStepInstanceResponse | None:
        row = await self._session.scalar(
            select(RecipeStepInstanceRow)
            .where(RecipeStepInstanceRow.run_id == run_id, RecipeStepInstanceRow.step_path == path)
            .execution_options(populate_existing=True)
        )
        return self._step(row) if row else None

    async def list_steps(self, run_id: str, *, after: str = "", limit: int = 100) -> list[RecipeStepInstanceResponse]:
        if not 1 <= limit <= 128:
            raise ValueError("Recipe step page size must be between 1 and 128.")
        rows = await self._session.scalars(
            select(RecipeStepInstanceRow)
            .where(RecipeStepInstanceRow.run_id == run_id, RecipeStepInstanceRow.step_path > after)
            .order_by(RecipeStepInstanceRow.step_path)
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return [self._step(row) for row in rows]

    async def project_step(
        self, instance: RecipeStepInstanceResponse, state: RecipeState, reason: str
    ) -> RecipeStepInstanceResponse:
        if state not in {"ready", "blocked", "skipped", "canceled"}:
            raise ValueError("Cursor projection cannot manufacture a runner outcome.")
        updated = await self._session.scalar(
            update(RecipeStepInstanceRow)
            .where(
                RecipeStepInstanceRow.id == instance.id,
                RecipeStepInstanceRow.version == instance.version,
                RecipeStepInstanceRow.state.in_(("blocked", "failed", "ready")),
            )
            .values(state=state, reason=reason, version=instance.version + 1, updated_at=datetime.now(UTC))
            .returning(RecipeStepInstanceRow)
            .execution_options(populate_existing=True)
        )
        if updated is None:
            raise StateConflictError("Step changed before its cursor update.")
        return self._step(updated)

    async def claim_attempt(
        self,
        instance: RecipeStepInstanceResponse,
        *,
        inputs: dict[str, RecipeInputReceipt],
        runner: RecipeRunner,
        capabilities: list[RecipeCapability],
        workspace: RecipeResolvedWorkspace,
    ) -> RecipeAttemptResponse:
        current = await self.get_step(instance.id)
        if (
            current is None
            or current.version != instance.version
            or current.definition != instance.definition
            or current.run_id != instance.run_id
        ):
            raise StateConflictError("The selected step snapshot is stale.")
        run = await self.get(instance.run_id)
        if run is None:
            raise StateConflictError("The selected run no longer exists.")
        if runner.kind != instance.definition.runner.kind or set(capabilities) != set(instance.definition.capabilities):
            raise StateConflictError("Attempt runner/capabilities differ from the pinned step contract.")
        if workspace.policy != instance.definition.workspace or workspace.path != normalize_repo_path(workspace.path):
            raise StateConflictError("Attempt workspace must preserve its pinned policy and use its canonical path.")
        if inputs.keys() != instance.definition.inputs.keys():
            raise StateConflictError("Attempt input names differ from the pinned step contract.")
        for name, received in inputs.items():
            port = instance.definition.inputs[name]
            if isinstance(port.binding, RecipeOutputBinding) and received.present and received.receipt_id is None:
                raise StateConflictError(f"Input '{name}' requires the exact producing output receipt.")
            if isinstance(port.binding, RecipeLiteralBinding) and (
                not received.present
                or received.checksum != content_checksum(port.binding.value)
                or received.receipt_id is not None
            ):
                raise StateConflictError(f"Input '{name}' differs from its pinned literal.")
            if (
                isinstance(port.binding, RecipeParameterBinding)
                and "/" not in instance.step_path
                and (
                    port.binding.parameter not in run.inputs
                    or not received.present
                    or received.checksum != content_checksum(run.inputs[port.binding.parameter])
                    or received.receipt_id is not None
                )
            ):
                raise StateConflictError(f"Input '{name}' differs from its pinned run parameter.")
            if (
                received.type != port.type
                or (
                    received.present
                    and (
                        not value_matches(received.value, port.type)
                        or received.checksum != content_checksum(received.value)
                        or (
                            received.degraded_reason is not None
                            and (not port.optional or received.degraded_reason != port.degraded_reason)
                        )
                        or (received.receipt_id is None) != (received.producing_attempt_id is None)
                    )
                )
                or (
                    not received.present
                    and (
                        not port.optional
                        or received.degraded_reason != port.degraded_reason
                        or received.value is not None
                        or received.receipt_id is not None
                        or received.producing_attempt_id is not None
                        or received.checksum is not None
                        or received.producer_outcome is not None
                    )
                )
            ):
                raise StateConflictError(
                    f"Input '{name}' does not have a valid typed receipt or explicit degraded reason."
                )
            if received.receipt_id is not None:
                source_receipt = await self._session.get(RecipeOutputReceiptRow, received.receipt_id)
                source_instance = await self._session.scalar(
                    select(RecipeStepInstanceRow)
                    .join(RecipeAttemptRow, RecipeAttemptRow.step_instance_id == RecipeStepInstanceRow.id)
                    .where(RecipeAttemptRow.id == received.producing_attempt_id)
                )
                source_result = await self._session.get(RecipeAttemptResultRow, received.producing_attempt_id)
                prefix = instance.step_path.rsplit("/", 1)[0] + "/" if "/" in instance.step_path else ""
                expected_source = (
                    prefix + port.binding.step_id if isinstance(port.binding, RecipeOutputBinding) else None
                )
                if (
                    source_receipt is None
                    or source_instance is None
                    or source_instance.run_id != instance.run_id
                    or (
                        source_instance.state != "succeeded"
                        and not (
                            source_instance.state == "failed"
                            and port.optional
                            and received.degraded_reason == port.degraded_reason
                        )
                    )
                    or source_result is None
                    or source_result.outcome != received.producer_outcome
                    or json.loads(source_result.evidence_json).get("uncertain", False)
                    or source_instance.current_attempt_id != received.producing_attempt_id
                    or (expected_source is not None and source_instance.step_path != expected_source)
                    or (isinstance(port.binding, RecipeOutputBinding) and source_receipt.name != port.binding.output)
                    or source_receipt.value_type != received.type
                    or source_receipt.attempt_id != received.producing_attempt_id
                    or source_receipt.checksum != received.checksum
                    or json.loads(source_receipt.content_json) != received.value
                ):
                    raise StateConflictError(f"Input '{name}' differs from the retained producing attempt's receipt.")
        now = datetime.now(UTC)
        live_job = exists(select(JobRow.id).where(JobRow.id == RecipeRunRow.job_id, JobRow.state == "running"))
        admitted_run = await self._session.scalar(
            update(RecipeRunRow)
            .where(
                RecipeRunRow.id == instance.run_id,
                RecipeRunRow.state.in_(_ACTIVE_RUN_STATES),
                RecipeRunRow.cancel_requested_at.is_(None),
                live_job,
            )
            .values(state="running", version=RecipeRunRow.version + 1, updated_at=now)
            .returning(RecipeRunRow.id)
        )
        if admitted_run is None:
            raise StateConflictError("The run or owning Job no longer admits execution.")
        number = (
            await self._session.scalar(
                select(func.max(RecipeAttemptRow.number)).where(RecipeAttemptRow.step_instance_id == instance.id)
            )
            or 0
        ) + 1
        if number > instance.definition.retry.max_attempts:
            raise StateConflictError("The step's total attempt limit is exhausted.")
        attempt_id = str(uuid4())
        claimed = await self._session.scalar(
            update(RecipeStepInstanceRow)
            .where(
                RecipeStepInstanceRow.id == instance.id,
                RecipeStepInstanceRow.version == instance.version,
                RecipeStepInstanceRow.state == "ready",
            )
            .values(
                state="running",
                current_attempt_id=attempt_id,
                version=instance.version + 1,
                reason=None,
                updated_at=now,
            )
            .returning(RecipeStepInstanceRow.id)
        )
        if claimed is None:
            raise StateConflictError("The selected step is no longer ready.")
        self._session.add(
            RecipeAttemptRow(
                id=attempt_id,
                step_instance_id=instance.id,
                number=number,
                inputs_json=_INPUTS.dump_json(inputs).decode(),
                runner_json=runner.model_dump_json(),
                capabilities_json=json.dumps(capabilities),
                workspace_json=workspace.model_dump_json(),
                dispatch_intent_at=now,
            )
        )
        await self._session.flush()
        if runner.kind in {"agent", "command"} and ("write" in capabilities or "shell" in capabilities):
            lease = await self._session.scalar(
                insert(RecipeWorkspaceLeaseRow)
                .values(workspace=workspace.path, attempt_id=attempt_id, acquired_at=now)
                .on_conflict_do_nothing(index_elements=["workspace"])
                .returning(RecipeWorkspaceLeaseRow.attempt_id)
            )
            if lease is None:
                raise StateConflictError("A writer already owns this mutable workspace.")
        result = await self.get_attempt(attempt_id)
        assert result is not None
        return result

    async def get_attempt(self, attempt_id: str) -> RecipeAttemptResponse | None:
        row = await self._session.get(RecipeAttemptRow, attempt_id)
        if row is None:
            return None
        outcome = await self._session.get(RecipeAttemptResultRow, attempt_id)
        executions = await self._session.scalars(
            select(RecipeAttemptExecutionRow)
            .where(RecipeAttemptExecutionRow.attempt_id == attempt_id)
            .order_by(RecipeAttemptExecutionRow.sequence)
            .limit(100)
        )
        outputs = await self._session.scalars(
            select(RecipeOutputReceiptRow)
            .where(RecipeOutputReceiptRow.attempt_id == attempt_id)
            .order_by(RecipeOutputReceiptRow.name)
            .limit(64)
        )
        result = (
            None
            if outcome is None
            else RecipeAttemptResult.model_validate(
                {
                    "outcome": outcome.outcome,
                    "evidence": json.loads(outcome.evidence_json),
                    "reason": outcome.reason,
                    "completed_at": outcome.completed_at,
                }
            )
        )
        return RecipeAttemptResponse(
            id=row.id,
            step_instance_id=row.step_instance_id,
            number=row.number,
            inputs=_INPUTS.validate_json(row.inputs_json),
            runner=_RUNNER.validate_json(row.runner_json),
            capabilities=json.loads(row.capabilities_json),
            workspace=RecipeResolvedWorkspace.model_validate_json(row.workspace_json),
            dispatch_intent_at=row.dispatch_intent_at,
            result=result,
            executions=[
                RecipeAttemptExecution(
                    execution_token=item.execution_token,
                    session_id=item.session_id,
                    provider=item.provider,
                    model=item.model,
                    started_at=item.started_at,
                )
                for item in executions
            ],
            outputs=[self._output(item) for item in outputs],
        )

    async def list_attempts(self, step_instance_id: str) -> list[RecipeAttemptResponse]:
        ids = await self._session.scalars(
            select(RecipeAttemptRow.id)
            .where(RecipeAttemptRow.step_instance_id == step_instance_id)
            .order_by(RecipeAttemptRow.number)
            .limit(20)
        )
        attempts = []
        for attempt_id in ids:
            attempt = await self.get_attempt(attempt_id)
            assert attempt is not None
            attempts.append(attempt)
        return attempts

    async def record_execution(self, attempt_id: str, execution: RecipeAttemptExecution) -> None:
        attempt = await self.get_attempt(attempt_id)
        if attempt is None or attempt.runner.kind != "agent" or attempt.result is not None:
            raise StateConflictError("Only a live agent attempt can register an SDK execution.")
        if len(attempt.executions) >= 100:
            raise StateConflictError("The attempt's retained execution limit is exhausted.")
        self._session.add(RecipeAttemptExecutionRow(attempt_id=attempt_id, **execution.model_dump()))
        await self._session.flush()

    async def bind_gate(self, proposal: RecipeGateProposal, approval_id: str) -> None:
        attempt = await self.get_attempt(proposal.attempt_id)
        instance = await self.get_step(proposal.step_instance_id)
        run = await self.get(proposal.recipe_run_id)
        approval = await self._session.get(ApprovalRow, approval_id)
        if (
            attempt is None
            or instance is None
            or run is None
            or approval is None
            or attempt.step_instance_id != instance.id
            or instance.run_id != run.id
            or instance.current_attempt_id != attempt.id
            or instance.state != "running"
            or not isinstance(attempt.runner, RecipeHumanRunner)
            or approval.job_id != run.job_id
            or not approval.requires_explicit_approval
            or run.cancel_requested_at is not None
        ):
            raise StateConflictError("This approval does not bind a current human attempt.")
        try:
            recorded_proposal = RecipeGateProposal.model_validate_json(approval.proposed_action or "{}")
        except ValidationError as exc:
            raise StateConflictError("The selected approval is not a valid recipe gate proposal.") from exc
        expected_checksum = content_checksum(
            {key: value.model_dump(mode="json", exclude_unset=True) for key, value in attempt.inputs.items()}
        )
        if (
            proposal.input_checksum != expected_checksum
            or proposal.proposed_action != attempt.runner.proposed_action
            or proposal.next_steps != {key: route.next_steps for key, route in instance.definition.routes.items()}
            or recorded_proposal != proposal
        ):
            raise StateConflictError("The gate proposal differs from the exact pinned input/action/next-step snapshot.")
        self._session.add(
            RecipeGateBindingRow(
                attempt_id=attempt.id,
                approval_id=approval_id,
                input_checksum=proposal.input_checksum,
                proposed_action=proposal.proposed_action,
                next_steps_json=json.dumps(proposal.next_steps),
                created_at=datetime.now(UTC),
            )
        )
        await self._session.execute(
            update(RecipeStepInstanceRow)
            .where(RecipeStepInstanceRow.id == instance.id, RecipeStepInstanceRow.version == instance.version)
            .values(
                state="needs_decision",
                reason="Waiting for explicit approval of the pinned inputs and action.",
                version=instance.version + 1,
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.flush()

    async def _validate_human_decision(
        self, attempt: RecipeAttemptResponse, evidence: RecipeHumanResult, outcome: str
    ) -> None:
        binding = await self._session.get(RecipeGateBindingRow, attempt.id)
        approval = await self._session.get(ApprovalRow, evidence.approval_id)
        if (
            binding is None
            or approval is None
            or binding.approval_id != approval.id
            or evidence.input_checksum != binding.input_checksum
            or content_checksum(
                {key: value.model_dump(mode="json", exclude_unset=True) for key, value in attempt.inputs.items()}
            )
            != binding.input_checksum
            or evidence.decision != outcome
            or approval.resolution != outcome
        ):
            raise StateConflictError(
                "The decision is missing, stale, rejected for this action, or names another attempt."
            )
        for received in attempt.inputs.values():
            if received.producing_attempt_id is None:
                continue
            producer = await self._session.scalar(
                select(RecipeStepInstanceRow)
                .join(RecipeAttemptRow, RecipeAttemptRow.step_instance_id == RecipeStepInstanceRow.id)
                .where(RecipeAttemptRow.id == received.producing_attempt_id)
            )
            receipt = await self._session.get(RecipeOutputReceiptRow, received.receipt_id)
            if (
                producer is None
                or producer.current_attempt_id != received.producing_attempt_id
                or (
                    producer.state != "succeeded"
                    and not (producer.state == "failed" and received.degraded_reason is not None)
                )
                or receipt is None
                or receipt.checksum != received.checksum
                or content_checksum(json.loads(receipt.content_json)) != received.checksum
            ):
                raise StateConflictError(
                    "Gate inputs changed after the approval request. A fresh decision is required."
                )

    async def finish_attempt(
        self, attempt_id: str, result: RecipeAttemptResult, outputs: dict[str, JsonValue]
    ) -> RecipeAttemptResponse:
        attempt = await self.get_attempt(attempt_id)
        if attempt is None:
            raise StateConflictError("Attempt does not exist.")
        if attempt.result is not None:
            raise StateConflictError("A completed attempt is immutable.")
        instance = await self.get_step(attempt.step_instance_id)
        assert instance is not None
        if instance.current_attempt_id != attempt_id or instance.state not in {"running", "needs_decision"}:
            raise StateConflictError("The selected attempt is no longer current.")
        run = await self.get(instance.run_id)
        assert run is not None
        allowed_outcomes = set(instance.definition.routes) | {"failed", "interrupted", "canceled"}
        if result.outcome not in allowed_outcomes:
            raise StateConflictError("The runner returned an undeclared outcome.")
        if result.evidence.kind != attempt.runner.kind:
            raise StateConflictError("The evidence does not match the pinned runner kind.")
        if isinstance(result.evidence, RecipeHumanResult) and result.outcome in instance.definition.routes:
            await self._validate_human_decision(attempt, result.evidence, result.outcome)
        if isinstance(result.evidence, RecipeCommandResult):
            command_evidence = result.evidence
            if (
                attempt.runner.kind != "command"
                or command_evidence.argv != attempt.runner.argv
                or command_evidence.workspace != attempt.workspace.path
                or command_evidence.start_code_ref != attempt.workspace.code_ref
            ):
                raise StateConflictError(
                    "Command evidence belongs to a different invocation or code/workspace reference."
                )
            if (
                command_evidence.exit_status is None
                or command_evidence.stdout is None
                or command_evidence.stderr is None
                or command_evidence.end_code_ref is None
            ):
                result = result.model_copy(
                    update={
                        "evidence": command_evidence.model_copy(update={"uncertain": True}),
                        "reason": "The command's exit status, captured output or final code reference is unrecorded.",
                    }
                )
            elif command_evidence.exit_status != 0 and result.outcome == "succeeded":
                result = result.model_copy(update={"outcome": "failed", "reason": "The command exited unsuccessfully."})
        if getattr(result.evidence, "uncertain", False):
            result = result.model_copy(update={"outcome": "interrupted"})
        job_state = await self._session.scalar(select(JobRow.state).where(JobRow.id == run.job_id))
        if run.cancel_requested_at is not None or run.state == "canceled" or job_state == "canceled":
            result = result.model_copy(update={"outcome": "canceled", "reason": "Cancellation preceded completion."})
        expected = instance.definition.outputs
        invalid_outputs = set(outputs) != set(expected) or any(
            not value_matches(value, expected[name].type) for name, value in outputs.items() if name in expected
        )
        needs_output_contract = result.outcome == "succeeded" or (
            isinstance(attempt.runner, RecipeHumanRunner) and result.outcome in attempt.runner.decisions
        )
        if needs_output_contract and invalid_outputs:
            result = result.model_copy(
                update={
                    "outcome": "failed",
                    "reason": "The runner did not satisfy the declared output names and types.",
                }
            )
        self._session.add(
            RecipeAttemptResultRow(
                attempt_id=attempt_id,
                outcome=result.outcome,
                evidence_json=result.evidence.model_dump_json(),
                reason=result.reason,
                completed_at=result.completed_at,
            )
        )
        for name, value in outputs.items():
            if name not in expected or not value_matches(value, expected[name].type):
                continue
            self._session.add(
                RecipeOutputReceiptRow(
                    id=str(uuid4()),
                    attempt_id=attempt_id,
                    name=name,
                    value_type=expected[name].type,
                    content_json=json.dumps(value, allow_nan=False),
                    checksum=content_checksum(value),
                    created_at=result.completed_at,
                )
            )
        state = result.outcome if result.outcome in {"failed", "interrupted", "canceled"} else "succeeded"
        await self._session.execute(
            update(RecipeStepInstanceRow)
            .where(RecipeStepInstanceRow.id == instance.id)
            .values(state=state, reason=result.reason, version=instance.version + 1, updated_at=result.completed_at)
        )
        if not getattr(result.evidence, "uncertain", False):
            await self._session.execute(
                delete(RecipeWorkspaceLeaseRow).where(RecipeWorkspaceLeaseRow.attempt_id == attempt_id)
            )
        await self._session.flush()
        finished = await self.get_attempt(attempt_id)
        assert finished is not None
        return finished

    async def request_cancel(self, run_id: str) -> None:
        now = datetime.now(UTC)
        await self._session.execute(
            update(RecipeRunRow)
            .where(RecipeRunRow.id == run_id, RecipeRunRow.state.in_(_ACTIVE_RUN_STATES + ("interrupted",)))
            .values(state="canceled", cancel_requested_at=now, updated_at=now, version=RecipeRunRow.version + 1)
        )
