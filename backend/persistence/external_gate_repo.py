"""External gate state in existing binding, approval and immutable event storage."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select
from sqlalchemy.dialects.sqlite import insert

from backend.models.db import ApprovalRow, EventRow, IntegrationBindingRow
from backend.models.domain import StateConflictError
from backend.models.events import SessionEvent, new_event
from backend.models.schemas.external_gate import ExternalGateDecision, ExternalGatePhase
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.job_repo import JobRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql.elements import ColumnElement

    from backend.models.execution_contracts import ExecutionContractPin
    from backend.models.session_event import EventMetadata
    from backend.services.adapters.contracts import ExecutionIdentity


def correlation(source_id: str, request_id: str) -> str:
    return f"external-gate:{source_id}:{request_id}"


_APPROVAL_CORRELATION: ColumnElement[str | None] = case(
    (func.json_valid(ApprovalRow.proposed_action), func.json_extract(ApprovalRow.proposed_action, "$.correlation_id")),
    else_=None,
)


class ExternalGateRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def owner_bound(
        self,
        execution: ExecutionIdentity,
        pin: ExecutionContractPin,
        *,
        native: bool,
        sdk: str,
        session_id: str | None,
        attempt_id: str | None,
    ) -> bool:
        if await ExecutionContractRepository(self._factory).get(pin.job_id, pin.admission_id) != pin:
            return False
        async with self._factory() as session:
            events = EventRepository(session)
            if native:
                event = await events.get(f"native:{execution.execution_token}:bound")
                return bool(
                    event is not None
                    and event.session_id == execution.job_id
                    and event.metadata.source_framework == "codeplane"
                    and event.metadata.namespace == ("control", "native-execution")
                    and event.payload.get("execution") == asdict(execution)
                    and event.payload.get("admission_id") == pin.admission_id
                    and await events.get(f"native:{execution.execution_token}:released") is None
                )
            from backend.persistence.execution_contract_repo import _PIN_NAMESPACE

            rows = await session.scalars(
                select(EventRow).where(
                    EventRow.job_id == execution.job_id,
                    EventRow.kind == "execution.contract.handle_bound",
                    func.json_extract(EventRow.payload, "$.execution_token") == execution.execution_token,
                )
            )
            for row in rows:
                event = events._to_domain(row)
                if (
                    event.metadata.source_framework == "codeplane"
                    and event.metadata.namespace == _PIN_NAMESPACE
                    and event.metadata.integration_binding_id is None
                    and event.payload
                    == {
                        "admission_id": pin.admission_id,
                        "budget_id": pin.budget_id,
                        "sdk": sdk,
                        "session_id": session_id,
                        "live_epoch": execution.epoch,
                        "execution_token": execution.execution_token,
                        "attempt_id": attempt_id,
                    }
                ):
                    return True
            return False

    async def bind(
        self,
        source_id: str,
        credential_hash: str,
        execution: ExecutionIdentity,
        pin: ExecutionContractPin,
        external_session_id: str | None,
    ) -> None:
        if await ExecutionContractRepository(self._factory).get(pin.job_id, pin.admission_id) != pin:
            raise StateConflictError("External gate requires the actual durable execution pin")
        async with serialized_write(self._factory) as session:
            job = await JobRepository(session).get(execution.job_id)
            if job is None or job.observation_only:
                raise StateConflictError("Observation history cannot own an external gate")
            now = datetime.now(UTC)
            session.add(
                IntegrationBindingRow(
                    id=source_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    adapter_id="external-gate",
                    mode="execution",
                    enabled=True,
                    credential_hash=credential_hash,
                    config_json=json.dumps(
                        {
                            "purpose": "external-gate-control-v1",
                            "execution": asdict(execution),
                            "admission_id": pin.admission_id,
                            "budget_id": pin.budget_id,
                            "external_session_id": external_session_id,
                        },
                        sort_keys=True,
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )

    async def active(
        self, source_id: str, credential_hash: str, execution: ExecutionIdentity, pin: ExecutionContractPin
    ) -> bool:
        async with self._factory() as session:
            row = await session.get(IntegrationBindingRow, source_id)
            if row is None:
                return False
            config = json.loads(row.config_json)
            return bool(
                row.enabled
                and row.version == 1
                and row.mode == "execution"
                and row.adapter_id == "external-gate"
                and row.job_id == execution.job_id
                and row.credential_hash == credential_hash
                and config.get("purpose") == "external-gate-control-v1"
                and config.get("execution") == asdict(execution)
                and config.get("admission_id") == pin.admission_id
                and config.get("budget_id") == pin.budget_id
            )

    async def disable(self, source_id: str) -> None:
        async with serialized_write(self._factory) as session:
            row = await session.get(IntegrationBindingRow, source_id)
            if row is not None and row.adapter_id == "external-gate" and row.enabled:
                row.enabled = False
                row.version += 1
                row.updated_at = datetime.now(UTC)

    async def requested(
        self,
        source_id: str,
        request_id: str,
        digest: str,
        execution: ExecutionIdentity,
        metadata: EventMetadata,
    ) -> ExternalGateDecision:
        async with serialized_write(self._factory) as session:
            events = EventRepository(session)
            identity = correlation(source_id, request_id)
            previous = await events.get(f"{identity}:requested")
            if previous is None:
                await events.append(
                    new_event(
                        execution.job_id,
                        "external.gate.requested",
                        {
                            "source_id": source_id,
                            "request_id": request_id,
                            "action_digest": digest,
                            "execution": asdict(execution),
                        },
                        event_id=f"{identity}:requested",
                        metadata=metadata,
                    )
                )
            elif previous.payload.get("action_digest") != digest or previous.payload.get("execution") != asdict(
                execution
            ):
                raise StateConflictError("External gate request identity was reused for another action")
            return await self._state(session, source_id, request_id)

    async def state(self, source_id: str, request_id: str) -> ExternalGateDecision:
        async with self._factory() as session:
            return await self._state(session, source_id, request_id)

    async def _state(self, session: AsyncSession, source_id: str, request_id: str) -> ExternalGateDecision:
        events = EventRepository(session)
        identity = correlation(source_id, request_id)
        requested = await events.get(f"{identity}:requested")
        if requested is None:
            raise StateConflictError("External gate request is not registered")
        terminal = await events.get(f"{identity}:terminal")
        decided = await events.get(f"{identity}:decision")
        phase = ExternalGatePhase.pending
        reason = "awaiting_policy"
        if terminal is not None:
            phase = ExternalGatePhase(terminal.payload["phase"])
            reason = terminal.payload["reason"]
        elif decided is not None:
            phase = ExternalGatePhase.allowed if decided.payload["allowed"] else ExternalGatePhase.denied
            reason = "approved" if decided.payload["allowed"] else "policy_denied"
        approval_id = await session.scalar(
            select(ApprovalRow.id)
            .where(
                ApprovalRow.job_id == requested.session_id,
                _APPROVAL_CORRELATION.is_not_distinct_from(identity),
            )
            .order_by(ApprovalRow.requested_at.desc())
            .limit(1)
        )
        return ExternalGateDecision(
            source_id=source_id,
            request_id=request_id,
            action_digest=requested.payload["action_digest"],
            phase=phase,
            reason=reason,
            approval_id=approval_id,
        )

    async def decided(
        self,
        source_id: str,
        request_id: str,
        allowed: bool,
        metadata: EventMetadata,
    ) -> ExternalGateDecision:
        async with serialized_write(self._factory) as session:
            events = EventRepository(session)
            identity = correlation(source_id, request_id)
            request = await events.get(f"{identity}:requested")
            if request is None:
                raise StateConflictError("External gate request is not registered")
            if await events.get(f"{identity}:terminal") is None:
                await events.append(
                    new_event(
                        request.session_id,
                        "external.gate.decided",
                        {
                            "source_id": source_id,
                            "request_id": request_id,
                            "allowed": allowed,
                            "action_digest": request.payload["action_digest"],
                        },
                        event_id=f"{identity}:decision",
                        metadata=metadata,
                    )
                )
            return await self._state(session, source_id, request_id)

    async def finish(
        self,
        source_id: str,
        request_id: str,
        *,
        consume: bool,
        digest: str,
        reason: str,
        metadata: EventMetadata,
    ) -> tuple[ExternalGateDecision, bool]:
        async with serialized_write(self._factory) as session:
            state = await self._state(session, source_id, request_id)
            if consume and (state.phase != ExternalGatePhase.allowed or state.action_digest != digest):
                return state, False
            request = await EventRepository(session).get(f"{correlation(source_id, request_id)}:requested")
            assert request is not None
            phase = ExternalGatePhase.consumed if consume else ExternalGatePhase.retired
            event = new_event(
                request.session_id,
                "external.gate.consumed" if consume else "external.gate.retired",
                {
                    "source_id": source_id,
                    "request_id": request_id,
                    "action_digest": state.action_digest,
                    "phase": phase.value,
                    "reason": reason,
                },
                event_id=f"{correlation(source_id, request_id)}:terminal",
                metadata=metadata,
            )
            # One immutable terminal row is the database-level consumption CAS.
            inserted = await session.scalar(
                insert(EventRow)
                .values(
                    event_id=event.id,
                    job_id=event.session_id,
                    kind=event.kind,
                    timestamp=event.timestamp,
                    payload=json.dumps(dict(event.payload)),
                    event_metadata=event.metadata.model_dump_json(),
                )
                .on_conflict_do_nothing(index_elements=["event_id"])
                .returning(EventRow.id)
            )
            return await self._state(session, source_id, request_id), inserted is not None

    async def pending_approvals(self, source_id: str, request_id: str) -> list[str]:
        async with self._factory() as session:
            return list(
                await session.scalars(
                    select(ApprovalRow.id).where(
                        ApprovalRow.resolution.is_(None),
                        _APPROVAL_CORRELATION.is_not_distinct_from(correlation(source_id, request_id)),
                    )
                )
            )

    async def report(self, event: SessionEvent) -> None:
        async with serialized_write(self._factory) as session:
            await EventRepository(session).append(event)

    async def stale_sources(self, epoch: str) -> list[tuple[str, str, list[str]]]:
        async with self._factory() as session:
            rows = list(
                await session.scalars(
                    select(IntegrationBindingRow).where(
                        IntegrationBindingRow.adapter_id == "external-gate",
                        func.json_extract(IntegrationBindingRow.config_json, "$.execution.epoch") != epoch,
                    )
                )
            )
            result = []
            for row in rows:
                requests = list(
                    await session.scalars(
                        select(EventRow).where(
                            EventRow.kind == "external.gate.requested",
                            func.json_extract(EventRow.payload, "$.source_id") == row.id,
                        )
                    )
                )
                result.append((row.id, row.job_id or "", [json.loads(item.payload)["request_id"] for item in requests]))
            return result
