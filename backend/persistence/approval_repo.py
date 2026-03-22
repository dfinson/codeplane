"""Approval request persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select, update

from backend.models.db import ApprovalRow
from backend.models.domain import Approval
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime


class ApprovalRepository(BaseRepository):
    """Database access for approval request records."""

    @staticmethod
    def _to_domain(row: ApprovalRow) -> Approval:
        # SQLAlchemy Column descriptors return Any at the type level;
        # cast() documents the expected runtime type for each field.
        return Approval(
            id=cast("str", row.id),
            job_id=cast("str", row.job_id),
            description=cast("str", row.description),
            proposed_action=cast("str | None", row.proposed_action),
            requested_at=cast("datetime", row.requested_at),
            resolved_at=cast("datetime | None", row.resolved_at),
            resolution=cast("str | None", row.resolution),
            requires_explicit_approval=cast("bool", row.requires_explicit_approval or False),
        )

    async def create(self, approval: Approval) -> Approval:
        """Insert an approval request record."""
        row = ApprovalRow(
            id=approval.id,
            job_id=approval.job_id,
            description=approval.description,
            proposed_action=approval.proposed_action,
            requested_at=approval.requested_at,
            resolved_at=approval.resolved_at,
            resolution=approval.resolution,
            requires_explicit_approval=approval.requires_explicit_approval,
        )
        self._session.add(row)
        await self._session.flush()
        return approval

    async def get(self, approval_id: str) -> Approval | None:
        """Get a single approval by ID."""
        stmt = select(ApprovalRow).where(ApprovalRow.id == approval_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list_for_job(self, job_id: str) -> list[Approval]:
        """List all approvals for a given job, ordered by requested_at."""
        stmt = select(ApprovalRow).where(ApprovalRow.job_id == job_id).order_by(ApprovalRow.requested_at)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def list_pending(self, job_id: str | None = None) -> list[Approval]:
        """List unresolved approvals, optionally filtered by job_id."""
        stmt = select(ApprovalRow).where(ApprovalRow.resolution.is_(None))
        if job_id is not None:
            stmt = stmt.where(ApprovalRow.job_id == job_id)
        stmt = stmt.order_by(ApprovalRow.requested_at)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def resolve(
        self,
        approval_id: str,
        resolution: str,
        resolved_at: datetime,
    ) -> Approval | None:
        """Mark an approval as resolved atomically. Returns updated approval or None.

        Uses UPDATE ... WHERE resolution IS NULL to prevent double-resolve race.
        Returns None if the row doesn't exist or was already resolved.
        """
        stmt = (
            update(ApprovalRow)
            .where(ApprovalRow.id == approval_id, ApprovalRow.resolution.is_(None))
            .values(resolution=resolution, resolved_at=resolved_at)
        )
        result = await self._session.execute(stmt)
        # CursorResult.rowcount is always present but not in the generic type stub
        if cast("int", result.rowcount) == 0:  # type: ignore[attr-defined]
            return None
        await self._session.flush()
        # Re-fetch the updated row
        fetch_stmt = select(ApprovalRow).where(ApprovalRow.id == approval_id)
        fetch_result = await self._session.execute(fetch_stmt)
        row = fetch_result.scalar_one_or_none()
        return self._to_domain(row) if row else None
