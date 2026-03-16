"""Job lifecycle orchestration."""

from __future__ import annotations

import glob
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    InvalidStateTransitionError,
    Job,
    JobState,
    validate_state_transition,
)

if TYPE_CHECKING:
    from backend.config import TowerConfig
    from backend.persistence.job_repo import JobRepository
    from backend.services.git_service import GitService
    from backend.services.naming_service import NamingService

log = structlog.get_logger()


class RepoNotAllowedError(Exception):
    """Raised when a repo path is not in the allowlist."""


class JobNotFoundError(Exception):
    """Raised when a job ID does not exist."""


class StateConflictError(Exception):
    """Raised when a job action conflicts with its current state."""


class JobService:
    """Orchestrates job creation, state transitions, and control actions."""

    def __init__(
        self,
        job_repo: JobRepository,
        git_service: GitService,
        config: TowerConfig,
        naming_service: NamingService | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._git = git_service
        self._config = config
        self._naming = naming_service

    def _resolve_repos(self) -> set[str]:
        """Expand glob patterns and return the full set of allowed repo paths."""
        allowed: set[str] = set()
        for pattern in self._config.repos:
            expanded = Path(pattern).expanduser()
            if "*" in pattern or "?" in pattern:
                for match in glob.glob(str(expanded), recursive=True):
                    p = Path(match).resolve()
                    if p.is_dir() and (p / ".git").exists():
                        allowed.add(str(p))
            else:
                allowed.add(str(expanded.resolve()))
        return allowed

    def validate_repo(self, repo: str) -> str:
        """Validate a repo path is in the allowlist. Returns resolved path."""
        resolved = str(Path(repo).expanduser().resolve())
        allowed = self._resolve_repos()
        if resolved not in allowed:
            raise RepoNotAllowedError(f"Repository '{repo}' is not in the allowlist.")
        return resolved

    async def create_job(
        self,
        repo: str,
        prompt: str,
        base_ref: str | None = None,
        branch: str | None = None,
        strategy: str = "single_agent",
        permission_mode: str = "permissive",
        model: str | None = None,
    ) -> Job:
        """Create a new job, set up workspace, and persist it.

        Returns the created Job domain object.
        Raises RepoNotAllowedError if the repo is not in the allowlist.
        """
        resolved_repo = self.validate_repo(repo)

        # Determine base_ref
        if base_ref is None:
            base_ref = await self._git.get_default_branch(resolved_repo)

        now = datetime.now(UTC)

        # Generate job ID atomically via the database
        job_id = await self._job_repo.next_id()

        # Pre-work: generate intelligent title and branch name from the prompt
        title: str | None = None
        if self._naming is not None and branch is None:
            try:
                title, generated_branch = await self._naming.generate(prompt)
                if generated_branch:
                    branch = generated_branch
                log.info("naming_preflight_complete", job_id=job_id, title=title, branch=branch)
            except Exception:
                log.warning("naming_preflight_failed", job_id=job_id, exc_info=True)

        # Always use a secondary worktree so every job is fully isolated.
        # Reusing the main worktree caused diff leakage: stale untracked
        # files and race-condition branch switching contaminated diffs.
        from backend.services.git_service import GitError

        try:
            worktree_path, branch_name = await self._git.create_worktree(
                repo_path=resolved_repo,
                job_id=job_id,
                base_ref=base_ref,
                branch=branch,
                use_main=False,
            )
        except GitError as exc:
            # Worktree creation failed — create the job in failed state
            job = Job(
                id=job_id,
                repo=resolved_repo,
                prompt=prompt,
                state=JobState.failed,
                strategy=strategy,
                base_ref=base_ref,
                branch=None,
                worktree_path=None,
                session_id=None,
                created_at=now,
                updated_at=now,
                completed_at=now,
                title=title,
                permission_mode=permission_mode,
                model=model,
            )
            await self._job_repo.create(job)
            log.error("job_worktree_failed", job_id=job_id, error=str(exc))
            return job

        # Job starts as queued; RuntimeService.start_or_enqueue() transitions
        # to running immediately when capacity allows, or keeps it queued.
        initial_state = JobState.queued

        job = Job(
            id=job_id,
            repo=resolved_repo,
            prompt=prompt,
            state=initial_state,
            strategy=strategy,
            base_ref=base_ref,
            branch=branch_name,
            worktree_path=worktree_path,
            session_id=None,
            created_at=now,
            updated_at=now,
            title=title,
            permission_mode=permission_mode,
            model=model,
        )
        await self._job_repo.create(job)
        log.info("job_created", job_id=job_id, title=title, repo=resolved_repo, state=initial_state)
        return job

    async def get_job(self, job_id: str) -> Job:
        """Get a job by ID. Raises JobNotFoundError if not found."""
        job = await self._job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist.")
        return job

    async def list_jobs(
        self,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        archived: bool | None = None,
    ) -> tuple[list[Job], str | None, bool]:
        """List jobs with optional filtering and pagination.

        Args:
            archived: True = only archived, False = exclude archived, None = all.

        Returns (jobs, next_cursor, has_more).
        """
        include_archived: bool | None = None  # repo default: return all
        if archived is True:
            include_archived = True  # only archived
        elif archived is False:
            include_archived = False  # exclude archived

        jobs = await self._job_repo.list(
            state=state,
            limit=limit + 1,
            cursor=cursor,
            include_archived=include_archived,
        )
        has_more = len(jobs) > limit
        if has_more:
            jobs = jobs[:limit]
        next_cursor = jobs[-1].id if has_more and jobs else None
        return jobs, next_cursor, has_more

    async def transition_state(self, job_id: str, new_state: str, *, failure_reason: str | None = None) -> Job:
        """Transition a job's state. Validates the transition."""
        job = await self.get_job(job_id)
        validate_state_transition(job.state, new_state)

        now = datetime.now(UTC)
        completed_at = now if new_state in TERMINAL_STATES else None
        await self._job_repo.update_state(job_id, new_state, now, completed_at, failure_reason=failure_reason)

        job.state = new_state
        job.updated_at = now
        if completed_at:
            job.completed_at = completed_at
        if failure_reason is not None:
            job.failure_reason = failure_reason

        log.info("job_state_changed", job_id=job_id, new_state=new_state)
        return job

    async def cancel_job(self, job_id: str) -> Job:
        """Cancel a running or queued job. Raises StateConflictError if not cancellable."""
        job = await self.get_job(job_id)
        if job.state in TERMINAL_STATES:
            raise StateConflictError(f"Cannot cancel job {job_id}: already in terminal state '{job.state}'.")
        try:
            return await self.transition_state(job_id, JobState.canceled)
        except InvalidStateTransitionError as exc:
            raise StateConflictError(str(exc)) from exc

    async def rerun_job(self, job_id: str) -> Job:
        """Create a new job from an existing job's configuration."""
        original = await self.get_job(job_id)
        return await self.create_job(
            repo=original.repo,
            prompt=original.prompt,
            base_ref=original.base_ref,
            strategy=original.strategy,
            permission_mode=original.permission_mode,
            model=original.model,
        )

    async def continue_job(self, job_id: str, instruction: str) -> Job:
        """Create a follow-up job using a new instruction on the same repo/config."""
        original = await self.get_job(job_id)
        return await self.create_job(
            repo=original.repo,
            prompt=instruction,
            base_ref=original.base_ref,
            strategy=original.strategy,
            permission_mode=original.permission_mode,
            model=original.model,
        )

    async def count_active_jobs(self) -> int:
        """Count currently active (non-terminal) jobs."""
        jobs = await self._job_repo.list(
            state=",".join(ACTIVE_STATES),
            limit=10000,
        )
        return len(jobs)

    async def count_queued_jobs(self) -> int:
        """Count queued jobs."""
        jobs = await self._job_repo.list(state=JobState.queued, limit=10000)
        return len(jobs)

    async def resolve_job(self, job_id: str, action: str) -> Job:
        """Resolve a succeeded job by merging, creating a PR, or discarding."""
        job = await self.get_job(job_id)
        if job.state != JobState.succeeded:
            raise StateConflictError(f"Job {job_id} is in state {job.state!r}, not 'succeeded'")
        if job.resolution not in (None, "unresolved", "conflict"):
            raise StateConflictError(f"Job {job_id} already resolved as {job.resolution!r}")
        return job

    async def archive_job(self, job_id: str) -> Job:
        """Archive a job (hide from Kanban board)."""
        job = await self.get_job(job_id)
        if job.state not in TERMINAL_STATES:
            raise StateConflictError(f"Job {job_id} is in state {job.state!r}, cannot archive active jobs")
        await self._job_repo.update_archived_at(job_id, datetime.now(UTC))
        return await self.get_job(job_id)

    async def unarchive_job(self, job_id: str) -> Job:
        """Unarchive a job (show on Kanban board again)."""
        await self.get_job(job_id)
        await self._job_repo.update_archived_at(job_id, None)
        return await self.get_job(job_id)
