"""Task execution regressions across the production transaction boundary."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

import backend
from backend.config import CPLConfig
from backend.models.db import Base, JobRow
from backend.models.domain import Chat, TaskLinkState
from backend.persistence import database
from backend.persistence.chat_repo import ChatRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.persistence.task_link_repo import TaskLinkRepository
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.events.event_bus import EventBus
from backend.services.git.git_service import GitService
from backend.services.job.approval_service import ApprovalService
from backend.services.job.job_service import JobService
from backend.services.project.project_service import ProjectService
from backend.services.recipe.recipe_service import RecipeService
from backend.services.runtime import RuntimeService


@pytest.fixture
async def task_db(tmp_path, monkeypatch):
    assert Path(backend.__file__).resolve().parents[1] == Path.cwd().resolve()
    monkeypatch.setattr(database, "_write_lock", None)
    engine = database.create_engine(tmp_path / "tasks.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = database.create_session_factory(engine)
    config = CPLConfig(repos=[str(tmp_path)])
    async with factory() as session:
        await ProjectRepository(session).create("project", "Recovery", [str(tmp_path)])
        await session.commit()
    yield factory, config, engine, tmp_path
    await engine.dispose()


def _git():
    git = AsyncMock(spec=GitService)
    git.get_default_branch.return_value = "main"
    git.list_worktree_names = MagicMock(return_value=set())
    return git


def _coordinator(factory, config, git, approvals):
    """Adapt only the approved composition change, never production behavior."""
    module_name = "backend.services.task_execution_service"
    if importlib.util.find_spec(module_name) is None:
        return None
    module = importlib.import_module(module_name)
    return module.TaskExecutionService(factory, config, git_service=git, approval_service=approvals)


def _recipe(session, config, git, approvals):
    return RecipeService(
        TaskLinkRepository(session),
        ProjectService(ProjectRepository(session), config),
        job_service=JobService.from_session(session, config, git_service=git),
        job_repo=JobRepository(session),
        chat_repo=ChatRepository(session),
        approval_service=approvals,
    )


async def _seed(task_db, *, chain=False, gated=False):
    factory, _, _, path = task_db
    async with factory() as session:
        entries = [{"repo_path": str(path), "story_node_id": "root", "depends_on": []}]
        if chain:
            entries.append(
                {
                    "repo_path": str(path),
                    "story_node_id": "next",
                    "depends_on": [f"{path}::root"],
                }
            )
        repo = TaskLinkRepository(session)
        links = await repo.upsert_many("project", entries)
        if chain:
            now = datetime.now(UTC)
            session.add(
                JobRow(
                    id="predecessor",
                    repo=str(path),
                    project_id="project",
                    prompt="root",
                    state="completed",
                    resolution="merged",
                    base_ref="main",
                    created_at=now,
                    updated_at=now,
                    session_count=1,
                    artifact_collection_status="completed",
                    artifact_collection_session_count=1,
                )
            )
            await session.flush()
            await repo.set_job_id(links[0].id, "predecessor")
            await repo.set_state(links[0].id, TaskLinkState.completed)
        if gated:
            now = datetime.now(UTC)
            await ChatRepository(session).create(
                Chat(
                    id="chat",
                    project_id="project",
                    title="Gate",
                    created_at=now,
                    last_message_at=now,
                    status="open",
                    task_link_id=links[0].id,
                )
            )
        await session.commit()
        return links


class _Approvals(ApprovalService):
    def __init__(self, factory):
        super().__init__(factory)
        self.entered = asyncio.Event()

    async def create_request(self, *args, **kwargs):
        self.entered.set()
        return await super().create_request(*args, **kwargs)


async def _cancel(task):
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _startup(task_db, execution, approvals):
    factory, config, _, _ = task_db
    if execution is not None:
        await execution.recover_on_startup()
    runtime = RuntimeService(
        factory,
        EventBus(),
        AdapterRegistry(),
        config,
        approval_service=approvals,
        git_service=_git(),
    )
    await runtime.recover_on_startup()


async def _completion(task_db, execution, git, approvals, *, outer_lock=True):
    if execution is not None:
        return await execution.handle_job_completed("predecessor", resolution="merged")
    factory, config, _, _ = task_db
    context = database.serialized_write(factory) if outer_lock else factory()
    async with context as session:
        result = await _recipe(session, config, git, approvals).handle_job_completed(
            "predecessor",
            resolution="merged",
        )
        if not outer_lock:
            await session.commit()
        return result


async def _assert_gated_progress(task_db, *, outer_lock):
    factory, config, _, _ = task_db
    await _seed(task_db, chain=True, gated=True)
    approvals = _Approvals(factory)
    execution = _coordinator(factory, config, _git(), approvals)
    task = asyncio.create_task(_completion(task_db, execution, _git(), approvals, outer_lock=outer_lock))
    await asyncio.wait_for(approvals.entered.wait(), 3)
    try:
        await asyncio.wait_for(asyncio.shield(task), 0.5)
        completed = True
    except TimeoutError:
        completed = False
    pending = await approvals.list_pending()
    await _cancel(task)
    assert completed, f"Approval blocked: outer_lock={outer_lock}, persisted={len(pending)}"
    assert len(pending) == 1
    async with database.serialized_write(factory) as session:
        assert await ProjectRepository(session).get("project") is not None
    async with factory() as session:
        assert (await session.execute(select(JobRow.id))).scalars().all() == ["predecessor"]


async def test_production_writer_boundary_allows_gated_completion(task_db):
    await _assert_gated_progress(task_db, outer_lock=True)


async def test_separate_sqlite_writer_allows_gated_completion(task_db):
    await _assert_gated_progress(task_db, outer_lock=False)


async def test_cancelled_root_claim_is_retryable(task_db):
    factory, config, _, _ = task_db
    root = (await _seed(task_db))[0]
    entered = asyncio.Event()
    git = _git()
    approvals = ApprovalService(factory)
    execution = _coordinator(factory, config, git, approvals)

    async def pause(_):
        entered.set()
        await asyncio.Event().wait()

    git.get_default_branch.side_effect = pause

    async def start():
        if execution is not None:
            return await execution.start_task_link("project", root.id)
        async with factory() as session:
            result = await _recipe(session, config, git, approvals).start_task_link("project", root.id)
            await session.commit()
            return result

    task = asyncio.create_task(start())
    await asyncio.wait_for(entered.wait(), 3)
    await _cancel(task)
    recovered = _coordinator(factory, config, _git(), approvals)
    await _startup(task_db, recovered, approvals)
    async with factory() as session:
        links = await _recipe(session, config, _git(), approvals).list_task_links("project")
        jobs = (await session.execute(select(JobRow.id))).scalars().all()
    assert jobs == []
    assert links[0].state == TaskLinkState.ready, f"Canceled root remains {links[0].state}"


async def test_process_loss_claim_is_recovered(task_db):
    factory, config, engine, path = task_db
    root = (await _seed(task_db))[0]
    code = """
import asyncio, os, sys
from pathlib import Path
from backend.persistence.database import create_engine, create_session_factory
from backend.persistence.task_link_repo import TaskLinkRepository
async def crash():
    engine = create_engine(Path(sys.argv[1]))
    async with create_session_factory(engine)() as session:
        claim = await TaskLinkRepository(session).claim_ready_and_commit(sys.argv[2])
        assert claim is not None
        os._exit(23)
asyncio.run(crash())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "--no-sync", "python", "-c", code, str(path / "tasks.db"), root.id],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 23, result.stderr
    await engine.dispose()
    approvals = ApprovalService(factory)
    execution = _coordinator(factory, config, _git(), approvals)
    await _startup(task_db, execution, approvals)
    async with factory() as session:
        links = await _recipe(session, config, _git(), approvals).list_task_links("project")
    assert links[0].state == TaskLinkState.ready, f"Process loss left {links[0].state}, job={links[0].job_id}"


async def test_completion_cannot_overwrite_a_live_start_claim(task_db):
    factory, config, _, _ = task_db
    links = await _seed(task_db, chain=True)
    entered, release = asyncio.Event(), asyncio.Event()
    git = _git()
    approvals = ApprovalService(factory)
    execution = _coordinator(factory, config, git, approvals)

    async def pause(_):
        entered.set()
        await release.wait()
        return "main"

    git.get_default_branch.side_effect = pause

    async def start():
        if execution is not None:
            return await execution.start_task_link("project", links[1].id)
        async with factory() as session:
            result = await _recipe(session, config, git, approvals).start_task_link("project", links[1].id)
            await session.commit()
            return result

    starter = asyncio.create_task(start())
    await asyncio.wait_for(entered.wait(), 3)
    other_git = git if execution is not None else _git()
    completion = asyncio.create_task(_completion(task_db, execution, other_git, approvals))
    # Give the competing completion time to reach the exclusion/claim boundary.
    await asyncio.sleep(0.1)
    release.set()
    results = await asyncio.wait_for(asyncio.gather(starter, completion, return_exceptions=True), 5)
    assert not isinstance(results[0], BaseException), f"Live start lost ownership: {results[0]!r}"
    preparations = git.get_default_branch.await_count
    if other_git is not git:
        preparations += other_git.get_default_branch.await_count
    assert preparations == 1, f"Completion caused {preparations} preparations"
    async with factory() as session:
        linked = await TaskLinkRepository(session).get(links[1].id)
        jobs = (await session.execute(select(JobRow.id))).scalars().all()
    assert linked is not None and linked.job_id is not None
    assert len(jobs) == 2
