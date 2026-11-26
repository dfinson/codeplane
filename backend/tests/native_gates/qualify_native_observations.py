"""Canonical local native observation through real persisted Runtime admission."""

import asyncio
import json
import sqlite3

import pytest

from backend.models.domain import StateConflictError
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.services.adapters.native_gates import observation
from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.events.export_status import ExportDropReason, ExportHealth
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.native_gates.producers import make_producer
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


async def test_openai_canonical_history_precedes_optional_metadata_export(composed_runtime, tmp_path, monkeypatch):
    host, _, governance, create = composed_runtime
    job = await create()
    agents = load_sdk("agents")
    tracing = load_sdk("agents.tracing")
    providers = load_sdk("agents.tracing.provider")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "false")
    provider = providers.DefaultTraceProvider()
    target = agents.Agent(name="local-observer", tools=[])
    exported = []
    identity = None

    def export(value):
        with sqlite3.connect(tmp_path / "data.db") as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM events WHERE job_id=? "
                "AND json_extract(event_metadata, '$.integration_binding_id') IS NOT NULL",
                (job.id,),
            ).fetchone()[0]
        assert count > 0, "Metadata reached export before canonical local persistence"
        exported.append(value)

    async def work():
        nonlocal identity
        (execution,) = host._native_executions.for_job(job.id)
        identity = execution.identity
        owner = await host.open_native_observation(
            identity,
            format_id="openai_agents",
            external_session_id="trace_canonical",
        )
        subscription = await observation.subscribe_openai(
            owner,
            provider=provider,
            export_sink=export,
            identity_key=b"k" * 32,
            approved_models=frozenset({"local"}),
        )
        with (
            provider.create_trace("local", trace_id="trace_canonical") as trace,
            provider.create_span(
                tracing.GenerationSpanData(
                    input=[{"content": "secret=do-not-export"}],
                    output=[{"content": "private text"}],
                    model="local",
                    usage={"input_tokens": 0, "output_tokens": 3},
                ),
                parent=trace,
            ),
        ):
            pass
        await owner.close()
        await subscription.detach()
        assert not subscription.blocking

    await (await host.start_native(job.id, family="openai_agents", target=target, invoke=work))
    stored = await events_for(host, job.id)
    observed = [event for event in stored if event.metadata.integration_binding_id is not None]
    assert observed
    assert identity is not None
    assert all(event.session_id == job.id for event in observed)
    assert any(event.metadata.run_id == "trace_canonical" for event in observed)
    assert all(event.metadata.execution_epoch == identity.epoch for event in observed)
    assert all(event.metadata.execution_token == identity.execution_token for event in observed)
    assert any(event.payload.get("trace_id") == "trace_canonical" for event in observed)
    assert any(event.payload.get("input_tokens") == 0 and event.payload.get("output_tokens") == 3 for event in observed)
    assert exported and "secret" not in json.dumps(exported) and "private text" not in json.dumps(exported)
    assert any(value.get("input_tokens") == 0 and value.get("output_tokens") == 3 for value in exported)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0
    with sqlite3.connect(tmp_path / "data.db") as connection:
        assert connection.execute("SELECT id FROM jobs").fetchall() == [(job.id,)]


async def test_crewai_history_retains_native_ids_without_export_and_preserves_import_fence(composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    producer = make_producer("crewai-sync")
    accepted = asyncio.Event()

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)

    async def work():
        (execution,) = host._native_executions.for_job(job.id)
        owner = await host.open_native_observation(
            execution.identity,
            format_id="crewai",
            external_session_id=str(producer.target.id),
        )
        original = owner._append

        async def witnessed(raw):
            events = await original(raw)
            if raw.get("type") == "tool_usage_finished":
                accepted.set()
            return events

        owner._append = witnessed
        subscription = await observation.subscribe_crewai(owner, producer.target)
        await producer.invoke()
        await asyncio.wait_for(accepted.wait(), 5)
        await owner.close()
        await subscription.detach()

    await (await host.start_native(job.id, family="crewai", target=producer.target, invoke=work))
    stored = await events_for(host, job.id)
    observed = [event for event in stored if event.metadata.integration_binding_id is not None]
    assert observed and len(producer.calls) == 1
    assert any(event.metadata.run_id == str(producer.target.id) for event in observed)
    tools = [event for event in observed if event.kind.startswith("tool.call.")]
    assert {event.kind for event in tools} >= {"tool.call.started", "tool.call.completed"}
    for event in tools:
        for key, value in (
            ("agent_id", str(producer.target.agents[0].id)),
            ("task_id", str(producer.target.tasks[0].id)),
            ("crew_id", str(producer.target.id)),
        ):
            assert event.payload[key] == value
            assert event.raw_event is not None and event.raw_event[key] == value
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    binding_id = observed[0].metadata.integration_binding_id
    assert binding_id is not None
    async with host._session_factory() as session:
        service = ObservationRecords(IntegrationRepository(session), JobRepository(session), host._event_bus)
        with pytest.raises(StateConflictError):
            await service.append(
                binding_id,
                ObservationRecordBatch(
                    source_key="untrusted-import", start_index=0, records=[{"type": "flow_started"}]
                ),
            )


@pytest.mark.parametrize("failure", ["sink", "filter", "health"])
async def test_optional_export_failure_does_not_fail_committed_local_history(failure, composed_runtime, monkeypatch):
    host, _, governance, create = composed_runtime
    job = await create()
    agents = load_sdk("agents")
    tracing = load_sdk("agents.tracing")
    providers = load_sdk("agents.tracing.provider")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "false")
    provider = providers.DefaultTraceProvider()
    exported = []
    invocations = []
    health = ExportHealth(enabled=True)

    def broken_export(record):
        exported.append(record)
        raise OSError("Optional destination unavailable")

    def broken_filter(*args, **kwargs):
        raise ValueError("Optional metadata filter withheld this record")

    def broken_health(*args, **kwargs):
        raise OSError("Optional health sink unavailable")

    if failure == "filter":
        monkeypatch.setattr(observation, "export_metadata", broken_filter)
    elif failure == "health":
        monkeypatch.setattr(health, "record_drop", broken_health)

    async def work():
        invocations.append("actual-run")
        (execution,) = host._native_executions.for_job(job.id)
        owner = await host.open_native_observation(
            execution.identity, format_id="openai_agents", external_session_id="trace_export_failure"
        )
        subscription = await observation.subscribe_openai(
            owner, provider=provider, export_sink=broken_export, identity_key=b"k" * 32, export_health=health
        )
        with provider.create_trace("local", trace_id="trace_export_failure") as trace:
            for value in ("before-export-outage", "after-export-outage"):
                with provider.create_span(tracing.FunctionSpanData("local", "{}", value), parent=trace):
                    pass
        await owner.close()
        await subscription.detach()
        return "actual-result"

    result = await (
        await host.start_native(
            job.id, family="openai_agents", target=agents.Agent(name="local", tools=[]), invoke=work
        )
    )
    assert result == "actual-result" and invocations == ["actual-run"]
    assert len(exported) == (0 if failure == "filter" else 1)
    if failure != "health":
        snapshot = health.snapshot()
        assert snapshot.dropped_spans > 0
        assert snapshot.enabled == (failure == "filter")
        reason = ExportDropReason.metadata_withheld if failure == "filter" else ExportDropReason.sink_failure
        assert dict(snapshot.dropped_by_reason)[reason] > 0
    tools = [
        event
        for event in await events_for(host, job.id)
        if event.kind.startswith("tool.call.") and event.metadata.integration_binding_id is not None
    ]
    assert {event.payload.get("result") for event in tools} >= {"before-export-outage", "after-export-outage"}
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


async def test_local_acceptance_failure_fences_source_without_retrying_runnable(composed_runtime, monkeypatch):
    host, _, _, create = composed_runtime
    job = await create()
    agents = load_sdk("agents")
    tracing = load_sdk("agents.tracing")
    providers = load_sdk("agents.tracing.provider")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "false")
    provider = providers.DefaultTraceProvider()
    invocations, writes = [], []
    exported: list[dict[str, object]] = []

    async def fail_commit(*args, **kwargs):
        writes.append("attempt")
        raise OSError("Canonical store unavailable")

    async def work():
        invocations.append("actual-run")
        (execution,) = host._native_executions.for_job(job.id)
        owner = await host.open_native_observation(
            execution.identity, format_id="openai_agents", external_session_id="trace_local_failure"
        )
        await observation.subscribe_openai(
            owner, provider=provider, export_sink=exported.append, identity_key=b"k" * 32
        )
        monkeypatch.setattr(host._native_executions._repository, "commit_observation", fail_commit)
        with provider.create_trace("local", trace_id="trace_local_failure") as trace:
            for value in ("first", "must-not-retry-mutated-projection"):
                with provider.create_span(tracing.FunctionSpanData("local", "{}", value), parent=trace):
                    pass
        return "actual-result"

    task = await host.start_native(
        job.id, family="openai_agents", target=agents.Agent(name="local", tools=[]), invoke=work
    )
    with pytest.raises(ExceptionGroup):
        await task
    assert invocations == ["actual-run"] and writes == ["attempt"] and not exported
    events = await events_for(host, job.id)
    assert any(event.kind == "execution.native.completed" for event in events)
    assert not any(event.kind == "integration.batch.accepted" for event in events)
    assert host._native_executions.for_job(job.id) == ()


@pytest.mark.parametrize("first_failure", [False, True])
async def test_disable_fences_all_executions_before_draining_any_source(first_failure, composed_runtime):
    host, _, _, create = composed_runtime
    first, second = await create("observer-first"), await create("observer-second")
    agents = load_sdk("agents")
    producer = make_producer("openai_agents-async")
    first_ready, first_closing, release_close, release_work, second_closed, permission = (
        asyncio.Event() for _ in range(6)
    )
    executions = []

    async def requested(event):
        if event.kind == "permission.requested" and event.session_id == second.id:
            permission.set()

    host._event_bus.subscribe(requested)

    async def first_work():
        (execution,) = host._native_executions.for_job(first.id)
        executions.append(execution)
        source = await host.open_native_observation(
            execution.identity, format_id="openai_agents", external_session_id="trace_first"
        )
        original = source._finish

        async def finish():
            first_closing.set()
            await release_close.wait()
            await original()
            if first_failure:
                raise OSError("First source retirement failed")

        source._finish = finish
        first_ready.set()
        await release_work.wait()

    async def second_work():
        (execution,) = host._native_executions.for_job(second.id)
        executions.append(execution)
        source = await host.open_native_observation(
            execution.identity, format_id="openai_agents", external_session_id="trace_second"
        )
        original = source._finish

        async def finish():
            await original()
            second_closed.set()

        source._finish = finish
        await producer.invoke()

    first_task = await host.start_native(
        first.id, family="openai_agents", target=agents.Agent(name="first", tools=[]), invoke=first_work
    )
    await asyncio.wait_for(first_ready.wait(), 5)
    second_task = await host.start_native(second.id, family="openai_agents", target=producer.target, invoke=second_work)
    await asyncio.wait_for(permission.wait(), 5)
    host._config.runtime.copilot_only = True
    retiring = asyncio.create_task(host.reconcile_native_observers())
    try:
        await asyncio.wait_for(first_closing.wait(), 5)
        assert all(not execution.gates.binding.active for execution in executions)
        await asyncio.wait_for(second_closed.wait(), 5)
        assert not producer.calls
        assert not retiring.done()
        release_close.set()
        if first_failure:
            with pytest.raises(ExceptionGroup, match="Disabled integrations require reconciliation") as rejected:
                await retiring
            assert len(rejected.value.exceptions) == 1
            observation = rejected.value.exceptions[0]
            assert isinstance(observation, ExceptionGroup)
            assert "observation" in observation.message
            assert len(observation.exceptions) == 1
            assert isinstance(observation.exceptions[0], OSError)
            assert str(observation.exceptions[0]) == "First source retirement failed"
        else:
            await retiring
    finally:
        release_close.set()
        release_work.set()
        first_task.cancel()
        second_task.cancel()
        await asyncio.gather(retiring, first_task, second_task, return_exceptions=True)


async def test_native_observation_after_cancel_uses_retained_not_action_ownership(composed_runtime, monkeypatch):
    host, _, _, create = composed_runtime
    job = await create()
    agents = load_sdk("agents")
    tracing = load_sdk("agents.tracing")
    providers = load_sdk("agents.tracing.provider")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "false")
    provider = providers.DefaultTraceProvider()
    target = agents.Agent(name="retained-observer", tools=[])
    ready, finish = asyncio.Event(), asyncio.Event()

    async def work():
        (execution,) = host._native_executions.for_job(job.id)
        owner = await host.open_native_observation(
            execution.identity,
            format_id="openai_agents",
            external_session_id="trace_cancelled",
        )
        await observation.subscribe_openai(owner, provider=provider)
        with provider.create_trace("local", trace_id="trace_cancelled") as trace:
            span = provider.create_span(tracing.FunctionSpanData("local", "{}", "observed-after-cancel"), parent=trace)
            span.start()
            ready.set()
            await finish.wait()
            span.finish()
        await owner.close()

    task = await host.start_native(job.id, family="openai_agents", target=target, invoke=work)
    await asyncio.wait_for(ready.wait(), 5)
    try:
        await host.cancel_job(job.id)
        assert not task.done()
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
    observed = [event for event in await events_for(host, job.id) if event.metadata.integration_binding_id is not None]
    assert "observed-after-cancel" in json.dumps([event.model_dump(mode="json") for event in observed])
    assert host._native_executions.for_job(job.id) == ()
