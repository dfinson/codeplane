"""Shared startup ordering without requiring the recipe executor or policy catalogue."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.domain import SIDECAR_ARBITER
from backend.persistence.job_repo import JobRepository
from backend.tests.unit.test_runtime_service import _create_db_job, _make_job
from backend.tests.unit.test_runtime_service import adapter as adapter
from backend.tests.unit.test_runtime_service import config as config
from backend.tests.unit.test_runtime_service import engine as engine
from backend.tests.unit.test_runtime_service import event_bus as event_bus
from backend.tests.unit.test_runtime_service import runtime as runtime
from backend.tests.unit.test_runtime_service import session_factory as session_factory


def sidecars(runtime, phases):
    manager = MagicMock()
    manager.open.side_effect = lambda *args, **kwargs: phases.append("builtin")
    runtime._sidecar_sessions = manager
    runtime._sidecar_dispatcher = MagicMock(
        run_postflight=AsyncMock(),
        deactivate=AsyncMock(),
    )
    runtime._activate_custom_sidecars = AsyncMock(side_effect=lambda job: phases.append("preflight"))
    runtime._setup_action_policy = AsyncMock(side_effect=lambda *args, **kwargs: phases.append("policy"))
    return manager


async def test_policy_precedes_all_sidecar_and_scout_startup(runtime, session_factory, config):
    phases: list[str] = []
    sidecars(runtime, phases)
    runtime._preflight_curator = object()

    async def scout(job, session_config):
        phases.append("scout")
        return session_config

    runtime._run_preflight_curator = AsyncMock(side_effect=scout)
    job = _make_job(repo=config.repos[0])
    await _create_db_job(session_factory, job)
    await runtime.start_or_enqueue(job)
    await asyncio.wait_for(runtime._tasks[job.id], timeout=5)
    assert phases == ["policy", "builtin", "builtin", "builtin", "preflight", "scout"]
    assert not runtime._legacy_runner_slots
    assert not runtime._starting_tasks


async def test_policy_failure_never_opens_a_sidecar(runtime, session_factory, config):
    manager = sidecars(runtime, [])
    runtime._setup_action_policy.side_effect = RuntimeError("Policy unavailable")
    job = _make_job(repo=config.repos[0])
    await _create_db_job(session_factory, job)
    with pytest.raises(RuntimeError, match="Policy unavailable"):
        await runtime.start_or_enqueue(job, session_token="test-token")
    manager.open.assert_not_called()
    runtime._activate_custom_sidecars.assert_not_awaited()
    assert not runtime._tasks and not runtime._starting_tasks
    assert job.id not in runtime._queued_session_tokens


@pytest.mark.parametrize("action", ["start", "cancel"])
async def test_queued_job_defers_sidecars_and_keeps_token_only_until_needed(runtime, session_factory, config, action):
    manager = sidecars(runtime, [])
    occupants = [asyncio.create_task(asyncio.Event().wait()) for _ in range(runtime.max_concurrent)]
    runtime._tasks.update({f"occupied-{index}": task for index, task in enumerate(occupants)})
    job = _make_job(job_id="queued-job", repo=config.repos[0])
    await _create_db_job(session_factory, job)
    try:
        await runtime.start_or_enqueue(job, session_token="test-token")
        manager.open.assert_not_called()
        runtime._activate_custom_sidecars.assert_not_awaited()
        assert runtime._queued_session_tokens[job.id] == "test-token"
        if action == "cancel":
            await runtime.cancel_job(job.id)
            assert job.id not in runtime._queued_session_tokens
    finally:
        for index, task in enumerate(occupants):
            task.cancel()
            runtime._tasks.pop(f"occupied-{index}", None)
        await asyncio.gather(*occupants, return_exceptions=True)
    if action == "start":
        await runtime._dequeue_next()
        await asyncio.wait_for(runtime._tasks[job.id], timeout=5)
        manager.open.assert_any_call(job.id, SIDECAR_ARBITER, token="test-token")
        assert job.id not in runtime._queued_session_tokens


async def test_concurrent_starts_reserve_before_the_database_await(runtime, session_factory, config, monkeypatch):
    job = _make_job(repo=config.repos[0])
    await _create_db_job(session_factory, job)
    original = JobRepository.claim_for_start
    entered, release = asyncio.Event(), asyncio.Event()
    claims = []

    async def delayed(repository, job_id):
        claims.append(job_id)
        entered.set()
        await release.wait()
        return await original(repository, job_id)

    monkeypatch.setattr(JobRepository, "claim_for_start", delayed)
    first = asyncio.create_task(runtime._start_job(job))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.wait_for(runtime._start_job(job), timeout=1)
        assert claims == [job.id]
    finally:
        release.set()
        await first
    await asyncio.wait_for(runtime._tasks[job.id], timeout=5)
