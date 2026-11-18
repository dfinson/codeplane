"""Real admission -> Runtime task -> pinned authority -> native local tool."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from functools import partial

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import CPLConfig, RuntimeConfig
from backend.models.db import Base, ProjectRow
from backend.models.domain import ApprovalResolution, Job, JobState, StateConflictError
from backend.models.events import EventKind, SessionEvent
from backend.persistence import database
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.event_repo import EventRepository
from backend.persistence.execution_contract_repo import ExecutionContractRepository
from backend.persistence.native_execution_repo import NativeExecutionRepository
from backend.services.action_policy.production import StandaloneGovernance
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.integration_policy import IntegrationDisabledError
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.events.event_bus import EventBus
from backend.services.git.git_service import GitService
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime.service import RuntimeService
from backend.tests.native_gates.producers import make_producer


@pytest.fixture
async def composed_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_write_lock", asyncio.Lock())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'data.db'}")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = CPLConfig(runtime=RuntimeConfig(copilot_only=False, utility_sdk="unavailable-local-qualification"))
    events = EventBus()
    approvals = ApprovalService(factory)
    host = RuntimeService(
        factory,
        events,
        AdapterRegistry(config=config),
        config,
        approval_service=approvals,
        git_service=GitService(config),
    )
    governance = StandaloneGovernance(host, tmp_path)
    host._standalone_governance = governance
    await governance.start()
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            ProjectRow(
                id="project",
                name="local",
                repo_paths=json.dumps([str(tmp_path)]),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async def create(job_id="native-job"):
        job = Job(
            id=job_id,
            project_id="project",
            repo=str(tmp_path),
            prompt="local native tool",
            state=JobState.queued,
            base_ref="main",
            branch=None,
            worktree_path=str(tmp_path),
            session_id=None,
            created_at=now,
            updated_at=now,
        )
        async with factory() as session:
            await host._make_job_service(session).persist_prepared_job(job)
            await session.commit()
        return job

    yield host, approvals, governance, create
    await host.shutdown()
    await governance.close()
    await engine.dispose()


async def events_for(host, job_id):
    async with host._session_factory() as session:
        return [
            row.event
            for row in await EventRepository(session).list_after(0, limit=1000)
            if row.event.session_id == job_id
        ]


@pytest.mark.parametrize(
    "family",
    [
        "crewai",
        "langchain",
        "langgraph",
        "semantic_kernel",
        "maf",
        "smolagents",
        "pydantic_ai",
        "openai_agents",
    ],
)
async def test_real_runtime_admission_authority_and_native_tool(family, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    mode = family + ("-sync" if family in {"crewai", "smolagents"} else "-async")
    producer = make_producer(mode)
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(on_event)
    running = await host.start_native(
        job.id,
        family=family,
        target=producer.target,
        invoke=producer.invoke,
    )
    event = await asyncio.wait_for(requested.get(), 10)
    assert host._tasks[job.id] is running
    assert not running.done() and not producer.calls
    (execution,) = host._native_executions.for_job(job.id)
    assert execution.owner is running
    proposed = json.loads(event.payload["proposed_action"])
    assert proposed["execution"]["execution_token"] == execution.identity.execution_token
    assert proposed["execution"]["binding_id"] == execution.identity.binding_id
    assert proposed["binding_sha256"]
    stored_pin = await ExecutionContractRepository(host._session_factory).get(job.id, proposed["admission_id"])
    assert stored_pin == execution.pin
    assert event.metadata.attempt_id is None
    assert host._adapter_registry._adapters == {}
    assert job.id not in host._agent_sessions
    await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
    result = await asyncio.wait_for(running, 10)
    assert result is not None
    assert len(producer.calls) == 1
    assert host._native_executions.for_job(job.id) == ()
    assert job.id not in host._tasks
    assert (await host._get_job(job.id)).state == JobState.review
    receipts = await events_for(host, job.id)
    kinds = [event.kind for event in receipts]
    assert "execution.contract.pinned" in kinds
    assert "execution.native.bound" in kinds
    assert "execution.native.completed" in kinds
    assert "execution.native.released" in kinds
    assert "execution.contract.handle_bound" not in kinds
    totals = await governance._ledger.job_totals(job.id)
    assert totals.executed_calls == 1
    assert not totals.verified_usd_present
    for receipt in receipts:
        await governance.observe(receipt)
        await governance.observe(receipt)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    if family == "pydantic_ai":
        usage = next(receipt for receipt in receipts if receipt.kind == "execution.native.usage")
        assert {value["unit"] for value in usage.payload["usage"]} == {"input_tokens", "output_tokens"}
        assert all(value["producer"] == family for value in usage.payload["usage"])
    completion = next(receipt for receipt in receipts if receipt.kind == "execution.native.tool_result")
    await governance.observe(completion.model_copy(update={"id": "unstored-native-receipt"}))
    await governance.observe(
        completion.model_copy(update={"metadata": completion.metadata.model_copy(update={"execution_token": "forged"})})
    )
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    with pytest.raises(StateConflictError, match="original durable execution binding"):
        await NativeExecutionRepository(host._session_factory).receipt(
            execution.identity,
            execution.pin,
            family,
            execution.metadata,
            operation_id="late",
            phase="tool_result",
            payload={"started": True, "succeeded": True},
        )
    with pytest.raises(StateConflictError, match="original caller-owned runnable"):
        await host._start_reserved_job(await host._get_job(job.id))


async def test_disabled_profile_and_unowned_task_cannot_install(composed_runtime):
    host, _, _, create = composed_runtime
    job = await create()
    producer = make_producer("langchain-async")
    host._config.runtime.copilot_only = True
    with pytest.raises(IntegrationDisabledError):
        await host.start_native(job.id, family="langchain", target=producer.target, invoke=producer.invoke)
    assert "_codeplane_native_gate" not in vars(producer.target)
    assert host._adapter_registry._adapters == {}
    assert not host._tasks
    host._config.runtime.copilot_only = False
    with pytest.raises(StateConflictError, match="owning Runtime task"):
        await host.invoke_native(job.id, family="langchain", target=producer.target, invoke=producer.invoke)


async def test_cancel_retires_only_exact_native_approval(composed_runtime):
    host, approvals, _, create = composed_runtime
    job = await create()
    producer = make_producer("langchain-async")
    pending = asyncio.Event()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            pending.set()

    host._event_bus.subscribe(on_event)
    task = await host.start_native(job.id, family="langchain", target=producer.target, invoke=producer.invoke)
    await asyncio.wait_for(pending.wait(), 10)
    (execution,) = host._native_executions.for_job(job.id)
    (native_approval,) = await approvals.list_pending(job.id)
    sibling = await approvals.create_request(
        job.id, "Independent orchestration request", '{"kind":"recipe_gate"}', requires_explicit_approval=True
    )
    await host._native_executions.cancel_job(job.id)
    assert (await approvals.get(native_approval.id)).resolution == ApprovalResolution.rejected
    assert (await approvals.get(sibling.id)).resolution is None
    assert execution.cancel_requested and not execution.gates.binding.active
    await asyncio.gather(task, return_exceptions=True)
    assert not producer.calls


async def test_sync_worker_is_retained_until_actual_completion(composed_runtime):
    tools = load_sdk("langchain_core.tools")

    host, approvals, _, create = composed_runtime
    job = await create()
    started, release = threading.Event(), threading.Event()

    def body() -> str:
        """Wait for a local signal, with no external I/O."""
        started.set()
        assert release.wait(10)
        return "finished"

    tool = tools.StructuredTool.from_function(body)
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(on_event)
    running = await host.start_native(
        job.id, family="langchain", target=tool, invoke=partial(tool.invoke, {}), synchronous=True
    )
    approval = await asyncio.wait_for(requested.get(), 10)
    await approvals.resolve(approval.payload["approval_id"], ApprovalResolution.approved)
    assert await asyncio.to_thread(started.wait, 5)
    try:
        await host.cancel(job.id)
        await asyncio.sleep(0)
        assert not running.done()
        (execution,) = host._native_executions.for_job(job.id)
        assert execution.cancel_requested
        assert host._tasks[job.id] is running
        assert "_codeplane_native_gate" in vars(tool)
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
    assert host._native_executions.for_job(job.id) == ()
    assert "_codeplane_native_gate" not in vars(tool)


@pytest.mark.parametrize("resolution", [ApprovalResolution.approved, ApprovalResolution.rejected])
async def test_real_failure_counts_once_and_denial_does_not_count(resolution, composed_runtime):
    tools = load_sdk("langchain_core.tools")

    host, approvals, governance, create = composed_runtime
    job = await create()
    calls = []

    def failing() -> str:
        """A locally failing native body."""
        calls.append(True)
        raise ValueError("native body failed")

    tool = tools.StructuredTool.from_function(failing)
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(on_event)
    task = await host.start_native(
        job.id, family="langchain", target=tool, invoke=partial(tool.invoke, {}), synchronous=True
    )
    event = await asyncio.wait_for(requested.get(), 10)
    await approvals.resolve(event.payload["approval_id"], resolution)
    expected_error = ValueError if resolution == ApprovalResolution.approved else tools.ToolException
    with pytest.raises(expected_error):
        await task
    expected_count = int(resolution == ApprovalResolution.approved)
    assert len(calls) == expected_count
    for event in await events_for(host, job.id):
        await governance.observe(event)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == expected_count


async def test_native_detach_while_waiting_does_not_release_late_approval(composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    producer = make_producer("pydantic_ai-async")
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(on_event)
    task = await host.start_native(job.id, family="pydantic_ai", target=producer.target, invoke=producer.invoke)
    event = await asyncio.wait_for(requested.get(), 10)
    (execution,) = host._native_executions.for_job(job.id)
    assert await host.detach_native(execution.identity) == "cancel-requested"
    await asyncio.gather(task, return_exceptions=True)
    assert not producer.calls
    assert (await approvals.get(event.payload["approval_id"])).resolution == ApprovalResolution.rejected
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0
    with pytest.raises(StateConflictError):
        await host.detach_native(execution.identity)
