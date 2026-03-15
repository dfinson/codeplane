"""Tests for the JobService and state machine."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from backend.config import TowerConfig
from backend.models.db import Base
from backend.models.domain import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    InvalidStateTransitionError,
    JobState,
    validate_state_transition,
)
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.services.git_service import GitService
from backend.services.job_service import (
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
def config(tmp_path: object) -> TowerConfig:
    return TowerConfig(repos=["/repos/test"])


@pytest.fixture
def git_service(config: TowerConfig) -> GitService:
    return GitService(config)


@pytest.fixture
def job_service(
    session: AsyncSession,
    config: TowerConfig,
    git_service: GitService,
) -> JobService:
    return JobService(
        job_repo=JobRepository(session),
        git_service=git_service,
        config=config,
    )


# --- State Machine Unit Tests ---


class TestStateMachine:
    def test_valid_initial_transitions(self) -> None:
        validate_state_transition(None, JobState.queued)
        validate_state_transition(None, JobState.running)

    def test_invalid_initial_transition(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_state_transition(None, JobState.succeeded)

    def test_queued_to_running(self) -> None:
        validate_state_transition(JobState.queued, JobState.running)

    def test_queued_to_canceled(self) -> None:
        validate_state_transition(JobState.queued, JobState.canceled)

    def test_queued_to_succeeded_invalid(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_state_transition(JobState.queued, JobState.succeeded)

    def test_running_to_succeeded(self) -> None:
        validate_state_transition(JobState.running, JobState.succeeded)

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
        assert JobState.succeeded in TERMINAL_STATES
        assert JobState.failed in TERMINAL_STATES
        assert JobState.canceled in TERMINAL_STATES

    def test_active_states_values(self) -> None:
        assert JobState.queued in ACTIVE_STATES
        assert JobState.running in ACTIVE_STATES
        assert JobState.waiting_for_approval in ACTIVE_STATES


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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            job = await job_service.create_job(
                repo="/repos/test",
                prompt="Fix the bug",
            )
            await session.commit()

        assert job.id == "job-1"
        assert job.state == JobState.queued
        assert job.repo == "/repos/test"
        assert job.prompt == "Fix the bug"
        assert job.branch == "tower/job-1"

    @pytest.mark.asyncio
    async def test_create_job_repo_not_allowed(
        self,
        job_service: JobService,
    ) -> None:
        with pytest.raises(RepoNotAllowedError):
            await job_service.create_job(
                repo="/repos/not-allowed",
                prompt="Fix it",
            )

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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix it")
            await session.commit()

        job = await job_service.get_job("job-1")
        assert job.id == "job-1"

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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix 1")
            await session.commit()

        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "tower/job-2"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix 2")
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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix it")
            await session.commit()

        job = await job_service.cancel_job("job-1")
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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix it")
            await session.commit()

        # First cancel succeeds
        await job_service.cancel_job("job-1")
        await session.commit()

        # Second cancel fails
        with pytest.raises(StateConflictError):
            await job_service.cancel_job("job-1")

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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            original = await job_service.create_job(
                repo="/repos/test",
                prompt="Fix it",
            )
            await session.commit()

        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "tower/job-2"),
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

        assert new_job.id == "job-2"
        assert new_job.prompt == original.prompt
        assert new_job.repo == original.repo

    @pytest.mark.asyncio
    async def test_rerun_nonexistent_job(self, job_service: JobService) -> None:
        with pytest.raises(JobNotFoundError):
            await job_service.rerun_job("job-999")

    @pytest.mark.asyncio
    async def test_sequential_job_ids(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        for i in range(1, 4):
            with (
                patch.object(
                    job_service._git,
                    "create_worktree",
                    new_callable=AsyncMock,
                    return_value=("/repos/test", f"tower/job-{i}"),
                ),
                patch.object(
                    job_service._git,
                    "get_default_branch",
                    new_callable=AsyncMock,
                    return_value="main",
                ),
            ):
                job = await job_service.create_job(
                    repo="/repos/test",
                    prompt=f"Task {i}",
                )
                await session.commit()
                assert job.id == f"job-{i}"

    @pytest.mark.asyncio
    async def test_worktree_failure_creates_failed_job(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        from backend.services.git_service import GitError

        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                side_effect=GitError("worktree failed"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            job = await job_service.create_job(
                repo="/repos/test",
                prompt="Fix it",
            )
            await session.commit()

        assert job.state == JobState.failed
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_transition_state_running_to_succeeded(
        self,
        job_service: JobService,
        session: AsyncSession,
    ) -> None:
        with (
            patch.object(
                job_service._git,
                "create_worktree",
                new_callable=AsyncMock,
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix it")
            await session.commit()

        # Phase 4: jobs start as queued, must transition through running first
        await job_service.transition_state("job-1", JobState.running)
        job = await job_service.transition_state("job-1", JobState.succeeded)
        assert job.state == JobState.succeeded
        assert job.completed_at is not None

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
                return_value=("/repos/test", "tower/job-1"),
            ),
            patch.object(
                job_service._git,
                "get_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            await job_service.create_job(repo="/repos/test", prompt="Fix it")
            await session.commit()

        with pytest.raises(InvalidStateTransitionError):
            await job_service.transition_state("job-1", JobState.queued)
