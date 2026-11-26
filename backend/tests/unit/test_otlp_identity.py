import json
from copy import deepcopy

import pytest

from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_otlp_admission import _ingest
from backend.tests.unit.test_otlp_admission import otlp_store as otlp_store
from backend.tests.unit.test_otlp_consumers import ProductionConsumer, _tool


@pytest.mark.parametrize("snake_case", [False, True])
async def test_historical_and_live_hex_case_share_operation_but_retain_raw_records(otlp_store, snake_case):
    factory, job, token = otlp_store
    body = _tool(1, 1)
    raw = deepcopy(body["resourceSpans"][0]["scopeSpans"][0]["spans"][0])
    for camel, snake in (("traceId", "trace_id"), ("spanId", "span_id"), ("parentSpanId", "parent_span_id")):
        value = raw.pop(camel)
        raw[snake if snake_case else camel] = value.upper()
    # Historical simplified spans have no resource envelope.
    raw["attributes"] = {entry["key"]: next(iter(entry["value"].values())) for entry in raw["attributes"]}
    consumer = ProductionConsumer(factory)
    async with factory() as session:
        await ObservationRecords(IntegrationRepository(session), JobRepository(session), consumer.bus).append(
            "otlp",
            ObservationRecordBatch(source_key="history", start_index=0, records=[raw]),
        )
    while consumer.pending:
        await consumer.pending.pop(0)
    await consumer.append(factory, token, body)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        tools = [event for event in events if event.kind == "tool.call.completed"]
        assert len(tools) == 2 and tools[0].raw_event == raw
        assert (
            tools[0].payload["_codeplane_observation"]["operation_id"]
            == tools[1].payload["_codeplane_observation"]["operation_id"]
        )
        assert tools[0].metadata.span_id == tools[1].metadata.span_id
        assert tools[0].metadata.parent_id == tools[1].metadata.parent_id
        assert tools[0].payload["turn_id"] == tools[1].payload["turn_id"]
        assert (await TelemetrySummaryRepository(session).get(job.id))["tool_call_count"] == 1


async def test_same_local_tool_id_in_other_trace_never_inherits_arguments(otlp_store):
    factory, job, token = otlp_store
    start = _tool(1)
    span = start["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span.pop("endTimeUnixNano")
    span["attributes"].append(
        {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": '{"path":"trace-A-only"}'}}
    )
    await _ingest(factory, token, start)
    other = _tool(1, 1)
    other["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] = "abcdef1234567890abcdef1234567890"
    await _ingest(factory, token, other)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        completed = next(event for event in events if event.kind == "tool.call.completed")
        assert "arguments" not in completed.payload
        assert completed.payload["_codeplane_pairing"]["reason"] == "missing_start"
        projection = await IntegrationRepository(session).projection("otlp")
        assert len(projection.buffer["pairing"]) == 1


async def test_live_hex_normalization_does_not_mutate_original_raw_observation(otlp_store):
    factory, job, token = otlp_store
    body = _tool(1, 1)
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["traceId"] = span["traceId"].upper()
    assert await _ingest(factory, token, body) == (1, 0)
    span["traceId"] = span["traceId"].lower()
    assert await _ingest(factory, token, body) == (0, 1)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        completed = next(event for event in events if event.kind == "tool.call.completed")
        assert completed.raw_event["traceId"] == span["traceId"].upper()
        assert completed.metadata.span_id.startswith(f"otlp:{span['traceId']}:")


async def test_metadata_only_operation_pairs_after_restore_without_fabricated_tool_call_id(otlp_store):
    factory, job, token = otlp_store
    start = _tool(1)
    raw = start["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    raw.pop("endTimeUnixNano")
    raw["attributes"] = [entry for entry in raw["attributes"] if entry["key"] != "gen_ai.tool.call.id"]
    raw["attributes"].append({"key": "gen_ai.tool.call.arguments", "value": {"stringValue": '{"path":"retained"}'}})
    await _ingest(factory, token, start)
    finished = _tool(1, 1)
    raw_end = finished["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    raw_end["attributes"] = [entry for entry in raw_end["attributes"] if entry["key"] != "gen_ai.tool.call.id"]
    # A new service/repository reconstructs pairing from the persisted checkpoint.
    await _ingest(factory, token, finished)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        completed = next(event for event in events if event.kind == "tool.call.completed")
        assert json.loads(completed.payload["arguments"]) == {"path": "retained"}
        assert "tool_call_id" not in completed.payload
        assert "_codeplane_pairing" not in completed.payload
        assert (await IntegrationRepository(session).projection("otlp")).buffer["pairing"] == []


async def test_opaque_historical_identifiers_are_not_wire_validated_or_case_folded(otlp_store):
    factory, job, _ = otlp_store
    async with factory() as session:
        service = ObservationRecords(IntegrationRepository(session), JobRepository(session), EventBus())
        await service.append(
            "otlp",
            ObservationRecordBatch(
                source_key="opaque",
                start_index=0,
                records=[
                    {"name": "chat", "trace_id": "OpaqueTrace", "span_id": "OpaqueSpan", "attributes": {}},
                ],
            ),
        )
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        observed = next(event for event in events if event.kind == "raw")
        assert observed.metadata.span_id == "otlp:OpaqueTrace:OpaqueSpan"
