"""Bounded Board reads using persisted Project ownership, never loaded UI pages."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import case, exists, func, literal, or_, select, union_all

from backend.models.api_schemas import (
    ApprovalResponse,
    JobResponse,
    ProjectApprovalPage,
    ProjectBoardColumn,
    ProjectBoardItem,
    ProjectBoardPage,
    ProjectBoardTotals,
    ProjectDeliveryPage,
    ProjectRecipeStep,
    ProjectRecipeSummary,
    ProjectTaskSummary,
)
from backend.models.db import ApprovalRow, JobRow, RecipeRunRow, RecipeStepInstanceRow, TaskLinkRow, TrackerOperationRow
from backend.models.domain import StateConflictError
from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.repository import BaseRepository
from backend.persistence.tracker_operation_repo import TrackerOperationRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.sql.selectable import Subquery

BOARD_COLUMNS: tuple[ProjectBoardColumn, ...] = ("ready_blocked", "in_progress", "needs_decision", "failed")
_DELIVERY_ATTENTION = ("awaiting_approval", "approved", "dispatching", "uncertain", "failed", "not_applied")


class ProjectBoardRepository(BaseRepository):
    @asynccontextmanager
    async def read_snapshot(self) -> AsyncIterator[None]:
        # SQLite's legacy transaction mode does not BEGIN on SELECT. A savepoint
        # pins these bounded reads without serializing concurrent WAL writers.
        async with self._session.begin_nested():
            yield

    @staticmethod
    def _work(project_id: str) -> Subquery:
        pending = (
            select(func.count(ApprovalRow.id))
            .where(ApprovalRow.job_id == JobRow.id, ApprovalRow.resolution.is_(None))
            .scalar_subquery()
        )
        task_count = (
            select(func.count(TaskLinkRow.id))
            .where(
                TaskLinkRow.project_id == project_id,
                TaskLinkRow.job_id == JobRow.id,
            )
            .scalar_subquery()
        )
        task_failed = exists(
            select(TaskLinkRow.id).where(
                TaskLinkRow.project_id == project_id,
                TaskLinkRow.job_id == JobRow.id,
                TaskLinkRow.state == "failed",
            )
        )
        failure = or_(JobRow.state == "failed", task_failed)
        delivery = (
            select(func.count(TrackerOperationRow.id))
            .where(
                TrackerOperationRow.project_ref == project_id,
                TrackerOperationRow.job_ref == JobRow.id,
                TrackerOperationRow.state.in_(_DELIVERY_ATTENTION),
            )
            .scalar_subquery()
        )
        delivery_decision = exists(
            select(TrackerOperationRow.id).where(
                TrackerOperationRow.project_ref == project_id,
                TrackerOperationRow.job_ref == JobRow.id,
                TrackerOperationRow.state.in_(("awaiting_approval", "uncertain", "failed", "not_applied")),
            )
        )
        review = or_(
            JobRow.state.in_(("review", "waiting_for_approval")),
            (JobRow.state == "completed") & or_(JobRow.resolution.is_(None), JobRow.resolution == "unresolved"),
        )
        jobs = select(
            (literal("job:") + JobRow.id).label("key"),
            JobRow.id.label("job_id"),
            literal(None).label("task_id"),
            case(
                (pending > 0, "needs_decision"),
                (delivery_decision, "needs_decision"),
                (delivery > 0, "in_progress"),
                (JobRow.archived_at.is_not(None), None),
                (review, "needs_decision"),
                (JobRow.state.in_(("preparing", "queued", "running")), "in_progress"),
                (failure, "failed"),
                else_=None,
            ).label("bucket"),
            task_count.label("task_count"),
            pending.label("pending_count"),
            delivery.label("delivery_count"),
            failure.label("has_failure"),
        ).where(JobRow.project_id == project_id)
        owned_job = exists(
            select(JobRow.id).where(
                JobRow.id == TaskLinkRow.job_id,
                JobRow.project_id == project_id,
            )
        )
        tasks = select(
            (literal("task:") + TaskLinkRow.id).label("key"),
            literal(None).label("job_id"),
            TaskLinkRow.id.label("task_id"),
            case(
                (TaskLinkRow.job_id.is_not(None), "failed"),
                (TaskLinkRow.state == "failed", "failed"),
                (TaskLinkRow.state.in_(("starting", "running")), "in_progress"),
                (TaskLinkRow.state == "completed", None),
                else_="ready_blocked",
            ).label("bucket"),
            literal(1).label("task_count"),
            literal(0).label("pending_count"),
            literal(0).label("delivery_count"),
            or_(TaskLinkRow.state == "failed", TaskLinkRow.job_id.is_not(None)).label("has_failure"),
        ).where(TaskLinkRow.project_id == project_id, ~owned_job)
        return union_all(jobs, tasks).subquery()

    async def totals(self, project_id: str) -> ProjectBoardTotals:
        task_count = await self._session.scalar(
            select(func.count(TaskLinkRow.id)).where(TaskLinkRow.project_id == project_id)
        )
        linked_job_count = await self._session.scalar(
            select(func.count(func.distinct(TaskLinkRow.job_id)))
            .join(JobRow, JobRow.id == TaskLinkRow.job_id)
            .where(TaskLinkRow.project_id == project_id, JobRow.project_id == project_id)
        )
        job_count = await self._session.scalar(select(func.count(JobRow.id)).where(JobRow.project_id == project_id))
        pending_count = await self._session.scalar(
            select(func.count(ApprovalRow.id))
            .join(JobRow, JobRow.id == ApprovalRow.job_id)
            .where(JobRow.project_id == project_id, ApprovalRow.resolution.is_(None))
        )
        review_count = await self._session.scalar(
            select(func.count(JobRow.id)).where(
                JobRow.project_id == project_id,
                JobRow.archived_at.is_(None),
                JobRow.state == "review",
            )
        )
        delivery_count = await self._session.scalar(
            select(func.count(TrackerOperationRow.id)).where(
                TrackerOperationRow.project_ref == project_id,
                TrackerOperationRow.state.in_(_DELIVERY_ATTENTION),
            )
        )
        work = self._work(project_id)
        counts = {column: 0 for column in BOARD_COLUMNS}
        for bucket, count in await self._session.execute(
            select(work.c.bucket, func.count()).where(work.c.bucket.is_not(None)).group_by(work.c.bucket)
        ):
            counts[bucket] = count
        return ProjectBoardTotals(
            task_count=task_count or 0,
            linked_job_count=linked_job_count or 0,
            standalone_job_count=(job_count or 0) - (linked_job_count or 0),
            pending_approval_count=pending_count or 0,
            review_job_count=review_count or 0,
            delivery_attention_count=delivery_count or 0,
            work_counts=counts,
        )

    async def page(
        self, project_id: str, column: ProjectBoardColumn, *, after: str = "", limit: int = 20
    ) -> ProjectBoardPage:
        if column not in BOARD_COLUMNS or not 1 <= limit <= 100:
            raise StateConflictError("Invalid Project Board page.")
        work = self._work(project_id)
        rows = list(
            (
                await self._session.execute(
                    select(work)
                    .where(work.c.bucket == column, work.c.key > after)
                    .order_by(work.c.key)
                    .limit(limit + 1)
                )
            ).mappings()
        )
        items = []
        for row in rows[:limit]:
            job = await JobRepository(self._session).get(row["job_id"]) if row["job_id"] else None
            task_filter = TaskLinkRow.job_id == row["job_id"] if row["job_id"] else TaskLinkRow.id == row["task_id"]
            tasks = (
                await self._session.execute(
                    select(
                        TaskLinkRow.id,
                        TaskLinkRow.repo_path,
                        TaskLinkRow.story_node_id,
                        TaskLinkRow.tracker_ticket_ref,
                        TaskLinkRow.state,
                    )
                    .where(
                        TaskLinkRow.project_id == project_id,
                        task_filter,
                    )
                    .order_by(TaskLinkRow.id)
                    .limit(20)
                )
            ).mappings()
            recipe = None
            if job is not None:
                current = (
                    (
                        await self._session.execute(
                            select(
                                RecipeRunRow.id,
                                RecipeRunRow.state,
                                RecipeRunRow.reason,
                                func.json_extract(RecipeRunRow.snapshot_json, "$.revision").label("revision"),
                                func.json_extract(RecipeRunRow.snapshot_json, "$.definition.name").label("name"),
                            )
                            .where(RecipeRunRow.job_id == job.id)
                            .order_by(RecipeRunRow.resolved_at.is_(None).desc(), RecipeRunRow.run_number.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
                if current is not None:
                    step_filter = (
                        RecipeStepInstanceRow.run_id == current["id"],
                        RecipeStepInstanceRow.state.in_(
                            ("running", "needs_decision", "interrupted", "failed", "ready", "blocked")
                        ),
                    )
                    step_count = (
                        await self._session.scalar(
                            select(func.count()).select_from(RecipeStepInstanceRow).where(*step_filter)
                        )
                        or 0
                    )
                    steps = list(
                        (
                            await self._session.execute(
                                select(
                                    RecipeStepInstanceRow.id,
                                    RecipeStepInstanceRow.state,
                                    RecipeStepInstanceRow.reason,
                                    func.json_extract(RecipeStepInstanceRow.definition_json, "$.title").label("title"),
                                )
                                .where(*step_filter)
                                .order_by(
                                    case(
                                        (RecipeStepInstanceRow.state == "needs_decision", 0),
                                        (RecipeStepInstanceRow.state == "running", 1),
                                        (RecipeStepInstanceRow.state.in_(("failed", "interrupted")), 2),
                                        else_=3,
                                    ),
                                    RecipeStepInstanceRow.step_path,
                                )
                                .limit(3)
                            )
                        ).mappings()
                    )
                    recipe = ProjectRecipeSummary(
                        run_id=current["id"],
                        revision=current["revision"],
                        name=current["name"],
                        state=current["state"],
                        reason=current["reason"],
                        steps=[ProjectRecipeStep.model_validate(dict(step)) for step in steps],
                        remaining_step_count=max(0, step_count - len(steps)),
                    )
            items.append(
                ProjectBoardItem(
                    id=row["key"],
                    column=column,
                    job=JobResponse.from_domain(job) if job else None,
                    tasks=[ProjectTaskSummary.model_validate(dict(task)) for task in tasks],
                    task_count=row["task_count"],
                    pending_approval_count=row["pending_count"],
                    delivery_attention_count=row["delivery_count"],
                    recipe=recipe,
                    has_failure=bool(row["has_failure"]),
                )
            )
        return ProjectBoardPage(items=items, next_cursor=rows[limit - 1]["key"] if len(rows) > limit else None)

    async def approvals(self, project_id: str, *, after: str = "", limit: int = 30) -> ProjectApprovalPage:
        if not 1 <= limit <= 100:
            raise StateConflictError("Invalid Project approval page.")
        rows = list(
            await self._session.scalars(
                select(ApprovalRow)
                .join(JobRow, JobRow.id == ApprovalRow.job_id)
                .where(JobRow.project_id == project_id, ApprovalRow.resolution.is_(None), ApprovalRow.id > after)
                .order_by(ApprovalRow.id)
                .limit(limit + 1)
            )
        )
        return ProjectApprovalPage(
            items=[
                ApprovalResponse.model_validate(ApprovalRepository._to_domain(row), from_attributes=True)
                for row in rows[:limit]
            ],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    async def deliveries(self, project_id: str, *, after: str = "", limit: int = 20) -> ProjectDeliveryPage:
        if not 1 <= limit <= 100:
            raise StateConflictError("Invalid Project delivery page.")
        rows = list(
            await self._session.scalars(
                select(TrackerOperationRow)
                .where(
                    TrackerOperationRow.project_ref == project_id,
                    TrackerOperationRow.state.in_(_DELIVERY_ATTENTION),
                    TrackerOperationRow.id > after,
                )
                .order_by(TrackerOperationRow.id)
                .limit(limit + 1)
            )
        )
        return ProjectDeliveryPage(
            items=[TrackerOperationRepository._response(row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )
