"""Actual optional SDK/exporter 1.45.2 qualification, intentionally opt-in."""

import asyncio
import socket
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from types import MappingProxyType
from typing import TypedDict

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response

from backend.config import ExportConfig
from backend.models.events import new_event
from backend.models.session_event import EventMetadata
from backend.models.usage import UsageAmount
from backend.persistence.event_repo import EventRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.export_binding import ExportBinding, ExportDestination
from backend.services.events.export_pipeline import MetadataExportPipeline
from backend.services.events.export_status import ExportDropReason
from backend.services.events.optional_export import OptionalExportOwner
from backend.tests.unit.test_approval_service import session_factory as session_factory

ExportTraceServiceRequest = import_module(
    "opentelemetry.proto.collector.trace.v1.trace_service_pb2"
).ExportTraceServiceRequest


class CollectorState(TypedDict):
    codes: list[int]
    retry_after: str | None


@pytest.fixture
async def collector():
    app = FastAPI()
    received = []
    arrived = asyncio.Event()
    release = asyncio.Event()
    release.set()
    state: CollectorState = {"codes": [], "retry_after": None}

    @app.post("/v1/traces")
    async def accept(request: Request):
        body = await request.body()
        received.append((ExportTraceServiceRequest.FromString(body), dict(request.headers), body, time.monotonic()))
        arrived.set()
        await release.wait()
        status = state["codes"].pop(0) if state["codes"] else 200
        headers = {"retry-after": state["retry_after"]} if state["retry_after"] is not None else {}
        return Response(b"", status_code=status, headers=headers, media_type="application/x-protobuf")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/v1/traces", received, arrived, release, state
    finally:
        release.set()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


def _pipeline(endpoint, metrics=None):
    return MetadataExportPipeline(
        ExportBinding(
            ExportDestination(
                endpoint, "http/protobuf", MappingProxyType({"authorization": "Bearer transport-only-secret"}), None
            ),
            metrics,
            b"private-local-key-material-32-bytes",
            frozenset({"approved-model"}),
        )
    )


def _event(index=1):
    return new_event(
        "job-1",
        "tool.call.completed",
        {
            "role": "primary",
            "provider": "fixture",
            "model": "approved-model",
            "content": "private prompt",
            "arguments": {"command": "private command"},
            "result": "private result",
            "path": "/private/path",
            "success": True,
            "tool_call_id": "personal@source.example",
        },
        event_id=f"event-{index}",
        timestamp=datetime.now(UTC),
        metadata=EventMetadata(
            source_framework="fixture",
            integration_binding_id="binding",
            run_id="run",
            span_id=f"source-span-{index}",
            parent_id="source-parent",
            correlation_id="source-trace",
            execution_epoch="epoch",
            duration_ms=2,
        ),
        raw_event={"secret": "private raw record"},
    )


def _enqueue(pipeline, index=1, **options):
    return pipeline.enqueue(_event(index), integration="fixture", provider="fixture", role="primary", **options)


def _spans(request):
    return [span for resource in request.resource_spans for scope in resource.scope_spans for span in scope.spans]


async def test_real_protobuf_export_filters_before_span_and_queue_and_flushes_in_two_seconds(collector):
    endpoint, received, _, _, _ = collector
    pipeline = _pipeline(endpoint)
    try:
        event = _event()
        raw_before = event.model_dump_json()
        assert _enqueue(pipeline).status == "queued"
        rejected = pipeline.enqueue(
            event,
            integration="ghp_" + "a" * 36,
            provider="fixture",
            role="primary",
        )
        assert rejected.status == "withheld"
        assert pipeline.health.snapshot().queued_spans == 1
        queued = tuple(pipeline._processor._queue)
        assert len(queued) == 1
        assert "private" not in repr(dict(queued[0].attributes))
        started = time.monotonic()
        await pipeline.aclose()
        assert time.monotonic() - started < 2.5
        assert len(received) == 1
        request, headers, body, _ = received[0]
        assert headers["authorization"] == "Bearer transport-only-secret"
        assert headers["content-type"] == "application/x-protobuf"
        assert not any(
            token in body
            for token in (
                b"private",
                b"personal@",
                b"transport-only-secret",
                b"source-span",
                b"source-parent",
                b"source-trace",
            )
        )
        span = _spans(request)[0]
        keys = {attribute.key for attribute in span.attributes}
        assert keys <= {
            "codeplane.schema",
            "codeplane.integration",
            "codeplane.provider",
            "codeplane.role",
            "codeplane.kind",
            "codeplane.job_id",
            "codeplane.event_id",
            "codeplane.execution_id",
            "codeplane.source_id",
            "codeplane.source_run_id",
            "codeplane.source_trace_id",
            "codeplane.tool_call_id",
            "codeplane.span_id",
            "codeplane.parent_span_id",
            "codeplane.duration_ms",
            "codeplane.outcome",
            "codeplane.model",
        }
        assert event.model_dump_json() == raw_before
        assert pipeline.health.snapshot().exported_spans == 1
    finally:
        await pipeline.aclose()


async def test_batch_128_queue_2048_overflow_and_disable_drain(collector):
    endpoint, received, arrived, release, _ = collector
    release.clear()
    pipeline = _pipeline(endpoint)
    try:
        for index in range(128):
            _enqueue(pipeline, index)
        await asyncio.wait_for(arrived.wait(), 2)
        assert len(_spans(received[0][0])) == 128
        for index in range(128, 128 + 2051):
            _enqueue(pipeline, index)
        snapshot = pipeline.health.snapshot()
        assert snapshot.queued_spans == 2048
        assert dict(snapshot.dropped_by_reason)["queue_full"] == 3
        started = time.monotonic()
        pipeline.disable()
        await pipeline.aclose()
        assert time.monotonic() - started < 2.5
        assert _enqueue(pipeline).status == "disabled"
        assert len(received) == 1
        assert not pipeline._processor._worker_thread.is_alive()
    finally:
        release.set()
        await pipeline.aclose()


async def test_real_five_second_schedule_retry_500_and_429_then_auth_disables(collector):
    endpoint, received, arrived, _, state = collector
    pipeline = _pipeline(endpoint)
    try:
        started = time.monotonic()
        _enqueue(pipeline)
        await asyncio.wait_for(arrived.wait(), 7)
        assert 4.5 <= received[0][3] - started < 7
        state["codes"] = [500, 429, 200]
        state["retry_after"] = "0"
        for index in range(128):
            _enqueue(pipeline, index + 10)
        async with asyncio.timeout(4):
            while len(received) < 4:
                await asyncio.sleep(0.01)
        state["codes"] = [401]
        for index in range(128):
            _enqueue(pipeline, index + 200)
        async with asyncio.timeout(3):
            while pipeline.health.snapshot().enabled:
                await asyncio.sleep(0.01)
        assert pipeline.health.snapshot().last_reason == ExportDropReason.authentication
        assert _enqueue(pipeline).status == "disabled"
        assert len(received) == 5
    finally:
        await pipeline.aclose()


async def test_shutdown_deadline_interrupts_a_real_hanging_http_request(collector):
    endpoint, _, arrived, release, _ = collector
    release.clear()
    pipeline = _pipeline(endpoint)
    for index in range(128):
        _enqueue(pipeline, index)
    await asyncio.wait_for(arrived.wait(), 2)
    started = time.monotonic()
    await pipeline.aclose()
    assert time.monotonic() - started < 2.5
    release.set()
    async with asyncio.timeout(1):
        while pipeline._processor._worker_thread.is_alive():
            await asyncio.sleep(0.01)
    assert pipeline.health.snapshot().dropped_spans == 128


async def test_persisted_source_and_explicit_role_required_before_optional_enqueue(
    collector, session_factory, monkeypatch, tmp_path
):
    endpoint, received, _, _, _ = collector
    monkeypatch.setattr("backend.config.get_codeplane_dir", lambda: tmp_path)
    config = ExportConfig(enabled=True, endpoint=endpoint, approved_models=["approved-model"])
    owner = OptionalExportOwner(config, session_factory)
    bus = EventBus()
    await owner.start(bus)
    try:
        event = _event()
        await bus.publish(event)
        assert owner.snapshot()["queued_spans"] == 0
        async with session_factory() as session:
            await EventRepository(session).append(event)
            await session.commit()
        await bus.publish(event)
        assert owner.snapshot()["queued_spans"] == 1
        unknown = _event(2)
        unknown.payload.pop("role")
        unknown.payload["cost_usd"] = 100
        async with session_factory() as session:
            await EventRepository(session).append(unknown)
            await session.commit()
        await bus.publish(unknown)
        assert owner.snapshot()["dropped_by_reason"]["metadata_withheld"] == 1
        bad_currency = _event(3)
        bad_currency.payload["usage"] = [
            {
                "amount": "1",
                "unit": "USD",
                "producer": "fixture",
                "model": "approved-model",
                "job_id": "job-1",
                "execution_epoch": "epoch",
                "role": "primary",
                "source": "unqualified",
                "verified": False,
                "currency": None,
            }
        ]
        async with session_factory() as session:
            await EventRepository(session).append(bad_currency)
            await session.commit()
        await bus.publish(bad_currency)
        assert owner.snapshot()["dropped_by_reason"]["metadata_withheld"] == 2
        await owner.close()
        assert len(received) == 1 and len(_spans(received[0][0])) == 1
    finally:
        await owner.close()


async def test_explicit_disable_then_reenable_revalidates_the_binding_without_duplicate_workers(
    collector,
    session_factory,
    monkeypatch,
    tmp_path,
):
    endpoint, received, _, _, _ = collector
    monkeypatch.setattr("backend.config.get_codeplane_dir", lambda: tmp_path)
    config = ExportConfig(enabled=True, endpoint=endpoint)
    owner = OptionalExportOwner(config, session_factory)
    bus = EventBus()
    await owner.start(bus)
    first = owner._pipeline
    config.enabled = False
    assert (
        owner.enqueue_committed(_event(), integration="fixture", provider="fixture", role="primary").status
        == "disabled"
    )
    await owner.close()
    assert not first._processor._worker_thread.is_alive() and received == []
    config.enabled = True
    await owner.start(bus)
    try:
        assert owner._pipeline is not first
        assert bus._subscribers.count(owner.on_event) == 1
        assert (
            owner.enqueue_committed(_event(), integration="fixture", provider="fixture", role="primary").status
            == "queued"
        )
    finally:
        await owner.close()
    assert len(received) == 1


async def test_native_sanitized_sink_does_not_leak_or_fail_local_work(collector):
    from backend.services.events.export_metadata import export_metadata

    endpoint, received, _, _, _ = collector
    pipeline = _pipeline(endpoint)
    try:
        safe = export_metadata(
            _event(),
            identity_key=pipeline.binding.identity_key,
            integration="native",
            provider="fixture",
            role="primary",
            approved_models=frozenset({"approved-model"}),
        )
        assert pipeline.enqueue_projection({**safe, "private_field": "private text"}).status == "withheld"
        assert pipeline.enqueue_projection(safe).status == "queued"
        await pipeline.aclose()
        assert len(received) == 1 and len(_spans(received[0][0])) == 1
        assert b"private" not in received[0][2]
    finally:
        await pipeline.aclose()


async def test_real_request_timeout_is_five_seconds_before_retry(collector):
    endpoint, received, arrived, release, _ = collector
    release.clear()
    pipeline = _pipeline(endpoint)
    try:
        for index in range(128):
            _enqueue(pipeline, index)
        await asyncio.wait_for(arrived.wait(), 2)
        async with asyncio.timeout(8):
            while len(received) < 2:
                await asyncio.sleep(0.02)
        elapsed = received[1][3] - received[0][3]
        assert 5.8 <= elapsed < 7.5
        release.set()
        async with asyncio.timeout(2):
            while pipeline.health.snapshot().exported_spans != 128:
                await asyncio.sleep(0.01)
    finally:
        release.set()
        await pipeline.aclose()


async def test_legacy_endpoint_environment_never_installs_raw_global_exporters(monkeypatch):
    from backend.services.analytics import telemetry

    def no_network(*args, **kwargs):
        raise AssertionError("legacy environment attempted export")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("OTEL_EXPORTER_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    telemetry.init_telemetry()
    telemetry.start_job_span("local-only", "copilot", repo="/private/repo", branch="private-branch")
    telemetry.end_job_span("local-only")
    assert telemetry.get_span_exporter() is not None
    processors = telemetry.tracer_provider._active_span_processor._span_processors
    assert all(processor.span_exporter is telemetry.get_span_exporter() for processor in processors)
    telemetry.tokens_input.add(4, {"job_id": "local-only", "sdk": "copilot"})
    assert telemetry.get_memory_reader().get_metrics_data() is not None


@pytest.fixture
async def grpc_collector():
    grpc = import_module("grpc")
    metrics_service_pb2 = import_module("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    metrics_service_pb2_grpc = import_module("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc")
    trace_service_pb2 = import_module("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
    trace_service_pb2_grpc = import_module("opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc")

    traces, metrics, headers = [], [], []

    class Traces:
        async def Export(self, request, context):  # noqa: N802 - protobuf service method.
            traces.append(request)
            headers.append(dict(context.invocation_metadata()))
            return trace_service_pb2.ExportTraceServiceResponse()

    class Metrics:
        async def Export(self, request, context):  # noqa: N802 - protobuf service method.
            metrics.append(request)
            headers.append(dict(context.invocation_metadata()))
            return metrics_service_pb2.ExportMetricsServiceResponse()

    server = grpc.aio.server()
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(Traces(), server)
    metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(Metrics(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield f"http://127.0.0.1:{port}", traces, metrics, headers
    finally:
        await server.stop(0)


async def test_preserved_grpc_traces_and_metrics_receive_only_typed_sanitized_usage(grpc_collector):
    endpoint, traces, metrics, headers = grpc_collector
    destination = ExportDestination(endpoint, "grpc", MappingProxyType({"authorization": "Bearer grpc-only"}), None)
    pipeline = MetadataExportPipeline(
        ExportBinding(
            destination,
            destination,
            b"private-local-key-material-32-bytes",
            frozenset({"approved-model"}),
        )
    )
    usage = tuple(
        UsageAmount(
            amount=Decimal(amount),
            unit=unit,
            producer="fixture",
            model="approved-model",
            job_id="job-1",
            execution_epoch="epoch",
            role="primary",
            source="fixture.native.usage",
            verified=unit == "USD",
            currency="USD" if unit == "USD" else None,
        )
        for unit, amount in (("input_tokens", "0"), ("copilot_cost_unit", "0.5"), ("USD", "0.02"))
    )
    try:
        assert _enqueue(pipeline, usage=usage).status == "queued"
        await pipeline.aclose()
        assert len(traces) == len(metrics) == 1
        assert all(header["authorization"] == "Bearer grpc-only" for header in headers)
        wire = traces[0].SerializeToString() + metrics[0].SerializeToString()
        assert all(value not in wire for value in (b"private", b"grpc-only", b"personal@", b"/private/path"))
        points = {
            metric.name: metric
            for resource in metrics[0].resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert set(points) == {
            "codeplane.usage.input_tokens",
            "codeplane.usage.copilot_cost_unit",
            "codeplane.usage.USD",
        }
        assert points["codeplane.usage.input_tokens"].gauge.data_points[0].as_int == 0
        native = points["codeplane.usage.copilot_cost_unit"].gauge.data_points[0]
        usd = points["codeplane.usage.USD"].gauge.data_points[0]
        native_attrs = {item.key: item.value for item in native.attributes}
        usd_attrs = {item.key: item.value for item in usd.attributes}
        assert native_attrs["codeplane.usage.unit"].string_value == "copilot_cost_unit"
        assert "codeplane.usage.currency" not in native_attrs
        assert usd_attrs["codeplane.usage.currency"].string_value == "USD"
        assert usd_attrs["codeplane.usage.verified"].bool_value
    finally:
        await pipeline.aclose()


async def test_export_worker_wait_includes_event_loop_scheduling_delay(collector):
    from backend.services.events.export_transport import OtlpWire

    endpoint, received, _, _, _ = collector
    wire = OtlpWire(ExportDestination(endpoint, "http/protobuf", MappingProxyType({}), None))
    entered, finished = threading.Event(), threading.Event()
    result = []

    def send():
        entered.set()
        try:
            result.append(wire._http(b"", time.monotonic() + 0.05))
        finally:
            finished.set()

    thread = threading.Thread(target=send)
    thread.start()
    try:
        assert entered.wait(1)
        within_budget = finished.wait(0.3)  # Deliberately keep the event loop unavailable.
        await asyncio.sleep(0.05)
        await asyncio.to_thread(thread.join, 1)
        assert within_budget
        assert result and not result[0].success
        assert received == [], "Expired queued work must not start a later POST"
    finally:
        await wire.aclose()
        await asyncio.to_thread(thread.join, 1)
        assert not thread.is_alive()


async def test_expired_scheduled_coroutine_cannot_start_a_post(collector):
    from backend.services.events.export_transport import OtlpWire

    endpoint, received, _, _, _ = collector
    wire = OtlpWire(ExportDestination(endpoint, "http/protobuf", MappingProxyType({}), None))
    try:
        result = await wire._http_request(b"", time.monotonic() - 1)
        assert not result.success and result.retryable
        assert received == []
    finally:
        await wire.aclose()


async def test_metric_underflow_is_withheld_while_exact_trace_amount_and_zero_survive(grpc_collector):
    endpoint, traces, metrics, _ = grpc_collector
    destination = ExportDestination(endpoint, "grpc", MappingProxyType({}), None)
    pipeline = MetadataExportPipeline(
        ExportBinding(
            destination,
            destination,
            b"private-local-key-material-32-bytes",
            frozenset({"approved-model"}),
        )
    )
    usage = tuple(
        UsageAmount(
            amount=Decimal(amount),
            unit=unit,
            producer="fixture",
            model="approved-model",
            job_id="job-1",
            execution_epoch="epoch",
            role="primary",
            source="native",
        )
        for unit, amount in (("copilot_cost_unit", "1E-400"), ("input_tokens", "0"))
    )
    try:
        assert _enqueue(pipeline, usage=usage).status == "queued"
        await pipeline.aclose()
        attributes = {item.key: item.value.string_value for item in _spans(traces[0])[0].attributes}
        assert attributes["codeplane.usage.0.amount"] == "1E-400"
        points = {
            metric.name: metric
            for resource in metrics[0].resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert set(points) == {"codeplane.usage.input_tokens"}
        assert points["codeplane.usage.input_tokens"].gauge.data_points[0].as_int == 0
        assert dict(pipeline.health.snapshot().dropped_by_reason)["numeric_underflow"] == 1
    finally:
        await pipeline.aclose()
