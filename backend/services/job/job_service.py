"""Job lifecycle orchestration."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from backend.models.domain import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    InvalidStateTransitionError,
    Job,
    JobNotFoundError,
    JobSpec,
    JobState,
    ProjectNotFoundError,
    RepoNotAllowedError,
    Resolution,
    ServiceInitError,
    StateConflictError,
    validate_state_transition,
)
from backend.services.adapters.agent_adapter import validate_sdk_model
from backend.services.adapters.integration_policy import IntegrationPolicy
from backend.services.completers.naming_service import NamingError
from backend.services.git.git_service import GitError
from backend.services.project.repo_membership import normalize_repo_path

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.config import CPLConfig
    from backend.models.events import EventKind, SessionEvent
    from backend.persistence.event_repo import EventRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.project_repo import ProjectRepository
    from backend.services.completers.naming_service import NamingService
    from backend.services.events.event_bus import EventBus
    from backend.services.git.git_service import GitService
    from backend.services.merge_service import MergeService

log = structlog.get_logger()

_MAX_COUNT_LIMIT = 10_000  # upper bound for count queries that scan all jobs


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
        event_bus: EventBus | None = None,
        project_repo: ProjectRepository | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._git = git_service
        self._config = config
        self._naming = naming_service
        self._event_repo = event_repo
        self._event_bus = event_bus
        self._project_repo = project_repo

    @classmethod
    def from_session(
        cls,
        session: AsyncSession,
        config: CPLConfig,
        *,
        git_service: GitService | None = None,
        naming_service: NamingService | None = None,
        event_bus: EventBus | None = None,
    ) -> JobService:
        """Construct from a DB session."""
        from backend.persistence.event_repo import EventRepository
        from backend.persistence.job_repo import JobRepository
        from backend.persistence.project_repo import ProjectRepository

        job_repo = JobRepository(session)
        event_repo = EventRepository(session)
        project_repo = ProjectRepository(session)
        if git_service is None:
            from backend.services.git.git_service import GitService

            git_service = GitService(config)
        return cls(
            job_repo=job_repo,
            git_service=git_service,
            config=config,
            naming_service=naming_service,
            event_repo=event_repo,
            event_bus=event_bus,
            project_repo=project_repo,
        )

    async def validate_repo_async(self, repo: str) -> str:
        """Resolve a repo path (expand ``~``, make absolute).

        Does not itself enforce Project membership — job creation enforces
        that separately via :meth:`_resolve_job_project_id`, since the
        ownership check must run once against the same repo/project pairing
        (whether inferred or explicitly requested) rather than twice with
        different uniqueness semantics.
        """
        return str(Path(repo).expanduser().resolve())

    async def _resolve_job_project_id(self, resolved_repo: str, project_id: str | None) -> str:
        """Resolve and validate the immutable Project owner for a new job.

        Always returns a Project ID — a Job can never exist without an
        owning Project (AD-5). Raises if the repo does not unambiguously
        belong to a Project, or if an explicitly requested ``project_id``
        does not include the repo.
        """
        if self._project_repo is None:
            raise ServiceInitError("ProjectRepository required to validate project-scoped jobs")
        if project_id is not None:
            project = await self._project_repo.get(project_id)
            if project is None:
                raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
            if resolved_repo not in {normalize_repo_path(path) for path in project.repo_paths}:
                raise RepoNotAllowedError(f"Repository '{resolved_repo}' is not a member of Project '{project_id}'.")
            return project_id
        resolved_project_id = await self._project_repo.resolve_project_id_for_repo_path(resolved_repo)
        if resolved_project_id is None:
            raise RepoNotAllowedError(
                f"Repository '{resolved_repo}' does not belong to exactly one Project. "
                "Create or update a Project to include this repository before launching jobs."
            )
        return resolved_project_id

    async def list_events_by_job(
        self,
        job_id: str,
        kinds: list[EventKind],
        limit: int = 2000,
    ) -> list[SessionEvent]:
        """Query domain events for a job, filtered by kind.

        Delegates to the event repository so that API routes never need
        to import persistence classes directly.
        """
        if self._event_repo is None:
            raise ServiceInitError("JobService was created without an event_repo")
        return await self._event_repo.list_by_job(job_id, kinds, limit=limit)

    async def get_latest_progress_preview(self, job_id: str) -> ProgressPreview | None:
        """Return the latest persisted progress milestone for a job."""
        if self._event_repo is None:
            raise ServiceInitError("JobService was created without an event_repo")
        preview = await self._event_repo.get_latest_progress_preview(job_id)
        if preview is None:
            return None
        return ProgressPreview(headline=preview[0], summary=preview[1])

    async def list_latest_progress_previews(self, job_ids: list[str]) -> dict[str, ProgressPreview]:
        """Return the latest persisted progress milestone for each requested job."""
        if self._event_repo is None:
            raise ServiceInitError("JobService was created without an event_repo")
        previews = await self._event_repo.list_latest_progress_previews(job_ids)
        return {
            job_id: ProgressPreview(headline=headline, summary=summary)
            for job_id, (headline, summary) in previews.items()
        }

    async def _list_worktree_names(self, resolved_repo: str) -> set[str]:
        if self._git is None:
            return set()
        names = self._git.list_worktree_names(resolved_repo)
        if inspect.isawaitable(names):
            names = await names
        return set(names)

    async def _resolve_job_name(
        self,
        spec: JobSpec,
        resolved_repo: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Resolve title, description, branch, and worktree_name for a new job.

        Uses pre-computed names from the frontend if available, falls back to
        LLM naming, and finally to a hash-based fallback.

        Returns (title, description, branch, worktree_name).
        Raises NamingError (from naming_service) if LLM naming fails.
        """
        title = spec.title
        description = spec.description
        worktree_name = spec.worktree_name
        branch = spec.branch

        # When the frontend has pre-computed names via suggest-names, we can
        # skip the expensive LLM round-trip entirely.  We only need collision
        # checks (fast DB + git lookups).
        pre_named = title is not None and worktree_name is not None

        if pre_named:
            # Still verify the pre-computed worktree_name doesn't collide
            assert worktree_name is not None
            existing_job_ids = await self._job_repo.list_ids()
            if worktree_name in existing_job_ids or (await self._job_repo.get(worktree_name) is not None):
                # Collision — append a numeric suffix to preserve the LLM name.
                # No need to re-run the LLM; the base name is already good.
                counter = 2
                while f"{worktree_name}-{counter}" in existing_job_ids:
                    counter += 1
                worktree_name = f"{worktree_name}-{counter}"
                # Also deduplicate branch if provided
                if branch:
                    existing_branches = await self._git.list_branches(resolved_repo) if self._git else set()
                    if branch in existing_branches:
                        branch = f"{branch}-{counter}"
                log.info("pre_named_collision_suffixed", worktree_name=worktree_name)

        if not pre_named and worktree_name is None:
            # Naming is never allowed to block job creation: derive an instant,
            # human-readable slug locally instead of calling the naming LLM.
            # If a naming service is configured, the real title/description
            # (and, if still unset, branch) are filled in asynchronously after
            # the job is created — see `enrich_naming_async`.
            from backend.services.completers.naming_service import heuristic_slug

            existing_job_ids = await self._job_repo.list_ids()
            existing_worktrees = await self._list_worktree_names(resolved_repo)
            exclude_names = existing_job_ids | existing_worktrees
            base_slug = heuristic_slug(spec.prompt)
            candidate = base_slug
            counter = 2
            while candidate in exclude_names or await self._job_repo.get(candidate) is not None:
                candidate = f"{base_slug}-{counter}"
                counter += 1
            worktree_name = candidate
            if branch is None:
                branch = f"feat/{worktree_name}"

        return title, description, branch, worktree_name

    async def enrich_naming_async(self, job_id: str, spec: JobSpec, resolved_repo: str) -> SessionEvent | None:
        """Background LLM naming enrichment: fills in title/description after creation.

        Runs after `create_job` has already returned using the instant
        heuristic slug. Never touches `branch` or `worktree_name` — those are
        already used (or being used) to create the actual git worktree, and
        changing them post-hoc would desync the DB from git reality.
        """
        if self._naming is None:
            return None
        job = await self._job_repo.get(job_id)
        if job is None or job.title is not None:
            return None
        try:
            existing_branches = await self._git.list_branches(resolved_repo) if self._git else set()
            existing_worktrees = await self._list_worktree_names(resolved_repo)
            title, description, _branch, _worktree_name = await self._naming.generate(
                spec.prompt,
                existing_branches=existing_branches,
                existing_worktrees=existing_worktrees,
                parent_job_context=spec.parent_job_context,
            )
        except NamingError as exc:
            log.warning("naming_enrichment_failed", job_id=job_id, error=str(exc))
            return None

        job = await self._job_repo.get(job_id)
        if job is None:
            return None
        await self._job_repo.update_title_and_branch(job_id, title=title, description=description)

        from backend.models.events import EventKind, new_event

        log.info("naming_enrichment_complete", job_id=job_id, title=title)
        return new_event(
            session_id=job_id,
            timestamp=datetime.now(UTC),
            kind=EventKind.job_title_updated,
            payload={"title": title, "description": description},
        )

    async def create_job(self, spec: JobSpec) -> Job:
        """Prepare and persist a new Job, retaining caller-owned commit behavior.

        No workspace or agent is created here. Non-task callers retain their
        public creation contract; task execution uses the two phases with a
        closed read session between preparation and its attachment transaction.
        """
        return await self.persist_prepared_job(await self.prepare_job(spec), revalidate_project=False)

    async def prepare_job(self, spec: JobSpec) -> Job:
        """Read-only Git, ownership, SDK and naming preflight; never insert a row.

        The returned domain object is not an accepted Job until persisted.
        Naming failures retain the existing failed-Job representation.
        """
        policy = IntegrationPolicy(self._config)
        resolved_sdk = policy.require_enabled(spec.sdk or policy.default_sdk).value
        validate_sdk_model(resolved_sdk, spec.model)
        resolved_repo = await self.validate_repo_async(spec.repo)
        project_id = await self._resolve_job_project_id(resolved_repo, spec.project_id)

        if self._git is None:
            raise ServiceInitError("GitService required for job creation")

        # Determine base_ref
        base_ref = spec.base_ref
        if base_ref is None:
            base_ref = await self._git.get_default_branch(resolved_repo)

        now = datetime.now(UTC)

        try:
            title, description, branch, worktree_name = await self._resolve_job_name(spec, resolved_repo)
        except NamingError as exc:
            import hashlib

            h = hashlib.sha256(f"{spec.prompt}{now.isoformat()}".encode()).hexdigest()[:12]
            job_id = f"naming-failed-{h}"
            log.error("job_naming_failed", job_id=job_id, error=str(exc))
            return Job(
                id=job_id,
                repo=resolved_repo,
                prompt=spec.prompt,
                project_id=project_id,
                state=JobState.failed,
                base_ref=base_ref,
                branch=None,
                worktree_path=None,
                session_id=None,
                created_at=now,
                updated_at=now,
                completed_at=now,
                title=None,
                description=None,
                worktree_name=None,
                preset=spec.preset,
                model=spec.model,
                failure_reason=f"Naming failed: {exc}",
                parent_job_id=spec.parent_job_id,
            )

        assert worktree_name is not None
        log.info(
            "naming_preflight_complete",
            job_id=worktree_name,
            title=title,
            branch=branch,
            worktree_name=worktree_name,
        )
        return Job(
            id=worktree_name,
            repo=resolved_repo,
            prompt=spec.prompt,
            project_id=project_id,
            state=JobState.preparing,
            base_ref=base_ref,
            branch=branch,
            worktree_path=None,
            session_id=None,
            created_at=now,
            updated_at=now,
            title=title,
            description=description,
            worktree_name=worktree_name,
            preset=spec.preset,
            model=spec.model,
            sdk=resolved_sdk,
            verify=spec.verify,
            self_review=spec.self_review,
            max_turns=spec.max_turns,
            verify_prompt=spec.verify_prompt,
            self_review_prompt=spec.self_review_prompt,
            enable_stall_detection=spec.enable_stall_detection,
            enable_plan_tracking=spec.enable_plan_tracking,
            parent_job_id=spec.parent_job_id,
            mode=spec.mode,
        )

    async def persist_prepared_job(
        self,
        prepared: Job,
        *,
        revalidate_project: bool = True,
        align_generated_branch: bool = False,
    ) -> Job:
        """Insert a prepared Job with a final DB name check, without Git or commit.

        Task callers must hold their short serialized attachment transaction.
        Non-task create_job callers retain their existing transaction ownership.
        ``align_generated_branch`` is for task-generated ``feat/<slug>`` names;
        explicitly supplied non-task branches retain their existing behavior.
        """
        if revalidate_project:
            await self._resolve_job_project_id(prepared.execution_repo, prepared.project_id)
        job = prepared
        if await self._job_repo.get(job.id) is not None:
            existing_ids = await self._job_repo.list_ids()
            counter = 2
            while f"{prepared.id}-{counter}" in existing_ids:
                counter += 1
            job_id = f"{prepared.id}-{counter}"
            branch = prepared.branch
            if align_generated_branch and branch == f"feat/{prepared.id}":
                branch = f"feat/{job_id}"
            job = replace(
                prepared,
                id=job_id,
                worktree_name=job_id if prepared.worktree_name is not None else None,
                branch=branch,
            )
            log.warning("naming_collision_suffixed", job_id=job_id)
        await self._job_repo.create(job)
        log.info("job_created", job_id=job.id, title=job.title, repo=job.repo, state=job.state)
        return job

    async def setup_workspace(self, job_id: str) -> Job:
        """Create the worktree for a ``preparing`` job and transition to ``queued``.

        Called as a background task after the job row has been committed.
        Publishes ``job_setup_progress`` events during setup so the frontend
        can show a progress stepper.

        Returns the updated Job.
        Raises JobNotFoundError / StateConflictError on bad state.
        """
        from backend.models.events import EventKind, new_event

        if self._git is None:
            raise ServiceInitError("GitService required for workspace setup")

        job = await self._job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} does not exist.")
        if job.state != JobState.preparing:
            raise StateConflictError(f"Job {job_id} is in state {job.state!r}, expected 'preparing'.")
        repo_path, base_ref = job.execution_repo, job.execution_base_ref

        async def _emit_progress(step: str) -> None:
            if self._event_bus is not None:
                await self._event_bus.publish(
                    new_event(
                        session_id=job_id,
                        timestamp=datetime.now(UTC),
                        kind=EventKind.job_setup_progress,
                        payload={"step": step},
                    )
                )

        await _emit_progress("creating_workspace")

        try:
            worktree_path, branch_name = await self._git.create_worktree(
                repo_path=repo_path,
                job_id=job.worktree_name or job_id,
                base_ref=base_ref,
                branch=job.branch,
            )
        except GitError as exc:
            now = datetime.now(UTC)
            validate_state_transition(JobState.preparing, JobState.failed)
            await self._job_repo.update_state(job_id, JobState.failed, now)
            await self._job_repo.update_failure_reason(job_id, f"Worktree creation failed: {exc}")
            log.error("job_worktree_failed", job_id=job_id, error=str(exc))
            job = await self._job_repo.get(job_id)
            if job is None:
                raise JobNotFoundError(f"Job {job_id} disappeared after state update") from exc
            return job

        current = await self._job_repo.get(job_id)
        if current is None:
            raise JobNotFoundError(f"Job {job_id} disappeared during workspace creation")
        if current.state != JobState.preparing:
            await self._job_repo.update_worktree(job_id, worktree_path, branch_name)
            return current

        # Update the job with worktree info and transition to queued
        now = datetime.now(UTC)
        if not await self._job_repo.finish_workspace_setup(job_id, worktree_path, branch_name, now):
            await self._job_repo.update_worktree(job_id, worktree_path, branch_name)

        log.info("job_workspace_ready", job_id=job_id, worktree_path=worktree_path, branch=branch_name)

        job = await self._job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} disappeared after state update")
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

    async def list_all_jobs(
        self,
        state: str | None = None,
        archived: bool | None = None,
    ) -> list[Job]:
        """List all matching jobs without pagination.

        Use only for internal recovery/snapshot paths — not for API responses.
        """
        include_archived: bool | None = None
        if archived is True:
            include_archived = True
        elif archived is False:
            include_archived = False
        return await self._job_repo.list_all(state=state, include_archived=include_archived)

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
            JobSpec(
                repo=original.execution_repo,
                prompt=original.prompt,
                base_ref=original.execution_base_ref,
                preset=original.preset,
                model=original.model,
                sdk=original.sdk,
            )
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

    async def validate_for_resolution(self, job_id: str) -> Job:
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
        merge_service: MergeService,
    ) -> tuple[Resolution, str | None, list[str] | None, str | None]:
        """Execute merge/PR/discard resolution and persist the outcome.

        On successful resolution (merged, pr_created, discarded), the job
        transitions from ``review`` → ``completed``.  On conflict, it stays
        in ``review`` with resolution ``conflict``.

        Returns (resolution, pr_url, conflict_files, error).
        """
        result = await merge_service.resolve_job(job, action)

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

    async def resolve_and_complete(
        self,
        job: Job,
        action: str,
        merge_service: MergeService,
    ) -> tuple[Resolution, str | None, list[str] | None, str | None, list[SessionEvent]]:
        """Full resolve protocol: execute merge, persist result, build events.

        Returns (resolution, pr_url, conflict_files, error, events_to_publish).
        The caller should commit the session and then publish the events.
        """
        from backend.models.events import EventKind, new_event

        resolution, pr_url, conflict_files, error = await self.execute_resolve(
            job=job,
            action=action,
            merge_service=merge_service,
        )

        events: list[SessionEvent] = [
            self.build_job_resolved_event(
                job.id,
                resolution,
                pr_url=pr_url,
                conflict_files=conflict_files,
                error=error,
            )
        ]

        if resolution in (Resolution.merged, Resolution.pr_created, Resolution.discarded):
            events.append(
                new_event(
                    session_id=job.id,
                    timestamp=datetime.now(UTC),
                    kind=EventKind.job_completed,
                    payload={
                        "resolution": resolution,
                        "merge_status": resolution,
                        "pr_url": pr_url,
                    },
                )
            )

        return resolution, pr_url, conflict_files, error, events

    async def build_conflict_resume_prompt(self, job_id: str) -> str:
        """Build an agent prompt instructing it to resolve merge conflicts.

        Fetches the latest merge_conflict event for the job to identify
        conflicting files, then constructs a structured resolution prompt.
        """
        from backend.models.events import EventKind

        conflict_events = await self.list_events_by_job(
            job_id,
            kinds=[EventKind.merge_conflict],
        )
        conflict_files: list[str] = []
        if conflict_events:
            conflict_files = cast("list[str]", conflict_events[-1].payload.get("conflict_files", []))

        job = await self.get_job(job_id)
        files_detail = (
            "\nThe following files have conflicts:\n" + "\n".join(f"  - {f}" for f in conflict_files)
            if conflict_files
            else ""
        )
        return (
            f"A merge conflict was detected when attempting to merge branch '{job.branch}' "
            f"into '{job.base_ref}'.{files_detail}\n\n"
            "Please resolve the merge conflicts:\n"
            "1. Run `git merge <base_ref>` in the worktree to reproduce the conflict markers\n"
            "2. Edit the conflicting files to resolve all conflicts, preserving the functional "
            "intent of both sides without compromising either set of changes\n"
            "3. Stage and commit the resolved files\n"
            "Do not make any other modifications beyond resolving the merge conflicts."
        )

    def build_job_resolved_event(
        self,
        job_id: str,
        resolution: Resolution,
        *,
        pr_url: str | None = None,
        conflict_files: list[str] | None = None,
        error: str | None = None,
    ) -> SessionEvent:
        """Build a job_resolved event for publication after the caller commits."""
        from backend.models.events import EventKind, new_event

        payload: dict[str, object] = {"resolution": resolution}
        if pr_url:
            payload["pr_url"] = pr_url
        if conflict_files:
            payload["conflict_files"] = conflict_files
        if error:
            payload["error"] = error

        return new_event(session_id=job_id, timestamp=datetime.now(UTC), kind=EventKind.job_resolved, payload=payload)

    async def archive_job(self, job_id: str) -> Job:
        """Archive a job (hide from Kanban board) and clean up its worktree."""
        job = await self.get_job(job_id)
        if job.state not in TERMINAL_STATES:
            raise StateConflictError(f"Job {job_id} is in state {job.state!r}, cannot archive active jobs")
        await self._job_repo.update_archived_at(job_id, datetime.now(UTC))

        # Clean up worktree and branch immediately rather than waiting for
        # the daily retention sweep — the UI promises this happens on archive.
        if self._git and not job.observation_only and job.worktree_path and job.worktree_path != job.repo:
            # (only for abandoned jobs — merged jobs are handled by merge_index)

            try:
                await self._git.remove_worktree(job.execution_repo, job.worktree_path)
                log.info("archive_worktree_removed", job_id=job_id, worktree=job.worktree_path)
            except (GitError, OSError):
                log.warning("archive_worktree_cleanup_failed", job_id=job_id, exc_info=True)

        return await self.get_job(job_id)

    def build_job_archived_event(self, job_id: str) -> SessionEvent:
        """Build a job_archived event for publication after the caller commits."""
        from backend.models.events import EventKind, new_event

        return new_event(session_id=job_id, timestamp=datetime.now(UTC), kind=EventKind.job_archived, payload={})
