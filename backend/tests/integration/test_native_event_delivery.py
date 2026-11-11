"""Native delivery crosses real mapping, processor and persistence boundaries."""

from unittest.mock import AsyncMock

from traceforge.adapters.mapped_json import MappedJsonAdapter
from traceforge.config.mappings import resolve_mapping_path

from backend.persistence.event_repo import EventRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.services.ingest._base import TraceForgeIngestBase
from backend.tests.unit.test_approval_service import session_factory as native_factory  # noqa: F401


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
