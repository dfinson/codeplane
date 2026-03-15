"""Job persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, and_, func, or_, select

from backend.models.db import JobRow
from backend.models.domain import Job
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime


class JobRepository(BaseRepository):
    """Database access for job records."""

    async def next_id(self) -> str:
        """Generate the next sequential job ID atomically via the database."""
        result = await self._session.execute(select(func.max(func.cast(func.substr(JobRow.id, 5), Integer))))
        max_num = result.scalar() or 0
        return f"job-{max_num + 1}"

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
            pr_url=row.pr_url,  # type: ignore[arg-type]
            merge_status=row.merge_status,  # type: ignore[arg-type]
            title=row.title,  # type: ignore[arg-type]
            permission_mode=row.permission_mode or "auto",  # type: ignore[arg-type]
            session_count=row.session_count or 1,  # type: ignore[arg-type]
            sdk_session_id=row.sdk_session_id,  # type: ignore[arg-type]
            model=row.model,  # type: ignore[arg-type]
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
            pr_url=job.pr_url,
            merge_status=job.merge_status,
            title=job.title,
            permission_mode=job.permission_mode,
            session_count=job.session_count,
            sdk_session_id=job.sdk_session_id,
            model=job.model,
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

    async def update_pr_url(self, job_id: str, pr_url: str) -> None:
        """Store the PR URL on a job row."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.pr_url = pr_url  # type: ignore[assignment]
        await self._session.flush()

    async def update_merge_status(self, job_id: str, merge_status: str, pr_url: str | None = None) -> None:
        """Update the merge status (and optionally PR URL) on a job row."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.merge_status = merge_status  # type: ignore[assignment]
        if pr_url is not None:
            row.pr_url = pr_url  # type: ignore[assignment]
        await self._session.flush()

    async def reset_for_resume(self, job_id: str, new_session_count: int) -> None:
        """Reset a terminal job back to running state for resumption."""
        from datetime import UTC, datetime

        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.state = "running"  # type: ignore[assignment]
        row.completed_at = None  # type: ignore[assignment]
        row.session_id = None  # type: ignore[assignment]
        row.session_count = new_session_count  # type: ignore[assignment]
        row.updated_at = datetime.now(UTC)  # type: ignore[assignment]
        await self._session.flush()

    async def update_worktree_path(self, job_id: str, worktree_path: str) -> None:
        """Update the worktree path (e.g. after re-creating a cleaned-up worktree)."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.worktree_path = worktree_path  # type: ignore[assignment]
        await self._session.flush()

    async def update_sdk_session_id(self, job_id: str, sdk_session_id: str) -> None:
        """Persist the Copilot SDK session ID for future resumption."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.sdk_session_id = sdk_session_id  # type: ignore[assignment]
        await self._session.flush()
