import asyncio
import json

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import CPLConfig, RuntimeConfig
from backend.models.db import Base
from backend.models.domain import StateConflictError
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.export_metadata import export_metadata
from backend.services.events.observation import is_observation_history
from backend.services.ingest.credentials import IngestCredentials
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.record_service import ObservationRecords

CONFIG = CPLConfig(runtime=RuntimeConfig(copilot_only=False))


def otlp(*ids, producer="autogen", metadata_only=False):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": producer}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "chat",
                                "traceId": "1234567890abcdef1234567890abcdef",
                                "spanId": f"{identity:016x}",
                                "parentSpanId": "fedcba0987654321",
                                "startTimeUnixNano": "1000000",
                                "endTimeUnixNano": "3000000",
                                "attributes": [
                                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                                    {"key": "gen_ai.system", "value": {"stringValue": producer}},
                                    *(
                                        []
                                        if metadata_only
                                        else [
                                            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "0"}},
                                            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "7"}},
                                        ]
                                    ),
                                ],
                            }
                            for identity in ids
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
async def otlp_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        projects = ProjectRepository(session)
        await projects.create("project", "Project", [])
        bindings = IntegrationRepository(session)
        job = await HistoryRegistration(bindings, JobRepository(session), projects).register(
            binding_id="otlp",
            project_id="project",
            format_id="otlp_json",
            external_session_id="source-session",
        )
        await session.commit()
        token = await IngestCredentials(bindings, CONFIG).issue("otlp", enabled=True)
    yield factory, job, token
    await engine.dispose()


async def _ingest(factory, token, body):
    async with factory() as session:
        bindings = IntegrationRepository(session)
        authorization = await IngestCredentials(bindings, CONFIG).authorize("otlp", token)
        return await ObservationRecords(bindings, JobRepository(session), EventBus()).append_otlp(
            "otlp",
            json.dumps(body).encode(),
            authorization=authorization,
        )


@pytest.mark.parametrize("producer", ["autogen", "semantic_kernel", "vercel_ai", "mastra", "agno", "codex"])
async def test_named_preset_local_storage_replay_and_unknown_versus_zero_usage(otlp_store, producer):
    factory, job, token = otlp_store
    assert await _ingest(factory, token, otlp(1, producer=producer)) == (1, 0)
    assert await _ingest(factory, token, otlp(1, producer=producer)) == (0, 1)
    assert await _ingest(factory, token, otlp(1, 2, producer=producer)) == (1, 1)
    assert await _ingest(factory, token, otlp(3, producer=producer, metadata_only=True)) == (1, 0)
    async with factory() as session:
        rows = await EventRepository(session).list_after(0, job_id=job.id)
        events = [row.event for row in rows if row.event.kind == "llm.call.completed"]
        assert len(events) == 3
        assert len({event.id for event in events}) == 3
        assert events[0].payload["input_tokens"] == 0
        assert events[0].payload["output_tokens"] == 7
        assert "input_tokens" not in events[2].payload and "output_tokens" not in events[2].payload
        assert all("cost_usd" not in event.payload and "arguments" not in event.payload for event in events)
        assert all(event.payload["system"] == producer for event in events)
        assert all(event.metadata.run_id == "source-session" for event in events)
        assert all(event.metadata.integration_binding_id == "otlp" for event in events)
        assert events[0].metadata.span_id == "otlp:1234567890abcdef1234567890abcdef:0000000000000001"
        assert events[0].metadata.parent_id == "otlp:1234567890abcdef1234567890abcdef:fedcba0987654321"
        assert all(is_observation_history(event) for event in events)
        assert all(event.raw_event is not None for event in events)
        assert len([row for row in rows if row.event.kind == "integration.otlp.span.accepted"]) == 3
        exported = export_metadata(
            events[0],
            identity_key=b"local-fixture-key" * 2,
            integration="generic-otel",
            provider=producer,
            role="primary",
            approved_models=frozenset(),
        )
        assert exported["input_tokens"] == 0 and exported["output_tokens"] == 7
        assert "raw_event" not in exported and "payload" not in exported
        assert "1234567890abcdef" not in json.dumps(exported)


@pytest.mark.parametrize("change", ["invalid-id", "invalid-time", "oversized", "too-many"])
async def test_invalid_entire_batch_has_no_ack_or_partial_events(otlp_store, change):
    factory, job, token = otlp_store
    body = otlp(1, 2)
    second = body["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
    if change == "invalid-id":
        second["traceId"] = "0" * 32
    elif change == "invalid-time":
        second["endTimeUnixNano"] = "0"
    elif change == "oversized":
        second["large"] = "a" * 1024 * 1024
    else:
        body = otlp(*range(1, 1002))
    with pytest.raises(ValueError):
        await _ingest(factory, token, body)
    async with factory() as session:
        assert await EventRepository(session).list_after(0, job_id=job.id) == []
        assert await IntegrationRepository(session).projection("otlp") is None


@pytest.mark.parametrize("change", ["revoke", "replace", "disable", "project", "job", "source"])
async def test_inflight_credential_change_fences_atomic_commit(otlp_store, monkeypatch, change):
    factory, job, token = otlp_store
    original = IntegrationRepository.commit_records

    async def change_before_commit(repo, **kwargs):
        async with factory() as session:
            bindings = IntegrationRepository(session)
            binding = await bindings.get("otlp")
            service = IngestCredentials(bindings, CONFIG)
            if change == "revoke":
                await service.revoke("otlp")
            elif change == "replace":
                await service.issue("otlp", enabled=True)
            else:
                credential = dict(binding.config["ingest_credential"])
                if change != "disable":
                    key = {"project": "project_id", "job": "job_id", "source": "source_id"}[change]
                    credential[key] = "different-owner"
                await bindings.set_ingest_credential(
                    binding, credential, enabled=change != "disable", credential_hash=binding.credential_hash
                )
        return await original(repo, **kwargs)

    monkeypatch.setattr(IntegrationRepository, "commit_records", change_before_commit)
    with pytest.raises(PermissionError):
        await _ingest(factory, token, otlp(1))
    async with factory() as session:
        assert await EventRepository(session).list_after(0, job_id=job.id) == []
        assert await IntegrationRepository(session).projection("otlp") is None


async def test_concurrent_overlapping_requests_retry_without_duplicate_projection(otlp_store, monkeypatch):
    factory, job, token = otlp_store
    original = IntegrationRepository.commit_records
    both = asyncio.Event()
    arrived = 0

    async def simultaneous(repo, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 5)
        return await original(repo, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(IntegrationRepository, "commit_records", simultaneous)
        results = await asyncio.gather(
            _ingest(factory, token, otlp(1, 2)),
            _ingest(factory, token, otlp(2, 3)),
            return_exceptions=True,
        )
    assert sum(isinstance(result, StateConflictError) for result in results) == 1, results
    assert await _ingest(factory, token, otlp(1, 2, 3)) == (1, 2)
    async with factory() as session:
        rows = await EventRepository(session).list_after(0, job_id=job.id)
        assert len([row for row in rows if row.event.kind == "llm.call.completed"]) == 3
        assert len([row for row in rows if row.event.kind == "integration.otlp.span.accepted"]) == 3
        assert (await IntegrationRepository(session).projection("otlp")).state == "accepted"


async def test_reopened_database_retains_scoped_credentials_and_span_acknowledgments(otlp_store):
    factory, _, token = otlp_store
    assert await _ingest(factory, token, otlp(1)) == (1, 0)
    engine = create_async_engine(factory.kw["bind"].url)
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    reopened = async_sessionmaker(engine, expire_on_commit=False)
    try:
        assert await _ingest(reopened, token, otlp(1, 2)) == (1, 1)
        async with reopened() as session:
            credentials = IngestCredentials(IntegrationRepository(session), CONFIG)
            replacement = await credentials.issue("otlp", enabled=True)
        with pytest.raises(PermissionError):
            await _ingest(factory, token, otlp(3))
        assert await _ingest(factory, replacement, otlp(3)) == (1, 0)
    finally:
        await engine.dispose()
