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
from backend.services.agent_adapter import SDKModelMismatchError, validate_sdk_model

if TYPE_CHECKING:
    from backend.config import CPLConfig
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
        config: CPLConfig,
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
        permission_mode: str = "auto",
        model: str | None = None,
        sdk: str | None = None,
    ) -> Job:
        """Create a new job, set up workspace, and persist it.

        Naming is blocking: the LLM generates title, branch, and worktree name
        before the worktree is created. If naming fails, deterministic fallbacks
        are used.

        Returns the created Job domain object.
        Raises RepoNotAllowedError if the repo is not in the allowlist.
        """
        resolved_repo = self.validate_repo(repo)

        resolved_sdk = sdk or self._config.runtime.default_sdk

        # Validate SDK-model compatibility upfront
        validate_sdk_model(resolved_sdk, model)

        # Determine base_ref
        if base_ref is None:
            base_ref = await self._git.get_default_branch(resolved_repo)

        now = datetime.now(UTC)

        # Generate job ID atomically via the database
        job_id = await self._job_repo.next_id()

        # Blocking naming: generate title, branch, worktree_name via LLM
        title: str | None = None
        worktree_name: str | None = None

        if self._naming is not None:
            try:
                # Gather existing branches and worktrees for conflict detection
                existing_branches = await self._git.list_branches(resolved_repo)
                existing_worktrees = await self._git.list_worktree_names(resolved_repo)

                title, generated_branch, worktree_name = await self._naming.generate(
                    prompt,
                    existing_branches=existing_branches,
                    existing_worktrees=existing_worktrees,
                )
                if branch is None and generated_branch:
                    branch = generated_branch
                log.info(
                    "naming_preflight_complete",
                    job_id=job_id,
                    title=title,
                    branch=branch,
                    worktree_name=worktree_name,
                )
            except Exception:
                log.warning("naming_preflight_failed", job_id=job_id, exc_info=True)

        # Fallback worktree_name if naming didn't produce one
        if worktree_name is None:
            import hashlib

            h = hashlib.sha256(prompt.encode()).hexdigest()[:8]
            worktree_name = f"task-{h}"

        # Create worktree using worktree_name as the directory name
        from backend.services.git_service import GitError

        try:
            worktree_path, branch_name = await self._git.create_worktree(
                repo_path=resolved_repo,
                job_id=worktree_name,  # Use worktree_name as directory name
                base_ref=base_ref,
                branch=branch,
            )
        except GitError as exc:
            job = Job(
                id=job_id,
                repo=resolved_repo,
                prompt=prompt,
                state=JobState.failed,
                base_ref=base_ref,
                branch=None,
                worktree_path=None,
                session_id=None,
                created_at=now,
                updated_at=now,
                completed_at=now,
                title=title,
                worktree_name=worktree_name,
                permission_mode=permission_mode,
                model=model,
                sdk=resolved_sdk,
                failure_reason=f"Worktree creation failed: {exc}",
            )
            await self._job_repo.create(job)
            log.error("job_worktree_failed", job_id=job_id, error=str(exc))
            return job

        initial_state = JobState.queued

        job = Job(
            id=job_id,
            repo=resolved_repo,
            prompt=prompt,
            state=initial_state,
            base_ref=base_ref,
            branch=branch_name,
            worktree_path=worktree_path,
            session_id=None,
            created_at=now,
            updated_at=now,
            title=title,
            worktree_name=worktree_name,
            permission_mode=permission_mode,
            model=model,
            sdk=resolved_sdk,
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
            permission_mode=original.permission_mode,
            model=original.model,
            sdk=original.sdk,
        )

    async def continue_job(self, job_id: str, instruction: str) -> Job:
        """Create a follow-up job using a new instruction on the same repo/config."""
        original = await self.get_job(job_id)
        return await self.create_job(
            repo=original.repo,
            prompt=instruction,
            base_ref=original.base_ref,
            permission_mode=original.permission_mode,
            model=original.model,
            sdk=original.sdk,
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
