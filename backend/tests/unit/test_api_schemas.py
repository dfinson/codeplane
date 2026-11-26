"""Tests for new/modified Pydantic schemas in api_schemas.py."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.models.api_schemas import (
    CostAttributionBucket,
    JobTelemetryReport,
    ScorecardResponse,
)

# ---------------------------------------------------------------------------
# CostAttributionBucket.confidence
# ---------------------------------------------------------------------------


def test_cost_attribution_bucket_confidence_default() -> None:
    bucket = CostAttributionBucket(dimension="phase", bucket="reasoning")
    assert bucket.confidence == "exact"


def test_cost_attribution_bucket_confidence_approximate() -> None:
    bucket = CostAttributionBucket(dimension="activity", bucket="edit", confidence="approximate")
    assert bucket.confidence == "approximate"


# ---------------------------------------------------------------------------
# ScorecardResponse.daily_spend_limit_usd
# ---------------------------------------------------------------------------


def test_scorecard_response_daily_spend_limit_default() -> None:
    resp = ScorecardResponse(
        activity={"total_jobs": 0},
    )
    assert resp.daily_spend_limit_usd == 0.0


def test_scorecard_response_daily_spend_limit_set() -> None:
    resp = ScorecardResponse(
        activity={"total_jobs": 1},
        daily_spend_limit_usd=10.0,
    )
    assert resp.daily_spend_limit_usd == 10.0


# ---------------------------------------------------------------------------
# JobTelemetryReport
# ---------------------------------------------------------------------------


def test_job_telemetry_report_required_fields() -> None:
    now = datetime.now(UTC)
    report = JobTelemetryReport(
        instance_id="inst-123",
        job_id="job-1",
        sdk="copilot",
        created_at=now,
    )
    assert report.instance_id == "inst-123"
    assert report.job_id == "job-1"
    assert report.sdk == "copilot"
    assert report.total_cost_usd is None
    assert report.model == ""
    assert report.completed_at is None


def test_job_telemetry_report_full_fields() -> None:
    now = datetime.now(UTC)
    report = JobTelemetryReport(
        instance_id="inst-456",
        job_id="job-2",
        sdk="claude",
        model="claude-opus-4",
        repo="/repos/test",
        status="completed",
        resolution="merged",
        total_cost_usd=1.25,
        input_tokens=50_000,
        output_tokens=10_000,
        cache_read_tokens=5_000,
        premium_requests=42,
        duration_ms=120_000,
        total_turns=15,
        tool_call_count=88,
        diff_lines_added=200,
        diff_lines_removed=50,
        subagent_cost_usd=0.30,
        created_at=now,
        completed_at=now,
    )
    assert report.total_cost_usd == 1.25
    assert report.subagent_cost_usd == 0.30
    assert report.completed_at == now


def test_job_telemetry_report_camel_serialization() -> None:
    """Verify camelCase serialization via model_dump(by_alias=True)."""
    now = datetime.now(UTC)
    report = JobTelemetryReport(
        instance_id="inst",
        job_id="j",
        sdk="copilot",
        created_at=now,
        total_cost_usd=1.0,
    )
    dumped = report.model_dump(by_alias=True)
    assert "instanceId" in dumped
    assert "totalCostUsd" in dumped
    assert "createdAt" in dumped
