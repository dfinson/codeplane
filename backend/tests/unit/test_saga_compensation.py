"""Saga compensation tests for job creation.

Tests that when the DB persist fails after a worktree is created,
the worktree is cleaned up (compensation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.domain import Job, JobState, PermissionMode
from backend.services.job_service import JobService


def _make_config(**overrides: Any) -> Any:
    """Create a minimal CPLConfig mock."""
    config = MagicMock()
    config.repos = ["/repos/test"]
    config.runtime.default_sdk = "copilot"
    config.runtime.max_concurrent_jobs = 5
    return config


class TestJobCreationCompensation:
    """Worktree cleanup on DB failure during job creation."""

    @pytest.mark.asyncio
    async def test_db_failure_triggers_worktree_cleanup(self) -> None:
        """If job_repo.create() fails after worktree creation, remove_worktree is called."""
        job_repo = AsyncMock()
        job_repo.list_ids = AsyncMock(return_value=set())
        job_repo.create = AsyncMock(side_effect=Exception("DB write failed"))

        git_service = AsyncMock()
        git_service.get_default_branch = AsyncMock(return_value="main")
        git_service.list_branches = AsyncMock(return_value=set())
        git_service.list_worktree_names = AsyncMock(return_value=set())
        git_service.create_worktree = AsyncMock(return_value=("/repos/test/.cpl-worktrees/fix-bug", "fix/bug"))
        git_service.remove_worktree = AsyncMock()

        config = _make_config()

        svc = JobService(
            job_repo=job_repo,
            git_service=git_service,
            config=config,
        )

        # Patch validate_repo to return the resolved path
        with patch.object(svc, "validate_repo", return_value="/repos/test"):
            with pytest.raises(Exception, match="DB write failed"):
                await svc.create_job(repo="/repos/test", prompt="Fix the bug")

        # Compensation: worktree should be cleaned up
        git_service.remove_worktree.assert_called_once_with(
            "/repos/test", "/repos/test/.cpl-worktrees/fix-bug"
        )

    @pytest.mark.asyncio
    async def test_compensation_failure_doesnt_mask_original_error(self) -> None:
        """If both DB persist and worktree cleanup fail, the original error is raised."""
        job_repo = AsyncMock()
        job_repo.list_ids = AsyncMock(return_value=set())
        job_repo.create = AsyncMock(side_effect=Exception("DB write failed"))

        git_service = AsyncMock()
        git_service.get_default_branch = AsyncMock(return_value="main")
        git_service.list_branches = AsyncMock(return_value=set())
        git_service.list_worktree_names = AsyncMock(return_value=set())
        git_service.create_worktree = AsyncMock(return_value=("/repos/test/.cpl-worktrees/fix-bug", "fix/bug"))
        git_service.remove_worktree = AsyncMock(side_effect=Exception("Cleanup also failed"))

        config = _make_config()

        svc = JobService(
            job_repo=job_repo,
            git_service=git_service,
            config=config,
        )

        with patch.object(svc, "validate_repo", return_value="/repos/test"):
            with pytest.raises(Exception, match="DB write failed"):
                await svc.create_job(repo="/repos/test", prompt="Fix the bug")

        # Both called, but original error propagates
        git_service.remove_worktree.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_creation_no_cleanup(self) -> None:
        """Normal job creation does not trigger compensation."""
        now = datetime.now(UTC)
        job_repo = AsyncMock()
        job_repo.list_ids = AsyncMock(return_value=set())
        job_repo.create = AsyncMock(return_value=None)

        git_service = AsyncMock()
        git_service.get_default_branch = AsyncMock(return_value="main")
        git_service.list_branches = AsyncMock(return_value=set())
        git_service.list_worktree_names = AsyncMock(return_value=set())
        git_service.create_worktree = AsyncMock(return_value=("/repos/test/.cpl-worktrees/fix-bug", "fix/bug"))
        git_service.remove_worktree = AsyncMock()

        config = _make_config()

        svc = JobService(
            job_repo=job_repo,
            git_service=git_service,
            config=config,
        )

        with patch.object(svc, "validate_repo", return_value="/repos/test"):
            job = await svc.create_job(repo="/repos/test", prompt="Fix the bug")

        assert job.state == JobState.queued
        git_service.remove_worktree.assert_not_called()
