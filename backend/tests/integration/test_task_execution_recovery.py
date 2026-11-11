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


# Append after the parent's five critical tests. No new module-level imports.


def _execution_for_behavior(task_db, *, git=None, approvals=None, tracker=None):
    from backend.services.task_execution_service import TaskExecutionService

    factory, config, _, _ = task_db
    return TaskExecutionService(
        factory,
        config,
        git_service=git if git is not None else _git(),
        approval_service=approvals,
        tracker_write_service=tracker,
    )


async def _behavior_graph(task_db, entries):
    """Seed only input rows; production services perform every tested transition."""
    factory, _, _, path = task_db
    async with factory() as session:
        links = await TaskLinkRepository(session).upsert_many(
            "project",
            [
                {
                    "repo_path": str(path),
                    "story_node_id": node,
                    "depends_on": [f"{path}::{dep}" for dep in dependencies],
                }
                for node, dependencies in entries
            ],
        )
        await session.commit()
    return {link.story_node_id: link for link in links}


async def _behavior_job(
    task_db,
    job_id,
    *,
    task_id=None,
    state="completed",
    resolution="merged",
    session_count=1,
):
    factory, _, _, path = task_db
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            JobRow(
                id=job_id,
                project_id="project",
                repo=str(path),
                prompt=f"Seed {job_id}",
                state=state,
                resolution=resolution,
                base_ref="main",
                created_at=now,
                updated_at=now,
                completed_at=now if state in {"completed", "failed", "canceled"} else None,
                session_count=session_count,
                artifact_collection_status="completed",
                artifact_collection_session_count=session_count,
            )
        )
        await session.flush()
        if task_id is not None:
            assert await TaskLinkRepository(session).set_job_id(task_id, job_id) is not None
        await session.commit()


async def _change_behavior_job(task_db, job_id, **values):
    factory, _, _, _ = task_db
    async with factory() as session:
        row = await session.get(JobRow, job_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        if values.get("state") in {"completed", "failed", "canceled"}:
            row.completed_at = row.updated_at
        await session.commit()


async def _behavior_evidence(task_db):
    factory, _, _, _ = task_db
    async with factory() as session:
        tasks = {link.id: link for link in await TaskLinkRepository(session).list_by_project("project")}
        jobs = await JobRepository(session).list_all()
    return tasks, {job.id: job for job in jobs}


async def _attach_behavior_chat(task_db, task_id, chat_id="behavior-chat"):
    factory, _, _, _ = task_db
    now = datetime.now(UTC)
    async with factory() as session:
        await ChatRepository(session).create(
            Chat(
                id=chat_id,
                project_id="project",
                title="Gate this chain",
                created_at=now,
                last_message_at=now,
                status="open",
                task_link_id=task_id,
            )
        )
        await session.commit()


@pytest.mark.parametrize(
    ("state", "resolution", "expected_count"),
    [
        ("completed", "merged", 1),
        ("completed", "pr_created", 1),
        ("completed", "discarded", 0),
        ("review", None, 0),
        ("failed", None, 0),
        ("canceled", None, 0),
    ],
)
async def test_dependency_completion_requires_persisted_success(task_db, state, resolution, expected_count):
    from backend.models.domain import JobState

    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True)
    await _change_behavior_job(task_db, "predecessor", state=state, resolution=resolution)
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    spawned = await execution.handle_job_completed(
        "predecessor",
        resolution=resolution if resolution is not None else "merged",
    )

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert len(spawned) == expected_count
    assert len(jobs) == 1 + expected_count
    if expected_count:
        assert tasks[links[1].id].job_id == spawned[0].id
        assert jobs[spawned[0].id].state == JobState.preparing
        assert jobs[spawned[0].id].parent_job_id == "predecessor"
    else:
        assert tasks[links[1].id].job_id is None
        if resolution == "discarded":
            assert tasks[links[0].id].state == TaskLinkState.failed


async def test_unlinked_completed_job_does_not_create_task_jobs(task_db):
    factory, _, engine, _ = task_db
    links = await _behavior_graph(task_db, [("root", []), ("next", ["root"])])
    await _behavior_job(task_db, "unrelated")
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    assert await execution.handle_job_completed("unrelated", resolution="merged") == []

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert set(jobs) == {"unrelated"}
    assert all(tasks[link.id].job_id is None for link in links.values())


@pytest.mark.parametrize("terminal", ["failed", "canceled"])
async def test_failure_projects_task_without_satisfying_dependents(task_db, terminal):
    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True)
    await _change_behavior_job(task_db, "predecessor", state=terminal, resolution=None)
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    await execution.handle_job_failed("predecessor")
    listed = await execution.list_task_links("project")
    assert next(link for link in listed if link.id == links[1].id).state == TaskLinkState.waiting

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links[0].id].state == TaskLinkState.failed
    assert tasks[links[1].id].job_id is None
    assert set(jobs) == {"predecessor"}


async def test_join_waits_for_every_dependency_and_replay_is_idempotent(task_db):
    factory, _, engine, _ = task_db
    links = await _behavior_graph(task_db, [("left", []), ("right", []), ("join", ["left", "right"])])
    await _behavior_job(task_db, "left-job", task_id=links["left"].id)
    await _behavior_job(task_db, "right-job", task_id=links["right"].id, state="review", resolution=None)
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    assert await execution.handle_job_completed("left-job", resolution="merged") == []
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links["join"].id].job_id is None
    assert len(jobs) == 2
    await _change_behavior_job(task_db, "right-job", state="completed", resolution="pr_created")
    spawned = await execution.handle_job_completed("right-job", resolution="pr_created")
    assert len(spawned) == 1
    assert await execution.handle_job_completed("left-job", resolution="merged") == []
    assert await execution.handle_job_completed("right-job", resolution="pr_created") == []

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links["join"].id].job_id == spawned[0].id
    assert jobs[spawned[0].id].parent_job_id == "right-job"
    assert len(jobs) == 3


async def test_concurrent_completion_notifications_attach_one_job(task_db):
    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True)
    git = _git()
    execution = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))

    results = await asyncio.gather(
        execution.handle_job_completed("predecessor", resolution="merged"),
        execution.handle_job_completed("predecessor", resolution="merged"),
    )

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    returned = [job for result in results for job in result]
    assert len(returned) == 1
    assert tasks[links[1].id].job_id == returned[0].id
    assert len(jobs) == 2
    git.get_default_branch.assert_awaited_once()
    git.create_worktree.assert_not_awaited()


async def test_manual_successor_uses_exact_prompt_override(task_db):
    import json

    from backend.models.db import TaskLinkRow

    factory, _, engine, path = task_db
    root = (await _behavior_graph(task_db, [("root", [])]))["root"]
    await _behavior_job(task_db, "parent-job", task_id=root.id)
    prompt = "Implement only PAY-42.\nPreserve the exact requested wording."
    async with factory() as session:
        manual = await TaskLinkRepository(session).create_manual(
            project_id="project",
            repo_path=str(path),
            tracker_ticket_ref="PAY-42",
            prompt_override=prompt,
        )
        row = await session.get(TaskLinkRow, manual.id)
        row.depends_on = json.dumps([f"{path}::root"])
        row.chain_root_id = root.id
        row.state = TaskLinkState.waiting
        await session.commit()
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    spawned = await execution.handle_job_completed("parent-job", resolution="merged")

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert len(spawned) == 1
    assert tasks[manual.id].job_id == spawned[0].id
    assert tasks[manual.id].story_node_id is None
    assert jobs[spawned[0].id].prompt == prompt
    assert jobs[spawned[0].id].parent_job_id == "parent-job"


async def test_invalid_successor_does_not_block_valid_sibling(task_db):
    from backend.models.db import TaskLinkRow

    factory, _, engine, path = task_db
    links = await _behavior_graph(task_db, [("root", []), ("invalid", ["root"]), ("valid", ["root"])])
    await _behavior_job(task_db, "parent-job", task_id=links["root"].id)
    async with factory() as session:
        row = await session.get(TaskLinkRow, links["invalid"].id)
        row.repo_path = str(path / "not-a-project-member")
        await session.commit()
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    spawned = await execution.handle_job_completed("parent-job", resolution="merged")

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert len(spawned) == 1
    assert tasks[links["valid"].id].job_id == spawned[0].id
    assert tasks[links["invalid"].id].job_id is None
    assert tasks[links["invalid"].id].state == TaskLinkState.ready
    assert len(jobs) == 2


@pytest.mark.parametrize("persist_gate", [False, True])
async def test_direct_dependent_start_cannot_bypass_chain_gate(task_db, persist_gate):
    from backend.models.domain import StateConflictError

    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True, gated=True)
    approvals = ApprovalService(factory)
    execution = _execution_for_behavior(task_db, approvals=approvals)
    if persist_gate:
        assert await execution.handle_job_completed("predecessor", resolution="merged") == []

    with pytest.raises(StateConflictError):
        await execution.start_task_link("project", links[1].id)

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links[1].id].job_id is None
    assert set(jobs) == {"predecessor"}
    assert len(await approvals.list_pending("predecessor")) == int(persist_gate)


async def test_pending_legacy_root_gate_blocks_direct_start(task_db):
    from backend.models.domain import StateConflictError

    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    await _behavior_job(task_db, "legacy-owner")
    approvals = ApprovalService(factory)
    await approvals.create_request(
        "legacy-owner",
        "Legacy root gate",
        f"spawn_task:{root.id}",
        requires_explicit_approval=True,
    )
    execution = _execution_for_behavior(task_db, approvals=approvals)

    with pytest.raises(StateConflictError):
        await execution.start_task_link("project", root.id)

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id is None
    assert set(jobs) == {"legacy-owner"}
    assert len(await approvals.list_pending()) == 1


async def test_gated_chain_root_remains_manually_startable_once(task_db):
    from backend.models.domain import StateConflictError

    factory, _, engine, _ = task_db
    root = (await _seed(task_db, gated=True))[0]
    approvals = ApprovalService(factory)
    execution = _execution_for_behavior(task_db, approvals=approvals)

    attached, job = await execution.start_task_link("project", root.id)
    with pytest.raises(StateConflictError):
        await execution.start_task_link("project", root.id)

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert attached.job_id == job.id == tasks[root.id].job_id
    assert set(jobs) == {job.id}
    assert await approvals.list_pending() == []


async def test_preflight_has_no_durable_claim_and_competing_direct_start_is_excluded(task_db):
    from backend.models.domain import StateConflictError

    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    entered, release = asyncio.Event(), asyncio.Event()
    git = _git()

    async def paused_default_branch(_):
        entered.set()
        await release.wait()
        return "main"

    git.get_default_branch.side_effect = paused_default_branch
    execution = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))
    first = asyncio.create_task(execution.start_task_link("project", root.id))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        tasks, jobs = await _behavior_evidence(task_db)
        assert tasks[root.id].state == TaskLinkState.ready
        assert tasks[root.id].job_id is None
        assert jobs == {}
        async with database.serialized_write(factory) as session:
            await ProjectRepository(session).update("project", name="Unrelated writer progressed")
        second = asyncio.create_task(execution.start_task_link("project", root.id))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 5)
        assert not isinstance(results[0], BaseException)
        assert isinstance(results[1], StateConflictError)
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                await _cancel(task)
    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert len(jobs) == 1
    assert tasks[root.id].job_id in jobs
    git.get_default_branch.assert_awaited_once()
    git.create_worktree.assert_not_awaited()


async def test_failed_preflight_leaves_root_retryable_without_orphan_job(task_db):
    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    git = _git()
    git.get_default_branch.side_effect = RuntimeError("controlled preflight failure")
    execution = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))

    with pytest.raises(RuntimeError, match="controlled preflight failure"):
        await execution.start_task_link("project", root.id)
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].state == TaskLinkState.ready
    assert tasks[root.id].job_id is None
    assert jobs == {}
    git.get_default_branch.side_effect = None
    git.get_default_branch.return_value = "main"
    _, created = await execution.start_task_link("project", root.id)

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id == created.id
    assert set(jobs) == {created.id}


async def test_failed_attachment_rolls_back_both_job_and_claim(task_db, monkeypatch):
    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    original = TaskLinkRepository.attach_claimed_job

    async def fail_after_attachment(repo, *args, **kwargs):
        attached = await original(repo, *args, **kwargs)
        assert attached is not None
        raise RuntimeError("controlled failure before attachment commit")

    monkeypatch.setattr(TaskLinkRepository, "attach_claimed_job", fail_after_attachment)
    execution = _execution_for_behavior(task_db, approvals=ApprovalService(factory))

    with pytest.raises(RuntimeError, match="controlled failure before attachment commit"):
        await execution.start_task_link("project", root.id)

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].state == TaskLinkState.ready
    assert tasks[root.id].job_id is None
    assert jobs == {}


async def test_gated_completion_is_durable_idempotent_and_requires_actual_approval(task_db):
    from backend.models.domain import ApprovalResolution
    from backend.services.task_execution_service import parse_spawn_action

    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True, gated=True)
    approvals = ApprovalService(factory)
    execution = _execution_for_behavior(task_db, approvals=approvals)

    assert await execution.handle_job_completed("predecessor", resolution="merged") == []
    assert await execution.handle_job_completed("predecessor", resolution="merged") == []
    assert await execution.spawn_approved_task_link(links[1].id, parent_job_id="predecessor") is None
    pending = await approvals.list_pending("predecessor")
    assert len(pending) == 1
    assert pending[0].requires_explicit_approval is True
    assert parse_spawn_action(pending[0].proposed_action) == {
        "schemaVersion": 1,
        "kind": "spawn_task",
        "taskLinkId": links[1].id,
        "predecessorJobId": "predecessor",
        "sessionCount": 1,
    }
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links[1].id].state == TaskLinkState.ready
    assert tasks[links[1].id].job_id is None
    assert set(jobs) == {"predecessor"}
    await approvals.resolve(pending[0].id, ApprovalResolution.approved)
    created = await execution.spawn_approved_task_link(links[1].id, parent_job_id="predecessor")
    assert created is not None
    assert await execution.spawn_approved_task_link(links[1].id, parent_job_id="predecessor") is None
    assert await execution.spawn_approved_task_link("unknown-task", parent_job_id="predecessor") is None

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links[1].id].job_id == created.id
    assert len(jobs) == 2
    decisions = await approvals.list_for_job("predecessor")
    assert len(decisions) == 1
    assert decisions[0].resolution == ApprovalResolution.approved


async def test_gate_follows_persisted_chain_root_without_gating_unrelated_chain(task_db):
    from backend.models.domain import ApprovalResolution
    from backend.services.task_execution_service import parse_spawn_action

    factory, _, engine, _ = task_db
    links = await _behavior_graph(
        task_db,
        [("root", []), ("middle", ["root"]), ("leaf", ["middle"]), ("other", []), ("other-next", ["other"])],
    )
    await _behavior_job(task_db, "root-job", task_id=links["root"].id)
    await _behavior_job(task_db, "other-job", task_id=links["other"].id)
    await _attach_behavior_chat(task_db, links["root"].id)
    approvals = ApprovalService(factory)
    execution = _execution_for_behavior(task_db, approvals=approvals)

    assert await execution.handle_job_completed("root-job", resolution="merged") == []
    unrelated = await execution.handle_job_completed("other-job", resolution="merged")
    assert len(unrelated) == 1
    pending = await approvals.list_pending()
    assert len(pending) == 1
    await approvals.resolve(pending[0].id, ApprovalResolution.approved)
    middle = await execution.spawn_approved_task_link(links["middle"].id, parent_job_id="root-job")
    assert middle is not None
    await _change_behavior_job(task_db, middle.id, state="completed", resolution="merged")
    assert await execution.handle_job_completed(middle.id, resolution="merged") == []
    leaf_gate = await approvals.list_pending(middle.id)
    assert len(leaf_gate) == 1
    assert parse_spawn_action(leaf_gate[0].proposed_action)["taskLinkId"] == links["leaf"].id

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[links["leaf"].id].job_id is None
    assert tasks[links["other-next"].id].job_id == unrelated[0].id
    assert tasks[links["middle"].id].chain_root_id == tasks[links["root"].id].chain_root_id
    assert len(jobs) == 4


async def test_rejection_survives_replay_but_new_completion_attempt_can_request_again(task_db):
    from backend.models.domain import ApprovalResolution
    from backend.services.task_execution_service import parse_spawn_action

    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True, gated=True)
    approvals = ApprovalService(factory)
    execution = _execution_for_behavior(task_db, approvals=approvals)
    await execution.handle_job_completed("predecessor", resolution="merged")
    rejected = (await approvals.list_pending("predecessor"))[0]
    await approvals.resolve(rejected.id, ApprovalResolution.rejected)

    assert await execution.handle_job_completed("predecessor", resolution="merged") == []
    await _execution_for_behavior(task_db, approvals=ApprovalService(factory)).recover_on_startup()
    assert await execution.spawn_approved_task_link(links[1].id, parent_job_id="predecessor") is None
    assert len(await approvals.list_for_job("predecessor")) == 1
    assert await approvals.list_pending() == []
    await _change_behavior_job(
        task_db,
        "predecessor",
        state="completed",
        resolution="merged",
        session_count=2,
    )
    assert await execution.handle_job_completed("predecessor", resolution="merged") == []

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    decisions = await approvals.list_for_job("predecessor")
    assert len(decisions) == 2
    assert (await approvals.get(rejected.id)).resolution == ApprovalResolution.rejected
    pending = await approvals.list_pending("predecessor")
    assert len(pending) == 1
    assert parse_spawn_action(pending[0].proposed_action)["sessionCount"] == 2
    assert tasks[links[1].id].job_id is None
    assert set(jobs) == {"predecessor"}


@pytest.mark.parametrize("decision", ["ungated", "approved", "pending", "rejected"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_startup_recovers_only_eligible_authorized_successors_without_starting_agents(task_db, decision, legacy):
    import json

    from backend.models.domain import ApprovalResolution, JobState

    factory, _, engine, _ = task_db
    links = await _seed(task_db, chain=True, gated=decision != "ungated")
    approvals = ApprovalService(factory)
    if decision != "ungated":
        action = (
            f"spawn_task:{links[1].id}"
            if legacy
            else json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "spawn_task",
                    "taskLinkId": links[1].id,
                    "predecessorJobId": "predecessor",
                    "sessionCount": 1,
                }
            )
        )
        approval = await approvals.create_request(
            "predecessor",
            "Persisted gate",
            action,
            requires_explicit_approval=True,
        )
        if decision != "pending":
            await approvals.resolve(approval.id, ApprovalResolution(decision))
    git = _git()
    recovered = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))

    await recovered.recover_on_startup()
    await recovered.recover_on_startup()

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    if decision in {"ungated", "approved"}:
        assert len(jobs) == 2
        child = jobs[tasks[links[1].id].job_id]
        assert child.state == JobState.preparing
        assert child.parent_job_id == "predecessor"
        git.get_default_branch.assert_awaited_once()
    else:
        assert set(jobs) == {"predecessor"}
        assert tasks[links[1].id].job_id is None
        git.get_default_branch.assert_not_awaited()
    git.create_worktree.assert_not_awaited()


@pytest.mark.parametrize("gated", [False, True])
async def test_missing_approval_service_allows_only_ungated_successors(task_db, gated):
    _, _, engine, _ = task_db
    links = await _seed(task_db, chain=True, gated=gated)
    execution = _execution_for_behavior(task_db)

    spawned = await execution.handle_job_completed("predecessor", resolution="merged")

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    if gated:
        assert spawned == []
        assert tasks[links[1].id].job_id is None
        assert set(jobs) == {"predecessor"}
    else:
        assert len(spawned) == 1
        assert tasks[links[1].id].job_id == spawned[0].id
        assert len(jobs) == 2


async def _seed_tracker_output(task_db, *, manual, case):
    import json

    from backend.models.db import CredentialRow, TaskLinkRow
    from backend.persistence.tracker_link_repo import TrackerLinkRepository

    factory, _, _, path = task_db
    if manual:
        async with factory() as session:
            task = await TaskLinkRepository(session).create_manual(
                project_id="project",
                repo_path=str(path),
                tracker_ticket_ref="owner/repo#7",
                prompt_override="Manual ticket task",
            )
            await session.commit()
    else:
        task = (await _behavior_graph(task_db, [("output-story", [])]))["output-story"]
    await _behavior_job(task_db, "output-job", task_id=task.id)
    async with factory() as session:
        now = datetime.now(UTC)
        session.add(
            CredentialRow(
                id="output-credential",
                provider="github",
                label="Controlled test credential",
                base_url="https://api.github.com",
                encrypted_secret="not-used-no-worker",
                created_at=now.isoformat(),
            )
        )
        await ProjectRepository(session).create("other-project", "Other project", [])
        await session.flush()
        repo = TrackerLinkRepository(session)
        await repo.create(
            link_id="chosen-link",
            project_id="project",
            credential_id="output-credential",
            external_ref="owner/1",
        )
        await repo.create(
            link_id="alternate-link",
            project_id="project",
            credential_id="output-credential",
            external_ref="owner/2",
        )
        await repo.create(
            link_id="foreign-link",
            project_id="other-project",
            credential_id="output-credential",
            external_ref="owner/3",
        )
        row = await session.get(TaskLinkRow, task.id)
        row.output_routes = json.dumps([] if case == "no-route" else ["tracker_write"])
        row.tracker_link_id = None if case == "no-link" else "foreign-link" if case == "foreign-link" else "chosen-link"
        row.tracker_ticket_ref = None if case == "no-ticket" else "owner/repo#7"
        await session.flush()
        if case == "detached":
            assert await repo.delete_for_project(project_id="project", link_id="chosen-link")
        await session.commit()
    return task


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize(
    "case",
    ["valid", "no-route", "no-ticket", "no-link", "detached", "foreign-link", "no-service"],
)
async def test_tracker_output_uses_explicit_target_and_persists_one_intent_per_attempt(
    task_db,
    monkeypatch,
    manual,
    case,
):
    import sqlite3

    from backend.models.db import TrackerOperationRow
    from backend.services.tracker_write_service import TrackerWriteService

    factory, _, engine, path = task_db
    task = await _seed_tracker_output(task_db, manual=manual, case=case)
    approvals = ApprovalService(factory)
    tracker = TrackerWriteService(approvals, session_factory=factory)
    wake_snapshots = []

    def observe_committed_intent():
        # Control only worker scheduling. The real enqueue/persistence runs above
        # this boundary; a separate SQLite connection proves commit-before-wake.
        with sqlite3.connect(path / "tasks.db") as conn:
            state = conn.execute("SELECT state FROM task_links WHERE id = ?", (task.id,)).fetchone()[0]
            count = conn.execute("SELECT count(*) FROM tracker_operations").fetchone()[0]
        wake_snapshots.append((state, count))

    monkeypatch.setattr(tracker, "wake", observe_committed_intent)
    execution = _execution_for_behavior(
        task_db,
        approvals=approvals,
        tracker=None if case == "no-service" else tracker,
    )
    try:
        assert await execution.handle_job_completed("output-job", resolution="merged") == []
        assert await execution.handle_job_completed("output-job", resolution="merged") == []
        await engine.dispose()
        tasks, jobs = await _behavior_evidence(task_db)
        assert tasks[task.id].state == TaskLinkState.completed
        assert set(jobs) == {"output-job"}
        async with factory() as session:
            rows = list((await session.execute(select(TrackerOperationRow))).scalars())
            if case == "valid":
                assert len(rows) == 1
                operation = rows[0]
                assert operation.state == "awaiting_approval"
                assert operation.project_ref == "project"
                assert operation.job_ref == "output-job"
                assert operation.task_link_ref == task.id
                assert operation.tracker_link_ref == "chosen-link"
                assert operation.ticket_ref == "owner/repo#7"
                assert operation.value == f"Task '{task.story_node_id or task.id}' completed."
                assert operation.action == operation.target_kind == "comment"
                assert wake_snapshots and all(value == ("completed", 1) for value in wake_snapshots)
                first_source_key = operation.source_key
            else:
                assert rows == []
                assert wake_snapshots == []
        assert await approvals.list_pending() == []
        if case == "valid":
            await _change_behavior_job(
                task_db,
                "output-job",
                state="completed",
                resolution="merged",
                session_count=2,
            )
            assert await execution.handle_job_completed("output-job", resolution="merged") == []
            await engine.dispose()
            async with factory() as session:
                rows = list((await session.execute(select(TrackerOperationRow))).scalars())
                assert len(rows) == 2
                assert len({row.source_key for row in rows}) == 2
                assert first_source_key in {row.source_key for row in rows}
                assert all(row.ticket_ref == "owner/repo#7" for row in rows)
            assert wake_snapshots[-1] == ("completed", 2)
    finally:
        await tracker.close()


async def test_startup_does_not_backfill_tracker_intent_for_old_completion(task_db, monkeypatch):
    import json

    from backend.models.db import TaskLinkRow, TrackerOperationRow
    from backend.services.tracker_write_service import TrackerWriteService

    factory, _, engine, path = task_db
    root = await _seed_tracker_output(task_db, manual=False, case="valid")
    async with factory() as session:
        child = (
            await TaskLinkRepository(session).upsert_many(
                "project",
                [
                    {
                        "repo_path": str(path),
                        "story_node_id": "after-output",
                        "depends_on": [f"{path}::{root.story_node_id}"],
                    }
                ],
            )
        )[0]
        row = await session.get(TaskLinkRow, root.id)
        row.output_routes = json.dumps(["tracker_write"])
        await session.commit()
    approvals = ApprovalService(factory)
    tracker = TrackerWriteService(approvals, session_factory=factory)
    wake = MagicMock()
    monkeypatch.setattr(tracker, "wake", wake)
    execution = _execution_for_behavior(task_db, approvals=approvals, tracker=tracker)
    try:
        await execution.recover_on_startup()

        await engine.dispose()
        tasks, jobs = await _behavior_evidence(task_db)
        assert tasks[child.id].job_id is not None
        assert len(jobs) == 2
        async with factory() as session:
            assert list((await session.execute(select(TrackerOperationRow.id))).scalars()) == []
        wake.assert_not_called()
    finally:
        await tracker.close()


@pytest.mark.parametrize("continuation", ["approved-call", "startup"])
async def test_approved_legacy_root_without_linked_parent_recovers_exact_association_once(task_db, continuation):
    from backend.models.domain import ApprovalResolution, JobState

    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    await _behavior_job(task_db, "legacy-root-owner")
    approvals = ApprovalService(factory)
    action = f"spawn_task:{root.id}"
    approval = await approvals.create_request("legacy-root-owner", "Retained legacy root start", action)
    await approvals.resolve(approval.id, ApprovalResolution.approved)
    await engine.dispose()
    git = _git()
    execution = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))

    if continuation == "approved-call":
        results = await asyncio.gather(
            execution.spawn_approved_task_link(root.id, parent_job_id="legacy-root-owner"),
            execution.spawn_approved_task_link(root.id, parent_job_id="legacy-root-owner"),
        )
        assert sum(job is not None for job in results) == 1
    else:
        await execution.recover_on_startup()
        await execution.recover_on_startup()

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert len(jobs) == 2
    assert tasks[root.id].depends_on == []
    assert tasks[root.id].chain_root_id == root.chain_root_id
    created = jobs[tasks[root.id].job_id]
    assert created.state == JobState.preparing
    assert created.parent_job_id == "legacy-root-owner"
    async with factory() as session:
        assert await TaskLinkRepository(session).get_by_job_id("legacy-root-owner") is None
    retained = await approvals.get(approval.id)
    assert retained.proposed_action == action
    assert retained.resolution == ApprovalResolution.approved
    assert len(await approvals.list_for_job("legacy-root-owner")) == 1
    git.get_default_branch.assert_awaited_once()
    git.create_worktree.assert_not_awaited()


@pytest.mark.parametrize("continuation", ["approved-call", "startup"])
@pytest.mark.parametrize("case", ["pending", "rejected", "foreign-project", "wrong-task", "versioned-root"])
async def test_legacy_root_requires_matching_scoped_persisted_approval(task_db, continuation, case):
    import json

    from backend.models.domain import ApprovalResolution

    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    await _behavior_job(task_db, "legacy-root-owner")
    if case == "foreign-project":
        async with factory() as session:
            await ProjectRepository(session).create("foreign-project", "Not the task's owner", [])
            owner = await session.get(JobRow, "legacy-root-owner")
            owner.project_id = "foreign-project"
            await session.commit()
    action = f"spawn_task:{'not-this-task' if case == 'wrong-task' else root.id}"
    if case == "versioned-root":
        action = json.dumps(
            {
                "schemaVersion": 1,
                "kind": "spawn_task",
                "taskLinkId": root.id,
                "predecessorJobId": "legacy-root-owner",
                "sessionCount": 1,
            }
        )
    approvals = ApprovalService(factory)
    approval = await approvals.create_request("legacy-root-owner", "Legacy boundary check", action)
    if case != "pending":
        await approvals.resolve(
            approval.id,
            ApprovalResolution.rejected if case == "rejected" else ApprovalResolution.approved,
        )
    await engine.dispose()
    git = _git()
    execution = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))

    if continuation == "approved-call":
        assert await execution.spawn_approved_task_link(root.id, parent_job_id="legacy-root-owner") is None
    else:
        await execution.recover_on_startup()

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id is None
    assert tasks[root.id].depends_on == []
    assert set(jobs) == {"legacy-root-owner"}
    assert (await approvals.get(approval.id)).proposed_action == action
    assert len(await approvals.list_for_job("legacy-root-owner")) == 1
    git.get_default_branch.assert_not_awaited()
    git.create_worktree.assert_not_awaited()


async def test_legacy_root_cannot_borrow_approval_from_another_same_project_job(task_db):
    from backend.models.domain import ApprovalResolution

    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    await _behavior_job(task_db, "actual-owner")
    await _behavior_job(task_db, "other-owner")
    approvals = ApprovalService(factory)
    approval = await approvals.create_request("actual-owner", "Exact owner association", f"spawn_task:{root.id}")
    await approvals.resolve(approval.id, ApprovalResolution.approved)
    execution = _execution_for_behavior(task_db, approvals=approvals)

    assert await execution.spawn_approved_task_link(root.id, parent_job_id="other-owner") is None
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id is None
    assert set(jobs) == {"actual-owner", "other-owner"}
    await execution.recover_on_startup()

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert len(jobs) == 3
    assert jobs[tasks[root.id].job_id].parent_job_id == "actual-owner"
    assert tasks[root.id].depends_on == []


async def _crash_new_atomic_start(task_db, task_id, phase):
    import json

    _, _, engine, path = task_db
    await engine.dispose()
    code = r"""
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import event

import backend
from backend.config import CPLConfig
from backend.persistence.database import create_engine, create_session_factory
from backend.persistence.job_repo import JobRepository
from backend.persistence.task_link_repo import TaskLinkRepository
from backend.services.job.approval_service import ApprovalService
from backend.services.task_execution_service import TaskExecutionService

assert Path(backend.__file__).resolve().parents[1] == Path.cwd().resolve()
db_path, repo_path, task_id, phase = sys.argv[1:]
exit_codes = {"preflight": 71, "after_insert": 72, "after_commit": 73}

def crash(**evidence):
    print("TASK_START_CRASH:" + json.dumps({"phase": phase, **evidence}), flush=True)
    os._exit(exit_codes[phase])

async def main():
    engine = create_engine(Path(db_path))
    factory = create_session_factory(engine)

    class GitBoundary:
        async def get_default_branch(self, repo):
            assert repo == repo_path
            if phase == "preflight":
                async with factory() as session:
                    task = await TaskLinkRepository(session).get(task_id)
                    job_ids = sorted(await JobRepository(session).list_ids())
                    assert task is not None
                    assert task.state == "ready" and task.job_id is None and job_ids == []
                    crash(task_state=task.state, task_job_id=task.job_id, job_ids=job_ids)
            return "main"

        def list_worktree_names(self, repo):
            assert repo == repo_path
            return set()

        async def list_branches(self, repo):
            assert repo == repo_path
            return set()

        async def create_worktree(self, **kwargs):
            raise AssertionError("Task acceptance must not set up a workspace")

    if phase == "after_insert":
        def exit_after_real_job_insert(connection, cursor, statement, parameters, context, executemany):
            if not context.isinsert or context.compiled.statement.table.name != "jobs":
                return
            # Observe the real, still-uncommitted INSERT and earlier claim.
            # Nothing replaces JobRepository.create or TaskLink attachment.
            task_state, task_job_id = connection.exec_driver_sql(
                "SELECT state, job_id FROM task_links WHERE id = ?", (task_id,),
            ).one()
            job_ids = list(connection.exec_driver_sql("SELECT id FROM jobs").scalars())
            assert task_state == "starting" and task_job_id is None and len(job_ids) == 1
            crash(task_state=task_state, task_job_id=task_job_id, job_ids=job_ids)

        event.listen(engine.sync_engine, "after_cursor_execute", exit_after_real_job_insert)

    execution = TaskExecutionService(
        factory, CPLConfig(repos=[repo_path]),
        git_service=GitBoundary(), approval_service=ApprovalService(factory),
    )
    attached, job = await execution.start_task_link("project", task_id)
    assert phase == "after_commit", "Requested crash boundary was not reached"
    async with factory() as session:
        persisted = await JobRepository(session).get(job.id)
        task = await TaskLinkRepository(session).get(task_id)
        job_ids = sorted(await JobRepository(session).list_ids())
        assert persisted is not None and persisted.state == "preparing"
        assert persisted.worktree_path is None
        assert task is not None and task.job_id == attached.job_id == job.id
        assert job_ids == [job.id]
        crash(task_state=task.state, task_job_id=task.job_id, job_ids=job_ids)

asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "--no-sync", "python", "-c", code, str(path / "tasks.db"), str(path), task_id, phase],
        capture_output=True,
        text=True,
        timeout=25,
        cwd=str(Path.cwd()),
    )
    (path / f"atomic-crash-{phase}.json").write_text(
        json.dumps({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, indent=2),
        encoding="utf-8",
    )
    expected_exit = {"preflight": 71, "after_insert": 72, "after_commit": 73}[phase]
    assert result.returncode == expected_exit, (
        f"Child did not exit at {phase}: {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    markers = [
        line.removeprefix("TASK_START_CRASH:")
        for line in result.stdout.splitlines()
        if line.startswith("TASK_START_CRASH:")
    ]
    assert len(markers) == 1, result.stdout
    evidence = json.loads(markers[0])
    assert evidence["phase"] == phase
    await engine.dispose()
    return evidence


async def _assert_unaccepted_process_loss_is_retryable(task_db, phase):
    from backend.models.domain import JobState, StateConflictError

    factory, _, engine, _ = task_db
    root = (await _seed(task_db))[0]
    evidence = await _crash_new_atomic_start(task_db, root.id, phase)
    assert evidence["task_job_id"] is None
    if phase == "after_insert":
        assert evidence["task_state"] == "starting"
        assert len(evidence["job_ids"]) == 1
    else:
        assert evidence["task_state"] == "ready"
        assert evidence["job_ids"] == []

    # Check before startup repair: the new transaction itself must roll back.
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].state == TaskLinkState.ready
    assert tasks[root.id].job_id is None
    assert jobs == {}
    git = _git()
    execution = _execution_for_behavior(task_db, git=git, approvals=ApprovalService(factory))
    await execution.recover_on_startup()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].state == TaskLinkState.ready
    assert jobs == {}
    git.get_default_branch.assert_not_awaited()

    attached, retry_job = await execution.start_task_link("project", root.id)
    with pytest.raises(StateConflictError):
        await execution.start_task_link("project", root.id)

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert set(jobs) == {retry_job.id}
    assert tasks[root.id].job_id == attached.job_id == retry_job.id
    assert jobs[retry_job.id].state == JobState.preparing
    git.get_default_branch.assert_awaited_once()
    git.create_worktree.assert_not_awaited()


async def test_new_start_process_loss_during_read_only_preflight(task_db):
    await _assert_unaccepted_process_loss_is_retryable(task_db, "preflight")


async def test_new_start_process_loss_after_job_insert_before_attachment_commit(task_db):
    await _assert_unaccepted_process_loss_is_retryable(task_db, "after_insert")


async def test_new_start_process_loss_after_commit_is_recovered_by_runtime_once(task_db, monkeypatch):
    from backend.models.domain import JobState

    factory, config, engine, _ = task_db
    root = (await _seed(task_db))[0]
    evidence = await _crash_new_atomic_start(task_db, root.id, "after_commit")
    original_job_id = evidence["task_job_id"]
    assert original_job_id is not None
    assert evidence["job_ids"] == [original_job_id]
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id == original_job_id
    assert set(jobs) == {original_job_id}
    assert jobs[original_job_id].state == JobState.preparing

    git = _git()
    approvals = ApprovalService(factory)
    execution = _execution_for_behavior(task_db, git=git, approvals=approvals)
    await execution.recover_on_startup()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id == original_job_id
    assert set(jobs) == {original_job_id}
    assert jobs[original_job_id].state == JobState.preparing
    git.get_default_branch.assert_not_awaited()

    runtime = RuntimeService(
        factory, EventBus(), AdapterRegistry(), config, approval_service=approvals, git_service=git,
    )
    setup_finished = asyncio.Event()
    setup_tasks = []
    observed_jobs = []

    async def controlled_setup(job):
        setup_tasks.append(asyncio.current_task())
        try:
            tasks, jobs = await _behavior_evidence(task_db)
            assert job.id == original_job_id
            assert set(jobs) == {original_job_id}
            assert jobs[job.id].state == JobState.preparing
            assert tasks[root.id].job_id == job.id
            observed_jobs.append(job.id)
        finally:
            setup_finished.set()

    setup = AsyncMock(side_effect=controlled_setup)
    monkeypatch.setattr(runtime, "setup_and_start", setup)
    try:
        await runtime.recover_on_startup()
        await asyncio.wait_for(setup_finished.wait(), 3)
        await asyncio.gather(*setup_tasks)
        setup.assert_awaited_once()
        assert observed_jobs == [original_job_id]
    finally:
        for task in setup_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*setup_tasks, return_exceptions=True)
        await runtime.shutdown()

    await engine.dispose()
    tasks, jobs = await _behavior_evidence(task_db)
    assert tasks[root.id].job_id == original_job_id
    assert set(jobs) == {original_job_id}
    assert jobs[original_job_id].worktree_path is None
    git.create_worktree.assert_not_awaited()
