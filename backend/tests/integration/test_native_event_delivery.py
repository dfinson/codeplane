"""Native delivery crosses real mapping, processor and persistence boundaries."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from traceforge.adapters.mapped_json import MappedJsonAdapter
from traceforge.config.mappings import resolve_mapping_path

from backend.persistence.event_repo import EventRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.services.ingest._base import TraceForgeIngestBase
from backend.tests.unit.test_approval_service import session_factory as _source_factory

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
