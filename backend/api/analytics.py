"""Fleet-level analytics endpoints backed by OTEL telemetry data."""

from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(route_class=DishkaRoute, tags=["analytics"])


@router.get("/analytics/overview")
async def analytics_overview(
    session: FromDishka[AsyncSession],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
) -> dict[str, object]:
    """Aggregate analytics over the given period (days)."""
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

    repo = TelemetrySummaryRepo(session)
    agg = await repo.aggregate(period_days=period)
    cost_trend = await repo.cost_by_day(period_days=period)

    total_input = agg.get("total_input_tokens", 0) or 0
    total_cache = agg.get("total_cache_read", 0) or 0
    cache_rate = (total_cache / total_input * 100) if total_input else 0

    total_tools = agg.get("total_tool_calls", 0) or 0
    total_failures = agg.get("total_tool_failures", 0) or 0
    tool_success_rate = ((total_tools - total_failures) / total_tools * 100) if total_tools else 100

    return {
        "period": period,
        "totalJobs": agg.get("total_jobs", 0),
        "succeeded": agg.get("succeeded", 0),
        "failed": agg.get("failed", 0),
        "cancelled": agg.get("cancelled", 0),
        "running": agg.get("running", 0),
        "totalCostUsd": float(agg.get("total_cost_usd", 0) or 0),
        "totalTokens": agg.get("total_tokens", 0),
        "avgDurationMs": float(agg.get("avg_duration_ms", 0) or 0),
        "totalPremiumRequests": float(agg.get("total_premium_requests", 0) or 0),
        "totalToolCalls": total_tools,
        "totalToolFailures": total_failures,
        "toolSuccessRate": round(tool_success_rate, 1),
        "cacheHitRate": round(cache_rate, 1),
        "costTrend": cost_trend,
    }


@router.get("/analytics/models")
async def analytics_models(
    session: FromDishka[AsyncSession],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
) -> dict[str, object]:
    """Per-model cost and usage breakdown."""
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

    rows = await TelemetrySummaryRepo(session).cost_by_model(period_days=period)
    return {"period": period, "models": rows}


@router.get("/analytics/tools")
async def analytics_tools(
    session: FromDishka[AsyncSession],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, object]:
    """Tool performance stats (call counts, failure rates, latency)."""
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo

    stats = await TelemetrySpansRepo(session).tool_stats(period_days=period)
    return {"period": period, "tools": stats}


@router.get("/analytics/jobs")
async def analytics_jobs(
    session: FromDishka[AsyncSession],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
    sdk: str | None = None,
    model: str | None = None,
    status: str | None = None,
    repo: str | None = None,
    sort: str = "completed_at",
    desc: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """Paginated per-job telemetry table."""
    from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

    rows = await TelemetrySummaryRepo(session).query(
        period_days=period,
        sdk=sdk,
        model=model,
        status=status,
        repo=repo,
        sort=sort,
        desc=desc,
        limit=limit,
        offset=offset,
    )
    return {"period": period, "jobs": rows}
