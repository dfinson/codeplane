"""Job persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_, select

from backend.models.db import DiffSnapshotRow, JobRow
from backend.models.domain import GitMergeOutcome, Job, JobMode, JobNotFoundError, JobState, Preset, Resolution
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    import builtins
    from datetime import datetime


def _safe_job_state(raw: str) -> JobState:
    """Convert a raw state string to JobState, defaulting to queued for unknown values."""
    try:
        return JobState(raw) if raw else JobState.queued
    except ValueError:
        import structlog

        structlog.get_logger().warning("unknown_job_state_defaulting_to_queued", raw_state=raw)
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
            raise JobNotFoundError(f"Job {job_id!r} not found — cannot update a deleted or non-existent row")
        for field, value in updates.items():
            setattr(row, field, value)
        row.version = (row.version if row.version is not None else 0) + 1
        await self._session.flush()

    async def list_ids(self) -> set[str]:
        result = await self._session.execute(select(JobRow.id))
        return set(result.scalars().all())

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        return Job(
            id=row.id,
            repo=row.repo,
            project_id=row.project_id,
            prompt=row.prompt,
            state=_safe_job_state(row.state),
            base_ref=row.base_ref,
            branch=row.branch,
            worktree_path=row.worktree_path,
            session_id=row.session_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
            pr_url=row.pr_url,
            merge_status=GitMergeOutcome(row.merge_status) if row.merge_status else None,
            title=row.title,
            description=row.description,
            worktree_name=row.worktree_name,
            preset=Preset(row.preset) if row.preset else Preset.supervised,
            session_count=row.session_count or 1,
            sdk_session_id=row.sdk_session_id,
            copilot_credential_id=row.copilot_credential_id,
            copilot_credential_label=row.copilot_credential_label,
            model=row.model,
            resolution=Resolution(row.resolution) if row.resolution else None,
            archived_at=row.archived_at,
            failure_reason=row.failure_reason,
            sdk=row.sdk or "copilot",
            verify=row.verify,
            self_review=row.self_review,
            max_turns=row.max_turns,
            verify_prompt=row.verify_prompt,
            self_review_prompt=row.self_review_prompt,
            enable_stall_detection=row.enable_stall_detection,
            enable_plan_tracking=row.enable_plan_tracking,
            version=row.version or 1,
            parent_job_id=row.parent_job_id,
            source=row.source or "managed",
            external_session_id=row.external_session_id,
            tail_offset=row.tail_offset or 0,
            mode=JobMode(row.mode) if row.mode else JobMode.standard,
            artifact_collection_status=row.artifact_collection_status or "pending",
            artifact_collection_error=row.artifact_collection_error,
            artifact_collection_session_count=row.artifact_collection_session_count or 0,
            artifact_collection_updated_at=row.artifact_collection_updated_at,
        )

    async def create(self, job: Job) -> Job:
        """Insert a new job record."""
        row = JobRow(
            id=job.id,
            repo=job.repo,
            project_id=job.project_id,
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
            description=job.description,
            worktree_name=job.worktree_name,
            permission_mode=job.preset.value,  # legacy column — just store the preset name
            preset=job.preset,
            session_count=job.session_count,
            sdk_session_id=job.sdk_session_id,
            copilot_credential_id=job.copilot_credential_id,
            copilot_credential_label=job.copilot_credential_label,
            model=job.model,
            resolution=job.resolution,
            archived_at=job.archived_at,
            sdk=job.sdk,
            verify=job.verify,
            self_review=job.self_review,
            max_turns=job.max_turns,
            verify_prompt=job.verify_prompt,
            self_review_prompt=job.self_review_prompt,
            enable_stall_detection=job.enable_stall_detection,
            enable_plan_tracking=job.enable_plan_tracking,
            parent_job_id=job.parent_job_id,
            source=job.source,
            external_session_id=job.external_session_id,
            tail_offset=job.tail_offset,
            mode=job.mode,
            artifact_collection_status=job.artifact_collection_status,
            artifact_collection_error=job.artifact_collection_error,
            artifact_collection_session_count=job.artifact_collection_session_count,
            artifact_collection_updated_at=job.artifact_collection_updated_at,
        )
        self._session.add(row)
        await self._session.flush()
        return job

    async def claim_artifact_collection(
        self,
        job_id: str,
        *,
        updated_at: datetime,
        allow_stale: bool = False,
    ) -> Job | None:
        """Claim terminal artifact collection unless this session is already complete."""
        result = await self._session.execute(select(JobRow).where(JobRow.id == job_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise JobNotFoundError(f"Job {job_id!r} not found")
        if row.state not in {
            JobState.review.value,
            JobState.completed.value,
            JobState.failed.value,
            JobState.canceled.value,
        }:
            return None

        current_session = row.session_count or 1
        already_complete = (
            row.artifact_collection_status == "completed"
            and (row.artifact_collection_session_count or 0) >= current_session
        )
        if already_complete or (row.artifact_collection_status == "collecting" and not allow_stale):
            return None

        row.artifact_collection_status = "collecting"
        row.artifact_collection_error = None
        row.artifact_collection_updated_at = updated_at
        row.version = (row.version or 0) + 1
        await self._session.flush()
        return self._to_domain(row)

    async def finish_artifact_collection(
        self,
        job_id: str,
        *,
        status: str,
        error: str | None,
        session_count: int,
        updated_at: datetime,
    ) -> None:
        """Persist the terminal result of an artifact collection attempt."""
        await self._update_row(
            job_id,
            artifact_collection_status=status,
            artifact_collection_error=error,
            artifact_collection_session_count=session_count,
            artifact_collection_updated_at=updated_at,
        )

    async def list_artifact_collection_candidates(self) -> list[str]:
        """Return terminal jobs without a completed current-session collection."""
        stmt = (
            select(JobRow.id)
            .where(
                JobRow.state.in_(
                    [
                        JobState.review.value,
                        JobState.completed.value,
                        JobState.failed.value,
                        JobState.canceled.value,
                    ]
                )
            )
            .where(
                or_(
                    JobRow.artifact_collection_status != "completed",
                    JobRow.artifact_collection_session_count < JobRow.session_count,
                )
            )
            .order_by(JobRow.completed_at, JobRow.updated_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

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
    ) -> builtins.list[Job]:
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

    async def session_relatives(
        self, job: Job, *, children: bool, offset: int = 0, limit: int = 51
    ) -> builtins.list[Job]:
        stmt = select(JobRow).where(JobRow.project_id == job.project_id)
        if children:
            stmt = stmt.where(JobRow.parent_job_id == job.id)
        else:
            stmt = stmt.where(
                JobRow.id != job.id,
                JobRow.source == "managed",
                JobRow.sdk == "copilot",
                JobRow.state == JobState.running,
            )
        result = await self._session.execute(
            stmt.order_by(JobRow.created_at, JobRow.id).offset(offset).limit(limit)
        )
        return [self._to_domain(row) for row in result.scalars()]

    async def list_all(
        self,
        state: str | None = None,
        include_archived: bool | None = None,
    ) -> builtins.list[Job]:
        """List all jobs matching filters, without an upper bound.

        Use only in internal recovery/snapshot paths — not for API responses.
        """
        stmt = select(JobRow).order_by(JobRow.created_at.desc(), JobRow.id.desc())
        if state is not None:
            states = [s.strip() for s in state.split(",")]
            stmt = stmt.where(JobRow.state.in_(states))
        if include_archived is False:
            stmt = stmt.where(JobRow.archived_at.is_(None))
        elif include_archived is True:
            stmt = stmt.where(JobRow.archived_at.is_not(None))
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def update_state(
        self,
        job_id: str,
        new_state: JobState,
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

    async def update_merge_status(self, job_id: str, merge_status: GitMergeOutcome, pr_url: str | None = None) -> None:
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
        merge_status: GitMergeOutcome | None = None,
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
        merge_status: GitMergeOutcome | None,
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

    async def update_worktree(self, job_id: str, worktree_path: str, branch: str) -> None:
        """Update the worktree path and branch after workspace setup."""
        await self._update_row(job_id, worktree_path=worktree_path, branch=branch)

    async def update_failure_reason(self, job_id: str, reason: str) -> None:
        """Set the failure reason on a job row."""
        from datetime import UTC
        from datetime import datetime as dt

        await self._update_row(job_id, failure_reason=reason, completed_at=dt.now(UTC))

    async def update_resolution(self, job_id: str, resolution: Resolution, pr_url: str | None = None) -> None:
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
        return int(result.rowcount) > 0  # type: ignore[attr-defined]  # CursorResult.rowcount not in generic stub

    async def update_title_and_branch(
        self,
        job_id: str,
        title: str | None = None,
        branch: str | None = None,
        description: str | None = None,
    ) -> None:
        """Update the title/branch/description of a job (used by async naming)."""
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if branch is not None:
            updates["branch"] = branch
        if description is not None:
            updates["description"] = description
        if updates:
            await self._update_row(job_id, **updates)

    async def update_prompt(self, job_id: str, prompt: str) -> None:
        """Update the job prompt (e.g. from first user message in imported sessions)."""
        await self._update_row(job_id, prompt=prompt)

    async def update_model(self, job_id: str, model: str) -> None:
        """Update the model field on a job."""
        await self._update_row(job_id, model=model)

    async def update_preset(self, job_id: str, preset: str) -> None:
        """Update the preset (permission policy) on a job."""
        await self._update_row(job_id, preset=preset)

    async def update_mode(self, job_id: str, mode: str) -> None:
        """Update the execution mode (e.g. plan → plan_implementing).

        Only valid JobMode values are accepted. Use 'standard' or
        'plan_implementing' — 'plan' is set at creation time only.
        """
        # Validate the mode is a known enum value
        try:
            JobMode(mode)
        except ValueError:
            valid = ", ".join(e.value for e in JobMode)
            raise ValueError(f"Invalid mode {mode!r}. Valid options: {valid}") from None
        await self._update_row(job_id, mode=mode)

    async def update_tail_offset(self, job_id: str, offset: int) -> None:
        """Persist the events.jsonl tail offset for resume-after-restart."""
        await self._update_row(job_id, tail_offset=offset)

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
        return int(del_result.rowcount)  # type: ignore[attr-defined]  # CursorResult.rowcount not in generic stub
