"""Project-scoped task assignment and execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from backend.models.domain import (
    JobState,
    RepoNotAllowedError,
    StateConflictError,
    TaskLinkState,
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
    from backend.services.task_execution_service import TaskExecutionService
    from backend.services.tracker_write_service import TrackerWriteOutcome, TrackerWriteService

_SUCCESSFUL_RESOLUTIONS = frozenset({"merged", "pr_created"})


class RecipeService:
    """Project-scoped task assignment with application-scoped execution delegation."""

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
        execution_service: TaskExecutionService | None = None,
    ) -> None:
        self._task_link_repo = task_link_repo
        self._project_service = project_service
        self._job_service = job_service
        self._job_repo = job_repo
        self._chat_repo = chat_repo
        self._approval_service = approval_service
        self._tracker_link_repo = tracker_link_repo
        self._tracker_write_service = tracker_write_service
        self._execution = execution_service
        # Kept for callers that drain this public attribute. Durable intents
        # replace coroutine-based delivery, so execution never appends to it.
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
        """List tasks; when injected, the coordinator owns state reconciliation."""
        if self._execution is not None:
            return await self._execution.list_task_links(project_id)
        await self._project_service.get(project_id)
        return await self._refresh_states(await self._task_link_repo.list_by_project(project_id))

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
        target = links_by_key.get(dep_key)
        if target is None or target.job_id is None or self._job_repo is None:
            return False
        job = await self._job_repo.get(target.job_id)
        return (
            job is not None and job.state == JobState.completed and str(job.resolution or "") in _SUCCESSFUL_RESOLUTIONS
        )

    async def _refresh_states(self, links: list[TaskLink]) -> list[TaskLink]:
        """Compatibility for session-owned listing; never release a start claim."""
        if self._job_repo is None:
            return links
        links_by_key = {key: link for link in links if (key := self._composite_key(link)) is not None}
        refreshed: list[TaskLink] = []
        for link in links:
            if link.state == TaskLinkState.starting and link.job_id is None:
                refreshed.append(link)
                continue
            if link.job_id is not None:
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
                updated = await self._task_link_repo.compare_and_set_state(
                    link.id,
                    desired,
                    expected_state=link.state,
                    expected_job_id=link.job_id,
                )
                link = updated or link
            refreshed.append(link)
        return refreshed

    def _require_execution(self) -> TaskExecutionService:
        if self._execution is None:
            raise StateConflictError("Task execution requires the application's shared TaskExecutionService.")
        return self._execution

    async def start_task_link(self, project_id: str, task_link_id: str) -> tuple[TaskLink, Job]:
        """Start through the coordinator; callers must not hold a writer transaction."""
        return await self._require_execution().start_task_link(project_id, task_link_id)

    async def handle_job_completed(self, job_id: str, *, resolution: str | None) -> list[Job]:
        if self._execution is None and self._job_service is None:
            return []
        return await self._require_execution().handle_job_completed(job_id, resolution=resolution)

    async def spawn_approved_task_link(self, task_link_id: str, *, parent_job_id: str | None) -> Job | None:
        if self._execution is None and self._job_service is None:
            return None
        return await self._require_execution().spawn_approved_task_link(task_link_id, parent_job_id=parent_job_id)

    async def handle_job_failed(self, job_id: str) -> None:
        await self._require_execution().handle_job_failed(job_id)
