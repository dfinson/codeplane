"""Project CRUD orchestration (Story 2.1 / CAP-6)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from backend.models.domain import (
    Project,
    ProjectNotFoundError,
    ProjectSummary,
    RepoAlreadyAssignedError,
    StateConflictError,
)

if TYPE_CHECKING:
    import builtins

    from backend.config import CPLConfig
    from backend.persistence.project_repo import ProjectRepository
    from backend.services.git.git_service import GitService


class ProjectService:
    """Orchestrates Project creation and membership edits."""

    def __init__(
        self,
        project_repo: ProjectRepository,
        config: CPLConfig,
        git_service: GitService | None = None,
    ) -> None:
        self._project_repo = project_repo
        self._config = config
        self._git_service = git_service

    @staticmethod
    def _resolve(repo_path: str) -> str:
        return str(Path(repo_path).expanduser().resolve())

    async def _assert_repo_paths_available(
        self, repo_paths: builtins.list[str], *, exclude_project_id: str | None
    ) -> None:
        """Raise RepoAlreadyAssignedError if any repo_path belongs to another Project (NFR5)."""
        existing = await self._project_repo.list_all_repo_paths(exclude_project_id=exclude_project_id)
        for path in repo_paths:
            owner = existing.get(path)
            if owner is not None:
                raise RepoAlreadyAssignedError(f"Repo path '{path}' already belongs to Project '{owner}'.")

    async def _assert_valid_repositories(self, repo_paths: builtins.list[str]) -> None:
        """Validate the full proposed membership before any persistence write."""
        if self._git_service is None:
            return
        for path in repo_paths:
            if not await self._git_service.validate_repo(path):
                raise StateConflictError(f"Repository '{path}' does not exist or is not a valid Git repository.")

    async def create(self, name: str, repo_paths: builtins.list[str]) -> Project:
        """Create a new Project, registering each repo path via the existing clone/register logic."""
        resolved = [self._resolve(p) for p in repo_paths]
        if not resolved:
            raise StateConflictError("A Project must contain at least one repository.")
        await self._assert_valid_repositories(resolved)
        await self._assert_repo_paths_available(resolved, exclude_project_id=None)

        project_id = str(uuid.uuid4())
        return await self._project_repo.create(project_id, name, resolved)

    async def get(self, project_id: str) -> Project:
        project = await self._project_repo.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
        return project

    async def list(self) -> builtins.list[Project]:
        return await self._project_repo.list()

    async def update(
        self,
        project_id: str,
        name: str | None = None,
        repo_paths: builtins.list[str] | None = None,
        confirm_repo_removal: bool = False,
    ) -> Project:
        """Rename a Project and/or replace its repo membership.

        When ``repo_paths`` is provided, it replaces the Project's full
        membership list (add/remove semantics are expressed by the caller
        passing the desired resulting set).
        """
        existing = await self.get(project_id)

        resolved_repo_paths: list[str] | None = None
        if repo_paths is not None:
            resolved_repo_paths = [self._resolve(p) for p in repo_paths]
            if not resolved_repo_paths:
                raise StateConflictError("A Project must contain at least one repository.")
            await self._assert_valid_repositories(resolved_repo_paths)
            await self._assert_repo_paths_available(resolved_repo_paths, exclude_project_id=project_id)
            removed = sorted(set(existing.repo_paths) - set(resolved_repo_paths))
            if removed:
                impact = await self._project_repo.membership_impact(project_id, removed)
                consequence = (
                    f"Removal affects {impact.active_job_count} active jobs, "
                    f"{impact.historical_job_count} historical jobs, {impact.task_link_count} TaskLinks, "
                    f"and {impact.tracker_link_count} Project TrackerLinks. Historical jobs and TrackerLinks "
                    "are preserved."
                )
                if not confirm_repo_removal:
                    raise StateConflictError(f"Repository removal requires confirmation. {consequence}")
                if impact.active_job_count:
                    raise StateConflictError(f"Repository removal is blocked while active jobs exist. {consequence}")
                if impact.task_link_count:
                    raise StateConflictError(f"Repository removal is blocked while TaskLinks exist. {consequence}")
        updated = await self._project_repo.update(project_id, name=name, repo_paths=resolved_repo_paths)
        if updated is None:
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
        return updated

    async def summary_all(self) -> builtins.list[ProjectSummary]:
        """Batch Overview summary for every Project (Story 2.2 / CAP-2).

        Computes active/awaitingInput/failed job counts and last-activity per
        Project from a single cross-Project job query — never N sequential
        per-Project fetches. Projects with no jobs at all still appear with
        all-zero counts (never omitted).
        """
        projects = await self._project_repo.list()

        counts_by_project = await self._project_repo.job_counts_by_project([project.id for project in projects])

        summaries: list[ProjectSummary] = []
        for project in projects:
            summary = ProjectSummary(id=project.id, name=project.name, repo_paths=project.repo_paths)
            bucket = counts_by_project.get(project.id)
            if bucket is not None:
                summary.active_job_count = bucket.active
                summary.awaiting_input_count = bucket.awaiting
                summary.failed_count = bucket.failed
                summary.last_activity_at = bucket.last_activity
            summaries.append(summary)
        return summaries
