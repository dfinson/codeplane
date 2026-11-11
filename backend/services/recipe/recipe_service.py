"""Project-scoped task assignment and execution."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import (
    JobSpec,
    JobState,
    RepoNotAllowedError,
    SDKModelMismatchError,
    StateConflictError,
    TaskLinkNotFoundError,
    TaskLinkState,
)
from backend.services.tracker_write_service import (
    TrackerWriteAction,
    TrackerWriteOutcome,
    TrackerWriteRequest,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from backend.models.domain import Job, TaskLink
    from backend.persistence.chat_repo import ChatRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.task_link_repo import TaskLinkRepository
    from backend.persistence.tracker_link_repo import TrackerLinkRepository
    from backend.services.job.approval_service import ApprovalService
    from backend.services.job.job_service import JobService
    from backend.services.project.project_service import ProjectService
    from backend.services.tracker_write_service import TrackerWriteService

log = structlog.get_logger()


# Resolutions that count as a job "completing successfully" for the purpose of
# satisfying a dependent TaskLink's dependency (Story 4.5, AC #1). A job that
# reaches ``JobState.completed`` with resolution "discarded" still fires
# ``EventKind.job_completed`` (see backend/services/runtime/service.py), but
# discarding means the user threw the work away — that must never satisfy a
# dependency or spawn the next task in the chain.
_SUCCESSFUL_RESOLUTIONS = frozenset({"merged", "pr_created"})

# Prefix for the ``Approval.proposed_action`` created when a gated chain's
# dependent TaskLink becomes spawn-eligible (Story 5.4, AC #1). The suffix is
# the candidate TaskLink's id, so the approval-resolve route (and this
# service's own re-entrancy guard) can recover which TaskLink to spawn.
_SPAWN_TASK_ACTION_PREFIX = "spawn_task:"


class RecipeService:
    """Orchestrates Project-scoped task assignment and execution."""

    def __init__(
        self,
        task_link_repo: TaskLinkRepository,
        project_service: ProjectService,
        *,
        job_service: JobService | None = None,
        job_repo: JobRepository | None = None,
        chat_repo: ChatRepository | None = None,
        approval_service: ApprovalService | None = None,
        tracker_link_repo: TrackerLinkRepository | None = None,
        tracker_write_service: TrackerWriteService | None = None,
    ) -> None:
        self._task_link_repo = task_link_repo
        self._project_service = project_service
        # All optional: request-scoped DI call sites (assignment/listing, Story
        # 4.2-4.4) never spawn jobs and don't need them. The job-completion
        # subscriber wired in backend/lifespan.py supplies job_service/job_repo
        # (Story 4.5) plus chat_repo/approval_service (Story 5.4, gating), plus
        # tracker_link_repo/tracker_write_service (Story 4.6, tracker write-back).
        self._job_service = job_service
        self._job_repo = job_repo
        self._chat_repo = chat_repo
        self._approval_service = approval_service
        self._tracker_link_repo = tracker_link_repo
        self._tracker_write_service = tracker_write_service
        # Coroutines built by `_maybe_route_tracker_write` (Story 4.6), not yet
        # scheduled as tasks. `TrackerWriteService.execute`'s approval wait can
        # block indefinitely, so it must never be scheduled via a task tracked
        # only on this instance — `RecipeService` is a short-lived local built
        # fresh per `job_completed` event (see `backend/lifespan.py`) with no
        # outer reference keeping it alive, so any `asyncio.Task` stored only
        # on `self` risks garbage collection before the approval resolves.
        # Callers (the `lifespan.py` event subscriber) must drain this list via
        # `pending_tracker_writes` and schedule each coroutine through the
        # module-level `_fire_and_forget` helper, whose `_ephemeral_tasks` set
        # has app-lifetime scope — the same fix used for Story 5.4/PR #70.
        self.pending_tracker_writes: list[Coroutine[None, None, TrackerWriteOutcome]] = []

    async def create_manual_task_link(
        self,
        *,
        project_id: str,
        repo_path: str,
        tracker_link_id: str | None = None,
        tracker_ticket_ref: str,
        prompt_override: str,
        output_routes: list[str] | None = None,
    ) -> TaskLink:
        """Create a fresh TaskLink directly against an existing tracker ticket."""
        project = await self._project_service.get(project_id)
        resolved_repo_path = str(Path(repo_path).expanduser().resolve())
        if resolved_repo_path not in project.repo_paths:
            raise RepoNotAllowedError(f"Repo path '{resolved_repo_path}' does not belong to Project '{project_id}'.")
        if tracker_link_id is not None:
            if self._tracker_link_repo is None:
                raise StateConflictError("TrackerLink validation is unavailable.")
            tracker_link = await self._tracker_link_repo.get(tracker_link_id)
            if tracker_link is None or tracker_link["project_id"] != project_id:
                raise StateConflictError(f"TrackerLink '{tracker_link_id}' does not belong to Project '{project_id}'.")
        if tracker_link_id is None:
            return await self._task_link_repo.create_manual(
                project_id=project_id,
                repo_path=resolved_repo_path,
                tracker_ticket_ref=tracker_ticket_ref,
                prompt_override=prompt_override,
                output_routes=output_routes,
            )
        return await self._task_link_repo.create_manual(
            project_id=project_id,
            repo_path=resolved_repo_path,
            tracker_link_id=tracker_link_id,
            tracker_ticket_ref=tracker_ticket_ref,
            prompt_override=prompt_override,
            output_routes=output_routes,
        )

    async def list_task_links(self, project_id: str) -> list[TaskLink]:
        """List every TaskLink currently persisted for a Project."""
        # Ensure the Project exists (raises ProjectNotFoundError otherwise).
        await self._project_service.get(project_id)
        links = await self._task_link_repo.list_by_project(project_id)
        return await self._refresh_states(links)

    @staticmethod
    def _composite_key(task_link: TaskLink) -> str | None:
        """The ``"{repo_path}::{story_node_id}"`` key other TaskLinks' ``depends_on``
        entries reference. ``None`` for a manually-assigned TaskLink (no
        ``story_node_id``) — those are never valid dependency targets.
        """
        if task_link.story_node_id is None:
            return None
        return f"{task_link.repo_path}::{task_link.story_node_id}"

    @staticmethod
    def _derive_prompt(task_link: TaskLink) -> str:
        """Use an explicit override or identify an existing historical task.

        Source-backed rows retain their IDs without a stored task body. Their
        agent must locate the existing definition or report missing requirements.
        """
        if task_link.prompt_override:
            return task_link.prompt_override
        return (
            f"Implement task '{task_link.story_node_id}' in this repo. Locate and follow "
            "its full task definition in the repository for complete requirements. "
            "If the definition is missing, report that rather than inventing requirements. "
            "This prompt only identifies which task to implement."
        )

    async def _is_satisfied(self, dep_key: str, links_by_key: dict[str, TaskLink]) -> bool:
        """Whether the TaskLink identified by composite key ``dep_key`` has a
        successfully-completed linked Job (Story 4.5, AC #1/#2)."""
        target = links_by_key.get(dep_key)
        if target is None or target.job_id is None or self._job_repo is None:
            return False
        job = await self._job_repo.get(target.job_id)
        if job is None:
            return False
        return str(job.state) == "completed" and str(job.resolution or "") in _SUCCESSFUL_RESOLUTIONS

    async def _refresh_states(self, links: list[TaskLink]) -> list[TaskLink]:
        """Reconcile persisted TaskLink states with dependency and Job truth."""
        if self._job_repo is None:
            return links
        links_by_key = {key: link for link in links if (key := self._composite_key(link)) is not None}
        refreshed: list[TaskLink] = []
        for link in links:
            if link.state == TaskLinkState.starting and link.job_id is None:
                desired = TaskLinkState.starting
            elif link.job_id is not None:
                job = await self._job_repo.get(link.job_id)
                if job is None:
                    desired = TaskLinkState.failed
                elif job.state == JobState.completed:
                    desired = (
                        TaskLinkState.completed
                        if str(job.resolution or "") in _SUCCESSFUL_RESOLUTIONS
                        else TaskLinkState.failed
                    )
                elif job.state in {JobState.failed, JobState.canceled}:
                    desired = TaskLinkState.failed
                else:
                    desired = TaskLinkState.running
            else:
                satisfied = all([await self._is_satisfied(dep, links_by_key) for dep in link.depends_on])
                desired = TaskLinkState.ready if satisfied else TaskLinkState.waiting
            if link.state != desired:
                updated = await self._task_link_repo.set_state(link.id, desired)
                link = updated or link
            refreshed.append(link)
        return refreshed

    async def _start_ready_task_link(
        self,
        task_link: TaskLink,
        *,
        parent_job_id: str | None,
    ) -> tuple[TaskLink, Job] | None:
        """Commit a short claim before Git-backed Job creation, then attach atomically."""
        if self._job_service is None:
            raise StateConflictError("Job creation is unavailable.")
        claimed = await self._task_link_repo.claim_ready_and_commit(task_link.id)
        if claimed is None:
            return None
        try:
            new_job = await self._job_service.create_job(
                JobSpec(
                    repo=claimed.repo_path,
                    prompt=self._derive_prompt(claimed),
                    parent_job_id=parent_job_id,
                )
            )
        except Exception:
            await self._task_link_repo.recover_start_claim(claimed.id)
            raise
        attached = await self._task_link_repo.attach_claimed_job(
            claimed.id,
            new_job.id,
            state=TaskLinkState.failed if new_job.state == JobState.failed else TaskLinkState.running,
        )
        if attached is None:
            await self._task_link_repo.recover_start_claim(claimed.id)
            raise StateConflictError(f"TaskLink '{claimed.id}' lost its start claim.")
        return attached, new_job

    async def start_task_link(self, project_id: str, task_link_id: str) -> tuple[TaskLink, Job]:
        """Start a ready root or dependency-satisfied TaskLink exactly once."""
        await self._project_service.get(project_id)
        task_link = await self._task_link_repo.get(task_link_id)
        if task_link is None or task_link.project_id != project_id:
            raise TaskLinkNotFoundError(f"TaskLink '{task_link_id}' does not exist in Project '{project_id}'.")
        links = await self._refresh_states(await self._task_link_repo.list_by_project(project_id))
        task_link = next(link for link in links if link.id == task_link_id)
        if task_link.state != TaskLinkState.ready:
            raise StateConflictError(
                f"TaskLink '{task_link_id}' is {task_link.state.value}; only ready TaskLinks can start."
            )
        pending_action = f"{_SPAWN_TASK_ACTION_PREFIX}{task_link.id}"
        if self._approval_service is not None:
            pending = await self._approval_service.list_pending()
            if any(approval.proposed_action == pending_action for approval in pending):
                raise StateConflictError(
                    f"TaskLink '{task_link_id}' has a pending chain approval and cannot be started directly."
                )
        if task_link.depends_on and await self._is_chain_gated(task_link.chain_root_id or task_link.id):
            raise StateConflictError(
                f"TaskLink '{task_link_id}' belongs to a gated chain and requires approval before it can start."
            )
        started = await self._start_ready_task_link(task_link, parent_job_id=None)
        if started is None:
            raise StateConflictError(f"TaskLink '{task_link_id}' was already claimed.")
        return started

    async def handle_job_completed(self, job_id: str, *, resolution: str | None) -> list[Job]:
        """React to a Job reaching ``JobState.completed`` (Story 4.5 AC #1-#3; Story 5.4 AC #1-#2).

        Finds the TaskLink whose linked Job just completed, and for every other
        TaskLink in the same Project that depends on it and has no `job_id` of
        its own yet, checks whether *all* of its dependencies are now satisfied.

        If the Project has an open Chat attached to a chain (Story 5.3's
        `task_link_id` pointer) — i.e. the chain is in gated mode (Story 5.4,
        AC #1) — a `codeplane_approval` entry is created instead of spawning
        directly, and the actual spawn is deferred to
        `spawn_approved_task_link` once that approval is granted. With no
        attached open Chat, the pre-existing Story 4.5 immediate-spawn
        behavior is completely unchanged (AC #2).

        Returns the newly-created Jobs (so callers, e.g. the `lifespan.py`
        event-bus subscriber, can start each one via `RuntimeService.setup_and_start`).
        Jobs deferred behind a pending approval are never included here — they
        are returned later, from `spawn_approved_task_link`, once approved.

        Never raises: a bad dependent (disallowed repo, mismatched SDK/model)
        is logged and skipped so it never blocks other dependents or crashes
        the caller (an event-bus subscriber).
        """
        if self._job_service is None or self._job_repo is None:
            return []

        completed_link = await self._task_link_repo.get_by_job_id(job_id)
        if completed_link is None:
            return []

        terminal_state = TaskLinkState.completed if resolution in _SUCCESSFUL_RESOLUTIONS else TaskLinkState.failed
        await self._task_link_repo.set_state(completed_link.id, terminal_state)
        if resolution not in _SUCCESSFUL_RESOLUTIONS:
            return []

        await self._maybe_route_tracker_write(completed_link, job_id)

        completed_key = self._composite_key(completed_link)
        if completed_key is None:
            return []

        project_links = await self._task_link_repo.list_by_project(completed_link.project_id)
        links_by_key = {key: link for link in project_links if (key := self._composite_key(link)) is not None}

        gated = await self._is_chain_gated(completed_link.chain_root_id or completed_link.id)
        pending_spawn_actions: set[str] | None = None
        if gated:
            assert self._approval_service is not None  # narrowed by _is_chain_gated
            pending = await self._approval_service.list_pending()
            pending_spawn_actions = {a.proposed_action for a in pending if a.proposed_action is not None}

        spawned: list[Job] = []
        for candidate in project_links:
            if candidate.job_id is not None:
                # Already spawned — never spawn a second time (AC #3).
                continue
            if completed_key not in candidate.depends_on:
                continue

            satisfied = all([await self._is_satisfied(dep_key, links_by_key) for dep_key in candidate.depends_on])
            if not satisfied:
                continue

            if await self._task_link_repo.set_state(candidate.id, TaskLinkState.ready) is None:
                continue
            ready_candidate = replace(candidate, state=TaskLinkState.ready)

            if gated:
                assert self._approval_service is not None  # narrowed by _is_chain_gated
                assert pending_spawn_actions is not None
                action = f"{_SPAWN_TASK_ACTION_PREFIX}{candidate.id}"
                if action in pending_spawn_actions:
                    # Already have a pending approval for this exact candidate —
                    # never double-request (e.g. repeat event-bus deliveries, or
                    # multiple simultaneously-satisfied dependents on one pass).
                    continue
                await self._approval_service.create_request(
                    job_id=job_id,
                    description=(
                        f"Chain is gated by an attached Chat: dependent task "
                        f"'{candidate.story_node_id or candidate.tracker_ticket_ref}' is ready to "
                        "spawn. Approve to start its job."
                    ),
                    proposed_action=action,
                )
                continue

            try:
                started = await self._start_ready_task_link(ready_candidate, parent_job_id=job_id)
            except (RepoNotAllowedError, SDKModelMismatchError) as exc:
                log.warning(
                    "task_link_spawn_failed",
                    task_link_id=candidate.id,
                    repo_path=candidate.repo_path,
                    error=str(exc),
                )
                continue
            if started is not None:
                _, new_job = started
                spawned.append(new_job)

        return spawned

    async def _is_chain_gated(self, chain_root_id: str) -> bool:
        """Whether this persisted TaskLink chain is gated behind approval.

        A Chat attached to another TaskLink in the same Project must not gate
        this chain.
        """
        if self._chat_repo is None or self._approval_service is None:
            return False
        attached_chat = await self._chat_repo.get_attached_open_chat_for_chain(chain_root_id)
        return attached_chat is not None

    async def _maybe_route_tracker_write(self, task_link: TaskLink, job_id: str) -> None:
        """Route a completed TaskLink's `tracker_write` output route through the
        approval gate (Story 4.6, AC #1-#3).

        Fires for *any* TaskLink whose linked Job just completed successfully
        (ingested or manually-assigned, Story 4.3) — unlike dependent-spawn
        logic this doesn't require a `story_node_id`/composite key, since a
        manually-assigned TaskLink is exactly the kind most likely to be
        paired with a tracker ticket.

        Only fires when `tracker_ticket_ref` is set on the TaskLink itself:
        with none set, the write route is unavailable and there is no
        fallback to a Project-level default ticket (AC #2). When present, the
        write targets exactly that ticket, never any other ticket the Project
        might be linked to (AC #1) — the Project-level TrackerLink only
        supplies routing/credential context, never the ticket identity.

        Builds the `TrackerWriteService.execute(...)` call as an unscheduled
        coroutine appended to `pending_tracker_writes` rather than an
        `asyncio.Task` created here: the approval wait can block for an
        arbitrarily long time on operator action, and this `RecipeService`
        instance does not outlive `handle_job_completed`'s caller (see
        `pending_tracker_writes`'s docstring) — the caller must schedule each
        coroutine through `backend/lifespan.py`'s module-level
        `_fire_and_forget` helper instead.
        """
        if self._tracker_write_service is None or self._tracker_link_repo is None:
            return
        if "tracker_write" not in task_link.output_routes:
            return
        if not task_link.tracker_ticket_ref:
            return

        if not task_link.tracker_link_id:
            log.warning(
                "tracker_write_skipped_no_tracker_link",
                task_link_id=task_link.id,
                project_id=task_link.project_id,
                ticket_ref=task_link.tracker_ticket_ref,
            )
            return
        tracker_link = await self._tracker_link_repo.get(task_link.tracker_link_id)
        if tracker_link is None or tracker_link["project_id"] != task_link.project_id:
            log.warning(
                "tracker_write_skipped_invalid_tracker_link",
                task_link_id=task_link.id,
                tracker_link_id=task_link.tracker_link_id,
            )
            return

        request = TrackerWriteRequest(
            tracker_link_id=task_link.tracker_link_id,
            ticket_ref=task_link.tracker_ticket_ref,
            action=TrackerWriteAction.comment,
            value=f"Task '{task_link.story_node_id or task_link.id}' completed.",
        )

        self.pending_tracker_writes.append(self._tracker_write_service.execute(job_id, request))

    async def spawn_approved_task_link(self, task_link_id: str, *, parent_job_id: str | None) -> Job | None:
        """Spawn a TaskLink's job once its gated approval has been granted (Story 5.4, AC #1).

        Idempotent: returns ``None`` (no-op) if the TaskLink doesn't exist or
        already has a `job_id` — mirroring `TaskLinkRepository.set_job_id`'s
        own `job_id IS NULL` guard, preserving Story 4.5's "one TaskLink,
        zero-or-one real Job" invariant even for the deferred/gated path.
        """
        if self._job_service is None or self._job_repo is None:
            return None

        task_link = await self._task_link_repo.get(task_link_id)
        if task_link is None or task_link.job_id is not None:
            return None

        project_links = await self._refresh_states(await self._task_link_repo.list_by_project(task_link.project_id))
        task_link = next(link for link in project_links if link.id == task_link_id)
        if task_link.state != TaskLinkState.ready:
            return None
        try:
            started = await self._start_ready_task_link(
                task_link,
                parent_job_id=parent_job_id,
            )
        except (RepoNotAllowedError, SDKModelMismatchError) as exc:
            log.warning(
                "task_link_gated_spawn_failed",
                task_link_id=task_link.id,
                repo_path=task_link.repo_path,
                error=str(exc),
            )
            return None
        return started[1] if started is not None else None

    async def handle_job_failed(self, job_id: str) -> None:
        """Project a failed/canceled terminal Job onto its linked TaskLink."""
        task_link = await self._task_link_repo.get_by_job_id(job_id)
        if task_link is not None:
            await self._task_link_repo.set_state(task_link.id, TaskLinkState.failed)
