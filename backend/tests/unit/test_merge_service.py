"""Tests for MergeService — merge-back orchestration."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CompletionConfig, CPLConfig
from backend.models.db import Base
from backend.models.domain import Job
from backend.models.events import DomainEvent, DomainEventKind
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.services.event_bus import EventBus
from backend.services.git_service import GitService
from backend.services.merge_service import MergeService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@t.com",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(eng.sync_engine, "connect", _set_sqlite_pragmas)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, env=_GIT_ENV)


def _init_repo(path: Path) -> None:
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "t@t.com")
    (path / "README.md").write_text("# Test\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial")


def _branch_with_change(repo: Path, branch: str, filename: str, content: str) -> None:
    _git(repo, "checkout", "-b", branch)
    (repo / filename).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"add {filename}")
    _git(repo, "checkout", "main")


def _make_job(repo: str, job_id: str = "job-1", branch: str = "cpl/job-1") -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        repo=repo,
        prompt="test prompt",
        state="running",
        base_ref="main",
        branch=branch,
        worktree_path=None,
        session_id=None,
        created_at=now,
        updated_at=now,
    )


async def _insert_job(sf: async_sessionmaker[AsyncSession], job: Job) -> None:
    async with sf() as session:
        await JobRepository(session).create(job)
        await session.commit()


def _make_service(
    event_bus: EventBus,
    sf: async_sessionmaker[AsyncSession],
    **overrides: object,
) -> MergeService:
    defaults = dict(strategy="auto_merge", auto_push=False, cleanup_worktree=False, delete_branch_after_merge=False)
    defaults.update(overrides)
    return MergeService(
        git_service=GitService(CPLConfig()),
        event_bus=event_bus,
        session_factory=sf,
        config=CompletionConfig(**defaults),
    )


# ---------------------------------------------------------------------------
# Fast-forward merge
# ---------------------------------------------------------------------------


class TestFastForwardMerge:
    async def test_ff_merge_succeeds(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "new_file.py", "print('hello')\n")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "merged"
        assert result.strategy == "ff_only"
        assert (repo / "new_file.py").exists()

        merge_events = [e for e in published if e.kind == DomainEventKind.merge_completed]
        assert len(merge_events) == 1
        assert merge_events[0].payload["strategy"] == "ff_only"

    async def test_ff_merge_updates_db(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "file.py", "x = 1\n")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        async with session_factory() as session:
            updated = await JobRepository(session).get("job-1")
        assert updated is not None
        assert updated.merge_status == "merged"


# ---------------------------------------------------------------------------
# Regular merge (base diverged, no conflicts)
# ---------------------------------------------------------------------------


class TestRegularMerge:
    async def test_diverged_no_conflict(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "feature.py", "def feature(): pass\n")

        # Diverge main with a DIFFERENT file
        (repo / "other.py").write_text("# other\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "diverge main")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        result = await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "merged"
        assert result.strategy == "merge"
        assert (repo / "feature.py").exists()
        assert (repo / "other.py").exists()


# ---------------------------------------------------------------------------
# Conflict detection and PR fallback
# ---------------------------------------------------------------------------


class TestConflictFallback:
    async def test_conflict_detected_and_events_published(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "README.md", "# Branch\n")

        # Conflicting change on main
        (repo / "README.md").write_text("# Main\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "conflict on main")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "conflict"
        assert result.conflict_files is not None
        assert len(result.conflict_files) > 0

        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert len(conflict_events) == 1

        # Main worktree should be back on main (not stuck in merge state)
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "main"


# ---------------------------------------------------------------------------
# PR-only strategy
# ---------------------------------------------------------------------------


class TestPrOnlyStrategy:
    async def test_pr_only_does_not_merge(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "file.py", "x = 1\n")

        service = _make_service(event_bus, session_factory, strategy="pr_only")
        await _insert_job(session_factory, _make_job(str(repo)))

        result = await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        # No gh CLI in test — PR creation fails gracefully
        assert result.status in ("pr_created", "skipped", "error")
        # Main unchanged — file should NOT exist
        assert not (repo / "file.py").exists()


# ---------------------------------------------------------------------------
# False-positive conflict detection (regression tests)
# ---------------------------------------------------------------------------


def _add_failing_pre_merge_commit_hook(repo: Path) -> None:
    """Install a pre-merge-commit hook that always exits 1.

    This hook fires during ``git merge`` (just before the merge commit is
    created) but is NOT called when ``git merge --no-commit`` is used.
    That asymmetry is exactly what we rely on: the --no-commit probe in
    ``_get_conflict_file_list`` bypasses the hook and correctly reports
    "no real conflicts", while the actual merge attempt fails.
    """
    hooks_dir = repo / ".git" / "hooks"
    hook_path = hooks_dir / "pre-merge-commit"
    hook_path.write_text("#!/bin/sh\necho 'pre-merge-commit hook failed'\nexit 1\n")
    hook_path.chmod(0o755)


class TestFalsePositiveConflicts:
    """Regression tests for false-positive merge conflict detection.

    Before the fix, any git error during merge/cherry-pick was reported as a
    merge conflict.  These tests verify that only *actual* conflict markers
    trigger the "conflict" status — other failures (hooks, identity, locks)
    result in "error".
    """

    async def test_smart_merge_already_applied_is_not_a_conflict(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """Cherry-pick of already-applied commits must not be reported as a conflict.

        When the same changes already exist in the target branch (e.g. the
        commit was separately cherry-picked to main), git exits 1 with
        "the previous cherry-pick is now empty" but leaves no conflict markers.
        The old code treated this as "conflict with unknown files".
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "feature.py", "x = 1\n")

        # Apply the same change to main via cherry-pick (simulating another merge path)
        _git(repo, "cherry-pick", "cpl/job-1")

        # Diverge main so the branch is not simply fast-forward-able
        (repo / "extra.py").write_text("# extra\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "more main changes")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.resolve_job(
            job_id="job-1",
            action="smart_merge",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        # "Already applied" is not a real merge conflict — must be "error".
        assert result.status == "error", f"Expected error, got {result.status!r}"
        assert result.error == (
            "Cherry-pick stopped because one or more branch commits are already present on the "
            "base branch; rebase the branch or create a PR"
        )
        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert conflict_events == [], "No merge_conflict event should be published for already-applied commits"

    async def test_smart_merge_real_conflict_still_detected(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """Cherry-pick with a real conflict must still be reported as a conflict."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "README.md", "# Branch version\n")

        # Conflicting change on main
        (repo / "README.md").write_text("# Main version\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "conflicting change on main")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.resolve_job(
            job_id="job-1",
            action="smart_merge",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "conflict"
        assert result.conflict_files  # should list the conflicting file
        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert len(conflict_events) == 1

    async def test_auto_merge_hook_failure_is_not_a_conflict(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """Automatic merge failing due to a pre-merge-commit hook is not a conflict.

        The pre-merge-commit hook fires just before the merge commit is created
        but is NOT invoked when ``git merge --no-commit`` is used.  The probe
        therefore bypasses the hook and correctly reports "no real conflicts".
        The retry also fails (hook fires again), which should produce "error"
        rather than "conflict".
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "feature.py", "x = 1\n")

        # Diverge main so a merge commit is required (FF is not possible).
        (repo / "other.py").write_text("# other\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "diverge main")

        # Hook installed after branch commits are in place.
        _add_failing_pre_merge_commit_hook(repo)

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert str(result.status) == "error", f"Expected error, got {result.status!r}"
        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert conflict_events == [], "No merge_conflict event should be published for non-conflict merge failures"

        async with session_factory() as session:
            job = await JobRepository(session).get("job-1")
        assert job is not None
        assert job.merge_status == "not_merged"


# ---------------------------------------------------------------------------
# Operator-initiated merge (resolve_job action="merge" / action="smart_merge")
# ---------------------------------------------------------------------------


class TestOperatorMerge:
    async def test_operator_merge_hook_failure_returns_error_not_conflict(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """resolve_job(action='merge') hook failures must not persist merge_status=conflict."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "feature.py", "x = 1\n")

        (repo / "other.py").write_text("# other\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "diverge main")
        _add_failing_pre_merge_commit_hook(repo)

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.resolve_job(
            job_id="job-1",
            action="merge",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "error"
        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert conflict_events == []

        async with session_factory() as session:
            job = await JobRepository(session).get("job-1")
        assert job is not None
        assert job.merge_status == "not_merged"

    async def test_operator_merge_conflict_persists_merge_status(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """resolve_job(action='merge') conflict must persist merge_status=conflict in DB."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "README.md", "# Branch\n")

        # Conflicting change on main
        (repo / "README.md").write_text("# Main\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "conflict on main")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect)

        result = await service.resolve_job(
            job_id="job-1",
            action="merge",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "conflict"
        assert result.conflict_files

        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert len(conflict_events) == 1

        # merge_status must be persisted in the DB
        async with session_factory() as session:
            job = await JobRepository(session).get("job-1")
        assert job is not None
        assert job.merge_status == "conflict"

        # Main worktree must not be left in a broken state
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "", f"Main worktree is dirty after conflict: {out.stdout!r}"

    async def test_operator_smart_merge_conflict_persists_merge_status(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """resolve_job(action='smart_merge') conflict must persist merge_status=conflict in DB."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "README.md", "# Branch\n")

        # Conflicting change on main
        (repo / "README.md").write_text("# Main\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "conflict on main")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        published: list[DomainEvent] = []

        async def _collect2(e: DomainEvent) -> None:
            published.append(e)

        event_bus.subscribe(_collect2)

        result = await service.resolve_job(
            job_id="job-1",
            action="smart_merge",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "conflict"
        assert result.conflict_files

        conflict_events = [e for e in published if e.kind == DomainEventKind.merge_conflict]
        assert len(conflict_events) == 1

        # merge_status must be persisted in the DB
        async with session_factory() as session:
            job = await JobRepository(session).get("job-1")
        assert job is not None
        assert job.merge_status == "conflict"

    async def test_operator_merge_success_persists_merged(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        """resolve_job(action='merge') success must persist merge_status=merged in DB."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "feature.py", "x = 1\n")

        # Diverge main so a real merge commit is needed
        (repo / "other.py").write_text("# other\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "diverge main")

        service = _make_service(event_bus, session_factory)
        await _insert_job(session_factory, _make_job(str(repo)))

        result = await service.resolve_job(
            job_id="job-1",
            action="merge",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        assert result.status == "merged"

        async with session_factory() as session:
            job = await JobRepository(session).get("job-1")
        assert job is not None
        assert job.merge_status == "merged"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_no_branch_returns_skipped(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        service = _make_service(event_bus, session_factory)
        result = await service.try_merge_back(
            job_id="job-1",
            repo_path="/tmp/fake",
            worktree_path=None,
            branch=None,
            base_ref="main",
            prompt="test",
        )
        assert result.status == "skipped"

    async def test_invalid_branch_returns_skipped(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        service = _make_service(event_bus, session_factory)
        result = await service.try_merge_back(
            job_id="job-1",
            repo_path="/tmp/fake",
            worktree_path=None,
            branch="branch; rm -rf /",
            base_ref="main",
            prompt="test",
        )
        assert result.status == "skipped"

    async def test_branch_deleted_after_merge(
        self,
        tmp_path: Path,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _branch_with_change(repo, "cpl/job-1", "file.py", "x = 1\n")

        service = _make_service(event_bus, session_factory, delete_branch_after_merge=True)
        await _insert_job(session_factory, _make_job(str(repo)))

        await service.try_merge_back(
            job_id="job-1",
            repo_path=str(repo),
            worktree_path=None,
            branch="cpl/job-1",
            base_ref="main",
            prompt="test",
        )

        branches = subprocess.run(
            ["git", "branch"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
        )
        assert "cpl/job-1" not in branches.stdout
