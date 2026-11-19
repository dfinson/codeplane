"""Transactional tracker outbox access; callers own the commit boundary."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert

from backend.models.db import ApprovalRow, JobRow, TrackerOperationRow
from backend.models.domain import Approval, CodePlaneError, StateConflictError
from backend.models.schemas.tracker_operations import TrackerOperationResponse
from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import CursorResult

_JSON_FIELDS = ("approval_snapshot", "provider_receipt", "reconciliation")
_MUTABLE_FIELDS = frozenset(
    {
        "state",
        "approval_id",
        "approval_ref",
        "approval_snapshot",
        "claim_token",
        "claim_expires_at",
        "provider_receipt",
        "error_code",
        "error_detail",
        "reconciliation",
        "dispatch_started_at",
        "completed_at",
    }
)
_TRANSITIONS = {
    "awaiting_approval": {"approved", "rejected", "failed"},
    "approved": {"dispatching", "rejected", "failed"},
    "dispatching": {"applied", "failed", "uncertain"},
    "uncertain": {"applied", "not_applied"},
}


class TrackerOperationNotFoundError(CodePlaneError):
    """The operation does not exist in the requested Project history."""


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for field in _JSON_FIELDS:
        value = result.get(field)
        if value is not None:
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            result[field] = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return result


class TrackerOperationRepository(BaseRepository):
    @staticmethod
    def _response(row: TrackerOperationRow) -> TrackerOperationResponse:
        values = {column.name: getattr(row, column.name) for column in TrackerOperationRow.__table__.columns}
        for field in _JSON_FIELDS:
            if values[field] is not None:
                values[field] = json.loads(values[field])
        return TrackerOperationResponse.model_validate(values)

    async def get(self, operation_id: str) -> TrackerOperationResponse | None:
        row = await self._session.scalar(
            select(TrackerOperationRow)
            .where(TrackerOperationRow.id == operation_id)
            .execution_options(populate_existing=True)
        )
        return self._response(row) if row is not None else None

    async def get_by_source_key(self, source_key: str) -> TrackerOperationResponse | None:
        row = await self._session.scalar(
            select(TrackerOperationRow).where(TrackerOperationRow.source_key == source_key)
        )
        return self._response(row) if row is not None else None

    async def create(self, operation: TrackerOperationResponse) -> TrackerOperationResponse:
        values = operation.model_dump(exclude={"operation_id", "normalized_target"})
        for field in _JSON_FIELDS:
            values[field] = getattr(operation, field)
        await self._session.execute(
            insert(TrackerOperationRow).values(**_encode(values)).on_conflict_do_nothing(index_elements=["source_key"])
        )
        existing = await self.get_by_source_key(operation.source_key)
        assert existing is not None
        # A repeated source identity cannot be used to replace its request or owner.
        identity = (
            "project_ref",
            "job_ref",
            "task_link_ref",
            "tracker_link_ref",
            "provider",
            "base_url",
            "external_ref",
            "credential_id_ref",
            "ticket_ref",
            "action",
            "target_kind",
            "value",
            "normalized_value",
            "retry_of_id",
        )
        if any(getattr(existing, field) != getattr(operation, field) for field in identity):
            raise StateConflictError("Tracker source key already names a different request")
        return existing

    async def list_operations(
        self,
        project_ref: str,
        *,
        job_ref: str | None = None,
        tracker_link_ref: str | None = None,
    ) -> list[TrackerOperationResponse]:
        statement = select(TrackerOperationRow).where(TrackerOperationRow.project_ref == project_ref)
        if job_ref is not None:
            statement = statement.where(TrackerOperationRow.job_ref == job_ref)
        if tracker_link_ref is not None:
            statement = statement.where(TrackerOperationRow.tracker_link_ref == tracker_link_ref)
        result = await self._session.scalars(statement.order_by(TrackerOperationRow.created_at, TrackerOperationRow.id))
        return [self._response(row) for row in result]

    async def list_states(self, *states: str) -> list[TrackerOperationResponse]:
        statement = select(TrackerOperationRow)
        if states:
            statement = statement.where(TrackerOperationRow.state.in_(states))
        result = await self._session.scalars(statement.order_by(TrackerOperationRow.created_at, TrackerOperationRow.id))
        return [self._response(row) for row in result]

    async def list_for_job_ref(self, job_ref: str) -> list[TrackerOperationResponse]:
        statement = (
            select(TrackerOperationRow)
            .where(TrackerOperationRow.job_ref == job_ref)
            .order_by(TrackerOperationRow.created_at, TrackerOperationRow.id)
        )
        return [self._response(row) for row in await self._session.scalars(statement)]

    async def list_approvals(self) -> list[Approval]:
        rows = await self._session.scalars(select(ApprovalRow).order_by(ApprovalRow.requested_at, ApprovalRow.id))
        return [ApprovalRepository._to_domain(row) for row in rows]

    async def live_job_id(self, operation: TrackerOperationResponse) -> str:
        if operation.job_id is None:
            return ""
        return await self._session.scalar(select(JobRow.id).where(JobRow.id == operation.job_id)) or ""

    async def claim(
        self,
        operation_id: str,
        *,
        expected_version: int,
        token: str,
        now: datetime,
        expires_at: datetime,
    ) -> TrackerOperationResponse | None:
        result = await self._session.execute(
            update(TrackerOperationRow)
            .where(
                TrackerOperationRow.id == operation_id,
                TrackerOperationRow.version == expected_version,
                TrackerOperationRow.state == "awaiting_approval",
                TrackerOperationRow.approval_ref.is_(None),
                or_(TrackerOperationRow.claim_token.is_(None), TrackerOperationRow.claim_expires_at <= now),
            )
            .values(
                claim_token=token,
                claim_expires_at=expires_at,
                version=TrackerOperationRow.version + 1,
                updated_at=now,
            )
        )
        return await self.get(operation_id) if cast("CursorResult[Any]", result).rowcount else None

    async def release_claim(self, operation_id: str, token: str, now: datetime) -> None:
        await self._session.execute(
            update(TrackerOperationRow)
            .where(
                TrackerOperationRow.id == operation_id,
                TrackerOperationRow.claim_token == token,
                TrackerOperationRow.state == "awaiting_approval",
            )
            .values(claim_token=None, claim_expires_at=None, updated_at=now, version=TrackerOperationRow.version + 1)
        )

    async def compare_and_set(
        self,
        operation: TrackerOperationResponse,
        *,
        now: datetime,
        changes: dict[str, Any],
        claim_token: str | None = None,
    ) -> TrackerOperationResponse | None:
        if set(changes) - _MUTABLE_FIELDS:
            raise ValueError("Cannot change immutable tracker operation identity")
        new_state = changes.get("state", operation.state)
        if new_state != operation.state and new_state not in _TRANSITIONS.get(operation.state, set()):
            raise StateConflictError("Invalid tracker operation transition")
        if operation.approval_ref is not None and (
            ("approval_ref" in changes and changes["approval_ref"] != operation.approval_ref)
            or ("approval_id" in changes and changes["approval_id"] != operation.approval_id)
        ):
            raise StateConflictError("The canonical tracker approval cannot be replaced")
        if (
            operation.approval_ref is None
            and changes.get("approval_ref") is not None
            and (claim_token is None or operation.state != "awaiting_approval")
        ):
            raise StateConflictError("Canonical approval binding requires the current provisioning claim")
        statement = update(TrackerOperationRow).where(
            TrackerOperationRow.id == operation.id,
            TrackerOperationRow.version == operation.version,
            TrackerOperationRow.state == operation.state,
        )
        if claim_token is not None:
            statement = statement.where(
                TrackerOperationRow.claim_token == claim_token,
                TrackerOperationRow.claim_expires_at > now,
            )
        result = await self._session.execute(
            statement.values(
                **_encode(changes),
                updated_at=now,
                version=TrackerOperationRow.version + 1,
            )
        )
        return await self.get(operation.id) if cast("CursorResult[Any]", result).rowcount else None
