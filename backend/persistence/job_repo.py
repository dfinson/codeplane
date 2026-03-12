"""Job persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select

from backend.models.db import JobRow
from backend.models.domain import Job
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime


class JobRepository(BaseRepository):
    """Database access for job records."""

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        return Job(
            id=row.id,  # type: ignore[arg-type]
            repo=row.repo,  # type: ignore[arg-type]
            prompt=row.prompt,  # type: ignore[arg-type]
            state=row.state,  # type: ignore[arg-type]
            strategy=row.strategy,  # type: ignore[arg-type]
            base_ref=row.base_ref,  # type: ignore[arg-type]
            branch=row.branch,  # type: ignore[arg-type]
            worktree_path=row.worktree_path,  # type: ignore[arg-type]
            session_id=row.session_id,  # type: ignore[arg-type]
            created_at=row.created_at,  # type: ignore[arg-type]
            updated_at=row.updated_at,  # type: ignore[arg-type]
            completed_at=row.completed_at,  # type: ignore[arg-type]
        )

    async def create(self, job: Job) -> Job:
        """Insert a new job record."""
        row = JobRow(
            id=job.id,
            repo=job.repo,
            prompt=job.prompt,
            state=job.state,
            strategy=job.strategy,
            base_ref=job.base_ref,
            branch=job.branch,
            worktree_path=job.worktree_path,
            session_id=job.session_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
        )
        self._session.add(row)
        await self._session.flush()
        return job

    async def get(self, job_id: str) -> Job | None:
        """Retrieve a job by ID, or None if not found."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain(row)

    async def list(  # noqa: A003
        self,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[Job]:
        """List jobs, optionally filtered by state, with cursor-based pagination."""
        stmt = select(JobRow).order_by(JobRow.created_at.desc(), JobRow.id.desc())
        if state is not None:
            states = [s.strip() for s in state.split(",")]
            stmt = stmt.where(JobRow.state.in_(states))
        if cursor is not None:
            # Keyset pagination: look up the cursor row's created_at for proper ordering
            cursor_time = select(JobRow.created_at).where(JobRow.id == cursor).scalar_subquery()
            stmt = stmt.where(
                or_(
                    JobRow.created_at < cursor_time,
                    and_(JobRow.created_at == cursor_time, JobRow.id < cursor),
                )
            )
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def update_state(
        self,
        job_id: str,
        new_state: str,
        updated_at: datetime,
        completed_at: datetime | None = None,
    ) -> None:
        """Update a job's state and timestamps."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.state = new_state  # type: ignore[assignment]
        row.updated_at = updated_at  # type: ignore[assignment]
        if completed_at is not None:
            row.completed_at = completed_at  # type: ignore[assignment]
        await self._session.flush()
