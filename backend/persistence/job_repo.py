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

    async def list_ids(self) -> set[str]:
        """Return the set of all existing job IDs."""
        result = await self._session.execute(select(JobRow.id))
        return set(result.scalars().all())

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        return Job(
            id=row.id,  # type: ignore[arg-type]
            repo=row.repo,  # type: ignore[arg-type]
            prompt=row.prompt,  # type: ignore[arg-type]
            state=row.state,  # type: ignore[arg-type]
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
            worktree_name=row.worktree_name,  # type: ignore[arg-type]
            permission_mode=row.permission_mode or "auto",  # type: ignore[arg-type]
            session_count=row.session_count or 1,  # type: ignore[arg-type]
            sdk_session_id=row.sdk_session_id,  # type: ignore[arg-type]
            model=row.model,  # type: ignore[arg-type]
            resolution=row.resolution,  # type: ignore[arg-type]
            archived_at=row.archived_at,  # type: ignore[arg-type]
            failure_reason=row.failure_reason,  # type: ignore[arg-type]
            sdk=row.sdk or "copilot",  # type: ignore[arg-type]
            verify=row.verify,  # type: ignore[arg-type]
            self_review=row.self_review,  # type: ignore[arg-type]
            max_turns=row.max_turns,  # type: ignore[arg-type]
            verify_prompt=row.verify_prompt,  # type: ignore[arg-type]
            self_review_prompt=row.self_review_prompt,  # type: ignore[arg-type]
        )

    async def create(self, job: Job) -> Job:
        """Insert a new job record."""
        row = JobRow(
            id=job.id,
            repo=job.repo,
            prompt=job.prompt,
            state=job.state,
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
            worktree_name=job.worktree_name,
            permission_mode=job.permission_mode,
            session_count=job.session_count,
            sdk_session_id=job.sdk_session_id,
            model=job.model,
            resolution=job.resolution,
            archived_at=job.archived_at,
            sdk=job.sdk,
            verify=job.verify,
            self_review=job.self_review,
            max_turns=job.max_turns,
            verify_prompt=job.verify_prompt,
            self_review_prompt=job.self_review_prompt,
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
        include_archived: bool | None = None,
    ) -> list[Job]:
        """List jobs, optionally filtered by state, with cursor-based pagination.

        Args:
            include_archived: None = all jobs, False = exclude archived, True = only archived.
        """
        stmt = select(JobRow).order_by(JobRow.created_at.desc(), JobRow.id.desc())
        if state is not None:
            states = [s.strip() for s in state.split(",")]
            stmt = stmt.where(JobRow.state.in_(states))
        if include_archived is False:
            stmt = stmt.where(JobRow.archived_at.is_(None))
        elif include_archived is True:
            stmt = stmt.where(JobRow.archived_at.is_not(None))
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
        failure_reason: str | None = None,
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
        if failure_reason is not None:
            row.failure_reason = failure_reason  # type: ignore[assignment]
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
        row.resolution = None  # type: ignore[assignment]
        row.failure_reason = None  # type: ignore[assignment]
        row.archived_at = None  # type: ignore[assignment]
        row.merge_status = None  # type: ignore[assignment]
        row.pr_url = None  # type: ignore[assignment]
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

    async def update_resolution(self, job_id: str, resolution: str, pr_url: str | None = None) -> None:
        """Update the resolution status (and optionally PR URL) on a job row."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.resolution = resolution  # type: ignore[assignment]
        if pr_url is not None:
            row.pr_url = pr_url  # type: ignore[assignment]
        await self._session.flush()

    async def update_archived_at(self, job_id: str, archived_at: datetime | None) -> None:
        """Set or clear the archived_at timestamp."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.archived_at = archived_at  # type: ignore[assignment]
        await self._session.flush()

    async def update_sdk_session_id(self, job_id: str, sdk_session_id: str) -> None:
        """Persist the Copilot SDK session ID for future resumption."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.sdk_session_id = sdk_session_id  # type: ignore[assignment]
        await self._session.flush()

    async def update_title_and_branch(self, job_id: str, title: str | None = None, branch: str | None = None) -> None:
        """Update the title and/or branch of a job (used by async naming)."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        if title is not None:
            row.title = title  # type: ignore[assignment]
        if branch is not None:
            row.branch = branch  # type: ignore[assignment]
        await self._session.flush()
