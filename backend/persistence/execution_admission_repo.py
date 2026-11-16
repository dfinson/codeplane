"""Immutable legacy admission transitions in the existing application event store."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from backend.models.db import EventRow
from backend.models.domain import JobSource, StateConflictError
from backend.models.events import SessionEvent, new_event
from backend.models.execution_contracts import ExecutionAdmission
from backend.models.session_event import EventMetadata
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.repository import BaseRepository

_DECLARED = "execution.admission.declared"
_ABANDONED = "execution.admission.abandoned"
_NAMESPACE = ("control", "legacy-admission")


def _identity(job_id: str, admission_id: str, kind: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        ["codeplane-legacy-admission-v1", job_id, admission_id, kind], separators=(",", ":"),
    )))


def _trusted(event: SessionEvent, job_id: str) -> None:
    if (
        event.session_id != job_id or event.metadata.source_framework != "codeplane"
        or event.metadata.namespace != _NAMESPACE or event.metadata.integration_binding_id is not None
    ):
        raise StateConflictError("Legacy execution admission has untrusted provenance")


class ExecutionAdmissionRepository(BaseRepository):
    """Call writes inside the caller's serialized Job creation/reset transaction.

    The caller must positively select the non-recipe creation/continuation path.
    Absence of a not-yet-created recipe run is not an ownership discriminator.
    """

    async def _require_managed(self, job_id: str) -> None:
        job = await JobRepository(self._session).get(job_id)
        if job is None or job.observation_only or job.source != JobSource.managed:
            raise StateConflictError("Only managed Job admission may declare a legacy execution")

    async def _declared(self, job_id: str, admission_id: str) -> ExecutionAdmission:
        event = await EventRepository(self._session).get(_identity(job_id, admission_id, _DECLARED))
        if event is None or event.kind != _DECLARED:
            raise StateConflictError("Current legacy admission declaration is missing")
        _trusted(event, job_id)
        admission = ExecutionAdmission.model_validate(event.payload)
        if admission.job_id != job_id or admission.id != admission_id:
            raise StateConflictError("Legacy admission identity changed")
        return admission

    async def current(self, job_id: str) -> ExecutionAdmission | None:
        await self._require_managed(job_id)
        result = await self._session.execute(
            select(EventRow).where(
                EventRow.job_id == job_id, EventRow.kind.in_((_DECLARED, _ABANDONED)),
            ).order_by(EventRow.id.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        event = EventRepository._to_domain(row)
        _trusted(event, job_id)
        if event.kind == _DECLARED:
            admission = ExecutionAdmission.model_validate(event.payload)
            if admission.job_id != job_id or event.id != _identity(job_id, admission.id, _DECLARED):
                raise StateConflictError("Legacy admission declaration identity changed")
            return admission
        abandoned_id = event.payload.get("admission_id")
        if (
            set(event.payload) != {"admission_id", "restored_admission_id", "reason"}
            or not isinstance(event.payload.get("reason"), str) or not event.payload["reason"]
            or not isinstance(abandoned_id, str) or event.id != _identity(job_id, abandoned_id, _ABANDONED)
        ):
            raise StateConflictError("Legacy admission retirement identity changed")
        abandoned = await self._declared(job_id, abandoned_id)
        previous_id = abandoned.previous_admission_id
        if event.payload.get("restored_admission_id") != previous_id:
            raise StateConflictError("Legacy admission rollback target changed")
        return await self._declared(job_id, previous_id) if previous_id is not None else None

    async def declare_legacy(
        self,
        admission: ExecutionAdmission,
        *,
        expected_current: str | None,
    ) -> ExecutionAdmission:
        current = await self.current(admission.job_id)
        if (
            (current.id if current is not None else None) != expected_current
            or admission.previous_admission_id != expected_current
            or (admission.origin == "creation" and expected_current is not None)
        ):
            raise StateConflictError("Legacy admission changed before the Job transaction committed")
        events = EventRepository(self._session)
        identity = _identity(admission.job_id, admission.id, _DECLARED)
        if await events.get(identity) is not None:
            raise StateConflictError("Legacy admission identities cannot be reused")
        await events.append(new_event(
            admission.job_id, _DECLARED, admission.model_dump(mode="json"), event_id=identity,
            metadata=EventMetadata(source_framework="codeplane", namespace=_NAMESPACE),
        ))
        return admission

    async def abandon(self, admission: ExecutionAdmission, *, reason: str) -> None:
        """Restore the previous selection without deleting either admission or pin."""
        if not reason:
            raise ValueError("Admission abandonment requires a recorded reason")
        current = await self.current(admission.job_id)
        if current != admission:
            raise StateConflictError("Cannot abandon a replaced legacy admission")
        await EventRepository(self._session).append(new_event(
            admission.job_id, _ABANDONED,
            {
                "admission_id": admission.id, "restored_admission_id": admission.previous_admission_id,
                "reason": reason,
            },
            event_id=_identity(admission.job_id, admission.id, _ABANDONED),
            metadata=EventMetadata(source_framework="codeplane", namespace=_NAMESPACE),
        ))
