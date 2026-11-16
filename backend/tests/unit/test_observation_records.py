from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from backend.models.db import IntegrationSourceCheckpointRow
from backend.models.domain import StateConflictError
from backend.models.events import new_event
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.models.session_event import EventMetadata
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.persistence.step_repo import StepRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.mapping import PARSER_REVISION
from backend.services.ingest.record_service import ObservationRecords
from backend.services.steps.persistence import StepPersistenceSubscriber
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def test_durability_failure_rolls_back_events_checkpoint_and_publication(session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        bus = AsyncMock()
        service = ObservationRecords(bindings, jobs, bus)
        original = EventRepository.append
        writes = 0

        async def fail_after_prefix(repo, event):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("injected durable write failure")
            return await original(repo, event)

        with monkeypatch.context() as scoped:
            scoped.setattr(EventRepository, "append", fail_after_prefix)
            with pytest.raises(OSError, match="durable write"):
                await service.append("binding", ObservationRecordBatch(
                    source_key="events", start_index=0,
                    records=[{"type": "user.message", "data": {"content": "Inspect source"}}],
                ))
        assert await bindings.checkpoint("binding", "events", PARSER_REVISION) is None
        assert await bindings.projection("binding") is None
        assert await EventRepository(session).list_after(0, job_id=job.id) == []
        bus.publish.assert_not_awaited()


async def test_independent_sources_under_one_binding_do_not_collide(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, AsyncMock())
        raw = {"type": "user.message", "data": {"content": "Identical bytes"}}
        for source in ("first", "second"):
            batch = ObservationRecordBatch(source_key=source, start_index=0, records=[raw])
            assert not (await service.append("binding", batch)).replay
            assert (await service.append("binding", batch)).replay
        events = [
            item.event for item in await EventRepository(session).list_after(0, job_id=job.id)
            if item.event.kind == "message.user"
        ]
        assert len(events) == 2 and events[0].id != events[1].id
        assert events[0].payload["turn_id"] != events[1].payload["turn_id"]


async def test_stale_progress_cannot_clear_a_reconciliation_fence(session_factory) -> None:
    async with session_factory() as session:
        bindings = IntegrationRepository(session)
        await HistoryRegistration(bindings, JobRepository(session), ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        values = {
            "binding_id": "binding", "source_key": "events", "revision": PARSER_REVISION,
            "record_count": 1, "prefix_sha256": "1" * 64, "canonical_sha256": "2" * 64,
            "parser_sha256": "3" * 64, "buffer": {}, "events": [],
            "projection_parser_sha256": "4" * 64,
            "projection_canonical_sha256": "5" * 64, "projection_buffer": {},
        }
        assert await bindings.commit_records(expected_version=0, projection_version=0, **values) == 1
        await bindings.require_reconciliation(
            "binding", "events", PARSER_REVISION, "prefix_changed", expected_version=1,
            projection_version=1,
        )
        with pytest.raises(StateConflictError, match="concurrently"):
            await bindings.commit_records(expected_version=1, projection_version=1, **values)
        checkpoint = await bindings.checkpoint("binding", "events", PARSER_REVISION)
        assert checkpoint.state == "reconciliation_required" and checkpoint.version == 2
        projection = await bindings.projection("binding")
        assert projection.state == "reconciliation_required" and projection.version == 2


async def test_acquisition_channels_share_pairing_steps_and_one_source_session_projection(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="langchain", external_session_id="source",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, AsyncMock())
        first = ObservationRecordBatch(source_key="starts", start_index=0, records=[
            {"event": "on_tool_start", "run_id": "vendor-call", "name": "write", "inputs": {"path": "file"}},
        ])
        started = await service.append("binding", first)
        assert started.projection_version == 1
        second = ObservationRecordBatch(source_key="ends", start_index=0, records=[
            {"event": "on_tool_error", "run_id": "vendor-call", "error": {"message": "Denied"}},
        ])
        failed = await service.append("binding", second)
        projection = await bindings.projection("binding")
        assert projection.version == 2
        assert projection.buffer["pairing"] == []
        assert projection.buffer["steps"]["current"]["tool_count"] == 1
        assert projection.buffer["steps"]["current"]["files_written"] == []
        retained = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        failure = next(event for event in retained if event.kind == "tool.call.failed")
        assert failure.metadata.namespace == ("observation-source", "ends")
        assert failure.payload["tool_name"] == "write"
        assert failure.payload["error"] == {"message": "Denied"}
        assert len([event for event in retained if event.kind == "step.started"]) == 1
        assert not any(event.kind == "step.completed" for event in retained)
        assert (await service.append("binding", first)).replay
        finished = await service.append("binding", ObservationRecordBatch(
            source_key="ends", start_index=1, prefix_sha256=failed.prefix_sha256, records=[], end_session=True,
        ))
        assert finished.session_closed and finished.projection_version == 3
        projection = await bindings.projection("binding")
        assert projection.buffer["steps"]["current"] is None
        assert projection.buffer["activities"] == []
        assert (await jobs.get(job.id)).state.value == "running"


async def test_distinct_assistant_occurrences_keep_one_fallback_segment_across_channels(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, AsyncMock())
        for source in ("first", "second"):
            await service.append("binding", ObservationRecordBatch(
                source_key=source, start_index=0,
                records=[{"type": "assistant.message", "data": {"content": "Identical bytes"}}],
            ))
        retained = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        messages = [event for event in retained if event.kind == "message.assistant"]
        assert len(messages) == 2 and messages[0].id != messages[1].id
        assert messages[0].payload["turn_id"] == messages[1].payload["turn_id"]
        assert len([event for event in retained if event.kind == "step.started"]) == 1
        assert len([event for event in retained if event.kind == "turn.summary"]) == 1


async def test_import_end_retains_unfinished_tool_without_claiming_an_outcome(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, AsyncMock())
        receipt = await service.append("binding", ObservationRecordBatch(
            source_key="events", start_index=0, end_session=True,
            records=[{"type": "tool.execution_start", "data": {
                "toolCallId": "unfinished", "toolName": "write", "arguments": {"path": "file"},
            }}],
        ))
        assert receipt.session_closed
        stored = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        tool = next(event for event in stored if event.kind == "tool.call.started")
        assert tool.metadata.namespace == ("observation-source", "events")
        assert tool.payload["turn_id"] and tool.payload["step_id"]
        assert tool.payload["_codeplane_pairing"]["reason"] == "missing_completion"
        assert "success" not in tool.payload and "result" not in tool.payload
        assert not any(event.kind in {"tool.call.completed", "tool.call.failed", "job.completed"} for event in stored)


async def test_mapped_operation_error_does_not_terminate_the_registered_source(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="maf", external_session_id="source",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, AsyncMock())
        receipt = await service.append("binding", ObservationRecordBatch(
            source_key="events", start_index=0, records=[
                {
                    "name": "agents.storage.read", "start_time_unix_nano": 0, "end_time_unix_nano": 1_000_000_000,
                    "status": {"code": 2, "message": "Storage read failed"}, "attributes": {},
                },
                {"name": "agents.adapter.process", "attributes": {"activity.type": "message"}},
            ],
        ))
        assert not receipt.session_closed
        await service.append("binding", ObservationRecordBatch(
            source_key="events", start_index=2, prefix_sha256=receipt.prefix_sha256,
            records=[{"name": "agents.adapter.process", "attributes": {"activity.type": "message"}}],
        ))
        stored = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        assert any(
            event.kind == "session.error" and event.payload["message"] == "Storage read failed" for event in stored
        )
        assert len([event for event in stored if event.kind == "message.user"]) == 2
        assert not (await bindings.projection("binding")).buffer["closed"]


async def test_historical_step_clock_survives_batches_and_empty_import_end(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        bus = EventBus()
        steps = StepRepository(session_factory)
        bus.subscribe(StepPersistenceSubscriber(steps), required=True)
        first = await ObservationRecords(bindings, jobs, bus).append("binding", ObservationRecordBatch(
            source_key="events", start_index=0, records=[
                {"type": "user.message", "timestamp": "2000-01-02T03:04:05Z", "data": {"content": "Inspect"}},
            ],
        ))
        second = await ObservationRecords(bindings, jobs, bus).append("binding", ObservationRecordBatch(
            source_key="events", start_index=1, prefix_sha256=first.prefix_sha256, records=[
                {"type": "assistant.message", "timestamp": "2000-01-02T03:04:17Z", "data": {"content": "Observed"}},
            ],
        ))
        await ObservationRecords(bindings, jobs, bus).append("binding", ObservationRecordBatch(
            source_key="events", start_index=2, prefix_sha256=second.prefix_sha256, records=[], end_session=True,
        ))
        start = datetime(2000, 1, 2, 3, 4, 5, tzinfo=UTC)
        end = datetime(2000, 1, 2, 3, 4, 17, tzinfo=UTC)
        stored = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        opened = next(event for event in stored if event.kind == "step.started")
        closed = next(event for event in stored if event.kind == "step.completed")
        assert opened.timestamp == start and closed.timestamp == end
        assert closed.payload["duration_ms"] == 12_000
        row = (await steps.get_by_job(job.id))[0]
        assert row.timing_source == "source"
        assert row.started_at.replace(tzinfo=UTC) == start
        assert row.completed_at.replace(tzinfo=UTC) == end and row.duration_ms == 12_000


async def test_buffered_tool_start_supplies_the_source_step_start_bound(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        service = ObservationRecords(bindings, jobs, AsyncMock())
        first = await service.append("binding", ObservationRecordBatch(
            source_key="events", start_index=0, records=[{
                "type": "tool.execution_start", "timestamp": "2000-01-02T03:04:05Z",
                "data": {"toolCallId": "call", "toolName": "view"},
            }],
        ))
        await service.append("binding", ObservationRecordBatch(
            source_key="events", start_index=1, prefix_sha256=first.prefix_sha256, end_session=True, records=[{
                "type": "tool.execution_complete", "timestamp": "2000-01-02T03:04:17Z",
                "data": {"toolCallId": "call", "success": True},
            }],
        ))
        stored = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        opened = next(event for event in stored if event.kind == "step.started")
        closed = next(event for event in stored if event.kind == "step.completed")
        assert opened.timestamp == datetime(2000, 1, 2, 3, 4, 5, tzinfo=UTC)
        assert closed.payload["duration_ms"] == 12_000


@pytest.mark.parametrize("missing_bound", ["start", "end", "single"])
async def test_unknown_source_bounds_never_become_measured_step_duration(session_factory, missing_bound) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        records = [
            {"type": "user.message", "data": {"content": "Inspect"}, "timestamp": "2000-01-02T03:04:05Z"},
            {"type": "assistant.message", "data": {"content": "Observed"}, "timestamp": "2000-01-02T03:04:17Z"},
        ]
        if missing_bound == "single":
            records = records[:1]
        else:
            records[0 if missing_bound == "start" else 1].pop("timestamp")
        bus = EventBus()
        steps = StepRepository(session_factory)
        bus.subscribe(StepPersistenceSubscriber(steps), required=True)
        await ObservationRecords(bindings, jobs, bus).append("binding", ObservationRecordBatch(
            source_key="events", start_index=0, records=records, end_session=True,
        ))
        stored = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
        closed = next(event for event in stored if event.kind == "step.completed")
        assert closed.payload["duration_ms"] is None
        field = "source_started_at" if missing_bound == "start" else "source_completed_at"
        assert closed.payload[field] is None
        row = (await steps.get_by_job(job.id))[0]
        assert row.duration_ms is None and row.timing_source == "source"
        assert (row.started_at if missing_bound == "start" else row.completed_at) is None


async def test_source_control_records_never_become_live_or_replayed_commands(session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()
        bus = AsyncMock()
        source_event = new_event(
            job.id, "job.completed", {"source_report": "Not CodePlane execution"},
            metadata=EventMetadata(integration_binding_id="binding"),
        )
        monkeypatch.setattr(
            "backend.services.ingest.record_service.NativeMappingDecoder.decode", lambda *args: (source_event,),
        )
        service = ObservationRecords(bindings, jobs, bus)
        await service.append("binding", ObservationRecordBatch(
            source_key="events", start_index=0, records=[{"type": "source.completion"}],
        ))
        bus.publish.assert_not_awaited()
        retained = (await EventRepository(session).list_after(0, job_id=job.id))[0].event
        assert retained.kind == "job.completed"
        manager, connection = SSEManager(), SSEConnection(job_id=job.id)
        manager.register(connection)
        try:
            await manager.broadcast_domain_event(retained)
            assert connection.queue.empty()
            await manager.replay_events(connection, EventRepository(session), jobs, 0)
            assert connection.queue.empty()
        finally:
            manager.unregister(connection)


async def test_enrichment_failure_does_not_advance_source_progress(session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        await session.commit()

        def fail_pairing(*args):
            raise ValueError("Pairing failed")

        monkeypatch.setattr("backend.services.ingest.record_service.ToolPairing.process", fail_pairing)
        bus = AsyncMock()
        with pytest.raises(ValueError, match="Pairing failed"):
            await ObservationRecords(bindings, jobs, bus).append("binding", ObservationRecordBatch(
                source_key="events", start_index=0, records=[{"type": "user.message", "data": {"content": "Read"}}],
            ))
        assert await bindings.checkpoint("binding", "events", PARSER_REVISION) is None
        assert await EventRepository(session).list_after(0, job_id=job.id) == []
        bus.publish.assert_not_awaited()


async def test_ambiguous_prior_source_projections_are_not_merged_or_reset(session_factory) -> None:
    async with session_factory() as session:
        bindings, jobs = IntegrationRepository(session), JobRepository(session)
        await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
            binding_id="binding", project_id="proj-1", format_id="copilot", external_session_id="source",
        )
        legacy_buffer = '{ "unresolved_old_state": true }'
        session.add(IntegrationSourceCheckpointRow(
            binding_id="binding", source_key="old-stream", parser_revision="old-parser",
            record_count=7, prefix_sha256="a" * 64, canonical_sha256="b" * 64, parser_sha256="c" * 64,
            buffer_json=legacy_buffer, state="accepted", version=3, updated_at=datetime.now(UTC),
        ))
        await session.commit()
        with pytest.raises(StateConflictError, match="explicit reconciliation"):
            await ObservationRecords(bindings, jobs, AsyncMock()).append("binding", ObservationRecordBatch(
                source_key="new-stream", start_index=0,
                records=[{"type": "assistant.message", "data": {"content": "New input"}}],
            ))
        assert await bindings.projection("binding") is None
        old = await session.get(IntegrationSourceCheckpointRow, ("binding", "old-stream", "old-parser"))
        assert old.buffer_json == legacy_buffer and old.version == 3 and old.record_count == 7
