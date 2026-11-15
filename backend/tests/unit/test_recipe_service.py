"""Project-scoped task assignment, execution, dependencies and tracker routing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import (
    Project,
    RepoNotAllowedError,
    StateConflictError,
    TaskLink,
    TaskLinkState,
)
from backend.services.recipe.recipe_service import RecipeService

if TYPE_CHECKING:
    from pathlib import Path


def _make_project(project_id: str, repo_paths: list[str]) -> Project:
    now = datetime.now(UTC)
    return Project(
        id=project_id,
        name="Test Project",
        repo_paths=repo_paths,
        created_at=now,
        updated_at=now,
    )




@pytest.fixture
def mock_task_link_repo() -> AsyncMock:
    mock = AsyncMock()
    return mock


@pytest.fixture
def mock_project_service() -> AsyncMock:
    return AsyncMock()






class TestListTaskLinks:
    @pytest.mark.asyncio
    async def test_list_delegates_to_repo_after_project_check(
        self, mock_task_link_repo: AsyncMock, mock_project_service: AsyncMock
    ) -> None:
        mock_project_service.get.return_value = _make_project("proj-1", [])
        mock_task_link_repo.list_by_project.return_value = []

        service = RecipeService(mock_task_link_repo, mock_project_service)
        result = await service.list_task_links("proj-1")

        mock_project_service.get.assert_awaited_once_with("proj-1")
        mock_task_link_repo.list_by_project.assert_awaited_once_with("proj-1")
        assert result == []


class TestCreateManualTaskLink:
    @pytest.mark.asyncio
    async def test_preserves_explicit_tracker_link_for_manual_assignment(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_tracker_link_repo: AsyncMock,
    ) -> None:
        repo_path = tmp_path / "member-repo"
        repo_path.mkdir()
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_path)])
        mock_tracker_link_repo.get.return_value = {
            "id": "tracker-1",
            "project_id": "proj-1",
        }
        mock_task_link_repo.create_manual.return_value = _make_task_link(
            id="task-1",
            repo_path=str(repo_path),
            tracker_link_id="tracker-1",
            tracker_ticket_ref="PAY-42",
            prompt_override="Implement PAY-42",
            output_routes=None,
        )
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            tracker_link_repo=mock_tracker_link_repo,
        )

        await service.create_manual_task_link(
            project_id="proj-1",
            repo_path=str(repo_path),
            tracker_link_id="tracker-1",
            tracker_ticket_ref="PAY-42",
            prompt_override="Implement PAY-42",
        )

        mock_tracker_link_repo.get.assert_awaited_once_with("tracker-1")
        mock_task_link_repo.create_manual.assert_awaited_once_with(
            project_id="proj-1",
            repo_path=str(repo_path),
            tracker_link_id="tracker-1",
            tracker_ticket_ref="PAY-42",
            prompt_override="Implement PAY-42",
            output_routes=None,
        )

    @pytest.mark.asyncio
    async def test_creates_manual_link_for_project_member_repo(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        repo_path = tmp_path / "member-repo"
        repo_path.mkdir()
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_path)])
        expected = TaskLink(
            id="task-1",
            project_id="proj-1",
            repo_path=str(repo_path),
            story_node_id=None,
            depends_on=[],
            job_id=None,
            tracker_ticket_ref="JIRA-123",
            prompt_override="Implement this ticket",
            output_routes=None,
            epic_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_task_link_repo.create_manual.return_value = expected

        service = RecipeService(mock_task_link_repo, mock_project_service)
        result = await service.create_manual_task_link(
            project_id="proj-1",
            repo_path=str(repo_path),
            tracker_ticket_ref="JIRA-123",
            prompt_override="Implement this ticket",
        )

        assert result is expected
        mock_task_link_repo.create_manual.assert_awaited_once_with(
            project_id="proj-1",
            repo_path=str(repo_path),
            tracker_ticket_ref="JIRA-123",
            prompt_override="Implement this ticket",
            output_routes=None,
        )

    @pytest.mark.asyncio
    async def test_rejects_repo_outside_project(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        member_repo = tmp_path / "member-repo"
        other_repo = tmp_path / "other-repo"
        member_repo.mkdir()
        other_repo.mkdir()
        mock_project_service.get.return_value = _make_project("proj-1", [str(member_repo)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        with pytest.raises(RepoNotAllowedError):
            await service.create_manual_task_link(
                project_id="proj-1",
                repo_path=str(other_repo),
                tracker_ticket_ref="JIRA-123",
                prompt_override="Implement this ticket",
            )

        mock_task_link_repo.create_manual.assert_not_awaited()


def _make_task_link(
    *,
    id: str,  # noqa: A002
    project_id: str = "proj-1",
    repo_path: str = "/repo/a",
    story_node_id: str | None = None,
    depends_on: list[str] | None = None,
    state: TaskLinkState = TaskLinkState.ready,
    job_id: str | None = None,
    tracker_link_id: str | None = None,
    tracker_ticket_ref: str | None = None,
    prompt_override: str | None = None,
    chain_root_id: str | None = None,
    output_routes: list[str] | None = None,
) -> TaskLink:
    now = datetime.now(UTC)
    return TaskLink(
        id=id,
        project_id=project_id,
        repo_path=repo_path,
        story_node_id=story_node_id,
        depends_on=depends_on or [],
        state=state,
        job_id=job_id,
        tracker_link_id=tracker_link_id,
        tracker_ticket_ref=tracker_ticket_ref,
        prompt_override=prompt_override,
        epic_id=None,
        created_at=now,
        updated_at=now,
        chain_root_id=chain_root_id or id,
        output_routes=output_routes or [],
    )


@pytest.fixture
def mock_tracker_link_repo() -> AsyncMock:
    return AsyncMock()


class TestExecutionDelegation:
    """Wrapper wiring only; execution behavior is covered against file SQLite."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "args", "kwargs"),
        [
            ("start_task_link", ("proj-1", "task-1"), {}),
            ("handle_job_completed", ("job-1",), {"resolution": "merged"}),
            ("handle_job_failed", ("job-1",), {}),
            ("spawn_approved_task_link", ("task-1",), {"parent_job_id": "job-1"}),
            ("list_task_links", ("proj-1",), {}),
        ],
    )
    async def test_forwards_to_application_execution_service(
        self,
        mock_task_link_repo,
        mock_project_service,
        method,
        args,
        kwargs,
    ):
        execution = AsyncMock()
        expected = object()
        getattr(execution, method).return_value = expected
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            execution_service=execution,
        )

        result = await getattr(service, method)(*args, **kwargs)

        getattr(execution, method).assert_awaited_once_with(*args, **kwargs)
        if method != "handle_job_failed":
            assert result is expected
        else:
            assert result is None
        mock_task_link_repo.claim_ready_and_commit.assert_not_awaited()
        assert service.pending_tracker_writes == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "args", "kwargs"),
        [
            ("start_task_link", ("proj-1", "task-1"), {}),
            ("handle_job_completed", ("job-1",), {"resolution": "merged"}),
            ("handle_job_failed", ("job-1",), {}),
            ("spawn_approved_task_link", ("task-1",), {"parent_job_id": "job-1"}),
        ],
    )
    async def test_configured_execution_refuses_session_owned_fallback(
        self,
        mock_task_link_repo,
        mock_project_service,
        method,
        args,
        kwargs,
    ):
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=AsyncMock(),
            job_repo=AsyncMock(),
        )

        with pytest.raises(StateConflictError, match="shared TaskExecutionService"):
            await getattr(service, method)(*args, **kwargs)

        mock_task_link_repo.claim_ready_and_commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ingestion_only_service_retains_unconfigured_completion_noops(
        self,
        mock_task_link_repo,
        mock_project_service,
    ):
        service = RecipeService(mock_task_link_repo, mock_project_service)

        assert await service.handle_job_completed("job-1", resolution="merged") == []
        assert await service.spawn_approved_task_link("task-1", parent_job_id="job-1") is None
        mock_task_link_repo.get_by_job_id.assert_not_awaited()
        mock_task_link_repo.get.assert_not_awaited()
