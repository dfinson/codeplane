"""Tests for the redesigned analytics endpoints (scorecard, model-comparison, job-context)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scorecard_data() -> dict:
    return {
        "budget": [
            {
                "sdk": "copilot",
                "total_cost_usd": 1.25,
                "job_count": 5,
                "avg_cost_per_job": 0.25,
                "avg_duration_ms": 120_000,
                "premium_requests": 42,
            },
        ],
        "activity": {
            "total_jobs": 5,
            "running": 1,
            "in_review": 1,
            "merged": 2,
            "pr_created": 0,
            "discarded": 1,
            "failed": 0,
            "cancelled": 0,
        },
        "quota_json": None,
        "cost_trend": [{"date": "2025-01-01", "cost": 0.5, "jobs": 2}],
    }


def _model_comparison_rows() -> list[dict]:
    return [
        {
            "model": "claude-sonnet-4-20250514",
            "sdk": "claude",
            "job_count": 3,
            "total_cost_usd": 0.75,
            "avg_cost": 0.25,
            "avg_duration_ms": 90_000,
            "merged": 2,
            "pr_created": 0,
            "discarded": 1,
            "failed": 0,
            "cancelled": 0,
            "avg_verify_turns": 1.5,
            "verify_job_count": 2,
            "avg_diff_lines": 120,
            "cache_hit_rate": 0.35,
            "cost_per_minute": 0.17,
            "cost_per_turn": 0.05,
        }
    ]


def _job_context_data() -> dict:
    return {
        "job": {
            "job_id": "j-1",
            "cost_usd": 0.30,
            "duration_ms": 100_000,
            "tool_calls": 12,
            "tokens": 50_000,
            "model": "claude-sonnet-4-20250514",
            "sdk": "claude",
        },
        "repo_avg": {
            "avg_cost_usd": 0.25,
            "avg_duration_ms": 95_000,
            "avg_tool_calls": 10,
            "avg_tokens": 45_000,
            "job_count": 8,
        },
        "flags": [
            {"label": "Above avg cost", "level": "warning", "detail": "30% above repo avg"},
        ],
    }


# ---------------------------------------------------------------------------
# Test scorecard endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scorecard_returns_data():
    """analytics_scorecard delegates to repo and returns enriched dict."""
    session = AsyncMock()

    mock_repo_instance = SimpleNamespace(
        scorecard=AsyncMock(return_value=_scorecard_data()),
    )

    with patch(
        "backend.persistence.telemetry_summary_repo.TelemetrySummaryRepo",
        return_value=mock_repo_instance,
    ):
        from backend.api.analytics import analytics_scorecard

        result = await analytics_scorecard(session=session, period=7)

    assert result.activity.total_jobs == 5
    assert len(result.budget) > 0
    assert result.budget[0].sdk == "copilot"


# ---------------------------------------------------------------------------
# Test model comparison endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_comparison_returns_models():
    """analytics_model_comparison returns model rows with resolution data."""
    session = AsyncMock()
    rows = _model_comparison_rows()

    mock_repo_instance = SimpleNamespace(
        model_comparison=AsyncMock(return_value=rows),
    )

    with patch(
        "backend.persistence.telemetry_summary_repo.TelemetrySummaryRepo",
        return_value=mock_repo_instance,
    ):
        from backend.api.analytics import analytics_model_comparison

        result = await analytics_model_comparison(session=session, period=30, repo=None)

    assert result.period == 30
    assert result.repo is None
    assert len(result.models) == 1
    assert result.models[0].model == "claude-sonnet-4-20250514"
    assert result.models[0].merged == 2


@pytest.mark.asyncio
async def test_model_comparison_with_repo_filter():
    """analytics_model_comparison passes repo filter to the repo method."""
    session = AsyncMock()

    comparison_mock = AsyncMock(return_value=[])
    mock_repo_instance = SimpleNamespace(model_comparison=comparison_mock)

    with patch(
        "backend.persistence.telemetry_summary_repo.TelemetrySummaryRepo",
        return_value=mock_repo_instance,
    ):
        from backend.api.analytics import analytics_model_comparison

        result = await analytics_model_comparison(
            session=session, period=14, repo="/tmp/my-repo"
        )

    comparison_mock.assert_awaited_once_with(period_days=14, repo="/tmp/my-repo")
    assert result.repo == "/tmp/my-repo"
    assert result.models == []


# ---------------------------------------------------------------------------
# Test job context endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_context_returns_job_data():
    """analytics_job_context returns job + repo avg + flags."""
    session = AsyncMock()
    data = _job_context_data()

    mock_repo_instance = SimpleNamespace(
        job_context=AsyncMock(return_value=data),
    )

    with patch(
        "backend.persistence.telemetry_summary_repo.TelemetrySummaryRepo",
        return_value=mock_repo_instance,
    ):
        from backend.api.analytics import analytics_job_context

        result = await analytics_job_context(job_id="j-1", session=session)

    assert result["job"]["job_id"] == "j-1"
    assert result["repo_avg"]["avg_cost_usd"] == 0.25
    assert len(result["flags"]) == 1
    assert result["flags"][0]["level"] == "warning"


@pytest.mark.asyncio
async def test_job_context_returns_error_on_missing():
    """analytics_job_context returns error dict when telemetry is not found."""
    session = AsyncMock()

    mock_repo_instance = SimpleNamespace(
        job_context=AsyncMock(return_value=None),
    )

    with patch(
        "backend.persistence.telemetry_summary_repo.TelemetrySummaryRepo",
        return_value=mock_repo_instance,
    ):
        from backend.api.analytics import analytics_job_context

        result = await analytics_job_context(job_id="nonexistent", session=session)

    assert "error" in result
