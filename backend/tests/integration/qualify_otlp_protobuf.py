"""Opt-in codec qualification, excluded from default core test collection."""

import json

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
from backend.services.events.telemetry_subscriber import TelemetrySubscriber
from backend.services.ingest.otlp_codec import protobuf_to_json
from backend.tests.integration.test_api_otlp import otlp_http as otlp_http
from backend.tests.integration.test_api_otlp import register


async def test_real_protobuf_http_ingest_uses_same_scope_receipts_and_hex_identity(
    otlp_http,
    event_bus,
    session_factory,
):
    operator, source, _ = otlp_http
    job, token = await register(operator, "binary")
    pending = []
    subscriber = TelemetrySubscriber(session_factory=session_factory, schedule_write=pending.append)
    subscriber.subscribe(event_bus)
    request = ExportTraceServiceRequest()
    resource = request.resource_spans.add()
    scope = resource.scope_spans.add()
    span = scope.spans.add(
        name="chat",
        trace_id=bytes.fromhex("1234567890abcdef1234567890abcdef"),
        span_id=bytes.fromhex("1234567890abcdef"),
        parent_span_id=bytes.fromhex("fedcba0987654321"),
        start_time_unix_nano=1_000_000,
        end_time_unix_nano=3_000_000,
    )
    span.attributes.add(key="gen_ai.operation.name").value.string_value = "chat"
    span.attributes.add(key="gen_ai.usage.input_tokens").value.int_value = 0
    span.attributes.add(key="gen_ai.usage.output_tokens").value.int_value = 4
    tool = scope.spans.add(
        name="read",
        trace_id=span.trace_id,
        span_id=bytes.fromhex("fedcba0987654321"),
        start_time_unix_nano=1_000_000,
        end_time_unix_nano=3_000_000,
    )
    tool.attributes.add(key="gen_ai.operation.name").value.string_value = "execute_tool"
    tool.attributes.add(key="gen_ai.tool.name").value.string_value = "read_file"
    tool.status.code = 1
    body = request.SerializeToString()
    headers = {"x-codeplane-ingest-token": token, "content-type": "application/x-protobuf"}
    endpoint = "/api/integrations/binary/traces"
    first = await source.post(endpoint, content=body, headers=headers)
    assert first.status_code == 200 and first.headers["content-type"] == "application/x-protobuf", first.text
    assert ExportTraceServiceResponse.FromString(first.content) == ExportTraceServiceResponse()
    assert (await source.post(endpoint, content=body, headers=headers)).status_code == 200
    equivalent = json.loads(protobuf_to_json(body))
    duplicate = await source.post(endpoint, json=equivalent, headers={"x-codeplane-ingest-token": token})
    assert duplicate.status_code == 200
    while pending:
        await pending.pop(0)
    async with session_factory() as session:
        summary = await TelemetrySummaryRepository(session).get(job["id"])
        assert summary["tool_call_count"] == 1 and summary["tool_failure_count"] == 0
    events = (await operator.get("/api/integrations/binary/events")).json()["events"]
    models = [event for event in events if event["kind"] == "llm.call.completed"]
    assert len(models) == 1
    assert models[0]["payload"]["input_tokens"] == 0
    assert models[0]["payload"]["output_tokens"] == 4
    assert models[0]["metadata"]["span_id"] == "binary:1234567890abcdef1234567890abcdef:1234567890abcdef"
    assert (await source.post(endpoint, content=b"\xff", headers=headers)).status_code == 400
    assert (await operator.get("/api/integrations/binary/events")).json()["events"] == events
