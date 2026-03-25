"""Job lifecycle orchestration."""

from __future__ import annotations

import glob
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.domain import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    InvalidStateTransitionError,
    Job,
    JobState,
    PermissionMode,
    Resolution,
    validate_state_transition,
)
from backend.services.agent_adapter import validate_sdk_model

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.config import CPLConfig
    from backend.models.events import DomainEvent, DomainEventKind
    from backend.persistence.event_repo import EventRepository
    from backend.persistence.job_repo import JobRepository
    from backend.services.git_service import GitService
    from backend.services.naming_service import NamingService

log = structlog.get_logger()

_MAX_COUNT_LIMIT = 10_000  # upper bound for count queries that scan all jobs


class RepoNotAllowedError(Exception):
    """Raised when a repo path is not in the allowlist."""


class JobNotFoundError(Exception):
    """Raised when a job ID does not exist."""


class StateConflictError(Exception):
    """Raised when a job action conflicts with its current state."""


@dataclass(frozen=True)
class ProgressPreview:
    headline: str
    summary: str


class JobService:
    """Orchestrates job creation, state transitions, and control actions."""

    def __init__(
        self,
        job_repo: JobRepository,
        git_service: GitService | None,
        config: CPLConfig,
        naming_service: NamingService | None = None,
        event_repo: EventRepository | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._git = git_service
        self._config = config
        self._naming = naming_service
        self._event_repo = event_repo

    @classmethod
    def from_session(
        cls,
        session: AsyncSession,
        config: CPLConfig,
        *,
        git_service: GitService | None = None,
        naming_service: NamingService | None = None,
    ) -> JobService:
        """Construct a JobService from a DB session.

        This factory keeps persistence imports inside the service layer so
        that callers (e.g. API routes) never import repository classes.
        """
        from backend.persistence.event_repo import EventRepository
        from backend.persistence.job_repo import JobRepository

        job_repo = JobRepository(session)
        event_repo = EventRepository(session)
        if git_service is None:
            from backend.services.git_service import GitService

            git_service = GitService(config)
        return cls(
            job_repo=job_repo,
            git_service=git_service,
            config=config,
            naming_service=naming_service,
            event_repo=event_repo,
        )

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

    async def list_events_by_job(
        self,
        job_id: str,
        kinds: list[DomainEventKind],
        limit: int = 2000,
    ) -> list[DomainEvent]:
        """Query domain events for a job, filtered by kind.

        Delegates to the event repository so that API routes never need
        to import persistence classes directly.
        """
        if self._event_repo is None:
            raise RuntimeError("JobService was created without an event_repo")
        return await self._event_repo.list_by_job(job_id, kinds, limit=limit)

    async def get_latest_progress_preview(self, job_id: str) -> ProgressPreview | None:
        """Return the latest persisted progress milestone for a job."""
        if self._event_repo is None:
            raise RuntimeError("JobService was created without an event_repo")
        preview = await self._event_repo.get_latest_progress_preview(job_id)
        if preview is None:
            return None
        return ProgressPreview(headline=preview[0], summary=preview[1])

    async def list_latest_progress_previews(self, job_ids: list[str]) -> dict[str, ProgressPreview]:
        """Return the latest persisted progress milestone for each requested job."""
        if self._event_repo is None:
            raise RuntimeError("JobService was created without an event_repo")
        previews = await self._event_repo.list_latest_progress_previews(job_ids)
        return {
            job_id: ProgressPreview(headline=headline, summary=summary)
            for job_id, (headline, summary) in previews.items()
        }

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
        permission_mode: PermissionMode = PermissionMode.auto,
        model: str | None = None,
        sdk: str | None = None,
        verify: bool | None = None,
        self_review: bool | None = None,
        max_turns: int | None = None,
        verify_prompt: str | None = None,
        self_review_prompt: str | None = None,
    ) -> Job:
        """Create a new job, set up workspace, and persist it.

        The job ID is the LLM-generated worktree name (e.g. "fix-login-bug").
        Naming is blocking: the LLM generates title, branch, and worktree name
        before the worktree is created. If naming fails, NamingError is raised
        and a failed job record is persisted with a hash-based ID.

        Returns the created Job domain object.
        Raises RepoNotAllowedError if the repo is not in the allowlist.
        """
        resolved_repo = self.validate_repo(repo)

        assert self._git is not None, "GitService required for job creation"

        resolved_sdk = sdk or self._config.runtime.default_sdk

        # Validate SDK-model compatibility upfront
        validate_sdk_model(resolved_sdk, model)

        # Determine base_ref
        if base_ref is None:
            base_ref = await self._git.get_default_branch(resolved_repo)

        now = datetime.now(UTC)

        # Blocking naming: generate title, branch, worktree_name via LLM.
        # The worktree_name becomes the job ID.
        title: str | None = None
        worktree_name: str | None = None

        if self._naming is not None:
            from backend.services.naming_service import NamingError

            try:
                # Gather existing branches, worktrees, and job IDs for conflict detection
                existing_branches = await self._git.list_branches(resolved_repo)
                existing_worktrees = await self._git.list_worktree_names(resolved_repo)
                existing_job_ids = await self._job_repo.list_ids()

                title, generated_branch, worktree_name = await self._naming.generate(
                    prompt,
                    existing_branches=existing_branches,
                    existing_worktrees=existing_worktrees | existing_job_ids,
                )
                if branch is None and generated_branch:
                    branch = generated_branch
            except NamingError as exc:
                import hashlib

                h = hashlib.sha256(f"{prompt}{now.isoformat()}".encode()).hexdigest()[:12]
                job_id = f"naming-failed-{h}"
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
                    title=None,
                    worktree_name=None,
                    permission_mode=permission_mode,
                    model=model,
                    failure_reason=f"Naming failed: {exc}",
                )
                await self._job_repo.create(job)
                log.error("job_naming_failed", job_id=job_id, error=str(exc))
                return job

        # When no naming service is configured (e.g. tests without LLM), use a hash.
        # Check existing IDs to avoid collisions on reruns of the same prompt.
        if worktree_name is None:
            import hashlib

            base_hash = hashlib.sha256(prompt.encode()).hexdigest()[:8]
            candidate = f"task-{base_hash}"
            existing_ids = await self._job_repo.list_ids()
            counter = 0
            while candidate in existing_ids:
                counter += 1
                candidate = f"task-{base_hash}-{counter}"
            worktree_name = candidate

        job_id = worktree_name
        log.info(
            "naming_preflight_complete",
            job_id=job_id,
            title=title,
            branch=branch,
            worktree_name=worktree_name,
        )

        # Create worktree using worktree_name as the directory name
        from backend.services.git_service import GitError

        try:
            worktree_path, branch_name = await self._git.create_worktree(
                repo_path=resolved_repo,
                job_id=worktree_name,  # job ID equals the worktree directory name
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
            verify=verify,
            self_review=self_review,
            max_turns=max_turns,
            verify_prompt=verify_prompt,
            self_review_prompt=self_review_prompt,
        )
        try:
            await self._job_repo.create(job)
        except Exception:
            # Compensate: clean up the worktree that was already created
            # so we don't leave orphaned directories on disk.
            log.error("job_persist_failed_cleaning_worktree", job_id=job_id, worktree_path=worktree_path)
            try:
                await self._git.remove_worktree(resolved_repo, worktree_path)
            except Exception:
                log.warning(
                    "compensation_worktree_cleanup_failed",
                    job_id=job_id,
                    worktree_path=worktree_path,
                    exc_info=True,
                )
            raise
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

    async def transition_state(self, job_id: str, new_state: JobState, *, failure_reason: str | None = None) -> Job:
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
        """Cancel a running or queued job and auto-archive it.

        Cancelled jobs are immediately archived so they don't clutter the
        Kanban board — cancellation is a deliberate operator action.
        """
        job = await self.get_job(job_id)
        if job.state in TERMINAL_STATES:
            raise StateConflictError(f"Cannot cancel job {job_id}: already in terminal state '{job.state}'.")
        try:
            job = await self.transition_state(job_id, JobState.canceled)
        except InvalidStateTransitionError as exc:
            raise StateConflictError(str(exc)) from exc
        await self._job_repo.update_archived_at(job_id, datetime.now(UTC))
        return await self.get_job(job_id)

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

    async def count_active_jobs(self) -> int:
        """Count currently active (non-terminal) jobs."""
        jobs = await self._job_repo.list(
            state=",".join(ACTIVE_STATES),
            limit=_MAX_COUNT_LIMIT,
        )
        return len(jobs)

    async def count_queued_jobs(self) -> int:
        """Count queued jobs."""
        jobs = await self._job_repo.list(state=JobState.queued, limit=_MAX_COUNT_LIMIT)
        return len(jobs)

    async def resolve_job(self, job_id: str, action: str) -> Job:
        """Validate that a job is eligible for resolution.

        Raises StateConflictError if the job state or current resolution
        prevents the requested action.
        """
        job = await self.get_job(job_id)
        if job.state != JobState.review:
            raise StateConflictError(f"Job {job_id} is in state {job.state!r}, not 'review'")
        if job.resolution not in (None, Resolution.unresolved, Resolution.conflict):
            raise StateConflictError(f"Job {job_id} already resolved as {job.resolution!r}")
        return job

    async def execute_resolve(
        self,
        job: Job,
        action: str,
        merge_service: Any,
    ) -> tuple[str, str | None, list[str] | None, str | None]:
        """Execute merge/PR/discard resolution and persist the outcome.

        On successful resolution (merged, pr_created, discarded), the job
        transitions from ``review`` → ``completed``.  On conflict, it stays
        in ``review`` with resolution ``conflict``.

        Returns (resolution, pr_url, conflict_files, error).
        """
        from backend.services.merge_service import MergeService

        ms: MergeService = merge_service
        result = await ms.resolve_job(
            job_id=job.id,
            action=action,
            repo_path=job.repo,
            worktree_path=job.worktree_path,
            branch=job.branch,
            base_ref=job.base_ref,
            prompt=job.prompt,
        )

        from backend.services.merge_service import MergeStatus

        status_map = {
            MergeStatus.merged: Resolution.merged,
            MergeStatus.pr_created: Resolution.pr_created,
            MergeStatus.conflict: Resolution.conflict,
            MergeStatus.skipped: Resolution.discarded if action == "discard" else Resolution.unresolved,
            MergeStatus.error: Resolution.unresolved,
        }
        resolution = status_map.get(result.status, Resolution.unresolved)

        if result.error:
            log.warning(
                "job_resolution_failed",
                job_id=job.id,
                action=action,
                merge_status=str(result.status),
                error=result.error,
            )

        # Persist resolution
        await self._job_repo.update_resolution(job.id, resolution, pr_url=result.pr_url)

        # Transition review → completed for final resolutions
        final_resolutions = (Resolution.merged, Resolution.pr_created, Resolution.discarded)
        if resolution in final_resolutions and job.state == JobState.review:
            await self.transition_state(job.id, JobState.completed)

        return resolution, result.pr_url, result.conflict_files, result.error

    def build_job_resolved_event(
        self,
        job_id: str,
        resolution: str,
        *,
        pr_url: str | None = None,
        conflict_files: list[str] | None = None,
        error: str | None = None,
    ) -> DomainEvent:
        """Build a job_resolved event for publication after the caller commits."""
        from backend.models.events import DomainEvent, DomainEventKind

        payload: dict[str, object] = {"resolution": resolution}
        if pr_url:
            payload["pr_url"] = pr_url
        if conflict_files:
            payload["conflict_files"] = conflict_files
        if error:
            payload["error"] = error

        return DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=job_id,
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.job_resolved,
            payload=payload,
        )

    async def archive_job(self, job_id: str) -> Job:
        """Archive a job (hide from Kanban board) and clean up its worktree."""
        job = await self.get_job(job_id)
        if job.state not in TERMINAL_STATES:
            raise StateConflictError(f"Job {job_id} is in state {job.state!r}, cannot archive active jobs")
        await self._job_repo.update_archived_at(job_id, datetime.now(UTC))

        # Clean up worktree and branch immediately rather than waiting for
        # the daily retention sweep — the UI promises this happens on archive.
        if self._git and job.worktree_path and job.worktree_path != job.repo:
            try:
                await self._git.remove_worktree(job.repo, job.worktree_path)
                log.info("archive_worktree_removed", job_id=job_id, worktree=job.worktree_path)
            except Exception:
                log.warning("archive_worktree_cleanup_failed", job_id=job_id, exc_info=True)

        return await self.get_job(job_id)

    def build_job_archived_event(self, job_id: str) -> DomainEvent:
        """Build a job_archived event for publication after the caller commits."""
        from backend.models.events import DomainEvent, DomainEventKind

        return DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=job_id,
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.job_archived,
            payload={},
        )
