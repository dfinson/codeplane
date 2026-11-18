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
    raw_history = json.dumps([event.model_dump(mode="json") for event in observed])
    assert str(producer.target.agents[0].id) in raw_history
    assert str(producer.target.tasks[0].id) in raw_history
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
