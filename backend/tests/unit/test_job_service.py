"""Tests for the JobService and state machine."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.config import CPLConfig
from backend.models.db import Base
from backend.models.domain import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    InvalidStateTransitionError,
    JobSpec,
    JobState,
    validate_state_transition,
)
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.git.git_service import GitService
from backend.services.job.job_service import (
    JobNotFoundError,
    JobService,
    RepoNotAllowedError,
    StateConflictError,
)


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


@pytest.fixture
def config(tmp_path: object) -> CPLConfig:
    return CPLConfig(repos=["/repos/test"])


@pytest.fixture
def git_service(config: CPLConfig) -> GitService:
    return GitService(config)


@pytest.fixture
async def job_service(
    session: AsyncSession,
    config: CPLConfig,
    git_service: GitService,
) -> JobService:
    # A Job can never exist without an owning Project (AD-5) — seed a default
    # Project owning "/repos/test" so existing tests that don't care about
    # Project-resolution specifics keep working.
    await ProjectRepository(session).create("proj-default", "Default", ["/repos/test"])
    await session.commit()
    return JobService(
        job_repo=JobRepository(session),
        git_service=git_service,
        config=config,
        project_repo=ProjectRepository(session),
    )


# --- State Machine Unit Tests ---


class TestRuntimeContract:
    async def test_naming_returns_event_for_post_commit_publication(self):
        from backend.tests.unit.conftest import make_job

        repo = AsyncMock()
        repo.get.return_value = make_job(title=None)
        git = AsyncMock()
        git.list_branches.return_value = set()
        git.list_worktree_names.return_value = set()
        naming = AsyncMock()
        naming.generate.return_value = ("Fixture title", "Fixture description", "branch", "worktree")
        bus = AsyncMock()
        service = JobService(repo, git, CPLConfig(), naming_service=naming, event_bus=bus)
        event = await service.enrich_naming_async("job-1", JobSpec(repo="fixture", prompt="fixture"), "fixture")
        assert event is not None, "Naming must return an event for post-commit publication"
        assert event.kind == "job.title_updated"
        repo.update_title_and_branch.assert_awaited_once()
        bus.publish.assert_not_awaited()

    async def test_cancel_during_setup_cannot_revive_job(self):
        from backend.tests.unit.conftest import make_job

        job = make_job(state=JobState.preparing, repo="fixture", worktree_name="fixture")
        canceled = make_job(state=JobState.canceled, repo="fixture", worktree_name="fixture")
        repo = AsyncMock()
        repo.get.side_effect = [job, canceled, canceled]
        git = AsyncMock()
        git.create_worktree.return_value = ("fixture-worktree", "fixture-branch")
        service = JobService(repo, git, CPLConfig())
        result = await service.setup_workspace(job.id)
        assert result.state == JobState.canceled
        assert not any(call.args[1] == JobState.queued for call in repo.update_state.call_args_list)


class TestStateMachine:
    def test_valid_initial_transitions(self) -> None:
        validate_state_transition(None, JobState.queued)
        validate_state_transition(None, JobState.running)

    def test_invalid_initial_transition(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_state_transition(None, JobState.review)

    def test_queued_to_running(self) -> None:
        validate_state_transition(JobState.queued, JobState.running)

    def test_queued_to_canceled(self) -> None:
        validate_state_transition(JobState.queued, JobState.canceled)

    def test_queued_to_review_invalid(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_state_transition(JobState.queued, JobState.review)

    def test_running_to_review(self) -> None:
        validate_state_transition(JobState.running, JobState.review)

    def test_running_to_failed(self) -> None:
        validate_state_transition(JobState.running, JobState.failed)

    def test_running_to_canceled(self) -> None:
        validate_state_transition(JobState.running, JobState.canceled)

    def test_running_to_waiting(self) -> None:
        validate_state_transition(JobState.running, JobState.waiting_for_approval)

    def test_waiting_to_running(self) -> None:
        validate_state_transition(JobState.waiting_for_approval, JobState.running)

    def test_waiting_to_canceled(self) -> None:
        validate_state_transition(JobState.waiting_for_approval, JobState.canceled)

    def test_waiting_to_failed(self) -> None:
        validate_state_transition(JobState.waiting_for_approval, JobState.failed)

    def test_terminal_states_can_resume_to_running(self) -> None:
        """Terminal states allow transition to running for job resumption."""
        for terminal in TERMINAL_STATES:
            # Should NOT raise — terminal → running is valid for resume
            validate_state_transition(terminal, JobState.running)

    def test_terminal_states_cannot_transition_to_non_running(self) -> None:
        """Terminal states cannot transition to queued or waiting_for_approval."""
        for terminal in TERMINAL_STATES:
            for invalid in (JobState.queued, JobState.waiting_for_approval):
                with pytest.raises(InvalidStateTransitionError):
                    validate_state_transition(terminal, invalid)

    def test_terminal_states_values(self) -> None:
        assert JobState.completed in TERMINAL_STATES
        assert JobState.failed in TERMINAL_STATES
        assert JobState.canceled in TERMINAL_STATES

    def test_active_states_values(self) -> None:
        assert JobState.queued in ACTIVE_STATES
        assert JobState.running in ACTIVE_STATES
        assert JobState.waiting_for_approval in ACTIVE_STATES
        assert JobState.review in ACTIVE_STATES


# --- JobService Tests ---


class TestJobService:
    @pytest.mark.asyncio
    async def test_create_job_succeeds(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            job = await job_service.create_job(
                JobSpec(
                    repo="/repos/test",
                    prompt="Fix the bug",
                )
            )
            await session.commit()

        assert job.id != ""
        assert not job.id.startswith("job-")  # no more sequential job-N IDs
        assert job.state == JobState.preparing
        assert job.repo == str(Path("/repos/test").resolve())
        assert job.prompt == "Fix the bug"
        # Branch is stored but worktree is not created yet (background task)

    @pytest.mark.asyncio
    async def test_create_job_repo_not_allowed(
        self,
        job_service: JobService,
    ) -> None:
        """A repo that belongs to zero Projects is never job-eligible —
        there is no legacy allowlist fallback (AD-5)."""
        with pytest.raises(RepoNotAllowedError):
            await job_service.create_job(
                JobSpec(
                    repo="/repos/not-allowed",
                    prompt="Fix it",
                )
            )

    @pytest.mark.asyncio
    async def test_create_job_with_explicit_project_id_round_trips(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        await ProjectRepository(session).create("proj-1", "Payments", ["/repos/test"])
        await session.commit()

        with patch.object(
            job_service._git,
            "get_default_branch",
            new_callable=AsyncMock,
            return_value="main",
        ):
            job = await job_service.create_job(
                JobSpec(
                    repo="/repos/test",
                    prompt="Fix the bug",
                    project_id="proj-1",
                )
            )
            await session.commit()

        assert job.project_id == "proj-1"
        fetched = await job_service.get_job(job.id)
        assert fetched is not None
        assert fetched.project_id == "proj-1"

    @pytest.mark.asyncio
    async def test_create_job_rejects_explicit_project_id_for_non_member_repo(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        await ProjectRepository(session).create("proj-1", "Payments", ["/repos/other"])
        await session.commit()

        with pytest.raises(RepoNotAllowedError, match="is not a member"):
            await job_service.create_job(
                JobSpec(
                    repo="/repos/test",
                    prompt="Fix the bug",
                    project_id="proj-1",
                )
            )

    @pytest.mark.asyncio
    async def test_create_job_resolves_project_id_when_repo_has_single_match(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        await ProjectRepository(session).create("proj-solo", "Payments", ["/repos/solo"])
        await session.commit()

        with patch.object(
            job_service._git,
            "get_default_branch",
            new_callable=AsyncMock,
            return_value="main",
        ):
            job = await job_service.create_job(JobSpec(repo="/repos/solo", prompt="Fix the bug"))
            await session.commit()

        assert job.project_id == "proj-solo"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("projects", "label"),
        [
            ([], "no-match"),
            ([("proj-a", ["/repos/ambiguous"]), ("proj-b", ["/repos/ambiguous"])], "multi-match"),
        ],
    )
    async def test_create_job_rejects_zero_or_multiple_matches(
        self,
        job_service: JobService,
        session: AsyncSession,
        projects: list[tuple[str, list[str]]],
        label: str,
    ) -> None:
        """A repo with zero or ambiguous (>1) owning Projects is never
        job-eligible — the atomic unit is a Project, and a Job can never
        exist without exactly one resolvable owner (AD-5)."""
        project_repo = ProjectRepository(session)
        for project_id, repo_paths in projects:
            await project_repo.create(project_id, label, repo_paths)
        await session.commit()

        with pytest.raises(RepoNotAllowedError):
            await job_service.create_job(JobSpec(repo="/repos/ambiguous", prompt="Fix the bug"))

    async def test_create_job_allowed_via_project_membership(
        self,
        session: AsyncSession,
        config: CPLConfig,
        git_service: GitService,
    ) -> None:
        """A repo that belongs to exactly one Project is job-eligible even if
        it was never added to the legacy ``config.repos`` — Project
        membership is the sole authorization source (AD-5)."""
        from backend.persistence.project_repo import ProjectRepository

        project_repo = ProjectRepository(session)
        await project_repo.create("proj-1", "Project One", ["/repos/project-only"])
        await session.commit()

        svc = JobService(
            job_repo=JobRepository(session),
            git_service=git_service,
            config=config,
            project_repo=project_repo,
        )

        with (
            patch.object(
                git_service,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/project-only", "cpl/job-1"),
            ),
            patch.object(
                git_service,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            job = await svc.create_job(JobSpec(repo="/repos/project-only", prompt="Fix it"))
            await session.commit()

        assert job.state == JobState.preparing

    @pytest.mark.asyncio
    async def test_create_job_still_rejects_repo_in_no_project(
        self,
        session: AsyncSession,
        config: CPLConfig,
        git_service: GitService,
    ) -> None:
        """A repo absent from all Project repo_paths must remain rejected —
        there is no legacy allowlist to fall back to (AD-5)."""
        from backend.persistence.project_repo import ProjectRepository

        project_repo = ProjectRepository(session)
        await project_repo.create("proj-1", "Project One", ["/repos/project-only"])
        await session.commit()

        svc = JobService(
            job_repo=JobRepository(session),
            git_service=git_service,
            config=config,
            project_repo=project_repo,
        )

        with pytest.raises(RepoNotAllowedError):
            await svc.create_job(JobSpec(repo="/repos/totally-unrelated", prompt="Fix it"))

    @pytest.mark.asyncio
    async def test_get_job_found(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            created = await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix it"))
            await session.commit()

        job = await job_service.get_job(created.id)
        assert job.id == created.id

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, job_service: JobService) -> None:
        with pytest.raises(JobNotFoundError):
            await job_service.get_job("job-999")

    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, job_service: JobService) -> None:
        jobs, cursor, has_more = await job_service.list_jobs()
        assert jobs == []
        assert cursor is None
        assert has_more is False

    @pytest.mark.asyncio
    async def test_list_jobs_with_items(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix 1"))
            await session.commit()

        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-2"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix 2"))
            await session.commit()

        jobs, cursor, has_more = await job_service.list_jobs()
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_cancel_running_job(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            created = await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix it"))
            await session.commit()

        job = await job_service.cancel_job(created.id)
        assert job.state == JobState.canceled
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_cancel_terminal_job_raises_conflict(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            created = await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix it"))
            await session.commit()

        # First cancel succeeds
        await job_service.cancel_job(created.id)
        await session.commit()

        # Second cancel fails
        with pytest.raises(StateConflictError):
            await job_service.cancel_job(created.id)

    @pytest.mark.asyncio
    async def test_rerun_creates_new_job(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            original = await job_service.create_job(
                JobSpec(
                    repo="/repos/test",
                    prompt="Fix it",
                )
            )
            await session.commit()

        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-2"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            new_job = await job_service.rerun_job(original.id)
            await session.commit()

        assert new_job.id != original.id  # distinct jobs get distinct IDs
        assert not new_job.id.startswith("job-")
        assert new_job.prompt == original.prompt
        assert new_job.repo == original.repo

    @pytest.mark.asyncio
    async def test_rerun_nonexistent_job(self, job_service: JobService) -> None:
        with pytest.raises(JobNotFoundError):
            await job_service.rerun_job("job-999")

    @pytest.mark.asyncio
    async def test_job_ids_derived_from_naming(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        """Job IDs must come from worktree naming, never from sequential counters."""
        prompts = ["Task one", "Task two", "Task three"]
        ids: list[str] = []
        for i, prompt in enumerate(prompts, start=1):
            with (
                patch.object(
                    job_service._git,
                    "create_worktree",
                    new_callable=AsyncMock,
                    return_value=("/repos/test", f"cpl/task-{i}"),
                ),
                patch.object(
                    job_service._git,
                    "get_default_branch",
                    new_callable=AsyncMock,
                    return_value="main",
                ),
            ):
                job = await job_service.create_job(
                    JobSpec(
                        repo="/repos/test",
                        prompt=prompt,
                    )
                )
                await session.commit()
            assert not job.id.startswith("job-"), f"Expected no 'job-N' ID, got: {job.id}"
            assert job.id not in ids, "Duplicate job ID"
            ids.append(job.id)

    @pytest.mark.asyncio
    async def test_worktree_failure_creates_failed_job(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        """Worktree failure during setup_workspace transitions job to failed."""
        from backend.services.git.git_service import GitError

        with patch.object(
            job_service._git,
            "get_default_branch",
            new_callable=AsyncMock,
            return_value="main",
        ):
            job = await job_service.create_job(
                JobSpec(
                    repo="/repos/test",
                    prompt="Fix it",
                )
            )
            await session.commit()

        assert job.state == JobState.preparing

        # Now simulate worktree failure during setup
        with patch.object(
            job_service._git,
            "create_worktree",
            new_callable=AsyncMock,
            side_effect=GitError("worktree failed"),
        ):
            updated = await job_service.setup_workspace(job.id)
            await session.commit()

        assert updated.state == JobState.failed

    @pytest.mark.asyncio
    async def test_transition_state_running_to_review(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            created = await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix it"))
            await session.commit()

        # Phase 4: jobs start as preparing, must transition through queued → running first
        await job_service.transition_state(created.id, JobState.queued)
        await job_service.transition_state(created.id, JobState.running)
        job = await job_service.transition_state(created.id, JobState.review)
        assert job.state == JobState.review
        # review is active, not terminal — completed_at should be None
        assert job.completed_at is None

    @pytest.mark.asyncio
    async def test_transition_state_invalid(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "cpl/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            created = await job_service.create_job(JobSpec(repo="/repos/test", prompt="Fix it"))
            await session.commit()

        with pytest.raises(InvalidStateTransitionError):
            # preparing → running is invalid (must go through queued first)
            await job_service.transition_state(created.id, JobState.running)
