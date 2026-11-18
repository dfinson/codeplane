"""Registered custom mappings through real HTTP handlers and canonical storage."""

import hashlib
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.api.integrations import router
from backend.app_factory import _register_domain_exception_handlers
from backend.config import CPLConfig, NativeMappingsConfig
from backend.di import AppProvider, RequestProvider
from backend.models.db import IntegrationBindingRow
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.acquisition import AcquisitionChannel
from backend.services.ingest.acquisition_sources import file_snapshot
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.mapping import PARSER_REVISION
from backend.services.ingest.mapping_registry import MappingRegistry
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_approval_service import session_factory as session_factory
from backend.tests.unit.test_mapping_registry import _DEFINITION, _write
from backend.tests.unit.test_mapping_registry import mapping_home as mapping_home


@asynccontextmanager
async def _api(factory, config):
    application = FastAPI()
    _register_domain_exception_handlers(application)
    application.include_router(router, prefix="/api")
    container = make_async_container(
        AppProvider(),
        RequestProvider(),
        context={CPLConfig: config, async_sessionmaker: factory, EventBus: EventBus()},
    )
    setup_dishka(container, application)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://local") as client:
            yield client
    finally:
        await container.close()


def _registration(name="local"):
    return {
        "bindingId": "binding",
        "projectId": "proj-1",
        "formatId": name,
        "externalSessionId": "producer",
    }


def _batch(text="Independent expected answer"):
    return {"sourceKey": "events", "startIndex": 0, "records": [{"type": "answer", "text": text}]}


@pytest.mark.parametrize("name", ["local", "copilot"])
async def test_registered_configured_mapping_pins_content_and_replays_real_history(session_factory, mapping_home, name):
    path = _write(mapping_home / "explicit", name)
    config = CPLConfig(native_mappings=NativeMappingsConfig(extra_dirs=[str(path.parent)]))
    async with _api(session_factory, config) as client:
        registered = await client.post("/api/integrations/observations", json=_registration(name))
        assert registered.status_code == 201, registered.text
        job_id = registered.json()["id"]
        first = await client.post("/api/integrations/binding/records", json=_batch())
        assert first.status_code == 200, first.text
        before = (await client.get("/api/integrations/binding/events")).json()
        repeated = await client.post("/api/integrations/binding/records", json=_batch())
        assert repeated.status_code == 200 and repeated.json()["replay"]
        assert (await client.get("/api/integrations/binding/events")).json() == before
    async with session_factory() as session:
        binding = await IntegrationRepository(session).get("binding")
        pin = binding.config["mapping_pin"]
        assert pin["format_id"] == name and pin["framework_version"] == "1"
        assert pin["content_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        checkpoint = await IntegrationRepository(session).checkpoint("binding", "events", PARSER_REVISION)
        assert checkpoint.parser_sha256 == pin["decoder_sha256"]
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job_id)]
    messages = [event for event in events if event.kind == "message.assistant"]
    assert [event.payload["content"] for event in messages] == ["Independent expected answer"]
    assert messages[0].metadata.source_framework == "local-test"
    assert messages[0].payload["_codeplane_source"]["framework"] == name
    receipts = [event for event in events if event.kind == "integration.batch.accepted"]
    assert len(receipts) == 1 and receipts[0].raw_event["records"] == _batch()["records"]
    assert receipts[0].metadata.source_framework is None


@pytest.mark.parametrize("name", ["local", "copilot"])
@pytest.mark.parametrize("change", ["missing", "changed"])
async def test_definition_drift_blocks_new_progress_but_preserves_identity_and_accepted_retry(
    session_factory, mapping_home, name, change
):
    path = _write(mapping_home / "explicit", name)
    config = CPLConfig(native_mappings=NativeMappingsConfig(extra_dirs=[str(path.parent)]))
    async with _api(session_factory, config) as client:
        registered = await client.post("/api/integrations/observations", json=_registration(name))
        assert registered.status_code == 201, registered.text
        first = await client.post("/api/integrations/binding/records", json=_batch())
        assert first.status_code == 200, first.text
        before = (await client.get("/api/integrations/binding/events")).json()
        async with session_factory() as session:
            original = await IntegrationRepository(session).get("binding")
        if change == "missing":
            path.unlink()
        else:
            path.write_text(_DEFINITION.replace('"1"', '"2"'))
        repeated = await client.post("/api/integrations/observations", json=_registration(name))
        assert repeated.status_code == 201 and repeated.json()["id"] == registered.json()["id"]
        replay = await client.post("/api/integrations/binding/records", json=_batch())
        assert replay.status_code == 200 and replay.json()["replay"]
        next_batch = {**_batch("New"), "startIndex": 1, "prefixSha256": first.json()["prefixSha256"]}
        rejected = await client.post("/api/integrations/binding/records", json=next_batch)
        assert rejected.status_code == 409 and "reconciliation" in rejected.text
        assert (await client.get("/api/integrations/binding/events")).json() == before
        async with session_factory() as session:
            assert await IntegrationRepository(session).get("binding") == original


async def test_changed_definition_is_rejected_even_before_first_append(session_factory, mapping_home):
    path = _write(mapping_home / ".codeplane" / "mappings")
    async with _api(session_factory, CPLConfig()) as client:
        registered = await client.post("/api/integrations/observations", json=_registration())
        assert registered.status_code == 201, registered.text
        path.write_text(_DEFINITION.replace('"1"', '"2"'))
        rejected = await client.post("/api/integrations/binding/records", json=_batch())
        assert rejected.status_code == 409
        assert (await client.get("/api/integrations/binding/events")).json()["events"] == []


async def test_fresh_legacy_definition_migrates_and_ingests_from_configured_destination(session_factory, mapping_home):
    legacy = _write(mapping_home / ".traceforge" / "mappings")
    destination = mapping_home / "configured-migration"
    config = CPLConfig(native_mappings=NativeMappingsConfig(migrated_user_dir=str(destination)))
    async with _api(session_factory, config) as client:
        registered = await client.post("/api/integrations/observations", json=_registration())
        assert registered.status_code == 201, registered.text
        migrated = destination / "local.yaml"
        assert migrated.read_bytes() == legacy.read_bytes()
        legacy.unlink()
        first = await client.post("/api/integrations/binding/records", json=_batch())
        assert first.status_code == 200 and first.json()["acceptedRecords"] == 1
        replay = await client.post("/api/integrations/binding/records", json=_batch())
        assert replay.status_code == 200 and replay.json()["replay"]
        history = (await client.get("/api/integrations/binding/events")).json()["events"]
        assert [event["payload"]["content"] for event in history if event["kind"] == "message.assistant"] == [
            "Independent expected answer"
        ]
    async with session_factory() as session:
        binding = await IntegrationRepository(session).get("binding")
        assert binding.config["mapping_pin"]["source_path"] == str(migrated.resolve())


@pytest.mark.parametrize(
    "definition",
    ["events: [", "x" * (1024 * 1024 + 1), _DEFINITION + "preprocessor: missing\n"],
    ids=["malformed", "oversized", "unknown-preprocessor"],
)
async def test_invalid_fresh_mapping_does_not_create_binding_or_fall_back(session_factory, mapping_home, definition):
    _write(mapping_home / ".codeplane" / "mappings", "copilot", definition)
    async with _api(session_factory, CPLConfig()) as client:
        result = await client.post("/api/integrations/observations", json=_registration("copilot"))
        assert result.status_code == 400
    async with session_factory() as session:
        assert await IntegrationRepository(session).get("binding") is None


async def test_unpinned_history_keeps_legacy_resolution_without_implicit_migration(session_factory, mapping_home):
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        registration = HistoryRegistration(bindings, jobs, ProjectRepository(session))
        job = await registration.register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="producer"
        )
        row = await session.get(IntegrationBindingRow, "binding")
        row.config_json = json.dumps({"format": "copilot", "external_session_id": "producer"})
        await session.commit()
        legacy = _write(mapping_home / ".traceforge" / "mappings", "copilot")
        service = ObservationRecords(bindings, jobs, EventBus())
        batch = ObservationRecordBatch(source_key="events", start_index=0, records=_batch()["records"])
        await service.append("binding", batch)
        legacy.unlink()
        repeated = await registration.register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="producer"
        )
        assert repeated.id == job.id and "mapping_pin" not in (await bindings.get("binding")).config
        assert (await service.append("binding", batch)).replay
        assert not (mapping_home / ".codeplane" / "mappings").exists()


async def test_configured_custom_file_acquisition_uses_the_registered_mapping(session_factory, mapping_home):
    path = _write(mapping_home / "explicit")
    registry = MappingRegistry(NativeMappingsConfig(extra_dirs=[str(path.parent)]))
    async with session_factory() as session:
        job = await HistoryRegistration(
            IntegrationRepository(session),
            JobRepository(session),
            ProjectRepository(session),
            mapping_registry=registry,
        ).register(binding_id="binding", project_id="proj-1", format_id="local", external_session_id="producer")
        await session.commit()
    source = mapping_home / "snapshot.json"
    source.write_text(json.dumps(_batch()["records"]))
    channel = AcquisitionChannel(
        session_factory, EventBus(), binding_id="binding", source_key="snapshot", mapping_registry=registry
    )
    assert (await channel.accept(await file_snapshot(source))).accepted_records == 1
    assert await channel.accept(await file_snapshot(source)) is None
    assert (await channel.finish()).session_closed
    async with session_factory() as session:
        messages = [
            row.event
            for row in await EventRepository(session).list_after(0, job_id=job.id)
            if row.event.kind == "message.assistant"
        ]
    assert [event.payload["content"] for event in messages] == ["Independent expected answer"]
