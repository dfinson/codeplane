"""Tests for RecipeService.ingest_project — cross-repo depends_on resolution (Story 4.2, AD-9)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import (
    Approval,
    Job,
    JobState,
    Project,
    RepoNotAllowedError,
    StateConflictError,
    TaskLink,
    TaskLinkState,
)
from backend.services.recipe.recipe_service import RecipeService
from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest

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
    mock.upsert_many.side_effect = lambda project_id, entries: entries  # echo back for assertions
    return mock


@pytest.fixture
def mock_project_service() -> AsyncMock:
    return AsyncMock()



def _write_spec_task(repo_root: Path, filename: str, dependency: str = "") -> None:
    tasks_file = repo_root / "specs" / "fixture" / "tasks.md"
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    task_id = filename.removesuffix(".md")
    suffix = f" (depends on: {dependency})" if dependency else ""
    with tasks_file.open("a", encoding="utf-8") as handle:
        handle.write(f"- [ ] {task_id} Fixture task{suffix}\n")

class TestIngestProject:
    @pytest.mark.asyncio
    async def test_single_repo_same_repo_dependency(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        repo_a = tmp_path / "backend-repo"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T001.md")
        _write_spec_task(
            repo_a,
            "T002.md",
            "T001",
        )
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_a)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")

        entries = mock_task_link_repo.upsert_many.call_args.args[1]
        by_node = {e["story_node_id"]: e for e in entries}
        assert by_node["T002"]["depends_on"] == [f"{repo_a}::T001"]
        assert by_node["T001"]["depends_on"] == []
        assert by_node["T001"]["epic_id"] is None

    @pytest.mark.asyncio
    async def test_cross_repo_dependency_resolves_to_sibling_repo(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        backend_repo = tmp_path / "codeplane-backend"
        frontend_repo = tmp_path / "codeplane-frontend"
        backend_repo.mkdir()
        frontend_repo.mkdir()
        _write_spec_task(backend_repo, "T010.md")
        _write_spec_task(
            frontend_repo,
            "T020.md",
            "codeplane-backend/T010",
        )
        mock_project_service.get.return_value = _make_project("proj-1", [str(backend_repo), str(frontend_repo)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")

        entries = mock_task_link_repo.upsert_many.call_args.args[1]
        frontend_entry = next(e for e in entries if e["story_node_id"] == "T020")
        assert frontend_entry["depends_on"] == [f"{backend_repo}::T010"]

    @pytest.mark.asyncio
    async def test_unresolvable_cross_repo_dependency_preserved_raw(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        repo_a = tmp_path / "solo-repo"
        repo_a.mkdir()
        _write_spec_task(
            repo_a,
            "T001.md",
            "unknown-repo/T999",
        )
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_a)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")

        entries = mock_task_link_repo.upsert_many.call_args.args[1]
        assert entries[0]["depends_on"] == ["unknown-repo/T999"]

    @pytest.mark.asyncio
    async def test_root_and_nested_spec_tasks_both_ingested(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        repo_a = tmp_path / "mixed-repo"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T002.md")
        (repo_a / "tasks.md").write_text("- [ ] T001 Spec-kit task\n", encoding="utf-8")
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_a)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")

        entries = mock_task_link_repo.upsert_many.call_args.args[1]
        node_ids = {e["story_node_id"] for e in entries}
        assert node_ids == {"T002", "T001"}

    @pytest.mark.asyncio
    async def test_repo_with_no_source_files_yields_no_entries_but_does_not_fail(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        empty_repo = tmp_path / "empty-repo"
        empty_repo.mkdir()
        mock_project_service.get.return_value = _make_project("proj-1", [str(empty_repo)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")

        entries = mock_task_link_repo.upsert_many.call_args.args[1]
        assert entries == []

    @pytest.mark.asyncio
    async def test_never_writes_to_source_repo(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T002.md")
        story_file = repo_a / "specs" / "fixture" / "tasks.md"
        before_mtime = story_file.stat().st_mtime
        before_content = story_file.read_text(encoding="utf-8")
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_a)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")

        assert story_file.stat().st_mtime == before_mtime
        assert story_file.read_text(encoding="utf-8") == before_content

    @pytest.mark.asyncio
    async def test_idempotent_rerun_calls_upsert_not_insert_only(
        self,
        tmp_path: Path,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()
        _write_spec_task(repo_a, "T002.md")
        mock_project_service.get.return_value = _make_project("proj-1", [str(repo_a)])

        service = RecipeService(mock_task_link_repo, mock_project_service)
        await service.ingest_project("proj-1")
        await service.ingest_project("proj-1")

        assert mock_task_link_repo.upsert_many.call_count == 2
        first_entries = mock_task_link_repo.upsert_many.call_args_list[0].args[1]
        second_entries = mock_task_link_repo.upsert_many.call_args_list[1].args[1]
        assert first_entries == second_entries


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


def _make_job(*, id: str, state: JobState, resolution: str | None = None) -> Job:  # noqa: A002
    now = datetime.now(UTC)
    return Job(
        id=id,
        project_id="proj-1",
        repo="/repo/a",
        prompt="do the thing",
        state=state,
        base_ref="main",
        branch="feature",
        worktree_path="/tmp/wt",
        session_id=None,
        created_at=now,
        updated_at=now,
        resolution=resolution,
    )


@pytest.fixture
def mock_job_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_job_service() -> AsyncMock:
    return AsyncMock()


class TestHandleJobCompleted:
    """Story 4.5: auto-spawn a dependent TaskLink's job on completion."""

    @pytest.mark.asyncio
    async def test_no_op_when_job_service_not_configured(
        self, mock_task_link_repo: AsyncMock, mock_project_service: AsyncMock
    ) -> None:
        service = RecipeService(mock_task_link_repo, mock_project_service)
        result = await service.handle_job_completed("job-1", resolution="merged")
        assert result == []
        mock_task_link_repo.get_by_job_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_when_resolution_is_discarded(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        discarded_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        mock_task_link_repo.get_by_job_id.return_value = discarded_link
        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="discarded")
        assert result == []
        mock_task_link_repo.set_state.assert_awaited_once_with("link-a", TaskLinkState.failed)

    @pytest.mark.asyncio
    async def test_no_op_when_completed_job_has_no_linked_task_link(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        mock_task_link_repo.get_by_job_id.return_value = None
        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")
        assert result == []
        mock_job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_job_projects_failed_task_link_state(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
    ) -> None:
        mock_task_link_repo.get_by_job_id.return_value = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            state=TaskLinkState.running,
            job_id="job-1",
        )
        service = RecipeService(mock_task_link_repo, mock_project_service)

        await service.handle_job_failed("job-1")

        mock_task_link_repo.set_state.assert_awaited_once_with("link-a", TaskLinkState.failed)

    @pytest.mark.asyncio
    async def test_spawns_dependent_whose_single_dependency_is_now_satisfied(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        new_job = _make_job(id="job-2", state=JobState.preparing)
        mock_job_service.create_job.return_value = new_job
        mock_task_link_repo.claim_ready_and_commit.return_value = _make_task_link(
            id="link-b",
            story_node_id="1-2-b",
            depends_on=["/repo/a::1-1-a"],
            state=TaskLinkState.starting,
        )
        mock_task_link_repo.attach_claimed_job.return_value = _make_task_link(
            id="link-b",
            story_node_id="1-2-b",
            depends_on=["/repo/a::1-1-a"],
            state=TaskLinkState.running,
            job_id="job-2",
        )

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == [new_job]
        mock_job_service.create_job.assert_awaited_once()
        spec = mock_job_service.create_job.call_args.args[0]
        assert spec.repo == "/repo/a"
        assert spec.parent_job_id == "job-1"
        mock_task_link_repo.claim_ready_and_commit.assert_awaited_once_with("link-b")
        mock_task_link_repo.attach_claimed_job.assert_awaited_once_with("link-b", "job-2", state=TaskLinkState.running)

    @pytest.mark.asyncio
    async def test_does_not_spawn_until_all_dependencies_satisfied(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        other_dep = _make_task_link(id="link-c", story_node_id="1-1-c", job_id=None)
        dependent = _make_task_link(
            id="link-b",
            story_node_id="1-2-b",
            depends_on=["/repo/a::1-1-a", "/repo/a::1-1-c"],
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, other_dep, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == []
        mock_job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_never_spawns_a_dependent_twice(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(
            id="link-b",
            story_node_id="1-2-b",
            depends_on=["/repo/a::1-1-a"],
            job_id="job-already-spawned",
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == []
        mock_job_service.create_job.assert_not_awaited()
        mock_task_link_repo.set_job_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dependency_target_job_in_review_state_is_not_satisfied(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        # The dependency target's own job is only in `review`, not `completed`.
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.review)

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == []
        mock_job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manually_assigned_dependent_uses_prompt_override(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(
            id="link-b",
            story_node_id=None,
            depends_on=["/repo/a::1-1-a"],
            tracker_ticket_ref="JIRA-9",
            prompt_override="Do exactly this",
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        new_job = _make_job(id="job-2", state=JobState.preparing)
        mock_job_service.create_job.return_value = new_job
        mock_task_link_repo.claim_ready_and_commit.return_value = _make_task_link(
            id="link-b",
            story_node_id=None,
            depends_on=["/repo/a::1-1-a"],
            state=TaskLinkState.starting,
            tracker_ticket_ref="JIRA-9",
            prompt_override="Do exactly this",
        )
        mock_task_link_repo.attach_claimed_job.return_value = dependent

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        await service.handle_job_completed("job-1", resolution="merged")

        spec = mock_job_service.create_job.call_args.args[0]
        assert spec.prompt == "Do exactly this"

    @pytest.mark.asyncio
    async def test_spawn_failure_for_one_dependent_does_not_raise_or_block(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        mock_job_service.create_job.side_effect = RepoNotAllowedError("nope")

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == []
        mock_task_link_repo.set_job_id.assert_not_awaited()


@pytest.fixture
def mock_tracker_link_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_tracker_write_service() -> AsyncMock:
    return AsyncMock()


class TestHandleJobCompletedTrackerWrite:
    """Story 4.6: route a completed TaskLink's tracker write to its paired ticket."""

    @pytest.mark.asyncio
    async def test_routes_tracker_write_to_the_taskllinks_own_ticket(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_tracker_link_repo: AsyncMock,
        mock_tracker_write_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            job_id="job-1",
            tracker_link_id="trk-2",
            tracker_ticket_ref="JIRA-42",
            output_routes=["tracker_write"],
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link]
        mock_tracker_link_repo.get.return_value = {
            "id": "trk-2",
            "project_id": "proj-1",
            "credential_id": "cred-2",
            "external_ref": "PROJ",
        }

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            tracker_link_repo=mock_tracker_link_repo,
            tracker_write_service=mock_tracker_write_service,
        )
        await service.handle_job_completed("job-1", resolution="merged")
        await asyncio.gather(*service.pending_tracker_writes)

        mock_tracker_link_repo.get.assert_awaited_once_with("trk-2")
        mock_tracker_write_service.execute.assert_awaited_once()
        call_args = mock_tracker_write_service.execute.call_args
        assert call_args.args[0] == "job-1"
        request: TrackerWriteRequest = call_args.args[1]
        assert request.tracker_link_id == "trk-2"
        assert request.ticket_ref == "JIRA-42"
        assert request.action == TrackerWriteAction.comment

    @pytest.mark.asyncio
    async def test_manually_assigned_task_link_still_routes_tracker_write(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_tracker_link_repo: AsyncMock,
        mock_tracker_write_service: AsyncMock,
    ) -> None:
        # No story_node_id (manually-assigned, Story 4.3) — must not be skipped
        # by the dependency-graph early return, since a manually-assigned
        # TaskLink is exactly the kind most likely paired with a ticket.
        completed_link = _make_task_link(
            id="link-a",
            story_node_id=None,
            job_id="job-1",
            tracker_link_id="trk-1",
            tracker_ticket_ref="JIRA-7",
            output_routes=["tracker_write"],
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_tracker_link_repo.get.return_value = {
            "id": "trk-1",
            "project_id": "proj-1",
            "credential_id": "cred-1",
            "external_ref": "PROJ",
        }

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            tracker_link_repo=mock_tracker_link_repo,
            tracker_write_service=mock_tracker_write_service,
        )
        result = await service.handle_job_completed("job-1", resolution="merged")
        await asyncio.gather(*service.pending_tracker_writes)

        assert result == []  # no story_node_id: never a dependency-graph participant
        mock_tracker_write_service.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_write_when_task_link_has_no_ticket_ref(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_tracker_link_repo: AsyncMock,
        mock_tracker_write_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            job_id="job-1",
            tracker_ticket_ref=None,
            output_routes=["tracker_write"],
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link]

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            tracker_link_repo=mock_tracker_link_repo,
            tracker_write_service=mock_tracker_write_service,
        )
        await service.handle_job_completed("job-1", resolution="merged")

        mock_tracker_link_repo.get.assert_not_awaited()
        mock_tracker_write_service.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_write_when_tracker_output_route_is_not_configured(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_tracker_link_repo: AsyncMock,
        mock_tracker_write_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            job_id="job-1",
            tracker_link_id="trk-1",
            tracker_ticket_ref="JIRA-42",
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link]
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            tracker_link_repo=mock_tracker_link_repo,
            tracker_write_service=mock_tracker_write_service,
        )

        await service.handle_job_completed("job-1", resolution="merged")

        mock_tracker_link_repo.get.assert_not_awaited()
        mock_tracker_write_service.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_write_when_project_has_no_tracker_link(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_tracker_link_repo: AsyncMock,
        mock_tracker_write_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            job_id="job-1",
            tracker_link_id="missing",
            tracker_ticket_ref="JIRA-42",
            output_routes=["tracker_write"],
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link]
        mock_tracker_link_repo.get.return_value = None

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            tracker_link_repo=mock_tracker_link_repo,
            tracker_write_service=mock_tracker_write_service,
        )
        await service.handle_job_completed("job-1", resolution="merged")

        mock_tracker_write_service.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_write_when_tracker_write_service_not_configured(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            job_id="job-1",
            tracker_ticket_ref="JIRA-42",
            output_routes=["tracker_write"],
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link]

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        # Never raises even with tracker services entirely unconfigured.
        await service.handle_job_completed("job-1", resolution="merged")


@pytest.fixture
def mock_chat_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_approval_service() -> AsyncMock:
    return AsyncMock()


def _make_approval(*, proposed_action: str | None, job_id: str = "job-1") -> Approval:
    now = datetime.now(UTC)
    return Approval(
        id="appr-1",
        job_id=job_id,
        description="desc",
        proposed_action=proposed_action,
        requested_at=now,
    )


class TestStartTaskLinkGatingAndClaims:
    @staticmethod
    def _ready_chain() -> tuple[TaskLink, TaskLink]:
        root = _make_task_link(
            id="link-a",
            story_node_id="1-1-a",
            state=TaskLinkState.completed,
            job_id="job-1",
        )
        dependent = _make_task_link(
            id="link-b",
            story_node_id="1-2-b",
            depends_on=["/repo/a::1-1-a"],
            chain_root_id="link-a",
        )
        return root, dependent

    @pytest.mark.asyncio
    async def test_direct_dependent_start_rejects_attached_chat_gated_chain(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_chat_repo: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        root, dependent = self._ready_chain()
        mock_task_link_repo.get.return_value = dependent
        mock_task_link_repo.list_by_project.return_value = [root, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        mock_approval_service.list_pending.return_value = []
        mock_chat_repo.get_attached_open_chat_for_chain.return_value = object()
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            chat_repo=mock_chat_repo,
            approval_service=mock_approval_service,
        )

        with pytest.raises(StateConflictError, match="requires approval"):
            await service.start_task_link("proj-1", "link-b")

        mock_task_link_repo.claim_ready_and_commit.assert_not_awaited()
        mock_job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_direct_start_rejects_matching_pending_chain_approval(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        root = _make_task_link(id="link-a")
        mock_task_link_repo.get.return_value = root
        mock_task_link_repo.list_by_project.return_value = [root]
        mock_approval_service.list_pending.return_value = [_make_approval(proposed_action="spawn_task:link-a")]
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            approval_service=mock_approval_service,
        )

        with pytest.raises(StateConflictError, match="pending chain approval"):
            await service.start_task_link("proj-1", "link-a")

        mock_task_link_repo.claim_ready_and_commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dependency_free_root_start_remains_allowed_in_gated_chain(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_chat_repo: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        root = _make_task_link(id="link-a")
        claimed = _make_task_link(id="link-a", state=TaskLinkState.starting)
        attached = _make_task_link(id="link-a", state=TaskLinkState.running, job_id="job-1")
        job = _make_job(id="job-1", state=JobState.preparing)
        mock_task_link_repo.get.return_value = root
        mock_task_link_repo.list_by_project.return_value = [root]
        mock_task_link_repo.claim_ready_and_commit.return_value = claimed
        mock_task_link_repo.attach_claimed_job.return_value = attached
        mock_job_service.create_job.return_value = job
        mock_approval_service.list_pending.return_value = []
        mock_chat_repo.get_attached_open_chat_for_chain.return_value = object()
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            chat_repo=mock_chat_repo,
            approval_service=mock_approval_service,
        )

        started_link, started_job = await service.start_task_link("proj-1", "link-a")

        assert started_link == attached
        assert started_job == job
        mock_chat_repo.get_attached_open_chat_for_chain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_claim_is_committed_before_git_backed_job_creation(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        events: list[str] = []
        root = _make_task_link(id="link-a")
        claimed = _make_task_link(id="link-a", state=TaskLinkState.starting)
        job = _make_job(id="job-1", state=JobState.preparing)
        attached = _make_task_link(id="link-a", state=TaskLinkState.running, job_id="job-1")
        mock_task_link_repo.get.return_value = root
        mock_task_link_repo.list_by_project.return_value = [root]

        async def claim(_: str) -> TaskLink:
            events.append("claim-committed")
            return claimed

        async def create_job(_: object) -> Job:
            events.append("git-preparation")
            return job

        mock_task_link_repo.claim_ready_and_commit.side_effect = claim
        mock_job_service.create_job.side_effect = create_job
        mock_task_link_repo.attach_claimed_job.return_value = attached
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
        )

        await service.start_task_link("proj-1", "link-a")

        assert events == ["claim-committed", "git-preparation"]

    @pytest.mark.asyncio
    async def test_failed_job_creation_recovers_committed_claim(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        root = _make_task_link(id="link-a")
        mock_task_link_repo.get.return_value = root
        mock_task_link_repo.list_by_project.return_value = [root]
        mock_task_link_repo.claim_ready_and_commit.return_value = _make_task_link(
            id="link-a", state=TaskLinkState.starting
        )
        mock_job_service.create_job.side_effect = RuntimeError("git failed")
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
        )

        with pytest.raises(RuntimeError, match="git failed"):
            await service.start_task_link("proj-1", "link-a")

        mock_task_link_repo.recover_start_claim.assert_awaited_once_with("link-a")
        mock_task_link_repo.attach_claimed_job.assert_not_awaited()


class TestHandleJobCompletedGating:
    """Story 5.4: gate a chain's auto-spawn behind approval."""

    @pytest.mark.asyncio
    async def test_ungated_project_spawns_exactly_as_before(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_chat_repo: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        """No attached open Chat for the Project => ungated, unchanged Story 4.5 behavior."""
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        new_job = _make_job(id="job-2", state=JobState.preparing)
        mock_job_service.create_job.return_value = new_job
        mock_task_link_repo.set_job_id.return_value = _make_task_link(
            id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"], job_id="job-2"
        )
        mock_chat_repo.get_attached_open_chat_for_chain.return_value = None

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            chat_repo=mock_chat_repo,
            approval_service=mock_approval_service,
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == [new_job]
        mock_chat_repo.get_attached_open_chat_for_chain.assert_awaited_once_with("link-a")
        mock_job_service.create_job.assert_awaited_once()
        mock_approval_service.create_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gated_project_creates_approval_instead_of_spawning(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_chat_repo: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        mock_chat_repo.get_attached_open_chat_for_chain.return_value = object()
        mock_approval_service.list_pending.return_value = []

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            chat_repo=mock_chat_repo,
            approval_service=mock_approval_service,
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == []
        mock_job_service.create_job.assert_not_awaited()
        mock_task_link_repo.set_job_id.assert_not_awaited()
        mock_approval_service.create_request.assert_awaited_once()
        call = mock_approval_service.create_request.call_args
        assert call.kwargs["job_id"] == "job-1"
        assert call.kwargs["proposed_action"] == "spawn_task:link-b"
        mock_chat_repo.get_attached_open_chat_for_chain.assert_awaited_once_with("link-a")

    @pytest.mark.asyncio
    async def test_gated_project_never_double_requests_pending_approval(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_chat_repo: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        mock_chat_repo.get_attached_open_chat_for_chain.return_value = object()
        mock_approval_service.list_pending.return_value = [_make_approval(proposed_action="spawn_task:link-b")]

        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            chat_repo=mock_chat_repo,
            approval_service=mock_approval_service,
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == []
        mock_approval_service.create_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_successor_completion_uses_persisted_root_chain_identity(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
        mock_chat_repo: AsyncMock,
        mock_approval_service: AsyncMock,
    ) -> None:
        completed_successor = _make_task_link(
            id="link-b",
            story_node_id="1-2-b",
            depends_on=["/repo/a::1-1-a"],
            job_id="job-2",
            chain_root_id="link-a",
        )
        next_successor = _make_task_link(
            id="link-c",
            story_node_id="1-3-c",
            depends_on=["/repo/a::1-2-b"],
            chain_root_id="link-a",
        )
        mock_task_link_repo.get_by_job_id.return_value = completed_successor
        mock_task_link_repo.list_by_project.return_value = [completed_successor, next_successor]
        mock_job_repo.get.return_value = _make_job(
            id="job-2",
            state=JobState.completed,
            resolution="merged",
        )
        mock_chat_repo.get_attached_open_chat_for_chain.return_value = object()
        mock_approval_service.list_pending.return_value = []
        service = RecipeService(
            mock_task_link_repo,
            mock_project_service,
            job_service=mock_job_service,
            job_repo=mock_job_repo,
            chat_repo=mock_chat_repo,
            approval_service=mock_approval_service,
        )

        result = await service.handle_job_completed("job-2", resolution="merged")

        assert result == []
        mock_chat_repo.get_attached_open_chat_for_chain.assert_awaited_once_with("link-a")
        mock_approval_service.create_request.assert_awaited_once()
        assert mock_approval_service.create_request.call_args.kwargs["proposed_action"] == "spawn_task:link-c"

    @pytest.mark.asyncio
    async def test_missing_chat_repo_or_approval_service_falls_back_to_ungated(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        """No chat_repo/approval_service configured at all => never gated (Story 4.5 default)."""
        completed_link = _make_task_link(id="link-a", story_node_id="1-1-a", job_id="job-1")
        dependent = _make_task_link(id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"])
        mock_task_link_repo.get_by_job_id.return_value = completed_link
        mock_task_link_repo.list_by_project.return_value = [completed_link, dependent]
        mock_job_repo.get.return_value = _make_job(id="job-1", state=JobState.completed, resolution="merged")
        new_job = _make_job(id="job-2", state=JobState.preparing)
        mock_job_service.create_job.return_value = new_job
        mock_task_link_repo.set_job_id.return_value = _make_task_link(
            id="link-b", story_node_id="1-2-b", depends_on=["/repo/a::1-1-a"], job_id="job-2"
        )

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.handle_job_completed("job-1", resolution="merged")

        assert result == [new_job]


class TestSpawnApprovedTaskLink:
    """Story 5.4: deferred spawn once a gated approval is granted."""

    @pytest.mark.asyncio
    async def test_spawns_and_sets_job_id_when_task_link_has_none(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        task_link = _make_task_link(id="link-b", story_node_id="1-2-b")
        mock_task_link_repo.get.return_value = task_link
        mock_task_link_repo.list_by_project.return_value = [task_link]
        mock_task_link_repo.set_state.return_value = task_link
        mock_task_link_repo.claim_ready_and_commit.return_value = _make_task_link(
            id="link-b", story_node_id="1-2-b", state=TaskLinkState.starting
        )
        new_job = _make_job(id="job-2", state=JobState.preparing)
        mock_job_service.create_job.return_value = new_job
        mock_task_link_repo.attach_claimed_job.return_value = _make_task_link(
            id="link-b", story_node_id="1-2-b", state=TaskLinkState.running, job_id="job-2"
        )

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.spawn_approved_task_link("link-b", parent_job_id="job-1")

        assert result == new_job
        mock_job_service.create_job.assert_awaited_once()
        spec = mock_job_service.create_job.call_args.args[0]
        assert spec.repo == task_link.repo_path
        assert spec.parent_job_id == "job-1"
        mock_task_link_repo.claim_ready_and_commit.assert_awaited_once_with("link-b")
        mock_task_link_repo.attach_claimed_job.assert_awaited_once_with("link-b", "job-2", state=TaskLinkState.running)

    @pytest.mark.asyncio
    async def test_no_op_when_task_link_already_has_job_id(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        task_link = _make_task_link(id="link-b", story_node_id="1-2-b", job_id="job-already-there")
        mock_task_link_repo.get.return_value = task_link

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.spawn_approved_task_link("link-b", parent_job_id="job-1")

        assert result is None
        mock_job_service.create_job.assert_not_awaited()
        mock_task_link_repo.set_job_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_for_unknown_task_link(
        self,
        mock_task_link_repo: AsyncMock,
        mock_project_service: AsyncMock,
        mock_job_repo: AsyncMock,
        mock_job_service: AsyncMock,
    ) -> None:
        mock_task_link_repo.get.return_value = None

        service = RecipeService(
            mock_task_link_repo, mock_project_service, job_service=mock_job_service, job_repo=mock_job_repo
        )
        result = await service.spawn_approved_task_link("no-such-link", parent_job_id="job-1")

        assert result is None
        mock_job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_when_job_service_not_configured(
        self, mock_task_link_repo: AsyncMock, mock_project_service: AsyncMock
    ) -> None:
        service = RecipeService(mock_task_link_repo, mock_project_service)
        result = await service.spawn_approved_task_link("link-b", parent_job_id="job-1")

        assert result is None
        mock_task_link_repo.get.assert_not_awaited()
