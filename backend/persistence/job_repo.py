"""Job persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, or_, select

from backend.models.db import DiffSnapshotRow, JobRow
from backend.models.domain import Job, JobState, PermissionMode, Resolution
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    import builtins
    from datetime import datetime


def _safe_job_state(raw: str) -> JobState:
    """Convert a raw state string to JobState, defaulting to queued for unknown values."""
    try:
        return JobState(raw) if raw else JobState.queued
    except ValueError:
        return JobState.queued


class JobRepository(BaseRepository):
    """Database access for job records."""

    async def _update_row(self, job_id: str, **updates: Any) -> None:
        """Fetch a job row by ID and apply field updates with optimistic locking.

        Increments the ``version`` column on every update. If the row was
        concurrently modified (version mismatch after flush), the caller's
        session should detect the stale state on commit.
        """
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            return
        for field, value in updates.items():
            setattr(row, field, value)
        row.version = (row.version if row.version is not None else 0) + 1  # type: ignore[assignment]
        await self._session.flush()

    async def list_ids(self) -> set[str]:
        """Return the set of all existing job IDs."""
        result = await self._session.execute(select(JobRow.id))
        return set(result.scalars().all())

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        # SQLAlchemy Column descriptors return Any at the type level;
        # cast() documents the expected runtime type for each field.
        return Job(
            id=cast("str", row.id),
            repo=cast("str", row.repo),
            prompt=cast("str", row.prompt),
            state=_safe_job_state(cast("str", row.state)),
            base_ref=cast("str", row.base_ref),
            branch=cast("str | None", row.branch),
            worktree_path=cast("str | None", row.worktree_path),
            session_id=cast("str | None", row.session_id),
            created_at=cast("datetime", row.created_at),
            updated_at=cast("datetime", row.updated_at),
            completed_at=cast("datetime | None", row.completed_at),
            pr_url=cast("str | None", row.pr_url),
            merge_status=cast("str | None", row.merge_status),
            title=cast("str | None", row.title),
            worktree_name=cast("str | None", row.worktree_name),
            permission_mode=PermissionMode(cast("str", row.permission_mode) or "auto"),
            session_count=cast("int", row.session_count) or 1,
            sdk_session_id=cast("str | None", row.sdk_session_id),
            model=cast("str | None", row.model),
            resolution=Resolution(cast("str", row.resolution)) if row.resolution else None,
            archived_at=cast("datetime | None", row.archived_at),
            failure_reason=cast("str | None", row.failure_reason),
            sdk=cast("str", row.sdk) or "copilot",
            verify=cast("bool | None", row.verify),
            self_review=cast("bool | None", row.self_review),
            max_turns=cast("int | None", row.max_turns),
            verify_prompt=cast("str | None", row.verify_prompt),
            self_review_prompt=cast("str | None", row.self_review_prompt),
            version=cast("int", row.version) or 1,
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
        updates: dict[str, Any] = {"state": new_state, "updated_at": updated_at}
        if completed_at is not None:
            updates["completed_at"] = completed_at
        if failure_reason is not None:
            updates["failure_reason"] = failure_reason
        await self._update_row(job_id, **updates)

    async def update_pr_url(self, job_id: str, pr_url: str) -> None:
        """Store the PR URL on a job row."""
        await self._update_row(job_id, pr_url=pr_url)

    async def update_merge_status(self, job_id: str, merge_status: str, pr_url: str | None = None) -> None:
        """Update the merge status (and optionally PR URL) on a job row."""
        updates: dict[str, Any] = {"merge_status": merge_status}
        if pr_url is not None:
            updates["pr_url"] = pr_url
        await self._update_row(job_id, **updates)

    async def reset_for_resume(
        self,
        job_id: str,
        new_session_count: int,
        *,
        merge_status: str | None = None,
    ) -> None:
        """Reset a terminal job back to running state for resumption."""
        from datetime import UTC, datetime

        await self._update_row(
            job_id,
            state=JobState.running,
            completed_at=None,
            session_id=None,
            session_count=new_session_count,
            resolution=None,
            failure_reason=None,
            archived_at=None,
            merge_status=merge_status,
            pr_url=None,
            updated_at=datetime.now(UTC),
        )

    async def reset_for_recovery(
        self,
        job_id: str,
        new_session_count: int,
        *,
        new_state: JobState = JobState.running,
    ) -> None:
        """Prepare an active job for process-restart recovery without failing it."""
        from datetime import UTC, datetime

        await self._update_row(
            job_id,
            state=new_state,
            completed_at=None,
            session_id=None,
            session_count=new_session_count,
            failure_reason=None,
            updated_at=datetime.now(UTC),
        )

    async def restore_after_failed_resume(
        self,
        job_id: str,
        *,
        previous_state: JobState,
        previous_session_count: int,
        completed_at: datetime | None,
        resolution: str | None,
        failure_reason: str | None,
        archived_at: datetime | None,
        merge_status: str | None,
        pr_url: str | None,
    ) -> None:
        """Restore the persisted job row when resume setup fails before execution starts."""
        from datetime import UTC, datetime

        await self._update_row(
            job_id,
            state=previous_state,
            completed_at=completed_at,
            session_count=previous_session_count,
            resolution=resolution,
            failure_reason=failure_reason,
            archived_at=archived_at,
            merge_status=merge_status,
            pr_url=pr_url,
            updated_at=datetime.now(UTC),
        )

    async def update_worktree_path(self, job_id: str, worktree_path: str) -> None:
        """Update the worktree path (e.g. after re-creating a cleaned-up worktree)."""
        await self._update_row(job_id, worktree_path=worktree_path)

    async def update_resolution(self, job_id: str, resolution: str, pr_url: str | None = None) -> None:
        """Update the resolution status (and optionally PR URL) on a job row."""
        updates: dict[str, Any] = {"resolution": resolution}
        if pr_url is not None:
            updates["pr_url"] = pr_url
        await self._update_row(job_id, **updates)

    async def update_archived_at(self, job_id: str, archived_at: datetime | None) -> None:
        """Set or clear the archived_at timestamp."""
        await self._update_row(job_id, archived_at=archived_at)

    async def update_sdk_session_id(self, job_id: str, sdk_session_id: str | None) -> None:
        """Persist or clear the Copilot SDK session ID for future resumption."""
        await self._update_row(job_id, sdk_session_id=sdk_session_id)

    async def claim_for_start(self, job_id: str) -> bool:
        """Atomically claim a job for execution using a DB-level compare-and-swap.

        Sets state to 'running' only if the current state allows starting.
        Returns True if this call won the race, False if another caller
        already claimed the job.
        """
        from datetime import UTC, datetime

        from sqlalchemy import update

        stmt = (
            update(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.state.in_(
                    [
                        JobState.queued,
                        JobState.running,
                        # Active review state allowed for resume
                        JobState.review,
                        # Terminal states allowed for resume
                        JobState.completed,
                        JobState.failed,
                        JobState.canceled,
                    ]
                ),
            )
            .values(
                state=JobState.running,
                updated_at=datetime.now(UTC),
                version=JobRow.version + 1,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    async def update_title_and_branch(self, job_id: str, title: str | None = None, branch: str | None = None) -> None:
        """Update the title and/or branch of a job (used by async naming)."""
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if branch is not None:
            updates["branch"] = branch
        if updates:
            await self._update_row(job_id, **updates)

    # ------------------------------------------------------------------
    # Retention helpers
    # ------------------------------------------------------------------

    async def list_terminal_before(self, cutoff: datetime) -> builtins.list[Job]:
        """Return terminal-state jobs completed before *cutoff*."""
        from backend.models.domain import TERMINAL_STATES

        stmt = select(JobRow).where(
            JobRow.state.in_(TERMINAL_STATES),
            JobRow.completed_at < cutoff,
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def list_auto_archive_candidates(self, cutoff: datetime) -> builtins.list[Job]:
        """Return resolved jobs eligible for auto-archiving."""
        stmt = select(JobRow).where(
            JobRow.state == JobState.completed,
            JobRow.resolution.in_([Resolution.merged, Resolution.pr_created, Resolution.discarded]),
            JobRow.archived_at.is_(None),
            JobRow.completed_at < cutoff,
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def bulk_archive(self, job_ids: builtins.list[str], archived_at: datetime) -> int:
        """Set archived_at for multiple jobs at once. Returns count updated."""
        count = 0
        for jid in job_ids:
            await self._update_row(jid, archived_at=archived_at)
            count += 1
        return count

    async def delete_diff_snapshots_for_jobs(self, job_ids: builtins.list[str]) -> int:
        """Delete all diff snapshots belonging to the given jobs. Returns count deleted."""
        from sqlalchemy import delete

        del_result = await self._session.execute(delete(DiffSnapshotRow).where(DiffSnapshotRow.job_id.in_(job_ids)))
        await self._session.flush()
        return int(del_result.rowcount)  # type: ignore[attr-defined]
