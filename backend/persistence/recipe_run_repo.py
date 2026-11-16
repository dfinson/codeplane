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
    RecipeCodeReference,
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
    RecipeRunListResponse,
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
                "run_number": row.run_number,
                "start_request_key": row.start_request_key,
                "continuation_of_id": row.continuation_of_id,
                "instruction": row.instruction,
                "code_ref": json.loads(row.code_ref_json) if row.code_ref_json is not None else None,
                "reason": row.reason,
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
                "resolved_at": row.resolved_at,
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
            select(RecipeRunRow)
            .where(RecipeRunRow.job_id == job_id)
            .order_by(RecipeRunRow.resolved_at.is_(None).desc(), RecipeRunRow.run_number.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return self._run(row) if row else None

    async def get(self, run_id: str) -> RecipeRunResponse | None:
        row = await self._session.scalar(
            select(RecipeRunRow).where(RecipeRunRow.id == run_id).execution_options(populate_existing=True)
        )
        return self._run(row) if row else None

    async def create(
        self,
        job_id: str,
        snapshot: RecipeRevisionResponse,
        inputs: dict[str, JsonValue],
        *,
        start_request_key: str | None = None,
        continuation_of_id: str | None = None,
        instruction: str | None = None,
        code_ref: RecipeCodeReference | None = None,
        allow_revision_change: bool = False,
    ) -> RecipeRunResponse:
        request_key = start_request_key or f"initial:{job_id}"
        checksum = content_checksum(snapshot.model_dump(mode="json", exclude_unset=True))
        existing = await self._session.scalar(
            select(RecipeRunRow)
            .where(RecipeRunRow.start_request_key == request_key)
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            result = self._run(existing)
            if (
                result.job_id != job_id
                or result.snapshot_checksum != checksum
                or result.inputs != inputs
                or result.continuation_of_id != continuation_of_id
                or result.instruction != instruction
                or result.code_ref != code_ref
            ):
                raise StateConflictError("This run-start request already identifies different inputs or ownership.")
            return result
        current = await self.get_for_job(job_id)
        if current is not None:
            if current.resolved_at is None:
                raise StateConflictError("The Job's current run is unresolved. Reconcile it before another run.")
            if current.id != continuation_of_id:
                raise StateConflictError("Only explicit Resume of the current resolved run can create its successor.")
            if not start_request_key or not instruction or not instruction.strip() or code_ref is None:
                raise StateConflictError(
                    "Continuation requires an explicit request identity, instruction and code snapshot."
                )
            if snapshot.id != current.revision_id and not allow_revision_change:
                raise StateConflictError(
                    "Resume retains the selected revision unless a replacement is explicitly selected."
                )
        elif continuation_of_id is not None:
            raise StateConflictError("The preceding run does not belong to this Job.")
        now = datetime.now(UTC)
        run_id = str(uuid4())
        run_number = (current.run_number if current else 0) + 1
        created = await self._session.scalar(
            insert(RecipeRunRow)
            .values(
                id=run_id,
                job_id=job_id,
                run_number=run_number,
                start_request_key=request_key,
                continuation_of_id=continuation_of_id,
                instruction=instruction,
                code_ref_json=code_ref.model_dump_json() if code_ref is not None else None,
                revision_id=snapshot.id,
                snapshot_json=snapshot.model_dump_json(exclude_unset=True),
                snapshot_checksum=checksum,
                inputs_json=json.dumps(inputs, allow_nan=False),
                state="ready",
                version=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()
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
        stored = await self._session.scalar(select(RecipeRunRow).where(RecipeRunRow.start_request_key == request_key))
        if stored is None:
            raise StateConflictError("Another run start was admitted concurrently for this Job.")
        return self._run(stored)

    async def list_runs(
        self, job_id: str, *, after: int = 0, limit: int = 30, high_water: int | None = None
    ) -> RecipeRunListResponse:
        if after < 0 or not 1 <= limit <= 100 or (high_water is not None and high_water < after):
            raise StateConflictError("Invalid run history page.")
        if high_water is None:
            high_water = (
                await self._session.scalar(
                    select(func.max(RecipeRunRow.run_number)).where(RecipeRunRow.job_id == job_id)
                )
                or 0
            )
        rows = list(
            await self._session.scalars(
                select(RecipeRunRow)
                .where(
                    RecipeRunRow.job_id == job_id,
                    RecipeRunRow.run_number > after,
                    RecipeRunRow.run_number <= high_water,
                )
                .order_by(RecipeRunRow.run_number)
                .limit(limit + 1)
            )
        )
        return RecipeRunListResponse(
            items=[self._run(row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].run_number if len(rows) > limit else None,
            high_water=high_water,
        )

    async def resolve_run(self, run_id: str, state: RecipeState, *, reason: str | None = None) -> RecipeRunResponse:
        if state not in {"succeeded", "failed", "skipped", "canceled"}:
            raise StateConflictError("Only a terminal, reconciled run can be resolved.")
        run = await self.get(run_id)
        if run is None:
            raise StateConflictError("Run does not exist.")
        if run.resolved_at is not None:
            if run.state != state:
                raise StateConflictError("A resolved run's outcome is immutable.")
            return run
        active = await self._session.scalar(
            select(RecipeStepInstanceRow.id)
            .where(
                RecipeStepInstanceRow.run_id == run_id,
                RecipeStepInstanceRow.state.in_(("running", "needs_decision", "interrupted")),
            )
            .limit(1)
        )
        leased = await self._session.scalar(
            select(RecipeWorkspaceLeaseRow.attempt_id)
            .join(RecipeAttemptRow, RecipeAttemptRow.id == RecipeWorkspaceLeaseRow.attempt_id)
            .join(RecipeStepInstanceRow, RecipeStepInstanceRow.id == RecipeAttemptRow.step_instance_id)
            .where(RecipeStepInstanceRow.run_id == run_id)
            .limit(1)
        )
        if active is not None or leased is not None:
            raise StateConflictError("Execution or an uncertain workspace effect remains unresolved.")
        pending = await self._session.scalar(
            select(RecipeStepInstanceRow.id)
            .where(RecipeStepInstanceRow.run_id == run_id, RecipeStepInstanceRow.state.in_(("ready", "blocked")))
            .limit(1)
        )
        if state == "succeeded" and pending is not None:
            raise StateConflictError("Unfinished steps cannot become a successful run.")
        if state == "succeeded":
            from backend.services.recipe.run_state import completed_run_state

            roots = await self._session.scalars(
                select(RecipeStepInstanceRow).where(
                    RecipeStepInstanceRow.run_id == run_id,
                    RecipeStepInstanceRow.step_path.in_([step.id for step in run.snapshot.definition.steps]),
                )
            )
            instances = {row.step_path: self._step(row) for row in roots}
            attempts = {}
            for path, instance in instances.items():
                if instance.current_attempt_id:
                    attempt = await self.get_attempt(instance.current_attempt_id)
                    if attempt is not None:
                        attempts[path] = attempt
            if completed_run_state(run.snapshot.definition, instances, attempts) != "succeeded":
                raise StateConflictError("Recorded outcome routes do not permit a successful run.")
            for name, export in run.snapshot.definition.outputs.items():
                source = instances.get(export.binding.step_id)
                producer = attempts.get(export.binding.step_id)
                if (
                    source is None
                    or source.state != "succeeded"
                    or producer is None
                    or not any(
                        output.name == export.binding.output
                        and output.type == export.type
                        and output.checksum == content_checksum(output.value)
                        for output in producer.outputs
                    )
                ):
                    raise StateConflictError(f"Required run output '{name}' is unavailable.")
        now = datetime.now(UTC)
        if pending is not None:
            await self._session.execute(
                update(RecipeStepInstanceRow)
                .where(RecipeStepInstanceRow.run_id == run_id, RecipeStepInstanceRow.state.in_(("ready", "blocked")))
                .values(
                    state="canceled" if state == "canceled" else "skipped",
                    reason="Run ended before dispatch.",
                    updated_at=now,
                    version=RecipeStepInstanceRow.version + 1,
                )
            )
        changed = await self._session.scalar(
            update(RecipeRunRow)
            .where(RecipeRunRow.id == run_id, RecipeRunRow.version == run.version, RecipeRunRow.resolved_at.is_(None))
            .values(
                state=state, reason=reason, completed_at=now, resolved_at=now, updated_at=now, version=run.version + 1
            )
            .returning(RecipeRunRow.id)
        )
        if changed is None:
            raise StateConflictError("Run changed before final resolution.")
        result = await self.get(run_id)
        assert result is not None
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
                exists(
                    select(RecipeRunRow.id).where(
                        RecipeRunRow.id == RecipeStepInstanceRow.run_id, RecipeRunRow.resolved_at.is_(None)
                    )
                ),
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
                RecipeRunRow.resolved_at.is_(None),
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
        instance = await self.get_step(attempt.step_instance_id)
        run = await self.get(instance.run_id) if instance else None
        if instance is None or run is None or run.resolved_at is not None or instance.current_attempt_id != attempt_id:
            raise StateConflictError("The selected attempt no longer belongs to an unresolved run.")
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
        if run.resolved_at is not None:
            raise StateConflictError("A resolved run cannot accept another attempt outcome.")
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
