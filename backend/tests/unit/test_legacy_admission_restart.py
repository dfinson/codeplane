import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import CPLConfig, RuntimeConfig
from backend.models.db import Base
from backend.models.domain import JobState, StateConflictError
from backend.models.execution_contracts import ExecutionAdmission
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.execution_admission_repo import ExecutionAdmissionRepository
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.job_repo import JobRepository
from backend.services.adapters.completion_context import current_completion_context
from backend.services.events.event_bus import EventBus
from backend.tests.unit.test_execution_contracts import _contract
from backend.tests.unit.test_runtime_service import (
    FakeAdapterRegistry,
    FakeAgentAdapter,
    _create_db_job,
    _make_job,
    _make_runtime,
)


@asynccontextmanager
async def _database(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _host(sessions, config, *, selected="selected", available=None):
    runtime = _make_runtime(
        session_factory=sessions,
        event_bus=EventBus(),
        adapter_registry=FakeAdapterRegistry(FakeAgentAdapter()),
        config=config,
    )
    runtime.configure_execution_contracts(
        available=available or {selected: _contract(selected)},
        selected_for_new=selected,
    )
    runtime._setup_action_policy = AsyncMock()
    started, configurations = asyncio.Event(), []

    async def execute(job_id, _session, config, **kwargs):
        configurations.append(config)
        started.set()

    runtime._run_job_guarded = AsyncMock(side_effect=execute)
    return runtime, started, configurations


@pytest.mark.parametrize("state", [JobState.queued, JobState.preparing])
async def test_restart_before_first_reserved_start_preserves_new_admission(tmp_path, state):
    path = tmp_path / "history.db"
    config = CPLConfig(repos=[str(tmp_path)])
    async with _database(path) as sessions:
        job = _make_job(repo=str(tmp_path), state=state)
        await _create_db_job(sessions, job)
        async with sessions() as session:
            admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
            await session.commit()
        assert await ExecutionContractRepository(sessions).get(job.id, admission.id) is None

    async with _database(path) as sessions:
        restored, started, configurations = _host(sessions, config)

        async def prepare(job, **kwargs):
            async with sessions() as session:
                repository = JobRepository(session)
                await repository.update_state(job.id, JobState.queued, datetime.now(UTC))
                ready = await repository.get(job.id)
                await session.commit()
            await restored.start_or_enqueue(ready)

        restored.setup_and_start = AsyncMock(side_effect=prepare)
        try:
            await restored.recover_on_startup()
            await asyncio.wait_for(started.wait(), timeout=3)
            assert configurations[0].prompt == job.prompt
            pin = await ExecutionContractRepository(sessions).get(job.id, admission.id)
            assert pin is not None and pin.contract.id == "selected"
            async with sessions() as session:
                assert (await ExecutionAdmissionRepository(session).current(job.id)).id == admission.id
                assert (await JobRepository(session).get(job.id)).session_count == 1
        finally:
            await restored.shutdown()


@pytest.mark.parametrize("native", [False, True])
async def test_queued_continuation_restart_retains_exact_instruction_and_summary_numbers(tmp_path, native):
    path = tmp_path / "history.db"
    config = CPLConfig(repos=[str(tmp_path)], runtime=RuntimeConfig(max_concurrent_jobs=1))
    instruction = "Continue the newly requested operation, not the original prompt."
    async with _database(path) as sessions:
        job = _make_job(repo=str(tmp_path), state=JobState.review)
        job.prompt = "Original operation"
        job.session_count = 4
        job.sdk_session_id = "accepted-native-history" if native else None
        await _create_db_job(sessions, job)
        first, _, _ = _host(sessions, config)
        occupant = asyncio.create_task(asyncio.Event().wait())
        first._tasks["occupant"] = occupant
        try:
            resumed = await first.resume_job(job.id, instruction)
            assert resumed.session_count == 5
            first._setup_action_policy.assert_not_awaited()
            first._run_job_guarded.assert_not_awaited()
            async with sessions() as session:
                admission = await ExecutionAdmissionRepository(session).current(job.id)
            assert admission is not None
            assert await ExecutionContractRepository(sessions).get(job.id, admission.id) is None
        finally:
            await first.shutdown()

    async with _database(path) as sessions:
        restored, started, configurations = _host(sessions, config)
        summarized = []

        async def summarize(job_id, source_session_number, original_prompt):
            context = current_completion_context()
            assert restored._setup_action_policy.await_count == 1
            assert context.execution_contract.admission_id == admission.id
            assert original_prompt == job.prompt
            summarized.append(source_session_number)

        restored._summarization_service = SimpleNamespace(summarize_and_store=AsyncMock(side_effect=summarize))
        try:
            await restored.recover_on_startup()
            await asyncio.wait_for(started.wait(), timeout=3)
            (submitted,) = configurations
            if native:
                assert submitted.prompt == instruction
                assert submitted.resume_sdk_session_id == "accepted-native-history"
                assert summarized == []
            else:
                assert instruction in submitted.prompt
                assert "Original operation" in submitted.prompt
                assert summarized == [4]
            async with sessions() as session:
                assert (await JobRepository(session).get(job.id)).session_count == 5
                assert (await ExecutionAdmissionRepository(session).current(job.id)).id == admission.id
            pin = await ExecutionContractRepository(sessions).get(job.id, admission.id)
            assert pin is not None and pin.budget_id == job.id
        finally:
            await restored.shutdown()


async def test_restart_after_pin_before_started_witness_keeps_original_contract(tmp_path):
    path = tmp_path / "history.db"
    config = CPLConfig(repos=[str(tmp_path)])
    old, new = _contract("old"), _contract("new")
    async with _database(path) as sessions:
        job = _make_job(repo=str(tmp_path))
        await _create_db_job(sessions, job)
        async with sessions() as session:
            admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
            await session.commit()
        first, _, _ = _host(sessions, config, selected="old")
        pinned = await first.prepare_execution_contract(job.id, admission.id, recovery=False)
        await first.shutdown()
    async with _database(path) as sessions:
        restored, started, _ = _host(sessions, config, selected="new", available={"old": old, "new": new})
        try:
            await restored.recover_on_startup()
            await asyncio.wait_for(started.wait(), timeout=3)
            assert await ExecutionContractRepository(sessions).get(job.id, admission.id) == pinned
        finally:
            await restored.shutdown()


async def test_started_witness_cannot_precede_its_governed_pin(tmp_path):
    async with _database(tmp_path / "history.db") as sessions:
        job = _make_job(repo=str(tmp_path))
        await _create_db_job(sessions, job)
        async with sessions() as session:
            admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
            await session.commit()
        async with sessions() as session:
            repository = ExecutionAdmissionRepository(session)
            with pytest.raises(StateConflictError, match="committed contract pin"):
                await repository.mark_started(admission, contract_recorded=True)
            assert await repository.is_unstarted(admission)


@pytest.mark.parametrize("old_state", ["missing_intent", "legacy_started"])
async def test_unknown_or_started_legacy_admission_cannot_acquire_a_new_contract(tmp_path, old_state):
    path = tmp_path / "history.db"
    config = CPLConfig(repos=[str(tmp_path)])
    async with _database(path) as sessions:
        job = _make_job(repo=str(tmp_path))
        await _create_db_job(sessions, job)
        async with sessions() as session:
            repository = ExecutionAdmissionRepository(session)
            if old_state == "missing_intent":
                admission = ExecutionAdmission(id="historical", job_id=job.id, origin="creation")
                await repository.declare_legacy(admission, expected_current=None)
            else:
                admission = await JobRepository(session).declare_initial_legacy_admission(job.id)
                await repository.mark_started(admission, contract_recorded=False)
            await session.commit()
    async with _database(path) as sessions:
        restored, _, _ = _host(sessions, config)
        restored._legacy_recoveries[job.id] = object()
        try:
            with pytest.raises(StateConflictError, match="missing historical"):
                await restored._start_job(job)
            assert await ExecutionContractRepository(sessions).get(job.id, admission.id) is None
            restored._setup_action_policy.assert_not_awaited()
            restored._run_job_guarded.assert_not_awaited()
        finally:
            await restored.shutdown()
