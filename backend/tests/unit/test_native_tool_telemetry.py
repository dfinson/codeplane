"""Native bridge mappings must retain known outcomes without inventing unknown ones."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.persistence.telemetry_analytics_repo import TelemetryAnalyticsRepository
from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
from backend.services.events.observation import has_source_grouping
from backend.services.ingest.mapping import NativeMappingDecoder
from backend.tests.unit.test_telemetry_subscriber import _Harness, _MetricRecorder
from backend.tests.unit.test_telemetry_subscriber import session_factory as session_factory

BRIDGES = (
    "openai_client",
    "anthropic_client",
    "gemini_client",
    "mistral_client",
    "bedrock_client",
    "llamaindex",
    "autogen",
    "google_adk",
)


@pytest.mark.parametrize("bridge", BRIDGES)
@pytest.mark.parametrize("outcome", [True, False, None, "false"])
async def test_real_native_mapping_preserves_three_state_outcome_through_persisted_telemetry(
    bridge, outcome, session_factory, monkeypatch
):
    from backend.services.analytics import telemetry as tel

    metrics = _MetricRecorder()
    monkeypatch.setattr(tel, "tool_duration", metrics)
    mapping = Path(__file__).parents[2] / "services" / "adapters" / "profile_bridges" / f"{bridge}.yaml"
    decoder = NativeMappingDecoder(
        mapping,
        job_id="job-1",
        external_session_id="original-sdk",
        binding_id="native-binding",
        source_key="native-source",
        anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )
    event_type = (
        "function_completed"
        if bridge.endswith("_client")
        else ("tool_observed" if bridge == "google_adk" else "tool_completed")
    )
    raw = {"type": event_type, "tool_name": "read_file", "result": "actual response", "success": outcome}
    (event,) = decoder.decode(raw, 0)
    assert not has_source_grouping(event)
    expected = outcome if isinstance(outcome, bool) else None
    if isinstance(outcome, bool):
        assert event.payload["success"] is outcome
    harness = _Harness(session_factory)
    await harness.handle_and_drain(event)
    # Unknown must not seed a false failure/retry chain for the next invocation.
    (next_event,) = decoder.decode({**raw, "success": True}, 1)
    await harness.handle_and_drain(next_event)
    async with session_factory() as session:
        summary = await TelemetrySummaryRepository(session).get("job-1")
        spans = await TelemetrySpansRepository(session).list_for_job("job-1")
        stats = await TelemetrySpansRepository(session).tool_stats()
        aggregate = await TelemetryAnalyticsRepository(session).aggregate()
        from backend.persistence.cost_attribution_repo import CostAttributionRepository
        from backend.persistence.file_access_repo import FileAccessRepository
        from backend.persistence.job_repo import JobRepository
        from backend.persistence.latency_attribution_repo import LatencyAttributionRepository
        from backend.services.analytics.telemetry_query_service import TelemetryQueryService

        query = TelemetryQueryService(
            CostAttributionRepository(session),
            FileAccessRepository(session),
            JobRepository(session),
            LatencyAttributionRepository(session),
            TelemetrySpansRepository(session),
            TelemetrySummaryRepository(session),
        )
        response = await query.get_telemetry("job-1")
    assert summary["tool_call_count"] == 2
    assert summary["tool_failure_count"] == int(expected is False)
    assert summary["retry_count"] == int(expected is False)
    assert [span["attrs"]["success"] for span in spans] == [expected, True]
    assert stats[0]["failure_count"] == int(expected is False)
    assert stats[0]["unknown_count"] == int(expected is None)
    assert aggregate["total_tool_unknown"] == int(expected is None)
    assert [call.success for call in response.tool_calls] == [expected, True]
    if expected is None:
        assert "success" not in metrics.calls[0][1]
        assert "error_snippet" not in spans[0]["attrs"]
    else:
        assert metrics.calls[0][1]["success"] is expected


@pytest.mark.parametrize("bridge", BRIDGES)
async def test_native_failed_mapping_retains_explicit_failure_without_assuming_missing_outcome(bridge, session_factory):
    mapping = Path(__file__).parents[2] / "services" / "adapters" / "profile_bridges" / f"{bridge}.yaml"
    decoder = NativeMappingDecoder(
        mapping,
        job_id="job-1",
        external_session_id="original-sdk",
        anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )
    kind = "function_failed" if bridge.endswith("_client") else "tool_failed"
    harness = _Harness(session_factory)
    for index, value in enumerate((False, None)):
        (event,) = decoder.decode({"type": kind, "tool_name": f"tool-{index}", "success": value}, index)
        await harness.handle_and_drain(event)
    async with session_factory() as session:
        summary = await TelemetrySummaryRepository(session).get("job-1")
        spans = await TelemetrySpansRepository(session).list_for_job("job-1")
    assert summary["tool_call_count"] == 2 and summary["tool_failure_count"] == 1
    assert [span["attrs"]["success"] for span in spans] == [False, None]
