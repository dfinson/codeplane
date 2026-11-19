import json
from copy import deepcopy
from typing import TYPE_CHECKING

import pytest

from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
from backend.services.events.event_bus import EventBus
from backend.services.events.telemetry_subscriber import TelemetrySubscriber
from backend.services.ingest.credentials import IngestCredentials
from backend.services.ingest.record_service import ObservationRecords
from backend.tests.unit.test_otlp_admission import CONFIG, _ingest, otlp
from backend.tests.unit.test_otlp_admission import otlp_store as otlp_store

if TYPE_CHECKING:
    from collections.abc import Awaitable


def _tool(identity, status=None):
    body = otlp(identity)
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["name"] = "Read fixture"
    span["attributes"] = [
        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
        {"key": "gen_ai.tool.name", "value": {"stringValue": "read_file"}},
        {"key": "gen_ai.tool.call.id", "value": {"stringValue": f"tool-{identity}"}},
    ]
    if status is not None:
        span["status"] = {"code": status}
    return body


class ProductionConsumer:
    def __init__(self, factory):
        self.pending: list[Awaitable[None]] = []
        self.bus = EventBus()
        self.subscriber = TelemetrySubscriber(session_factory=factory, schedule_write=self.pending.append)
        self.subscriber.subscribe(self.bus)

    async def append(self, factory, token, body):
        async with factory() as session:
            bindings = IntegrationRepository(session)
            authorization = await IngestCredentials(bindings, CONFIG).authorize("otlp", token)
            await ObservationRecords(bindings, JobRepository(session), self.bus).append_otlp(
                "otlp",
                json.dumps(body).encode(),
                authorization=authorization,
            )
        while self.pending:
            await self.pending.pop(0)


async def test_revisions_survive_without_recounting_after_consumer_restart(otlp_store):
    factory, job, token = otlp_store
    body = _tool(1, 1)
    consumer = ProductionConsumer(factory)
    await consumer.append(factory, token, body)
    for revision in range(2):
        consumer = ProductionConsumer(factory)
        changed = deepcopy(body)
        changed["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
            {"key": "extra.observation", "value": {"intValue": str(revision)}}
        )
        await consumer.append(factory, token, changed)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        tools = [event for event in events if event.kind == "tool.call.completed"]
        assert len(tools) == 3 and len({event.id for event in tools}) == 3
        summary = await TelemetrySummaryRepository(session).get(job.id)
        assert summary["tool_call_count"] == 1
        assert summary["tool_failure_count"] == 0
        assert len(await TelemetrySpansRepository(session).list_for_job(job.id)) == 1
        assert len({event.payload["turn_id"] for event in tools}) == 1
        projection = await IntegrationRepository(session).projection("otlp")
        assert projection.buffer["steps"]["counter"] == 1
        assert projection.buffer["steps"]["current"]["tool_count"] == 1
        from backend.services.steps.tracker import StepTracker

        rebuilt = StepTracker(EventBus())
        for event in events:
            rebuilt.restore_event(job.id, event)
        assert rebuilt.current_step(job.id).tool_count == 1


async def test_known_error_and_unknown_outcomes_reach_production_telemetry(otlp_store):
    factory, job, token = otlp_store
    consumer = ProductionConsumer(factory)
    for identity, status in ((1, 1), (2, 2), (3, None)):
        await consumer.append(factory, token, _tool(identity, status))
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        tools = [event for event in events if event.kind in {"tool.call.completed", "tool.call.failed"}]
        assert [event.payload.get("success") for event in tools] == [True, False, None]
        summary = await TelemetrySummaryRepository(session).get(job.id)
        assert summary["tool_call_count"] == 3
        assert summary["tool_failure_count"] == 1
        spans = await TelemetrySpansRepository(session).list_for_job(job.id)
        assert [span["attrs"].get("success") for span in spans] == [True, False, None]
        from backend.api.analytics import analytics_overview
        from backend.persistence.cost_attribution_repo import CostAttributionRepository
        from backend.persistence.file_access_repo import FileAccessRepository
        from backend.persistence.latency_attribution_repo import LatencyAttributionRepository
        from backend.services.analytics.analytics_service import AnalyticsService
        from backend.services.analytics.telemetry_query_service import TelemetryQueryService

        query = TelemetryQueryService(
            CostAttributionRepository(session),
            FileAccessRepository(session),
            JobRepository(session),
            LatencyAttributionRepository(session),
            TelemetrySpansRepository(session),
            TelemetrySummaryRepository(session),
        )
        response = await query.get_telemetry(job.id)
        assert [call.success for call in response.tool_calls] == [True, False, None]
        overview = await analytics_overview(svc=AnalyticsService(session), period=7)
        assert overview.total_tool_unknown == 1
        assert overview.tool_success_rate == 50
        from backend.api.analytics import analytics_tools

        breakdown = await analytics_tools(svc=AnalyticsService(session), period=7)
        assert len(breakdown.tools) == 1
        assert breakdown.tools[0].count == 3
        assert breakdown.tools[0].failure_count == 1 and breakdown.tools[0].unknown_count == 1
        assert breakdown.model_dump(by_alias=True)["tools"][0]["unknownCount"] == 1


async def test_unknown_then_known_and_conflicting_outcomes_update_one_operation(otlp_store):
    from backend.api.analytics import analytics_overview
    from backend.services.analytics.analytics_service import AnalyticsService

    factory, job, token = otlp_store
    consumer = ProductionConsumer(factory)
    await consumer.append(factory, token, _tool(1))
    async with factory() as session:
        overview = await analytics_overview(svc=AnalyticsService(session), period=7)
        assert overview.tool_success_rate is None and overview.total_tool_unknown == 1
        stats = await TelemetrySpansRepository(session).tool_stats(period_days=7)
        assert stats[0]["count"] == stats[0]["unknown_count"] == 1 and stats[0]["failure_count"] == 0
    await ProductionConsumer(factory).append(factory, token, _tool(1, 2))
    async with factory() as session:
        summary = await TelemetrySummaryRepository(session).get(job.id)
        assert summary["tool_call_count"] == 1 and summary["tool_failure_count"] == 1
    await ProductionConsumer(factory).append(factory, token, _tool(1, 1))
    async with factory() as session:
        spans = await TelemetrySpansRepository(session).list_for_job(job.id)
        assert len(spans) == 1 and spans[0]["attrs"]["outcome_conflict"] is True
        assert spans[0]["attrs"]["success"] is None
        summary = await TelemetrySummaryRepository(session).get(job.id)
        assert summary["tool_call_count"] == 1 and summary["tool_failure_count"] == 0


async def test_paired_unknown_tool_has_no_success_or_failure_result_hint(otlp_store, monkeypatch):
    from backend.services.events import tool_pairing

    factory, job, token = otlp_store
    body = _tool(1)
    initial = deepcopy(body)
    initial["resourceSpans"][0]["scopeSpans"][0]["spans"][0].pop("endTimeUnixNano")
    await _ingest(factory, token, initial)
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["attributes"].append({"key": "gen_ai.tool.call.result", "value": {"stringValue": "Source output"}})
    calls = []
    original = tool_pairing.format_tool_display

    def record(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(tool_pairing, "format_tool_display", record)
    await _ingest(factory, token, body)
    assert calls and all(call.get("tool_result") is None for call in calls)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        completion = next(event for event in events if event.kind == "tool.call.completed")
        assert completion.payload["success"] is None
        assert completion.metadata.tool_display is not None


async def test_existing_known_managed_outcomes_keep_their_aggregate_behavior(otlp_store):
    from backend.api.analytics import analytics_overview
    from backend.models.events import new_event
    from backend.models.session_event import EventMetadata
    from backend.services.analytics.analytics_service import AnalyticsService

    factory, job, _ = otlp_store
    consumer = ProductionConsumer(factory)
    for success in (True, False):
        await consumer.bus.publish(
            new_event(
                job.id,
                "tool.call.completed",
                {"tool_name": "read_file", "success": success},
                metadata=EventMetadata(source_framework="copilot"),
            )
        )
    while consumer.pending:
        await consumer.pending.pop(0)
    async with factory() as session:
        overview = await analytics_overview(svc=AnalyticsService(session), period=7)
        assert overview.total_tool_calls == 2 and overview.total_tool_failures == 1
        assert overview.total_tool_unknown == 0 and overview.tool_success_rate == 50


async def test_no_observed_outcome_has_no_invented_success_rate(otlp_store):
    from backend.api.analytics import analytics_overview
    from backend.services.analytics.analytics_service import AnalyticsService

    factory, _, _ = otlp_store
    async with factory() as session:
        overview = await analytics_overview(svc=AnalyticsService(session), period=7)
        assert overview.total_tool_calls == 0 and overview.tool_success_rate is None


async def test_observed_tool_names_do_not_acquire_sdk_internal_title_control(otlp_store):
    factory, job, token = otlp_store
    body = _tool(1, 1)
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["attributes"][1]["value"]["stringValue"] = "report_intent"
    span["attributes"].append(
        {
            "key": "gen_ai.tool.call.arguments",
            "value": {"stringValue": '{"intent":"injected SDK instruction"}'},
        }
    )
    await _ingest(factory, token, body)
    async with factory() as session:
        projection = await IntegrationRepository(session).projection("otlp")
        current = projection.buffer["steps"]["current"]
        assert current["tool_count"] == 1
        assert current["intent"] == "Observed tool: report_intent"


async def test_metadata_only_model_and_raw_spans_group_without_fake_messages(otlp_store):
    factory, job, token = otlp_store
    await _ingest(factory, token, otlp(1, metadata_only=True))
    body = otlp(2, metadata_only=True)
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["name"] = "Agent operation"
    span["attributes"] = []
    await _ingest(factory, token, body)
    async with factory() as session:
        events = [row.event for row in await EventRepository(session).list_after(0, job_id=job.id)]
        observed = [event for event in events if event.kind in {"raw", "llm.call.completed"}]
        assert len(observed) == 2
        assert all(event.payload.get("step_id") and event.payload.get("turn_id") for event in observed)
        assert len({event.payload["turn_id"] for event in observed}) == 1
        assert not any(event.kind in {"message.user", "message.assistant"} for event in events)
        titles = [event.payload["title"] for event in events if event.kind == "turn.summary"]
        assert titles and all("Observed" in title and title != "Work in progress" for title in titles)


async def test_accepted_batch_replay_rechecks_revoked_credential(otlp_store, monkeypatch):
    factory, _, token = otlp_store
    async with factory() as session:
        bindings = IntegrationRepository(session)
        authorization = await IngestCredentials(bindings, CONFIG).authorize("otlp", token)
        original = bindings.checkpoint
        raced = False

        async def interleaved(*args):
            nonlocal raced
            previous = await original(*args)
            if not raced:
                raced = True
                await _ingest(factory, token, _tool(1, 1))
                async with factory() as other:
                    await IngestCredentials(IntegrationRepository(other), CONFIG).revoke("otlp")
            return previous

        monkeypatch.setattr(bindings, "checkpoint", interleaved)
        with pytest.raises(PermissionError):
            await ObservationRecords(bindings, JobRepository(session), EventBus()).append_otlp(
                "otlp",
                json.dumps(_tool(1, 1)).encode(),
                authorization=authorization,
            )
