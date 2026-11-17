"""Real admission/recovery regressions for native source history and decoder state."""

from copy import deepcopy
from dataclasses import asdict

import pytest

from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.export_metadata import export_metadata
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.mapping import PARSER_REVISION, DecoderState
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_approval_service import session_factory as session_factory
from backend.tests.unit.test_native_format_conformance import _decoder


async def _register(session, format_id, binding_id="binding"):
    bindings, jobs = IntegrationRepository(session), JobRepository(session)
    job = await HistoryRegistration(bindings, jobs, ProjectRepository(session)).register(
        binding_id=binding_id, project_id="proj-1", format_id=format_id, external_session_id="source"
    )
    await session.commit()
    return ObservationRecords(bindings, jobs, EventBus()), job


async def test_receipt_retains_zero_and_multi_output_records_without_fabricated_events(session_factory):
    records = [
        {"type": "future.record", "unmapped": {"sequence": 41, "private": "retained private source bytes"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading."},
                    {"type": "tool_use", "id": "call", "name": "read_file", "input": {"path": "a.py"}},
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "contents"}]},
        },
    ]
    async with session_factory() as session:
        service, job = await _register(session, "claude")
        batch = ObservationRecordBatch(source_key="events", start_index=0, records=records, end_session=True)
        first = await service.append("binding", batch)
        before = await EventRepository(session).list_after(0, job_id=job.id)
        assert first.accepted_records == first.next_index == 3
        assert (await service.append("binding", batch)).replay
        assert await EventRepository(session).list_after(0, job_id=job.id) == before
    async with session_factory() as session:
        rows = await EventRepository(session).list_after(0, job_id=job.id)
        receipt = next(row.event for row in rows if row.event.kind == "integration.batch.accepted")
        assert receipt.raw_event == {
            "contract": "codeplane-source-batch-v1",
            "source_key": "events",
            "start_index": 0,
            "prefix_sha256": "0" * 64,
            "records": records,
        }
        source_events = [row.event for row in rows if "_codeplane_source" in row.event.payload]
        assert [event.kind for event in source_events] == ["message.assistant", "tool.call.completed"]
        assert [event.payload["_codeplane_source"]["record_index"] for event in source_events] == [1, 2]
        assert source_events[1].payload["tool_call_id"] == "call"
        assert source_events[1].payload["arguments"] == {"path": "a.py"}
        exported = export_metadata(
            receipt,
            identity_key=b"x" * 32,
            integration="claude",
            provider="claude",
            role="primary",
            approved_models=frozenset(),
        )
        assert "retained private source bytes" not in str(exported)
        assert "raw_event" not in exported


async def test_admission_snapshots_records_before_awaiting_repositories(session_factory, monkeypatch):
    async with session_factory() as session:
        service, job = await _register(session, "copilot")
        batch = ObservationRecordBatch(
            source_key="events",
            start_index=0,
            records=[{"type": "assistant.message", "data": {"content": "Original"}}],
        )
        admitted = deepcopy(batch.records)
        original = IntegrationRepository.get

        async def change_caller_input(repository, binding_id):
            binding = await original(repository, binding_id)
            batch.records[0]["data"]["content"] = "Changed by caller"
            return binding

        monkeypatch.setattr(IntegrationRepository, "get", change_caller_input)
        await service.append("binding", batch)
        rows = await EventRepository(session).list_after(0, job_id=job.id)
        message = next(row.event for row in rows if row.event.kind == "message.assistant")
        receipt = next(row.event for row in rows if row.event.kind == "integration.batch.accepted")
        assert message.payload["content"] == "Original"
        assert receipt.raw_event["records"] == admitted


async def test_receipt_write_failure_rolls_back_raw_history_and_checkpoint(session_factory, monkeypatch):
    async with session_factory() as session:
        service, job = await _register(session, "copilot")
        first = await service.append(
            "binding",
            ObservationRecordBatch(
                source_key="events",
                start_index=0,
                records=[{"type": "assistant.message", "data": {"content": "First"}}],
            ),
        )
        repo = IntegrationRepository(session)
        checkpoint = await repo.checkpoint("binding", "events", PARSER_REVISION)
        projection = await repo.projection("binding")
        history = await EventRepository(session).list_after(0, job_id=job.id)
        batch = ObservationRecordBatch(
            source_key="events",
            start_index=1,
            prefix_sha256=first.prefix_sha256,
            records=[{"type": "assistant.message", "data": {"content": "Second"}}],
        )
        original = EventRepository.append

        async def fail_receipt(repository, event):
            if event.kind == "integration.batch.accepted":
                raise OSError("receipt write failed")
            return await original(repository, event)

        with monkeypatch.context() as scoped:
            scoped.setattr(EventRepository, "append", fail_receipt)
            with pytest.raises(OSError, match="receipt write failed"):
                await service.append("binding", batch)
        assert await repo.checkpoint("binding", "events", PARSER_REVISION) == checkpoint
        assert await repo.projection("binding") == projection
        assert await EventRepository(session).list_after(0, job_id=job.id) == history
        second = await service.append("binding", batch)
        assert second.next_index == 2 and not second.replay
        receipts = [
            row.event
            for row in await EventRepository(session).list_after(0, job_id=job.id)
            if row.event.kind == "integration.batch.accepted"
        ]
        assert len(receipts) == 2
        assert receipts[1].raw_event["records"] == batch.records


async def test_vscode_resume_citations_indexes_and_failed_record_survive_new_sessions(session_factory):
    citation_a = {"value": "a.py", "license": "MIT", "snippet": "a"}
    citation_b = {"value": "b.py", "license": "MIT", "snippet": "b"}
    async with session_factory() as session:
        service, job = await _register(session, "copilot_vscode")
        first = await service.append(
            "binding",
            ObservationRecordBatch(
                source_key="journal",
                start_index=0,
                records=[
                    {
                        "kind": 0,
                        "v": {
                            "version": 3,
                            "requests": [{"requestId": "first", "modelId": "model-a", "codeCitations": [citation_a]}],
                        },
                    }
                ],
            ),
        )
        other, _ = await _register(session, "copilot_vscode", "other")
        await other.append(
            "other",
            ObservationRecordBatch(
                source_key="journal",
                start_index=0,
                records=[{"kind": 0, "v": {"version": 3, "requests": [{"requestId": "unrelated"}]}}],
            ),
        )
    async with session_factory() as session:
        service = ObservationRecords(IntegrationRepository(session), JobRepository(session), EventBus())
        before = await EventRepository(session).list_after(0, job_id=job.id)
        with pytest.raises(ValueError, match="Malformed source timestamp"):
            await service.append(
                "binding",
                ObservationRecordBatch(
                    source_key="journal",
                    start_index=1,
                    prefix_sha256=first.prefix_sha256,
                    records=[
                        {"kind": 0, "v": {"version": 3, "requests": [{"requestId": "bad", "timestamp": "not-a-date"}]}}
                    ],
                ),
            )
        assert await EventRepository(session).list_after(0, job_id=job.id) == before
        second = await service.append(
            "binding",
            ObservationRecordBatch(
                source_key="journal",
                start_index=1,
                prefix_sha256=first.prefix_sha256,
                records=[
                    {"kind": 1, "k": ["requests", 0, "codeCitations"], "v": [citation_a, citation_b]},
                    {"kind": 2, "k": ["requests"], "v": [{"requestId": "second", "modelId": "model-b"}]},
                ],
            ),
        )
    async with session_factory() as session:
        service = ObservationRecords(IntegrationRepository(session), JobRepository(session), EventBus())
        await service.append(
            "binding",
            ObservationRecordBatch(
                source_key="journal",
                start_index=3,
                prefix_sha256=second.prefix_sha256,
                records=[{"kind": 2, "k": ["requests", 1, "response"], "v": [{"value": "Second answer"}]}],
            ),
        )
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        citations = [event for event in events if event.metadata.raw_kind == "code_citation"]
        assert [event.payload["value"] for event in citations] == ["a.py", "b.py"]
        assert all(event.payload["request_id"] == "first" for event in citations)
        answer = next(event for event in events if event.kind == "message.assistant")
        assert answer.payload["request_id"] == "second" and answer.payload["model"] == "model-b"


async def test_opencode_authorship_and_pairing_restore_across_batches(session_factory):
    running = {
        "type": "message.part.updated.1",
        "data": {
            "sessionID": "s1",
            "part": {
                "id": "p1",
                "messageID": "m1",
                "type": "tool",
                "callID": "call",
                "tool": "read_file",
                "state": {"status": "running", "input": {"path": "a.py"}},
            },
        },
    }
    async with session_factory() as session:
        service, job = await _register(session, "opencode")
        first = await service.append(
            "binding",
            ObservationRecordBatch(
                source_key="rows",
                start_index=0,
                records=[
                    {
                        "type": "message.updated.1",
                        "data": {"sessionID": "s1", "info": {"id": "m1", "role": "assistant"}},
                    },
                    running,
                ],
            ),
        )
        other, _ = await _register(session, "opencode", "other")
        await other.append(
            "other",
            ObservationRecordBatch(
                source_key="rows",
                start_index=0,
                records=[
                    {"type": "message.updated.1", "data": {"sessionID": "s1", "info": {"id": "m1", "role": "user"}}}
                ],
            ),
        )
    async with session_factory() as session:
        service = ObservationRecords(IntegrationRepository(session), JobRepository(session), EventBus())
        failed = deepcopy(running)
        failed["data"]["part"]["state"] = {"status": "error", "error": "missing"}
        await service.append(
            "binding",
            ObservationRecordBatch(
                source_key="rows",
                start_index=2,
                prefix_sha256=first.prefix_sha256,
                records=[
                    {
                        "type": "message.part.updated.1",
                        "data": {
                            "sessionID": "s1",
                            "part": {"id": "p2", "messageID": "m1", "type": "text", "text": "Answer"},
                        },
                    },
                    failed,
                ],
            ),
        )
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        answer = next(event for event in events if event.payload.get("text") == "Answer")
        assert answer.kind == "message.assistant"
        tool = next(event for event in events if event.kind == "tool.call.failed")
        assert tool.payload["call_id"] == "call"
        assert tool.payload["input"] == {"path": "a.py"}
        assert "_codeplane_pairing" not in tool.payload
        assert tool.metadata.tool_display is not None
        assert (await IntegrationRepository(session).projection("binding")).buffer["pairing"] == []


async def test_retained_receipt_counts_toward_total_projection_budget(session_factory):
    async with session_factory() as session:
        service, job = await _register(session, "copilot_vscode")
        batch = ObservationRecordBatch(
            source_key="journal",
            start_index=0,
            records=[
                {
                    "kind": 0,
                    "v": {
                        "version": 3,
                        "requests": [
                            {
                                "requestId": "large",
                                "modelId": "m" * 640_000,
                                "message": {"text": "q"},
                                "response": [{"value": "answer"} for _ in range(10)],
                            }
                        ],
                    },
                }
            ],
        )
        assert len(batch.model_dump_json().encode()) < 1024 * 1024
        with pytest.raises(ValueError, match="Observation projection exceeds its bounded buffer"):
            await service.append("binding", batch)
        assert await EventRepository(session).list_after(0, job_id=job.id) == []
        assert await IntegrationRepository(session).projection("binding") is None


@pytest.mark.parametrize("span_type", ["function", "generation", "agent"])
def test_unstarted_openai_spans_do_not_invent_execution(span_type):
    decoder = _decoder("openai_agents")
    assert decoder.decode({"object": "trace.span", "id": "unstarted", "span_data": {"type": span_type}}, 0) == ()


def test_opencode_invalid_record_restores_prior_role_and_checkpoint_is_detached():
    decoder = _decoder("opencode")
    decoder.decode(
        {"type": "message.updated.1", "data": {"sessionID": "s1", "info": {"id": "m1", "role": "assistant"}}}, 0
    )
    saved = decoder.checkpoint()
    with pytest.raises(ValueError, match="Malformed OpenCode event data JSON"):
        decoder.decode({"type": "message.updated.1", "data": "{"}, 1)
    assert decoder.checkpoint() == saved
    with pytest.raises(ValueError, match="Malformed source timestamp"):
        decoder.decode(
            {
                "type": "message.updated.1",
                "data": {"sessionID": "s1", "timestamp": "invalid", "info": {"id": "m1", "role": "user"}},
            },
            1,
        )
    assert decoder.checkpoint() == saved
    restored = _decoder("opencode")
    restored.restore(DecoderState(**asdict(saved)))
    saved.preprocessor["roles"]["s1"]["m1"] = "user"
    event = restored.decode(
        {
            "type": "message.part.updated.1",
            "data": {"sessionID": "s1", "part": {"id": "p1", "messageID": "m1", "type": "text", "text": "Answer"}},
        },
        1,
    )[0]
    assert event.kind == "message.assistant"
