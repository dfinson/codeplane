"""Native delivery crosses real mapping, processor and persistence boundaries."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from traceforge.adapters.mapped_json import MappedJsonAdapter
from traceforge.config.mappings import resolve_mapping_path

from backend.persistence.event_repo import EventRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.services.ingest._base import TraceForgeIngestBase
from backend.tests.unit.test_approval_service import session_factory as _source_factory
from backend.tests.unit.test_ingest_sources import _LOCAL_ID, _TASK_ID, _write_native_metadata

native_factory = _source_factory


@pytest.fixture
async def native_disk_factory(native_factory, tmp_path):
    from sqlalchemy import event as sa_event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.models.db import Base
    from backend.persistence.database import _set_sqlite_pragmas
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.project_repo import ProjectRepository

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'native.db'}")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with native_factory() as source:
        job = await JobRepository(source).get("job-1")
    async with factory() as target:
        await ProjectRepository(target).create("proj-1", "Native fixture", ["/test"])
        await JobRepository(target).create(job)
        await target.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def native_admission_stack(native_disk_factory, tmp_path, monkeypatch):
    from backend.config import CPLConfig
    from backend.models.domain import JobSource
    from backend.persistence.database import serialized_write
    from backend.persistence.job_repo import JobRepository
    from backend.services.completers.copilot_steer import CopilotSteerClient
    from backend.services.events.ingest_service import IngestService
    from backend.services.ingest.copilot_source import SessionStateWatcher
    from backend.services.runtime import RuntimeService

    factory = native_disk_factory
    async with serialized_write(factory) as session:
        original = await JobRepository(session).get("job-1")
        job = replace(original, id="native-admission", source=JobSource.copilot_cli, external_session_id=_LOCAL_ID)
        await JobRepository(session).create(job)
    root = tmp_path / "session-state"
    _write_native_metadata(root)
    monkeypatch.setattr("backend.services.ingest.copilot_source._SESSION_STATE_DIR", root)
    monkeypatch.setattr("backend.services.events.ingest_service.load_config", CPLConfig)
    bus = EventBus()
    sse = SSEManager()
    connection = SSEConnection(job.id)
    sse.register(connection)

    async def persist(event):
        async with serialized_write(factory) as session:
            cursor = await EventRepository(session).append(event)
        await sse.broadcast_domain_event(event, storage_cursor=cursor)

    async def exists(event):
        async with factory() as session:
            return await EventRepository(session).contains(event)

    bus.subscribe(persist, required=True)
    processor = EventProcessor(bus, persisted=exists)
    runtime = RuntimeService(session_factory=factory, event_bus=bus, adapter_registry=MagicMock(), config=CPLConfig())
    runtime._resume_orphaned = AsyncMock()
    steer = CopilotSteerClient("fixture-token")
    post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "https://fixture.invalid")))
    monkeypatch.setattr(steer._client, "post", post)
    watcher = SessionStateWatcher(processor, runtime, factory, CPLConfig(), steer_client=steer)
    watcher._prompt_captured.add(job.id)
    runtime._ingest_service = IngestService(factory, session_state_watcher=watcher)
    try:
        yield SimpleNamespace(
            runtime=runtime, factory=factory, job=job, post=post, watcher=watcher, connection=connection
        )
    finally:
        await steer.close()


class TestNativeOperatorAdmission:
    async def test_admission_without_echo_survives_new_runtime_and_database_connection(self, native_admission_stack):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from backend.config import CPLConfig
        from backend.services.runtime import RuntimeService

        stack = native_admission_stack
        assert await stack.runtime.send_operator_message(stack.job.id, "Inspect the fixture") is True
        stack.post.assert_awaited_once_with(
            f"https://api.enterprise.githubcopilot.com/agents/tasks/{_TASK_ID}/steer",
            json={"content": "Inspect the fixture", "type": "user"},
        )
        await stack.factory.kw["bind"].dispose()
        engine = create_async_engine(stack.factory.kw["bind"].url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        restarted = RuntimeService(
            session_factory=factory, event_bus=EventBus(), adapter_registry=MagicMock(), config=CPLConfig()
        )
        try:
            async with factory() as session:
                events = await EventRepository(session).list_all_events_by_job(stack.job.id)
            assert len(events) == 1, "Relay admission must survive without any native echo"
            receipt = events[0]
            assert receipt.kind == "message.user"
            assert receipt.payload["operator_content"] == "Inspect the fixture"
            assert receipt.payload["admission_status"] == "accepted"
            assert receipt.payload["execution_status"] == "unconfirmed"
            assert "Native operator admission receipt" in receipt.payload["content"]
            assert receipt.id in receipt.payload["content"]
            assert "Execution unconfirmed" in receipt.payload["content"]
            assert (await restarted._get_job(stack.job.id)).external_session_id == _LOCAL_ID
            assert not restarted._agent_sessions
            stack.runtime._resume_orphaned.assert_not_awaited()
        finally:
            await engine.dispose()

    async def test_identical_instructions_have_distinct_visible_receipts(self, native_admission_stack):
        from backend.services.artifacts.snapshot_helpers import _build_transcript

        stack = native_admission_stack
        for _ in range(2):
            assert await stack.runtime.send_operator_message(stack.job.id, "Repeat the instruction")
        async with stack.factory() as session:
            events = await EventRepository(session).list_all_events_by_job(stack.job.id)
        assert len(events) == 2, "Two admitted instructions need two durable identities"
        assert len({event.id for event in events}) == 2
        assert all(event.payload["operator_content"] == "Repeat the instruction" for event in events)
        assert events[0].payload["content"] != events[1].payload["content"]
        transcript = _build_transcript(events, [], None, None, filter_deltas=True)
        assert [entry.event_id for entry in transcript] == [event.id for event in events]
        assert all("Native operator admission receipt" in entry.content for entry in transcript)
        frames = [stack.connection.queue.get_nowait() for _ in range(stack.connection.queue.qsize())]
        assert len(frames) == 2
        for frame, event in zip(frames, events, strict=True):
            payload = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
            assert payload["eventId"] == event.id
            assert payload["content"] == event.payload["content"]

    @pytest.mark.parametrize("outcome", ["rejected", "uncertain"])
    async def test_failed_or_uncertain_relay_never_creates_accepted_receipt(self, native_admission_stack, outcome):
        from backend.services.completers.copilot_steer import NativeDeliveryError

        stack = native_admission_stack
        if outcome == "rejected":
            stack.post.return_value = httpx.Response(403, request=httpx.Request("POST", "https://fixture.invalid"))
        else:
            stack.post.side_effect = httpx.ReadTimeout("fixture response was lost")
        with pytest.raises(NativeDeliveryError) as failure:
            await stack.runtime.send_operator_message(stack.job.id, "Inspect the fixture")
        assert failure.value.uncertain is (outcome == "uncertain")
        async with stack.factory() as session:
            assert not await EventRepository(session).list_all_events_by_job(stack.job.id)
        stack.post.assert_awaited_once()
        stack.runtime._resume_orphaned.assert_not_awaited()

    async def test_uncorrelated_identical_observations_are_preserved_and_replay_is_idempotent(
        self, native_admission_stack
    ):
        from backend.services.ingest._base import _NativeIdentity

        stack = native_admission_stack
        assert await stack.runtime.send_operator_message(stack.job.id, "Identical instruction")
        identity = _NativeIdentity("copilot", _LOCAL_ID, stack.job.id, datetime.now(UTC))
        observations = [
            next(
                identity.events(
                    {"id": vendor_id, "type": "user.message", "data": {"content": "Identical instruction"}}, i
                )
            )
            for i, vendor_id in enumerate(("native-message-one", "native-message-two"), start=10)
        ]
        for event in observations:
            await stack.watcher._handle_tf_event(stack.job.id, event)
        for event in observations:
            await stack.watcher._handle_tf_event(stack.job.id, event)
        async with stack.factory() as session:
            events = await EventRepository(session).list_all_events_by_job(stack.job.id)
        assert len(events) == 3, "An admission and two distinct observations must not collapse by matching text"
        assert [event.id for event in events[1:]] == [event.id for event in observations]
        assert [event.payload["content"] for event in events[1:]] == ["Identical instruction"] * 2
        assert events[0].payload["admission_status"] == "accepted"
        assert "_codeplane_source" not in events[0].payload
        assert all(event.payload["_codeplane_source"]["session_id"] == _LOCAL_ID for event in events[1:])

    async def test_storage_failure_after_relay_is_uncertain_and_never_retried(
        self, native_admission_stack, monkeypatch
    ):
        from sqlalchemy.exc import OperationalError

        from backend.services.completers.copilot_steer import NativeDeliveryError

        stack = native_admission_stack
        append = AsyncMock(side_effect=OperationalError("INSERT", {}, RuntimeError("fixture disk unavailable")))
        monkeypatch.setattr(EventRepository, "append", append)
        with pytest.raises(NativeDeliveryError) as failure:
            await stack.runtime.send_operator_message(stack.job.id, "Inspect the fixture")
        assert failure.value.uncertain
        stack.post.assert_awaited_once()
        append.assert_awaited_once()
        assert stack.connection.queue.empty()


class TestRuntimeContract:
    async def test_native_event_reaches_owning_job(self, native_factory):
        bus = EventBus()
        sse = SSEManager()
        connection = SSEConnection("job-1")
        sse.register(connection)

        async def persist(event):
            async with native_factory() as session:
                cursor = await EventRepository(session).append(event)
                await session.commit()
            await sse.broadcast_domain_event(event, storage_cursor=cursor)

        bus.subscribe(persist)
        ingest = TraceForgeIngestBase(EventProcessor(bus), AsyncMock(), native_factory, "copilot")
        adapter = MappedJsonAdapter.from_yaml(str(resolve_mapping_path("copilot")), session_id="native-1")
        event = next(adapter.parse_dict({"type": "assistant.message", "data": {"content": "native output"}}))
        await ingest._handle_tf_event("job-1", event)
        async with native_factory() as session:
            stored = await EventRepository(session).list_after(0, job_id="job-1")
        assert len(stored) == 1
        assert stored[0].event.metadata.run_id == "native-1"
        assert not connection.queue.empty()

    def test_same_source_replay_preserves_ids_and_motivation(self):
        from datetime import UTC, datetime

        from backend.services.ingest import _base

        identity = getattr(_base, "_NativeIdentity", None)
        assert identity is not None, "Native mapped IDs must be stable across parser reconstruction"
        records = [
            {"type": "assistant.message", "data": {"content": "Inspect fixture"}},
            {"type": "tool.execution_start", "data": {"toolCallId": "x", "toolName": "view"}},
        ]
        anchor = datetime(2026, 11, 11, tzinfo=UTC)
        first = identity("copilot", "native-1", "job-1", anchor)
        second = identity("copilot", "native-1", "job-1", anchor)
        left = [e for i, record in enumerate(records) for e in first.events(record, i)]
        right = [e for i, record in enumerate(records) for e in second.events(record, i)]
        assert [e.model_dump(mode="json") for e in left] == [e.model_dump(mode="json") for e in right]
        assert left[1].metadata.motivation.source_event_ids == (left[0].id,)

    async def test_duplicate_identity_is_idempotent(self, native_factory):
        from sqlalchemy.exc import IntegrityError

        from backend.models.events import new_event

        event = new_event("job-1", "tool.vendor_extension", {"value": "fixture"}, event_id="stable-native")
        repeated = None
        async with native_factory() as session:
            first = await EventRepository(session).append(event)
            await session.commit()
        try:
            async with native_factory() as session:
                repeated = await EventRepository(session).append(event)
                await session.commit()
        except IntegrityError:
            pass
        assert repeated == first
        async with native_factory() as session:
            rows = await EventRepository(session).list_after(0, job_id="job-1")
        assert len(rows) == 1
        assert rows[0].event.kind == "tool.vendor_extension"


async def test_restart_reconstructs_buffered_start_without_duplicate_output(native_disk_factory, tmp_path):
    from traceforge.enricher import Enricher

    from backend.persistence.job_repo import JobRepository

    native_factory = native_disk_factory

    path = tmp_path / "events.jsonl"
    records = [
        {"type": "assistant.message", "data": {"content": "Inspect the fixture"}},
        {
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "call-1",
                "toolName": "view",
                "arguments": {"path": "fixture.py"},
            },
        },
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")

    def create_ingest():
        bus = EventBus()

        async def persist(event):
            async with native_factory() as session:
                await EventRepository(session).append(event)
                await session.commit()

        async def exists(event):
            async with native_factory() as session:
                return await EventRepository(session).contains(event)

        bus.subscribe(persist, required=True)
        return TraceForgeIngestBase(
            EventProcessor(bus, enricher=Enricher(), persisted=exists),
            AsyncMock(),
            native_factory,
            "copilot",
        )

    first = create_ingest()
    task = asyncio.create_task(first._tail_traceforge_events("native-replay", "job-1", path))
    try:
        for _ in range(500):
            async with native_factory() as session:
                checkpoint = await JobRepository(session).get_ingest_checkpoint("job-1")
            if checkpoint and checkpoint["record_count"] == 2:
                break
            await asyncio.sleep(0.02)
        assert checkpoint and checkpoint["record_count"] == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async with native_factory() as session:
        before = await EventRepository(session).list_all_events_by_job("job-1")
    assert len(before) == 1
    records.extend(
        [
            {
                "type": "tool.execution_complete",
                "data": {"toolCallId": "call-1", "success": True, "result": {"content": "fixture body"}},
            },
            {"type": "session.shutdown", "data": {"shutdownType": "normal"}},
        ]
    )
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    second = create_ingest()
    await asyncio.wait_for(second._tail_traceforge_events("native-replay", "job-1", path), timeout=10)
    async with native_factory() as session:
        after = await EventRepository(session).list_all_events_by_job("job-1")
        checkpoint = await JobRepository(session).get_ingest_checkpoint("job-1")
    assert checkpoint["record_count"] == 4
    assert sum(event.id == before[0].id for event in after) == 1
    completion = next(event for event in after if event.kind == "tool.call.completed")
    assert completion.payload["tool_name"] == "view"
    assert completion.payload["arguments"] == {"path": "fixture.py"}
    assert completion.metadata.motivation.source_event_ids == (before[0].id,)
    assert len({event.id for event in after}) == len(after)


def test_native_checkpoint_upgrade_uses_real_migration_chain(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import backend.config as config
    from backend.persistence.database import run_migrations

    monkeypatch.setattr(config, "_codeplane_dir", tmp_path / "home")
    database = tmp_path / "native.db"
    run_migrations(database)
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        alembic_config = Config()
        alembic_config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
        history = ScriptDirectory.from_config(alembic_config).iterate_revisions(revision, "base")
        assert "0073" in {item.revision for item in history}
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(jobs)")}
        assert "tail_offset" in columns
        assert columns["ingest_checkpoint"][2] == "TEXT"
        assert columns["ingest_checkpoint"][3] == 0
