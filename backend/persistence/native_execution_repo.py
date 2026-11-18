"""Admission checks and receipts for in-process, caller-owned native objects."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

from sqlalchemy import select

from backend.models.db import EventRow, IntegrationBindingRow
from backend.models.domain import JobState, StateConflictError
from backend.models.events import SessionEvent, new_event
from backend.models.session_event import EventMetadata
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import Job
    from backend.models.execution_contracts import ExecutionContractPin
    from backend.services.adapters.contracts import ExecutionIdentity


@dataclass(frozen=True)
class NativeAdmission:
    workspace: str
    repo: str
    metadata: EventMetadata


class ObservationCommit(TypedDict):
    source_key: str
    revision: str
    expected_version: int
    record_count: int
    prefix_sha256: str
    canonical_sha256: str
    parser_sha256: str
    buffer: dict[str, Any]
    events: list[SessionEvent]
    projection_version: int
    projection_parser_sha256: str
    projection_canonical_sha256: str
    projection_buffer: dict[str, Any]


class NativeExecutionRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def bind_observation(
        self,
        execution: ExecutionIdentity,
        pin: ExecutionContractPin,
        *,
        binding_id: str,
        family: str,
        external_session_id: str,
        assert_retained: Callable[[], Awaitable[None]],
    ) -> Job:
        from backend.validators import validate_integration_binding_id

        validate_integration_binding_id(binding_id)
        config = {
            "format": family,
            "external_session_id": external_session_id,
            "execution": asdict(execution),
            "admission_id": pin.admission_id,
            "budget_id": pin.budget_id,
            "owner": "native-runtime-observation-v1",
        }
        async with serialized_write(self._factory) as session:
            await assert_retained()
            events = EventRepository(session)
            bound = await events.get(f"native:{execution.execution_token}:bound")
            job = await JobRepository(session).get(execution.job_id)
            if (
                job is None
                or job.observation_only
                or bound is None
                or bound.session_id != execution.job_id
                or bound.metadata.source_framework != "codeplane"
                or bound.metadata.namespace != ("control", "native-execution")
                or bound.payload.get("execution") != asdict(execution)
                or bound.payload.get("admission_id") != pin.admission_id
                or bound.payload.get("budget_id") != pin.budget_id
                or bound.payload.get("family") != family
                or await events.get(f"native:{execution.execution_token}:released") is not None
            ):
                raise StateConflictError("Native observation has no exact retained execution admission")
            existing = await session.get(IntegrationBindingRow, binding_id)
            if existing is not None:
                raise StateConflictError("Native observation source must reuse its retained Runtime owner")
            now = datetime.now(UTC)
            session.add(
                IntegrationBindingRow(
                    id=binding_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    adapter_id="native",
                    mode="execution",
                    enabled=True,
                    config_json=json.dumps(config, sort_keys=True),
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            await assert_retained()
        return job

    async def commit_observation(
        self,
        execution: ExecutionIdentity,
        pin: ExecutionContractPin,
        *,
        binding_id: str,
        assert_retained: Callable[[], Awaitable[None]],
        closed: bool,
        commit: ObservationCommit,
    ) -> None:
        async with serialized_write(self._factory) as session:
            await assert_retained()
            source = await session.get(IntegrationBindingRow, binding_id)
            bound = await EventRepository(session).get(f"native:{execution.execution_token}:bound")
            if (
                source is None
                or source.mode != "execution"
                or source.job_id != execution.job_id
                or not source.enabled
                or bound is None
                or bound.session_id != execution.job_id
                or bound.metadata.source_framework != "codeplane"
                or bound.metadata.namespace != ("control", "native-execution")
                or json.loads(source.config_json).get("execution") != asdict(execution)
                or json.loads(source.config_json).get("admission_id") != pin.admission_id
                or bound.payload.get("execution") != asdict(execution)
                or bound.payload.get("admission_id") != pin.admission_id
                or await EventRepository(session).get(f"native:{execution.execution_token}:released") is not None
            ):
                raise StateConflictError("Native observation source is no longer retained")
            for event in commit["events"]:
                if (
                    not isinstance(event, SessionEvent)
                    or event.session_id != execution.job_id
                    or event.metadata.integration_binding_id != binding_id
                    or event.metadata.execution_epoch != execution.epoch
                    or event.metadata.execution_token != execution.execution_token
                    or not event.metadata.namespace
                    or event.metadata.namespace[0] != "observation-source"
                ):
                    raise StateConflictError("Native observation output escaped its source ownership")
            if closed:
                source.enabled = False
                source.version += 1
                source.updated_at = datetime.now(UTC)
            await IntegrationRepository(session).commit_records(binding_id=binding_id, **commit)
            await assert_retained()

    async def retire_observation(self, execution: ExecutionIdentity, binding_id: str) -> None:
        async with serialized_write(self._factory) as session:
            source = await session.get(IntegrationBindingRow, binding_id)
            if (
                source is not None
                and source.enabled
                and json.loads(source.config_json).get("execution") == asdict(execution)
            ):
                source.enabled = False
                source.version += 1
                source.updated_at = datetime.now(UTC)

    async def has_native_admission(self, job_id: str) -> bool:
        async with self._factory() as session:
            admission = await ExecutionAdmissionRepository(session).current(job_id)
            if admission is None:
                return False
            from sqlalchemy import func

            return (
                await session.scalar(
                    select(EventRow.id)
                    .where(
                        EventRow.job_id == job_id,
                        EventRow.kind == "execution.native.bound",
                        func.json_extract(EventRow.payload, "$.admission_id") == admission.id,
                    )
                    .limit(1)
                )
                is not None
            )

    async def admission(
        self,
        pin: ExecutionContractPin,
        *,
        attempt_id: str | None,
        run_id: str | None,
        step_id: str | None,
    ) -> NativeAdmission:
        async with self._factory() as session:
            job = await JobRepository(session).get(pin.job_id)
            if job is None or job.observation_only or job.state != JobState.running:
                raise StateConflictError("Native invocation requires a running execution Job")
            recipes = RecipeRunRepository(session)
            run = await recipes.get_for_job(job.id)
            metadata = EventMetadata()
            workspace = job.execution_workspace
            if run is None:
                if attempt_id is not None or run_id is not None or step_id is not None:
                    raise StateConflictError("Native recipe attribution has no actual recipe owner")
                declared = await ExecutionAdmissionRepository(session).current(job.id)
                if declared is None or declared.id != pin.admission_id:
                    raise StateConflictError("Native invocation's durable admission has been replaced")
            else:
                if run.id != pin.admission_id or run.resolved_at is not None or run.cancel_requested_at is not None:
                    raise StateConflictError("Native invocation's recipe admission is no longer live")
                if attempt_id is not None:
                    attempt = await recipes.get_attempt(attempt_id)
                    step = await recipes.get_step(step_id) if step_id is not None else None
                    if (
                        run.id != run_id
                        or step is None
                        or step.run_id != run.id
                        or step.current_attempt_id != attempt_id
                        or attempt is None
                        or attempt.step_instance_id != step.id
                        or attempt.result is not None
                    ):
                        raise StateConflictError("Native recipe attempt ownership has changed")
                    workspace = attempt.workspace.path
                    metadata = EventMetadata(
                        recipe_run_id=run.id,
                        recipe_revision=run.snapshot.revision,
                        step_instance_id=step.id,
                        attempt_id=attempt_id,
                    )
            result = NativeAdmission(workspace, job.execution_repo, metadata)
        if await ExecutionContractRepository(self._factory).get(pin.job_id, pin.admission_id) != pin:
            raise StateConflictError("Native invocation requires its existing, exact durable contract pin")
        return result

    async def record(
        self,
        execution: ExecutionIdentity,
        pin: ExecutionContractPin,
        family: str,
        phase: str,
        metadata: EventMetadata,
    ) -> None:
        async with serialized_write(self._factory) as session:
            await EventRepository(session).append(
                new_event(
                    execution.job_id,
                    f"execution.native.{phase}",
                    {
                        "execution": asdict(execution),
                        "admission_id": pin.admission_id,
                        "budget_id": pin.budget_id,
                        "family": family,
                    },
                    event_id=f"native:{execution.execution_token}:{phase}",
                    metadata=metadata.model_copy(
                        update={"source_framework": "codeplane", "namespace": ("control", "native-execution")}
                    ),
                )
            )

    async def receipt(
        self,
        execution: ExecutionIdentity,
        pin: ExecutionContractPin,
        family: str,
        metadata: EventMetadata,
        *,
        operation_id: str,
        phase: str,
        payload: dict[str, object],
    ) -> SessionEvent:
        body = {
            **payload,
            "execution": asdict(execution),
            "admission_id": pin.admission_id,
            "budget_id": pin.budget_id,
            "family": family,
            "operation_id": operation_id,
        }
        event = new_event(
            execution.job_id,
            f"execution.native.{phase}",
            body,
            event_id=f"native:{execution.execution_token}:{phase}:{operation_id}",
            metadata=metadata.model_copy(
                update={"source_framework": "codeplane", "namespace": ("control", "native-execution")}
            ),
        )
        async with serialized_write(self._factory) as session:
            events = EventRepository(session)
            bound = await events.get(f"native:{execution.execution_token}:bound")
            if (
                bound is None
                or bound.payload["execution"] != asdict(execution)
                or bound.payload["admission_id"] != pin.admission_id
                or await events.get(f"native:{execution.execution_token}:released") is not None
            ):
                raise StateConflictError("Native result no longer has its original durable execution binding")
            await events.append(event)
        return event

    async def trusted_receipt(self, event: SessionEvent) -> bool:
        """Only exact stored app receipts, ordered inside their original binding."""
        md = event.metadata
        if (
            event.kind not in {"execution.native.tool_result", "execution.native.usage"}
            or md.source_framework != "codeplane"
            or md.namespace != ("control", "native-execution")
            or md.integration_binding_id is not None
            or not md.execution_token
        ):
            return False
        async with self._factory() as session:
            events = EventRepository(session)
            if await events.get(event.id) != event:
                return False
            bound_id, released_id = f"native:{md.execution_token}:bound", f"native:{md.execution_token}:released"
            bound = await events.get(bound_id)
            if (
                bound is None
                or bound.session_id != event.session_id
                or bound.metadata != md
                or any(
                    bound.payload.get(key) != event.payload.get(key)
                    for key in ("execution", "admission_id", "budget_id", "family")
                )
            ):
                return False
            rows = (
                await session.execute(
                    select(EventRow.event_id, EventRow.id).where(
                        EventRow.event_id.in_([bound_id, event.id, released_id])
                    )
                )
            ).all()
            positions: dict[str, int] = {identity: cursor for identity, cursor in rows}
            if not positions[bound_id] < positions[event.id] < positions.get(released_id, float("inf")):
                return False
        admission_id = event.payload.get("admission_id")
        if not isinstance(admission_id, str):
            return False
        pin = await ExecutionContractRepository(self._factory).get(event.session_id, admission_id)
        return pin is not None and pin.budget_id == event.payload.get("budget_id")
