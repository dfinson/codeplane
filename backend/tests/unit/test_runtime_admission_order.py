"""Shared startup ordering without requiring the recipe executor or policy catalogue."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.domain import SIDECAR_ARBITER, JobState, StateConflictError
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.job_repo import JobRepository
from backend.tests.unit.test_execution_contracts import _contract
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


async def test_runtime_admission_requires_the_wired_durable_gate(runtime, tmp_path):
    from backend.services.action_policy.production import StandaloneGovernance

    with pytest.raises(StateConflictError, match="not been wired"):
        await runtime.require_governance_admission()
    governance = StandaloneGovernance(runtime, tmp_path)
    runtime._standalone_governance = governance
    await governance._ledger.initialize()
    await runtime.require_governance_admission()
    state = await governance._ledger.admission_state()
    await governance._ledger.set_admission_control(
        expected_generation=state.generation, blocked=True, reason="Rollback is draining"
    )
    with pytest.raises(StateConflictError, match="Rollback is draining"):
        await runtime.require_governance_admission()


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


async def test_declared_pin_is_persisted_before_policy_and_preflight(runtime, session_factory, config):
    phases: list[str] = []
    sidecars(runtime, phases)
    contract = _contract("selected")
    runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
    job = _make_job(repo=config.repos[0])
    await _create_db_job(session_factory, job)
    async with session_factory() as session:
        admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
        await session.commit()

    async def policy(*args, **kwargs):
        persisted = await ExecutionContractRepository(session_factory).get(job.id, admission.id)
        assert persisted is not None and persisted == runtime._job_contract_pins[job.id]
        assert persisted.contract == contract
        phases.append("policy")

    runtime._setup_action_policy = AsyncMock(side_effect=policy)
    await runtime.start_or_enqueue(job)
    await asyncio.wait_for(runtime._tasks[job.id], timeout=5)
    assert phases == ["policy", "builtin", "builtin", "builtin", "preflight"]


async def test_recovery_cannot_mint_missing_pin_before_preflight(runtime, session_factory, config):
    manager = sidecars(runtime, [])
    contract = _contract("selected")
    runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
    job = _make_job(repo=config.repos[0], state=JobState.running)
    job.sdk_session_id = "retained-native-session"
    await _create_db_job(session_factory, job)
    async with session_factory() as session:
        admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
        await session.commit()
    with pytest.raises(StateConflictError, match="missing historical"):
        await runtime._recover_active_job(job.id)
    assert await ExecutionContractRepository(session_factory).get(job.id, admission.id) is None
    runtime._setup_action_policy.assert_not_awaited()
    manager.open.assert_not_called()
    runtime._activate_custom_sidecars.assert_not_awaited()


async def test_recovery_uses_retained_pin_not_new_admission_selection(runtime, session_factory, config):
    sidecars(runtime, [])
    old, new = _contract("old"), _contract("new")
    runtime.configure_execution_contracts(available={old.id: old}, selected_for_new=old.id)
    job = _make_job(repo=config.repos[0], state=JobState.running)
    job.sdk_session_id = "retained-native-session"
    await _create_db_job(session_factory, job)
    async with session_factory() as session:
        admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
        await session.commit()
    retained = await runtime.prepare_execution_contract(job.id, admission.id, recovery=False)
    runtime.configure_execution_contracts(available={old.id: old, new.id: new}, selected_for_new=new.id)
    seen = []
    runtime._setup_action_policy = AsyncMock(
        side_effect=lambda *args, **kwargs: seen.append(runtime._job_contract_pins[job.id]),
    )
    await runtime._recover_active_job(job.id)
    await asyncio.wait_for(runtime._tasks[job.id], timeout=5)
    assert seen == [retained]


async def test_continuation_handoff_utility_runs_after_new_pin_and_policy(runtime, session_factory, config):
    from backend.services.adapters.completion_context import current_completion_context

    phases: list[str] = []
    sidecars(runtime, phases)
    contract = _contract("selected")
    runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
    job = _make_job(repo=config.repos[0], state=JobState.review)
    job.session_count = 4
    await _create_db_job(session_factory, job)
    async with session_factory() as session:
        initial = await JobRepository(session).declare_initial_legacy_admission(job.id)
        await session.commit()
    seen = []

    async def summarize(job_id, source_session_number, original_task):
        context = current_completion_context()
        assert phases == ["policy"]
        assert context.job_id == job_id
        assert context.execution_contract == runtime._job_contract_pins[job_id]
        assert context.execution_contract.admission_id != initial.id
        assert source_session_number == 4
        seen.append(context.execution_contract)
        phases.append("handoff")

    summary = MagicMock()
    summary.summarize_and_store = AsyncMock(side_effect=summarize)
    runtime._summarization_service = summary
    runtime._run_job_guarded = AsyncMock()
    resumed = await runtime.resume_job(job.id, "Keep going with the prior context")
    await asyncio.wait_for(runtime._tasks[job.id], timeout=5)
    assert resumed.session_count == 5 and len(seen) == 1
    submitted = runtime._run_job_guarded.call_args.args[2]
    assert "[RESUMED SESSION" in submitted.prompt
    assert "Keep going with the prior context" in submitted.prompt
    assert phases == ["policy", "handoff", "builtin", "builtin", "builtin", "preflight"]


async def test_legacy_resume_cannot_declare_a_new_admission_before_previous_task_releases(
    runtime,
    session_factory,
    config,
):
    from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository

    job = _make_job(repo=config.repos[0], state=JobState.review)
    job.sdk_session_id = "previous-native-session"
    await _create_db_job(session_factory, job)
    task = asyncio.create_task(asyncio.Event().wait())
    runtime._tasks[job.id] = task
    try:
        with pytest.raises(StateConflictError, match="still releasing"):
            await runtime.resume_job(job.id, "Next admission")
        async with session_factory() as session:
            assert await ExecutionAdmissionRepository(session).current(job.id) is None
            retained = await JobRepository(session).get(job.id)
            assert retained.state == JobState.review and retained.session_count == 1
    finally:
        runtime._tasks.pop(job.id, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
