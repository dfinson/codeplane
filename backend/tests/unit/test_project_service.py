"""Tests for ProjectService — NFR5 enforcement and project membership."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.config import CPLConfig
from backend.models.domain import (
    Project,
    ProjectMembershipImpact,
    ProjectNotFoundError,
    RepoAlreadyAssignedError,
    StateConflictError,
)
from backend.services.project.project_service import ProjectService


def _make_project(project_id: str, name: str, repo_paths: list[str]) -> Project:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return Project(id=project_id, name=name, repo_paths=repo_paths, created_at=now, updated_at=now)


@pytest.fixture
def config() -> CPLConfig:
    return CPLConfig(repos=[])


@pytest.fixture
def mock_repo() -> AsyncMock:
    return AsyncMock()


class TestProjectServiceCreate:
    @pytest.mark.asyncio
    async def test_create_rejects_zero_repositories(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        with pytest.raises(StateConflictError):
            await ProjectService(mock_repo, config).create("Empty", [])

        mock_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_does_not_populate_legacy_repo_allowlist(
        self, mock_repo: AsyncMock, config: CPLConfig
    ) -> None:
        mock_repo.list_all_repo_paths.return_value = {}
        mock_repo.create.return_value = _make_project("proj-1", "Test", ["/repo/a", "/repo/b"])
        service = ProjectService(mock_repo, config)

        project = await service.create("Test", ["/repo/a", "/repo/b"])

        assert project.id == "proj-1"
        assert config.repos == []
        mock_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_rejects_repo_already_assigned(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        import backend.services.project.project_service as mod

        resolved = mod.ProjectService._resolve("/repo/a")
        mock_repo.list_all_repo_paths.return_value = {resolved: "other-project"}
        service = ProjectService(mock_repo, config)

        with pytest.raises(RepoAlreadyAssignedError):
            await service.create("Test", ["/repo/a"])

        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_validates_every_repository_before_persistence(
        self,
        mock_repo: AsyncMock,
        config: CPLConfig,
    ) -> None:
        git_service = AsyncMock()
        git_service.validate_repo.side_effect = [True, False]
        service = ProjectService(mock_repo, config, git_service)

        with pytest.raises(StateConflictError, match="not a valid Git repository"):
            await service.create("Test", ["/repo/valid", "/repo/not-git"])

        assert git_service.validate_repo.await_count == 2
        mock_repo.list_all_repo_paths.assert_not_awaited()
        mock_repo.create.assert_not_awaited()


class TestProjectServiceUpdate:
    @pytest.mark.asyncio
    async def test_removal_requires_confirmation_and_reports_consequences(
        self, mock_repo: AsyncMock, config: CPLConfig
    ) -> None:
        existing = _make_project("proj-1", "Test", ["/repo/a", "/repo/b"])
        mock_repo.get.return_value = existing
        mock_repo.list_all_repo_paths.return_value = {}
        mock_repo.membership_impact.return_value = ProjectMembershipImpact(historical_job_count=3)

        with pytest.raises(StateConflictError, match="3 historical jobs"):
            await ProjectService(mock_repo, config).update("proj-1", repo_paths=["/repo/a"])

        mock_repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirmed_removal_is_blocked_by_active_work(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        mock_repo.get.return_value = _make_project("proj-1", "Test", ["/repo/a", "/repo/b"])
        mock_repo.list_all_repo_paths.return_value = {}
        mock_repo.membership_impact.return_value = ProjectMembershipImpact(active_job_count=1)

        with pytest.raises(StateConflictError, match="blocked while active jobs"):
            await ProjectService(mock_repo, config).update("proj-1", repo_paths=["/repo/a"], confirm_repo_removal=True)

    @pytest.mark.asyncio
    async def test_confirmed_safe_removal_updates_membership(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        existing = _make_project("proj-1", "Test", ["/repo/a", "/repo/b"])
        mock_repo.get.return_value = existing
        mock_repo.list_all_repo_paths.return_value = {}
        mock_repo.membership_impact.return_value = ProjectMembershipImpact(historical_job_count=2)
        mock_repo.update.return_value = _make_project("proj-1", "Test", ["/repo/a"])

        result = await ProjectService(mock_repo, config).update(
            "proj-1", repo_paths=["/repo/a"], confirm_repo_removal=True
        )

        assert result.repo_paths == ["/repo/a"]
        mock_repo.update.assert_awaited_once_with(
            "proj-1",
            name=None,
            repo_paths=[ProjectService._resolve("/repo/a")],
        )

    @pytest.mark.asyncio
    async def test_update_name_does_not_touch_repo_paths(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        existing = _make_project("proj-1", "Old", ["/repo/a"])
        mock_repo.get.return_value = existing
        mock_repo.update.return_value = _make_project("proj-1", "New", ["/repo/a"])
        service = ProjectService(mock_repo, config)

        updated = await service.update("proj-1", name="New")

        assert updated.name == "New"
        mock_repo.update.assert_awaited_once_with("proj-1", name="New", repo_paths=None)

    @pytest.mark.asyncio
    async def test_update_adds_new_repo_path_without_registering_it(
        self, mock_repo: AsyncMock, config: CPLConfig
    ) -> None:
        import backend.services.project.project_service as mod

        existing_path = mod.ProjectService._resolve("/repo/a")
        existing = _make_project("proj-1", "Test", [existing_path])
        mock_repo.get.return_value = existing
        mock_repo.list_all_repo_paths.return_value = {}
        mock_repo.update.return_value = _make_project(
            "proj-1", "Test", [existing_path, mod.ProjectService._resolve("/repo/b")]
        )
        service = ProjectService(mock_repo, config)

        updated = await service.update("proj-1", repo_paths=["/repo/a", "/repo/b"])

        assert len(updated.repo_paths) == 2
        assert config.repos == []

    @pytest.mark.asyncio
    async def test_update_rejects_repo_assigned_to_another_project(
        self, mock_repo: AsyncMock, config: CPLConfig
    ) -> None:
        import backend.services.project.project_service as mod

        existing = _make_project("proj-1", "Test", ["/repo/a"])
        mock_repo.get.return_value = existing
        resolved = mod.ProjectService._resolve("/repo/b")
        mock_repo.list_all_repo_paths.return_value = {resolved: "other-project"}
        service = ProjectService(mock_repo, config)

        with pytest.raises(RepoAlreadyAssignedError):
            await service.update("proj-1", repo_paths=["/repo/a", "/repo/b"])

        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_rejects_invalid_added_repository_before_persistence(
        self,
        mock_repo: AsyncMock,
        config: CPLConfig,
    ) -> None:
        existing_path = ProjectService._resolve("/repo/a")
        mock_repo.get.return_value = _make_project(
            "proj-1",
            "Test",
            [existing_path],
        )
        git_service = AsyncMock()
        git_service.validate_repo.side_effect = [True, False]

        with pytest.raises(StateConflictError, match="not a valid Git repository"):
            await ProjectService(mock_repo, config, git_service).update(
                "proj-1",
                repo_paths=["/repo/a", "/repo/not-git"],
            )

        mock_repo.list_all_repo_paths.assert_not_awaited()
        mock_repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_missing_project_raises_not_found(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        mock_repo.get.return_value = None
        service = ProjectService(mock_repo, config)

        with pytest.raises(ProjectNotFoundError):
            await service.update("does-not-exist", name="X")


class TestProjectServiceGetList:
    @pytest.mark.asyncio
    async def test_get_missing_raises_not_found(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        mock_repo.get.return_value = None
        service = ProjectService(mock_repo, config)

        with pytest.raises(ProjectNotFoundError):
            await service.get("does-not-exist")

    @pytest.mark.asyncio
    async def test_list_delegates_to_repo(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        projects = [_make_project("proj-1", "A", ["/repo/a"])]
        mock_repo.list.return_value = projects
        service = ProjectService(mock_repo, config)

        result = await service.list()
        assert result == projects


class TestProjectServiceSummaryAll:
    @pytest.mark.asyncio
    async def test_zero_job_project_still_returns_all_zero_summary(
        self, mock_repo: AsyncMock, config: CPLConfig
    ) -> None:
        mock_repo.list.return_value = [_make_project("proj-1", "Idle", ["/repo/a"])]
        mock_repo.job_counts_by_project.return_value = {}
        service = ProjectService(mock_repo, config)

        summaries = await service.summary_all()

        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.id == "proj-1"
        assert summary.active_job_count == 0
        assert summary.awaiting_input_count == 0
        assert summary.failed_count == 0
        assert summary.last_activity_at is None

    @pytest.mark.asyncio
    async def test_counts_are_returned_for_the_persisted_project(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        from datetime import UTC, datetime

        from backend.persistence.project_repo import ProjectJobCounts

        newer = datetime(2026, 2, 1, tzinfo=UTC)

        counts_a = ProjectJobCounts()
        counts_a.active = 2
        counts_a.awaiting = 1
        counts_a.failed = 3
        counts_a.last_activity = newer

        mock_repo.list.return_value = [_make_project("proj-1", "Multi", ["/repo/a", "/repo/b"])]
        mock_repo.job_counts_by_project.return_value = {"proj-1": counts_a}
        service = ProjectService(mock_repo, config)

        summaries = await service.summary_all()

        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.active_job_count == 2
        assert summary.awaiting_input_count == 1
        assert summary.failed_count == 3
        assert summary.last_activity_at == newer

    @pytest.mark.asyncio
    async def test_single_batch_query_not_n_sequential_calls(self, mock_repo: AsyncMock, config: CPLConfig) -> None:
        mock_repo.list.return_value = [
            _make_project("proj-1", "A", ["/repo/a"]),
            _make_project("proj-2", "B", ["/repo/b"]),
            _make_project("proj-3", "C", ["/repo/c"]),
        ]
        mock_repo.job_counts_by_project.return_value = {}
        service = ProjectService(mock_repo, config)

        await service.summary_all()

        mock_repo.job_counts_by_project.assert_awaited_once_with(["proj-1", "proj-2", "proj-3"])
