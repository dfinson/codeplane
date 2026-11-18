"""Approval request persistence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select, update

from backend.models.db import (
    ApprovalRow,
    EventRow,
    RecipeAttemptExecutionRow,
    RecipeAttemptRow,
    RecipeGateBindingRow,
    RecipeRunRow,
    RecipeStepInstanceRow,
)
from backend.models.domain import Approval, ApprovalResolution
from backend.models.events import EventKind
from backend.models.session_event import EventMetadata
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime

    from backend.models.api_schemas import JobPendingDecisionPage

log = structlog.get_logger()


class ApprovalRepository(BaseRepository):
    """Database access for approval request records."""

    @staticmethod
    def _to_domain(row: ApprovalRow) -> Approval:
        return Approval(
            id=row.id,
            job_id=row.job_id,
            description=row.description,
            proposed_action=row.proposed_action,
            requested_at=row.requested_at,
            resolved_at=row.resolved_at,
            resolution=ApprovalResolution(row.resolution) if row.resolution else None,
            requires_explicit_approval=row.requires_explicit_approval or False,
            notes=row.notes,
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

    async def has_blocking_approvals(self, job_id: str, *, attempt_id: str | None = None) -> bool:
        """Exclude only positively attributed sibling gates; unknown scope blocks."""
        pending = await self.list_pending(job_id)
        if attempt_id is None:
            return bool(pending)
        if not pending:
            return False

        target = await self._session.scalar(
            select(RecipeAttemptRow.id)
            .join(RecipeStepInstanceRow, RecipeStepInstanceRow.id == RecipeAttemptRow.step_instance_id)
            .join(RecipeRunRow, RecipeRunRow.id == RecipeStepInstanceRow.run_id)
            .where(
                RecipeAttemptRow.id == attempt_id,
                RecipeStepInstanceRow.current_attempt_id == attempt_id,
                RecipeRunRow.job_id == job_id,
                RecipeRunRow.resolved_at.is_(None),
            )
        )
        if target is None:
            return True
        for approval in pending:
            owner = await self._attempt_owner(approval)
            if owner is None or owner == attempt_id:
                return True
        return False

    async def pending_decisions(self, job_id: str, *, after: str = "", limit: int = 20) -> JobPendingDecisionPage:
        from backend.models.api_schemas import ApprovalResponse, JobPendingDecision, JobPendingDecisionPage

        if not 1 <= limit <= 100:
            raise ValueError("Pending decision page size must be between 1 and 100.")
        rows = list(
            await self._session.scalars(
                select(ApprovalRow)
                .where(
                    ApprovalRow.job_id == job_id,
                    ApprovalRow.resolution.is_(None),
                    ApprovalRow.id > after,
                )
                .order_by(ApprovalRow.id)
                .limit(limit + 1)
            )
        )
        items = []
        for row in rows[:limit]:
            approval = self._to_domain(row)
            attempt_id = await self._attempt_owner(approval)
            step = (
                await self._session.scalar(
                    select(RecipeStepInstanceRow).where(
                        RecipeStepInstanceRow.current_attempt_id == attempt_id,
                    )
                )
                if attempt_id is not None
                else None
            )
            human_gate = (
                await self._session.scalar(
                    select(RecipeGateBindingRow.attempt_id).where(
                        RecipeGateBindingRow.approval_id == row.id,
                    )
                )
                if attempt_id is not None
                else None
            )
            items.append(
                JobPendingDecision(
                    approval=ApprovalResponse.model_validate(approval, from_attributes=True),
                    recipe_run_id=step.run_id if step else None,
                    step_instance_id=step.id if step else None,
                    attempt_id=attempt_id,
                    human_gate=human_gate is not None,
                )
            )
        return JobPendingDecisionPage(
            items=items,
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    async def _attempt_owner(self, approval: Approval) -> str | None:
        gate = await self._session.scalar(
            select(RecipeGateBindingRow).where(RecipeGateBindingRow.approval_id == approval.id)
        )
        if gate is not None:
            owner = await self._session.scalar(
                select(RecipeAttemptRow.id)
                .join(RecipeStepInstanceRow, RecipeStepInstanceRow.id == RecipeAttemptRow.step_instance_id)
                .join(RecipeRunRow, RecipeRunRow.id == RecipeStepInstanceRow.run_id)
                .where(
                    RecipeAttemptRow.id == gate.attempt_id,
                    RecipeStepInstanceRow.current_attempt_id == gate.attempt_id,
                    RecipeRunRow.job_id == approval.job_id,
                    RecipeRunRow.resolved_at.is_(None),
                )
            )
            if owner is None:
                log.warning("approval_gate_scope_conflict", approval_id=approval.id, job_id=approval.job_id)
            return owner
        # Tool arguments in proposed_action are not authority to declare scope.
        # Older SDK requests obtain scope only from their persisted app event.
        result = await self._session.execute(
            select(EventRow)
            .where(
                EventRow.job_id == approval.job_id,
                EventRow.kind == EventKind.approval_requested.value,
                func.json_extract(EventRow.payload, "$.approval_id") == approval.id,
            )
            .limit(2)
        )
        events = result.scalars().all()
        if len(events) != 1:
            if events:
                log.warning("approval_event_scope_ambiguous", approval_id=approval.id, job_id=approval.job_id)
            return None
        try:
            metadata = EventMetadata.model_validate_json(events[0].event_metadata or "{}")
        except ValidationError:
            log.warning("approval_event_scope_invalid", approval_id=approval.id, job_id=approval.job_id)
            return None
        if (
            metadata.integration_binding_id is not None
            or bool(metadata.namespace)
            or not metadata.attempt_id
            or not metadata.step_instance_id
            or not metadata.recipe_run_id
            or not metadata.execution_epoch
            or metadata.source_framework not in {"copilot", "claude", "codeplane.recipe"}
            or json.loads(events[0].payload).get("proposed_action") != approval.proposed_action
        ):
            return None
        attempt = await self._session.scalar(
            select(RecipeAttemptRow)
            .join(RecipeStepInstanceRow, RecipeStepInstanceRow.id == RecipeAttemptRow.step_instance_id)
            .join(RecipeRunRow, RecipeRunRow.id == RecipeStepInstanceRow.run_id)
            .where(
                RecipeAttemptRow.id == metadata.attempt_id,
                RecipeStepInstanceRow.id == metadata.step_instance_id,
                RecipeStepInstanceRow.current_attempt_id == metadata.attempt_id,
                RecipeRunRow.id == metadata.recipe_run_id,
                RecipeRunRow.job_id == approval.job_id,
                RecipeRunRow.resolved_at.is_(None),
            )
        )
        if attempt is None:
            log.warning("approval_event_owner_conflict", approval_id=approval.id, job_id=approval.job_id)
            return None
        if metadata.source_framework == "codeplane.recipe":
            return attempt.id if json.loads(attempt.runner_json).get("kind") == "command" else None
        execution = await self._session.scalar(
            select(RecipeAttemptExecutionRow)
            .where(RecipeAttemptExecutionRow.attempt_id == attempt.id)
            .order_by(RecipeAttemptExecutionRow.sequence.desc())
            .limit(1)
        )
        if (
            execution is None
            or not metadata.execution_token
            or not metadata.run_id
            or execution.execution_token != metadata.execution_token
            or execution.session_id != metadata.run_id
            or execution.provider != metadata.source_framework
        ):
            return None
        return attempt.id

    async def resolve(
        self,
        approval_id: str,
        resolution: ApprovalResolution,
        resolved_at: datetime,
        notes: str | None = None,
    ) -> Approval | None:
        """Mark an approval as resolved atomically. Returns updated approval or None.

        Uses UPDATE ... WHERE resolution IS NULL to prevent double-resolve race.
        Returns None if the row doesn't exist or was already resolved.
        """
        values: dict[str, object] = {"resolution": resolution, "resolved_at": resolved_at}
        if notes is not None:
            values["notes"] = notes
        stmt = (
            update(ApprovalRow).where(ApprovalRow.id == approval_id, ApprovalRow.resolution.is_(None)).values(**values)
        )
        result = await self._session.execute(stmt)
        # CursorResult.rowcount is always present for DML but missing from the generic Result type stub
        if result.rowcount == 0:  # type: ignore[attr-defined]  # CursorResult.rowcount not in generic stub
            return None
        await self._session.flush()
        # Re-fetch the updated row
        fetch_stmt = select(ApprovalRow).where(ApprovalRow.id == approval_id)
        fetch_result = await self._session.execute(fetch_stmt)
        row = fetch_result.scalar_one_or_none()
        return self._to_domain(row) if row else None
