"""Project persistence — the sole store for repo-path membership (AD-5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, select

from backend.models.db import ApprovalRow, JobRow, ProjectRow, TaskLinkRow, TrackerLinkRow
from backend.models.domain import Project, ProjectMembershipImpact
from backend.persistence.repository import BaseRepository
from backend.services.project.repo_membership import (
    normalize_repo_path,
    parse_project_repo_paths,
    resolve_matching_project_id,
)

if TYPE_CHECKING:
    import builtins


class ProjectJobCounts:
    """Job status counts and last activity for one persisted Project owner.

    Bucket boundaries mirror the frontend classifier
    (``frontend/src/store/selectors.ts``): active = preparing/queued/running;
    awaiting = waiting_for_approval/review/unresolved completed; failed = failed.
    """

    __slots__ = ("active", "awaiting", "failed", "last_activity")

    def __init__(self) -> None:
        self.active = 0
        self.awaiting = 0
        self.failed = 0
        self.last_activity: datetime | None = None


class ProjectRepository(BaseRepository):
    """Database access for Project records."""

    @staticmethod
    def _to_domain(row: ProjectRow) -> Project:
        return Project(
            id=row.id,
            name=row.name,
            repo_paths=parse_project_repo_paths(row.repo_paths),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, project_id: str, name: str, repo_paths: builtins.list[str]) -> Project:
        """Insert a new Project row."""
        now = datetime.now(UTC)
        row = ProjectRow(
            id=project_id,
            name=name,
            repo_paths=json.dumps(repo_paths),
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_domain(row)

    async def get(self, project_id: str) -> Project | None:
        """Get a single Project by ID."""
        stmt = select(ProjectRow).where(ProjectRow.id == project_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self) -> builtins.list[Project]:
        """List all Projects, ordered by creation time."""
        stmt = select(ProjectRow).order_by(ProjectRow.created_at)
        result = await self._session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def find_by_repo_path(self, repo_path: str) -> Project | None:
        """Return the sole owning Project for ``repo_path``, if unambiguous.

        Mirrors the "refuse to guess" contract of ``resolve_matching_project_id``:
        if more than one Project claims membership of the same repo path, this
        returns ``None`` rather than picking one arbitrarily.
        """
        normalized_repo = normalize_repo_path(repo_path)
        matches = [
            project
            for project in await self.list()
            if any(normalize_repo_path(path) == normalized_repo for path in project.repo_paths)
        ]
        return matches[0] if len(matches) == 1 else None

    async def resolve_project_id_for_repo_path(self, repo_path: str) -> str | None:
        """Return the sole owning Project ID for ``repo_path``, if unambiguous."""
        result = await self._session.execute(select(ProjectRow.id, ProjectRow.repo_paths))
        return resolve_matching_project_id(repo_path, result.all())

    async def list_all_repo_paths(self, exclude_project_id: str | None = None) -> dict[str, str]:
        """Return a mapping of ``repo_path -> project_id`` across all Projects.

        Used by the service layer to enforce NFR5 (a repo belongs to at most
        one explicit Project). ``exclude_project_id`` omits a given Project's
        own rows, so updating a Project's own repo membership doesn't
        conflict with itself.
        """
        stmt = select(ProjectRow)
        if exclude_project_id is not None:
            stmt = stmt.where(ProjectRow.id != exclude_project_id)
        result = await self._session.execute(stmt)
        mapping: dict[str, str] = {}
        for row in result.scalars().all():
            for path in parse_project_repo_paths(row.repo_paths):
                mapping[path] = row.id
        return mapping

    async def update(
        self,
        project_id: str,
        name: str | None = None,
        repo_paths: builtins.list[str] | None = None,
    ) -> Project | None:
        """Update a Project's name and/or repo membership. Returns None if not found."""
        stmt = select(ProjectRow).where(ProjectRow.id == project_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if name is not None:
            row.name = name
        if repo_paths is not None:
            row.repo_paths = json.dumps(repo_paths)
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return self._to_domain(row)

    async def membership_impact(
        self,
        project_id: str,
        removed_repo_paths: builtins.list[str],
    ) -> ProjectMembershipImpact:
        """Count work that would be affected by removing member repositories."""
        if not removed_repo_paths:
            return ProjectMembershipImpact()

        active_states = ("preparing", "queued", "running", "waiting_for_approval", "review")
        active_jobs = await self._session.scalar(
            select(func.count())
            .select_from(JobRow)
            .where(JobRow.repo.in_(removed_repo_paths), JobRow.state.in_(active_states))
        )
        all_jobs = await self._session.scalar(
            select(func.count()).select_from(JobRow).where(JobRow.repo.in_(removed_repo_paths))
        )
        task_links = await self._session.scalar(
            select(func.count())
            .select_from(TaskLinkRow)
            .where(TaskLinkRow.project_id == project_id, TaskLinkRow.repo_path.in_(removed_repo_paths))
        )
        tracker_links = await self._session.scalar(
            select(func.count()).select_from(TrackerLinkRow).where(TrackerLinkRow.project_id == project_id)
        )
        active_count = int(active_jobs or 0)
        return ProjectMembershipImpact(
            active_job_count=active_count,
            historical_job_count=max(0, int(all_jobs or 0) - active_count),
            task_link_count=int(task_links or 0),
            tracker_link_count=int(tracker_links or 0),
        )

    async def job_counts_by_project(self, project_ids: builtins.list[str]) -> dict[str, ProjectJobCounts]:
        """Count persisted owners, including pending decisions on archived Jobs."""
        if not project_ids:
            return {}
        pending = exists(
            select(ApprovalRow.id).where(
                ApprovalRow.job_id == JobRow.id,
                ApprovalRow.resolution.is_(None),
            )
        )
        stmt = select(
            JobRow.project_id,
            JobRow.state,
            JobRow.resolution,
            JobRow.updated_at,
            JobRow.archived_at,
            pending,
        ).where(JobRow.project_id.in_(project_ids))
        result = await self._session.execute(stmt)
        counts: dict[str, ProjectJobCounts] = {}
        for project_id, state, resolution, updated_at, archived_at, has_pending in result.all():
            bucket = counts.setdefault(project_id, ProjectJobCounts())
            if has_pending:
                bucket.awaiting += 1
            elif archived_at is not None:
                pass
            elif state in ("preparing", "queued", "running"):
                bucket.active += 1
            elif state in ("waiting_for_approval", "review") or (
                state == "completed" and (not resolution or resolution == "unresolved")
            ):
                bucket.awaiting += 1
            elif state == "failed":
                bucket.failed += 1
            if bucket.last_activity is None or updated_at > bucket.last_activity:
                bucket.last_activity = updated_at
        return counts
