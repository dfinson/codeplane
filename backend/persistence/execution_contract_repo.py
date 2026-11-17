"""Trusted admission records using the existing canonical event store."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from backend.models.domain import StateConflictError
from backend.models.events import SessionEvent, new_event
from backend.models.execution_contracts import ExecutionContractPin
from backend.models.session_event import EventMetadata
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_PIN_KIND = "execution.contract.pinned"
_PIN_NAMESPACE = ("control", "execution-contract")


def _identity(job_id: str, admission_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            json.dumps(
                ["codeplane-contract-pin-v1", job_id, admission_id],
                separators=(",", ":"),
            ),
        )
    )


def _decode(event: SessionEvent, job_id: str, admission_id: str) -> ExecutionContractPin:
    if (
        event.kind != _PIN_KIND
        or event.session_id != job_id
        or event.metadata.source_framework != "codeplane"
        or event.metadata.namespace != _PIN_NAMESPACE
        or event.metadata.integration_binding_id is not None
    ):
        raise StateConflictError("Execution contract pin has untrusted provenance")
    pin = ExecutionContractPin.model_validate(event.payload)
    if pin.job_id != job_id or pin.admission_id != admission_id or pin.budget_id != job_id:
        raise StateConflictError("Execution contract ownership or carried Job budget changed")
    return pin


class ExecutionContractRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, job_id: str, admission_id: str) -> ExecutionContractPin | None:
        if not job_id or not admission_id:
            raise ValueError("Contract lookup requires a durable Job and admission identity")
        async with self._session_factory() as session:
            job = await JobRepository(session).get(job_id)
            if job is None or job.observation_only:
                raise StateConflictError("Only trusted Job execution may consume an admission contract")
            event = await EventRepository(session).get(_identity(job_id, admission_id))
            return _decode(event, job_id, admission_id) if event is not None else None

    async def create(self, pin: ExecutionContractPin) -> ExecutionContractPin:
        async with serialized_write(self._session_factory) as session:
            job = await JobRepository(session).get(pin.job_id)
            if job is None or job.observation_only or pin.budget_id != pin.job_id:
                raise StateConflictError("Only trusted Job admission may pin an execution contract")
            events = EventRepository(session)
            identity = _identity(pin.job_id, pin.admission_id)
            previous = await events.get(identity)
            if previous is not None:
                retained = _decode(previous, pin.job_id, pin.admission_id)
                if retained != pin:
                    raise StateConflictError("Conflicting execution contract pin")
                return retained
            await events.append(
                new_event(
                    pin.job_id,
                    _PIN_KIND,
                    pin.model_dump(mode="json"),
                    event_id=identity,
                    metadata=EventMetadata(source_framework="codeplane", namespace=_PIN_NAMESPACE),
                )
            )
        return pin

    async def bind_handle(
        self,
        pin: ExecutionContractPin,
        *,
        sdk: str,
        session_id: str,
        live_epoch: str,
        execution_token: str,
        attempt_id: str | None = None,
    ) -> None:
        if not sdk or not session_id or not live_epoch or not execution_token:
            raise ValueError("A handle binding requires actual provider, session and live fencing identities")
        async with serialized_write(self._session_factory) as session:
            events = EventRepository(session)
            source = await events.get(_identity(pin.job_id, pin.admission_id))
            if source is None or _decode(source, pin.job_id, pin.admission_id) != pin:
                raise StateConflictError("A handle cannot bind a missing or different admission contract")
            identity = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    json.dumps(
                        [
                            "codeplane-contract-handle-v1",
                            pin.job_id,
                            pin.admission_id,
                            execution_token,
                        ],
                        separators=(",", ":"),
                    ),
                )
            )
            await events.append(
                new_event(
                    pin.job_id,
                    "execution.contract.handle_bound",
                    {
                        "admission_id": pin.admission_id,
                        "budget_id": pin.budget_id,
                        "sdk": sdk,
                        "session_id": session_id,
                        "live_epoch": live_epoch,
                        "execution_token": execution_token,
                        "attempt_id": attempt_id,
                    },
                    event_id=identity,
                    metadata=EventMetadata(
                        source_framework="codeplane",
                        namespace=_PIN_NAMESPACE,
                        run_id=session_id,
                        execution_epoch=live_epoch,
                        execution_token=execution_token,
                        attempt_id=attempt_id,
                    ),
                )
            )
