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
    ) -> None:
        self._job_repo = job_repo
        self._git = git_service
        self._config = config

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
        permission_mode: str = "auto",
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

        # Determine if we use main worktree or secondary
        all_jobs = await self._job_repo.list(limit=10000)
        active_on_repo = [j for j in all_jobs if j.repo == resolved_repo and j.state in ACTIVE_STATES]
        use_main = len(active_on_repo) == 0

        # Create worktree and branch
        from backend.services.git_service import GitError

        try:
            worktree_path, branch_name = await self._git.create_worktree(
                repo_path=resolved_repo,
                job_id=job_id,
                base_ref=base_ref,
                branch=branch,
                use_main=use_main,
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
                permission_mode=permission_mode,
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
            permission_mode=permission_mode,
        )
        await self._job_repo.create(job)
        log.info("job_created", job_id=job_id, repo=resolved_repo, state=initial_state)
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
    ) -> tuple[list[Job], str | None, bool]:
        """List jobs with optional filtering and pagination.

        Returns (jobs, next_cursor, has_more).
        """
        # Fetch one extra to determine has_more
        jobs = await self._job_repo.list(state=state, limit=limit + 1, cursor=cursor)
        has_more = len(jobs) > limit
        if has_more:
            jobs = jobs[:limit]
        next_cursor = jobs[-1].id if has_more and jobs else None
        return jobs, next_cursor, has_more

    async def transition_state(self, job_id: str, new_state: str) -> Job:
        """Transition a job's state. Validates the transition."""
        job = await self.get_job(job_id)
        validate_state_transition(job.state, new_state)

        now = datetime.now(UTC)
        completed_at = now if new_state in TERMINAL_STATES else None
        await self._job_repo.update_state(job_id, new_state, now, completed_at)

        job.state = new_state
        job.updated_at = now
        if completed_at:
            job.completed_at = completed_at

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
