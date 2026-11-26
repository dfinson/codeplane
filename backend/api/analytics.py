"""Fleet-level analytics endpoints backed by OTEL telemetry data."""

from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.api_schemas import (
    ActionPurposeCell,
    ActionPurposeMatrixResponse,
    ActivityPhaseCell,
    ActivityPhaseMatrixResponse,
    AnalyticsJobsResponse,
    AnalyticsModelsResponse,
    AnalyticsOverviewResponse,
    AnalyticsPricingResponse,
    AnalyticsReposResponse,
    AnalyticsToolsResponse,
    CacheEfficiencyResponse,
    CacheEfficiencyRow,
    CostAttributionBucket,
    CostDriverEntry,
    CostDriversJobResponse,
    DismissResponse,
    EditEfficiencyCategory,
    EditEfficiencyResponse,
    ExecutiveSummaryResponse,
    FileAccessJobResponse,
    FileCostEntry,
    FileCostResponse,
    FleetCostDriversResponse,
    FleetFileAccessResponse,
    FleetLatencyDriversResponse,
    FleetLatencyEntry,
    JobContextResponse,
    ModelComparisonResponse,
    ModelEfficiencyResponse,
    ModelEfficiencyRow,
    ModelPricingEntry,
    ObservationsListResponse,
    OutcomeMatrixCell,
    OutcomeMatrixResponse,
    RepoCostBreakdown,
    RepoCostDriversResponse,
    RetryCostResponse,
    ScorecardResponse,
    ShellCommandsResponse,
    SidecarCostEntry,
    TriggerAnalysisResponse,
    TurnEconomicsResponse,
    WasteBreakdown,
    YieldCategoryRow,
    YieldResponse,
)
from backend.models.currency import optional_float, sum_known
from backend.services.analytics.analytics_service import AnalyticsService
from backend.services.analytics.model_pricing import ModelPricingService

router = APIRouter(route_class=DishkaRoute, tags=["analytics"])
log = structlog.get_logger()


@router.get("/analytics/overview", response_model=AnalyticsOverviewResponse)
async def analytics_overview(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
) -> AnalyticsOverviewResponse:
    """Aggregate analytics over the given period (days)."""
    agg = await svc.aggregate(period_days=period)
    cost_trend = await svc.cost_by_day(period_days=period)

    total_input = agg.get("total_input_tokens", 0)
    total_cache = agg.get("total_cache_read", 0)
    cache_rate = (total_cache / total_input * 100) if total_input else 0

    total_tools = agg.get("total_tool_calls", 0)
    total_failures = agg.get("total_tool_failures", 0)
    total_unknown = agg.get("total_tool_unknown", 0)
    known_tools = total_tools - total_unknown
    total_agent_errors = agg.get("total_agent_errors", 0)
    total_tool_errors = total_failures - total_agent_errors
    tool_success_rate = (known_tools - total_failures) / known_tools * 100 if known_tools > 0 else None

    return AnalyticsOverviewResponse(
        cost_usage=await svc.cost_usage(period_days=period),
        estimated_cost_usd=optional_float(agg.get("estimated_cost_usd")),
        period=period,
        total_jobs=agg.get("total_jobs", 0),
        succeeded=agg.get("succeeded", 0),
        review=agg.get("review", 0),
        completed=agg.get("completed", 0),
        failed=agg.get("failed", 0),
        cancelled=agg.get("cancelled", 0),
        running=agg.get("running", 0),
        total_cost_usd=optional_float(agg.get("total_cost_usd")),
        total_tokens=agg.get("total_tokens", 0),
        avg_duration_ms=float(agg.get("avg_duration_ms", 0)),
        total_premium_requests=float(agg.get("total_premium_requests", 0)),
        total_tool_calls=total_tools,
        total_tool_failures=total_failures,
        total_tool_unknown=total_unknown,
        total_agent_errors=total_agent_errors,
        total_tool_errors=max(0, total_tool_errors),
        tool_success_rate=round(tool_success_rate, 1) if tool_success_rate is not None else None,
        cache_hit_rate=round(cache_rate, 1),
        cost_trend=cost_trend,
        total_subagent_cost_usd=optional_float(agg.get("total_subagent_cost_usd")),
        total_retry_cost_usd=optional_float(agg.get("total_retry_cost_usd")),
        total_retry_count=int(agg.get("total_retry_count", 0)),
    )


@router.get("/analytics/models", response_model=AnalyticsModelsResponse)
async def analytics_models(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
) -> AnalyticsModelsResponse:
    """Per-model cost and usage breakdown."""
    rows = await svc.cost_by_model(period_days=period)
    return AnalyticsModelsResponse(period=period, models=rows)


@router.get("/analytics/tools", response_model=AnalyticsToolsResponse)
async def analytics_tools(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AnalyticsToolsResponse:
    """Tool performance stats (call counts, failure rates, latency) + category mix."""
    stats = await svc.tool_stats(period_days=period)
    return AnalyticsToolsResponse(
        period=period,
        tools=stats,
    )


@router.get("/analytics/repos", response_model=AnalyticsReposResponse)
async def analytics_repos(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
) -> AnalyticsReposResponse:
    """Per-repo cost and usage breakdown."""
    rows = await svc.cost_by_repo(period_days=period)
    return AnalyticsReposResponse(period=period, repos=rows)


@router.get("/analytics/jobs", response_model=AnalyticsJobsResponse)
async def analytics_jobs(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
    sdk: str | None = None,
    model: str | None = None,
    status: str | None = None,
    repo: str | None = None,
    sort: str = "completed_at",
    desc: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnalyticsJobsResponse:
    """Paginated per-job telemetry table."""
    rows = await svc.query_jobs(
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
    return AnalyticsJobsResponse(period=period, jobs=rows)


@router.get("/analytics/pricing", response_model=AnalyticsPricingResponse)
def analytics_pricing(
    pricing_svc: FromDishka[ModelPricingService],
    models: str = Query(
        ...,
        description="Comma-separated model names to look up (e.g. 'claude-sonnet-4-6,claude-opus-4-5')",
    ),
) -> AnalyticsPricingResponse:
    """Return pricing info for the requested models.

    Looks up each model by exact key first, then falls back to normalised
    matching.  Returns ``null`` for models not found in the pricing data.
    """
    result: dict[str, ModelPricingEntry | None] = {}
    for raw in models.split(","):
        name = raw.strip()
        if not name:
            continue
        entry = pricing_svc.get(name)
        result[name] = ModelPricingEntry(**entry) if entry is not None else None
    return AnalyticsPricingResponse(models=result)


# ---------------------------------------------------------------------------
# Cost Analytics Endpoints
# ---------------------------------------------------------------------------


@router.get("/analytics/cost-drivers/{job_id}", response_model=CostDriversJobResponse)
async def cost_drivers_for_job(
    job_id: str,
    svc: FromDishka[AnalyticsService],
) -> CostDriversJobResponse:
    """Per-job cost attribution breakdown by dimension."""
    rows = await svc.cost_drivers_for_job(job_id)
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        dim = row.get("dimension", "unknown")
        by_dimension.setdefault(dim, []).append(dict(row))
    return CostDriversJobResponse(job_id=job_id, dimensions=by_dimension)


@router.get("/analytics/cost-drivers", response_model=None)
async def fleet_cost_drivers(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    dimension: str | None = None,
    group_by: str | None = None,
) -> FleetCostDriversResponse | RepoCostDriversResponse:
    """Fleet-wide cost attribution: top cost buckets across all dimensions."""
    if group_by == "repo":
        dim = dimension or "activity"
        rows = await svc.cost_by_repo_activity(period_days=period, dimension=dim)
        # Group by repo
        from collections import defaultdict

        repo_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            repo_map[r.get("repo", "")].append(r)
        repos = []
        for repo_name, buckets in sorted(
            repo_map.items(), key=lambda x: -(sum_known(b.get("cost_usd") for b in x[1]) or 0)
        ):
            total = sum_known(b.get("cost_usd") for b in buckets)
            repos.append(
                RepoCostBreakdown(
                    repo=repo_name or "(unknown)",
                    total_cost_usd=total,
                    buckets=[
                        CostAttributionBucket(dimension=dim, **{k: v for k, v in b.items() if k not in ("repo",)})
                        for b in buckets
                    ],
                )
            )
        return RepoCostDriversResponse(period=period, dimension=dim, repos=repos)

    if dimension:
        dim_rows = await svc.cost_by_dimension(dimension, period_days=period)
        dim_buckets = [CostDriverEntry(**r) for r in dim_rows]
        return FleetCostDriversResponse(period=period, dimension=dimension, buckets=dim_buckets)
    summary = await svc.fleet_cost_summary(period_days=period)
    # Activity-dimension costs use an equal-weight heuristic per turn, flag them.
    enriched: list[dict[str, Any]] = [dict(r) for r in summary]
    for row in enriched:
        row["confidence"] = "approximate" if row.get("dimension") == "activity" else "exact"
    return FleetCostDriversResponse(period=period, summary=enriched)


@router.get("/analytics/latency-drivers", response_model=FleetLatencyDriversResponse)
async def fleet_latency_drivers(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    dimension: str | None = None,
) -> FleetLatencyDriversResponse:
    """Fleet-wide latency attribution: time breakdown across all jobs."""
    summary = await svc.fleet_latency_summary(period_days=period, dimension=dimension)
    percentiles = await svc.job_duration_percentiles(period_days=period)
    return FleetLatencyDriversResponse(
        period=period,
        dimension=dimension,
        summary=[FleetLatencyEntry(**row) for row in summary],
        avg_job_duration_ms=percentiles.get("avg_ms", 0),
        p50_job_duration_ms=percentiles.get("p50_ms", 0),
        p95_job_duration_ms=percentiles.get("p95_ms", 0),
    )


@router.get("/analytics/file-access/{job_id}", response_model=FileAccessJobResponse)
async def file_access_for_job(
    job_id: str,
    svc: FromDishka[AnalyticsService],
) -> FileAccessJobResponse:
    """File access stats for a job — rereads, most-accessed files."""
    stats = await svc.reread_stats(job_id)
    top_files = await svc.most_accessed_files(job_id=job_id)
    return FileAccessJobResponse(job_id=job_id, stats=stats, top_files=top_files)


@router.get("/analytics/file-access", response_model=FleetFileAccessResponse)
async def fleet_file_access(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> FleetFileAccessResponse:
    """Fleet-wide most-accessed files across all jobs."""
    top_files = await svc.most_accessed_files(period_days=period)
    return FleetFileAccessResponse(period=period, top_files=top_files)


@router.get("/analytics/turn-economics/{job_id}", response_model=TurnEconomicsResponse)
async def turn_economics_for_job(
    job_id: str,
    svc: FromDishka[AnalyticsService],
) -> TurnEconomicsResponse:
    """Per-turn cost curve for a specific job, enriched with activity tags."""
    from backend.models.api_schemas import TelemetryCostBucket
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.services.analytics.telemetry_query_service import TelemetryQueryService

    summary = await svc.get_summary(job_id)
    turns = await svc.cost_drivers_for_job(job_id)
    turn_data = [
        TelemetryCostBucket(
            dimension=r.get("dimension", "turn"),
            bucket=r.get("bucket", ""),
            cost_usd=optional_float(r.get("cost_usd")),
            input_tokens=int(r.get("input_tokens", 0)),
            output_tokens=int(r.get("output_tokens", 0)),
            call_count=int(r.get("call_count", 0)),
        )
        for r in turns
        if r.get("dimension") == "turn"
    ]
    turn_data.sort(key=lambda b: int(b.bucket) if b.bucket.isdigit() else 0)

    # Enrich with activity/actions from raw spans
    spans = await TelemetrySpansRepository(svc._session).list_for_job(job_id)
    TelemetryQueryService._enrich_turn_curve(turn_data, spans)

    return TurnEconomicsResponse(
        job_id=job_id,
        total_turns=summary.get("total_turns", 0) if summary else 0,
        peak_turn_cost_usd=summary.get("peak_turn_cost_usd") if summary else None,
        avg_turn_cost_usd=summary.get("avg_turn_cost_usd") if summary else None,
        cost_first_half_usd=summary.get("cost_first_half_usd") if summary else None,
        cost_second_half_usd=summary.get("cost_second_half_usd") if summary else None,
        turn_curve=turn_data,
    )


# ---------------------------------------------------------------------------
# Scorecard / Redesigned Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/scorecard", response_model=ScorecardResponse)
async def analytics_scorecard(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 7,
) -> ScorecardResponse:
    """Top-level scorecard: budget per SDK, activity with resolution, quota, cost trend."""
    scorecard = await svc.enriched_scorecard(period_days=period)
    return ScorecardResponse(**scorecard)


@router.get("/analytics/model-comparison", response_model=ModelComparisonResponse)
async def analytics_model_comparison(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    repo: str | None = None,
) -> ModelComparisonResponse:
    """Per-model comparison with resolution data joined from jobs table."""
    rows = await svc.model_comparison(period_days=period, repo=repo)
    return ModelComparisonResponse(period=period, repo=repo, models=rows)


@router.get("/analytics/job-context/{job_id}", response_model=JobContextResponse)
async def analytics_job_context(
    job_id: str,
    svc: FromDishka[AnalyticsService],
) -> JobContextResponse:
    """Per-job context: metrics + repo comparison + noteworthy flags."""
    job_context = await svc.job_context(job_id)
    if job_context is None:
        raise HTTPException(status_code=404, detail="Job telemetry not found")
    return JobContextResponse(**job_context)


# ---------------------------------------------------------------------------
# Statistical Observations
# ---------------------------------------------------------------------------


@router.get("/analytics/observations", response_model=ObservationsListResponse)
async def list_observations(
    svc: FromDishka[AnalyticsService],
    category: str | None = None,
    severity: str | None = None,
) -> ObservationsListResponse:
    """List active cost observations / anomalies."""
    rows = await svc.list_observations(category=category, severity=severity)
    return ObservationsListResponse(observations=rows)


@router.post("/analytics/observations/{observation_id}/dismiss", response_model=DismissResponse)
async def dismiss_observation(
    observation_id: int,
    svc: FromDishka[AnalyticsService],
) -> DismissResponse:
    """Dismiss an observation."""
    await svc.dismiss_observation(observation_id)
    return DismissResponse(status="dismissed")


@router.post("/analytics/analyse", response_model=TriggerAnalysisResponse)
async def trigger_analysis(
    session: FromDishka[AsyncSession],
) -> TriggerAnalysisResponse:
    """Manually trigger the statistical analysis pass."""
    from backend.services.analytics.statistical_analysis import run_analysis

    count = await run_analysis(session)
    await session.commit()
    return TriggerAnalysisResponse(observations_written=count)


# ---------------------------------------------------------------------------
# Shell command breakdown
# ---------------------------------------------------------------------------


@router.get("/analytics/shell-commands", response_model=ShellCommandsResponse)
async def shell_command_breakdown(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ShellCommandsResponse:
    """Top shell commands by call count, aggregated from tool_target."""
    rows = await svc.shell_command_breakdown(period_days=period)
    return ShellCommandsResponse(period=period, commands=rows)


# ---------------------------------------------------------------------------
# Retry cost summary
# ---------------------------------------------------------------------------


@router.get("/analytics/retry-cost", response_model=RetryCostResponse)
async def retry_cost_summary(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> RetryCostResponse:
    """Fleet-wide retry cost and count."""
    summary = await svc.retry_cost_summary(period_days=period)
    total = optional_float(summary.get("total_cost_usd"))
    retry = optional_float(summary.get("retry_cost_usd"))
    return RetryCostResponse(
        period=period,
        retry_cost_usd=retry,
        retry_count=summary.get("retry_count", 0),
        total_spans=summary.get("total_spans", 0),
        total_cost_usd=total,
        retry_pct=round(retry / total * 100, 1) if retry is not None and total is not None and total > 0 else None,
    )


# ---------------------------------------------------------------------------
# Edit efficiency / one-shot rate
# ---------------------------------------------------------------------------


@router.get("/analytics/edit-efficiency", response_model=EditEfficiencyResponse)
async def fleet_edit_efficiency(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> EditEfficiencyResponse:
    """Fleet-wide one-shot success rate by activity category.

    Reads the ``edit_efficiency`` dimension from cost attribution rows.
    ``call_count`` = edit turns, ``input_tokens`` = one-shot turns,
    ``output_tokens`` = total retries (repurposed columns).
    """
    rows = await svc.cost_by_dimension("edit_efficiency", period_days=period)
    categories = []
    for row in rows:
        edit_turns = int(row.get("call_count") or 0)
        one_shot = int(row.get("input_tokens") or 0)
        retries = int(row.get("output_tokens") or 0)
        rate = round((one_shot / edit_turns * 100) if edit_turns > 0 else 0, 1)
        categories.append(
            EditEfficiencyCategory(
                activity=row.get("bucket", ""),
                edit_turns=edit_turns,
                one_shot_turns=one_shot,
                retries=retries,
                one_shot_rate=rate,
                job_count=row.get("job_count", 0),
            )
        )
    return EditEfficiencyResponse(period=period, categories=categories)


# ---------------------------------------------------------------------------
# Yield / ROI (Item 2)
# ---------------------------------------------------------------------------


@router.get("/analytics/yield", response_model=YieldResponse)
async def analytics_yield(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    repo: str | None = None,
) -> YieldResponse:
    """Cost yield breakdown by outcome category."""
    data = await svc.yield_summary(period_days=period, repo=repo)
    categories = [YieldCategoryRow(**c) for c in data["categories"]]
    return YieldResponse(
        period=data["period"],
        categories=categories,
        cost_per_merge_usd=data["cost_per_merge_usd"],
        total_cost_usd=data["total_cost_usd"],
        total_jobs=data["total_jobs"],
    )


# ---------------------------------------------------------------------------
# Model efficiency (Item 6)
# ---------------------------------------------------------------------------


@router.get("/analytics/model-efficiency", response_model=ModelEfficiencyResponse)
async def analytics_model_efficiency(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ModelEfficiencyResponse:
    """Per-model one-shot rate and retry statistics."""
    rows = await svc.model_efficiency(period_days=period)
    return ModelEfficiencyResponse(
        period=period,
        models=[ModelEfficiencyRow(**r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Cache efficiency (Item 7)
# ---------------------------------------------------------------------------


@router.get("/analytics/cache-efficiency", response_model=CacheEfficiencyResponse)
async def analytics_cache_efficiency(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    dimension: str = "phase",
) -> CacheEfficiencyResponse:
    """Cache token hit rate by execution phase or activity bucket."""
    rows = await svc.cache_efficiency(period_days=period, dimension=dimension)
    return CacheEfficiencyResponse(
        period=period,
        dimension=dimension,
        buckets=[CacheEfficiencyRow(**r) for r in rows],
    )


# ---------------------------------------------------------------------------
# File cost (Item 14)
# ---------------------------------------------------------------------------


@router.get("/analytics/file-cost", response_model=FileCostResponse)
async def analytics_file_cost(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> FileCostResponse:
    """Most expensive files across the fleet."""
    rows = await svc.file_cost_fleet(period_days=period, limit=limit)
    return FileCostResponse(
        files=[FileCostEntry(**r) for r in rows],
        period_days=period,
    )


# ---------------------------------------------------------------------------
# Outcome matrix (Item 15)
# ---------------------------------------------------------------------------


@router.get("/analytics/outcome-matrix", response_model=OutcomeMatrixResponse)
async def analytics_outcome_matrix(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> OutcomeMatrixResponse:
    """Cost breakdown by activity × job resolution."""
    cells = await svc.outcome_cost_matrix(period_days=period)
    total_waste = sum_known(c["cost_usd"] for c in cells if c.get("resolution") in ("discarded", "failed"))
    return OutcomeMatrixResponse(
        cells=[OutcomeMatrixCell(**c) for c in cells],
        period_days=period,
        total_waste_usd=total_waste,
    )


# ---------------------------------------------------------------------------
# Activity × Phase heatmap (Item 16)
# ---------------------------------------------------------------------------


@router.get("/analytics/activity-phase-matrix", response_model=ActivityPhaseMatrixResponse)
async def analytics_activity_phase_matrix(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ActivityPhaseMatrixResponse:
    """Phase × activity cost heatmap across the fleet."""
    cells = await svc.activity_phase_matrix(period_days=period)
    return ActivityPhaseMatrixResponse(
        cells=[ActivityPhaseCell(**c) for c in cells],
        period_days=period,
    )


# ---------------------------------------------------------------------------
# Action × Purpose heatmap (Item 19)
# ---------------------------------------------------------------------------


@router.get("/analytics/action-purpose-matrix", response_model=ActionPurposeMatrixResponse)
async def analytics_action_purpose_matrix(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ActionPurposeMatrixResponse:
    """Action × purpose cost heatmap across the fleet."""
    from backend.persistence.cost_attribution_repo import CostAttributionRepository

    attr_repo = CostAttributionRepository(svc._session)
    rows = await attr_repo.by_dimension("action_purpose", period_days=period, limit=200)
    cells = []
    for row in rows:
        parts = row["bucket"].split(":", 1)
        if len(parts) == 2:
            cells.append(
                ActionPurposeCell(
                    action=parts[0],
                    purpose=parts[1],
                    cost_usd=row["cost_usd"],
                    call_count=row.get("call_count", 0),
                )
            )
    return ActionPurposeMatrixResponse(cells=cells, period_days=period)


# ---------------------------------------------------------------------------
# Executive summary (Item 18)
# ---------------------------------------------------------------------------


@router.get("/analytics/executive-summary", response_model=ExecutiveSummaryResponse)
async def analytics_executive_summary(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ExecutiveSummaryResponse:
    """Simplified 3-bucket executive view: building / thinking / wasted."""
    data = await svc.executive_summary(period_days=period)
    return ExecutiveSummaryResponse(
        building_usd=data["building_usd"],
        thinking_usd=data["thinking_usd"],
        wasted_usd=data["wasted_usd"],
        total_usd=data["total_usd"],
        building_pct=data["building_pct"],
        thinking_pct=data["thinking_pct"],
        wasted_pct=data["wasted_pct"],
        waste_breakdown=WasteBreakdown(**data["waste_breakdown"]),
        period_days=data["period_days"],
    )


# ---------------------------------------------------------------------------
# CSV/JSON export (Item 10)
# ---------------------------------------------------------------------------


@router.get("/analytics/export", response_model=None)
async def analytics_export(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
    fmt: Annotated[str, Query(pattern="^(csv|json)$")] = "csv",
    sections: Annotated[str, Query()] = "overview,models,cost-drivers",
) -> Response:
    """Export analytics data as CSV or JSON for external analysis.

    ``sections`` is a comma-separated list of data sections to include.
    Supported: overview, models, cost-drivers, yield, observations.
    """
    import csv
    import io

    requested = {s.strip() for s in sections.split(",") if s.strip()}
    valid_sections = {"overview", "models", "cost-drivers", "yield", "observations"}
    requested = requested & valid_sections
    if not requested:
        requested = {"overview", "models", "cost-drivers"}

    combined: dict[str, list[Any]] = {}

    if "cost-drivers" in requested:
        summary = await svc.fleet_cost_summary(period_days=period)
        combined["cost-drivers"] = [dict(r) for r in summary]

    if "overview" in requested:
        scorecard = await svc.enriched_scorecard(period_days=period)
        combined["overview"] = [scorecard]

    if "models" in requested:
        models = await svc.model_comparison(period_days=period)
        combined["models"] = models

    if "yield" in requested:
        ys = await svc.yield_summary(period_days=period)
        combined["yield"] = [ys]

    if "observations" in requested:
        obs = await svc.list_observations()
        combined["observations"] = obs

    if fmt == "json":
        return Response(
            content=json.dumps(combined, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="codeplane-analytics-{period}d.json"'},
        )

    # CSV: write each section as its own block with per-section headers
    output = io.StringIO()
    first_section = True

    for section_name, rows in combined.items():
        # Flatten rows for this section, filtering out nested structures
        section_rows: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row) if isinstance(row, dict) else {}
            flat = {k: v for k, v in raw.items() if not isinstance(v, (list, dict))}
            if flat:
                section_rows.append(flat)

        if not section_rows:
            continue

        # Collect fieldnames for this section only
        section_keys: list[str] = []
        seen: set[str] = set()
        for row in section_rows:
            for k in row:
                if k not in seen:
                    section_keys.append(k)
                    seen.add(k)

        if not first_section:
            output.write("\r\n")
        first_section = False

        # Section label row
        output.write(f"# {section_name}\r\n")

        writer = csv.DictWriter(output, fieldnames=section_keys)
        writer.writeheader()
        writer.writerows(section_rows)

    content = output.getvalue()
    if not content:
        content = ""

    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="codeplane-analytics-{period}d.csv"'},
    )


@router.get("/analytics/sidecar-costs", response_model=list[SidecarCostEntry])
async def analytics_sidecar_costs(
    svc: FromDishka[AnalyticsService],
    period: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[SidecarCostEntry]:
    """Cost breakdown by session kind for sidecar sessions (preflight, memory, etc)."""
    rows = await svc.sidecar_cost_breakdown(period_days=period)
    return [SidecarCostEntry(**r) for r in rows]
