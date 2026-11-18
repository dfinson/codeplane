"""Telemetry, analytics, and cost attribution schemas."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field

from backend.models.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# Cost attribution
# ---------------------------------------------------------------------------


class CostAttributionBucket(CamelModel):
    """A single bucket within a cost attribution dimension."""

    dimension: str
    bucket: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    call_count: int = 0
    confidence: str = "exact"
    # Per-turn enrichment (only populated for dimension="turn")
    activity: str | None = None
    tools: list[str] | None = None


class TurnEconomics(CamelModel):
    """Turn economics summary for a single job."""

    total_turns: int = 0
    peak_turn_cost_usd: float = 0.0
    avg_turn_cost_usd: float = 0.0
    cost_first_half_usd: float = 0.0
    cost_second_half_usd: float = 0.0


class FileAccessStats(CamelModel):
    """File I/O statistics for a single job."""

    total_accesses: int = 0
    unique_files: int = 0
    total_reads: int = 0
    total_writes: int = 0
    reread_count: int = 0


class NormalizedModelMetrics(CamelModel):
    """Per-model metrics with normalization toggles."""

    model: str
    sdk: str
    job_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    cost_per_job: float = 0.0
    cost_per_minute: float = 0.0
    cost_per_turn: float = 0.0
    cost_per_tool_call: float = 0.0
    cost_per_diff_line: float = 0.0
    cost_per_mtok: float = 0.0
    cache_hit_rate: float = 0.0


# ---------------------------------------------------------------------------
# Scorecard / Redesigned Analytics
# ---------------------------------------------------------------------------


class ScorecardBudget(CamelModel):
    sdk: str
    total_cost_usd: float = 0.0
    premium_requests: int = 0
    job_count: int = 0
    avg_cost_per_job: float = 0.0
    avg_duration_ms: float = 0.0


class ScorecardActivity(CamelModel):
    total_jobs: int = 0
    running: int = 0
    in_review: int = 0
    merged: int = 0
    pr_created: int = 0
    discarded: int = 0
    failed: int = 0
    cancelled: int = 0


class CostTrendPoint(CamelModel):
    date: str
    cost_usd: float = 0.0


class CostTrendEntry(CamelModel):
    date: str
    cost: float = 0.0
    jobs: int = 0


class ModelCostMixEntry(CamelModel):
    model: str
    cost_per_mtok: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    pct_of_tokens: float = 0.0


class ScorecardResponse(CamelModel):
    period: int = 0
    activity: ScorecardActivity
    budget: list[ScorecardBudget] = []
    # Average cost per million tokens + top model mix
    avg_cost_per_mtok: float = 0.0
    model_cost_mix: list[ModelCostMixEntry] = []
    quota_json: str | None = None
    cost_trend: list[CostTrendEntry] = []
    daily_spend_limit_usd: float = 0.0
    # Monthly budget tracking (Item 5)
    monthly_budget_usd: float = 0.0
    month_spend_usd: float = 0.0
    projected_month_end_usd: float = 0.0
    days_elapsed: int = 0
    days_in_month: int = 0
    daily_avg_usd: float = 0.0
    pct_monthly_budget_used: float = 0.0
    # Cost-per-line (Item 9)
    cost_per_diff_line: float = 0.0
    total_diff_lines: int = 0
    # Compaction cost (Item 13)
    compaction_cost_usd: float = 0.0
    compaction_tokens: int = 0


class ModelComparisonRow(CamelModel):
    model: str
    sdk: str
    job_count: int = 0
    avg_cost: float = 0.0
    avg_duration_ms: float = 0.0
    total_cost_usd: float = 0.0
    premium_requests: int = 0
    merged: int = 0
    pr_created: int = 0
    discarded: int = 0
    failed: int = 0
    avg_verify_turns: float | None = None
    verify_job_count: int = 0
    avg_diff_lines: float = 0.0
    cache_hit_rate: float = 0.0
    cost_per_job: float = 0.0
    cost_per_minute: float = 0.0
    cost_per_turn: float = 0.0
    cost_per_tool_call: float = 0.0
    cost_per_diff_line: float = 0.0


class ModelComparisonResponse(CamelModel):
    period: int
    repo: str | None = None
    models: list[ModelComparisonRow] = []


# ---------------------------------------------------------------------------
# Yield / ROI (Item 2)
# ---------------------------------------------------------------------------


class YieldCategoryRow(CamelModel):
    category: str
    job_count: int = 0
    total_cost_usd: float = 0.0
    avg_cost_usd: float = 0.0
    pct_of_total: float = 0.0


class YieldResponse(CamelModel):
    period: int
    categories: list[YieldCategoryRow] = []
    cost_per_merge_usd: float = 0.0
    total_cost_usd: float = 0.0
    total_jobs: int = 0


# ---------------------------------------------------------------------------
# Model efficiency (Item 6)
# ---------------------------------------------------------------------------


class ModelEfficiencyRow(CamelModel):
    model: str
    edit_turns: int = 0
    one_shot_turns: int = 0
    retries: int = 0
    one_shot_rate: float = 0.0
    retry_rate: float = 0.0
    job_count: int = 0


class ModelEfficiencyResponse(CamelModel):
    period: int
    models: list[ModelEfficiencyRow] = []


# ---------------------------------------------------------------------------
# Cache efficiency (Item 7)
# ---------------------------------------------------------------------------


class CacheEfficiencyRow(CamelModel):
    bucket: str
    total_input_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    cache_hit_rate: float = 0.0
    job_count: int = 0


class CacheEfficiencyResponse(CamelModel):
    period: int
    dimension: str
    buckets: list[CacheEfficiencyRow] = []


# ---------------------------------------------------------------------------
# Per-repo cost drivers (Item 4)
# ---------------------------------------------------------------------------


class RepoCostBreakdown(CamelModel):
    repo: str
    total_cost_usd: float = 0.0
    buckets: list[CostAttributionBucket] = []


class RepoCostDriversResponse(CamelModel):
    period: int
    dimension: str
    repos: list[RepoCostBreakdown] = []


# ---------------------------------------------------------------------------
# Analytics pricing
# ---------------------------------------------------------------------------


class ModelPricingEntry(BaseModel):
    """Pricing info for a single model (snake_case keys from pricing JSON)."""

    model_config = ConfigDict(extra="allow")

    cache_read: float = 0
    cache_write: float = 0
    input: float = 0
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    output: float = 0
    provider: str = ""


class AnalyticsPricingResponse(CamelModel):
    """Pricing lookup response — model name → pricing entry (or null)."""

    models: dict[str, ModelPricingEntry | None]


# ---------------------------------------------------------------------------
# Job telemetry response models
# ---------------------------------------------------------------------------


class TelemetryToolCall(CamelModel):
    name: str
    display_label: str | None = None
    activity: str = "overhead"
    tool_category: str = "other"
    duration_ms: float = 0
    success: bool | None = Field(default=True, description="Null preserves an unknown or conflicting observed outcome.")
    offset_sec: float = 0
    motivation_summary: str | None = None
    edit_motivations: list[object] | None = None


class TelemetryLlmCall(CamelModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0
    duration_ms: float = 0
    is_subagent: bool = False
    offset_sec: float = 0
    call_count: int = 1


class TurnAction(CamelModel):
    """A single concrete action within a turn, tagged with its activity."""

    text: str
    activity: str


class TelemetryCostBucket(CamelModel):
    dimension: str = "unknown"
    bucket: str = "unknown"
    cost_usd: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0
    # Per-turn enrichment (only populated for dimension="turn")
    activity: str | None = None
    intent: str | None = None
    actions: list[TurnAction] | None = None


class TelemetryCostDrivers(CamelModel):
    activity: list[TelemetryCostBucket] = []
    phase: list[TelemetryCostBucket] = []
    activity_phase: list[TelemetryCostBucket] = []
    edit_efficiency: list[TelemetryCostBucket] = []


# ---------------------------------------------------------------------------
# Latency attribution
# ---------------------------------------------------------------------------


class TelemetryLatencyBucket(CamelModel):
    """A single bucket within a latency attribution dimension."""

    dimension: str = "unknown"
    bucket: str = "unknown"
    wall_clock_ms: int = 0
    sum_duration_ms: int = 0
    span_count: int = 0
    p50_ms: int = 0
    p95_ms: int = 0
    max_ms: int = 0
    pct_of_total: float = 0.0


class TelemetryLatencyDrivers(CamelModel):
    """Latency breakdown by multiple dimensions."""

    category: list[TelemetryLatencyBucket] = []
    activity: list[TelemetryLatencyBucket] = []
    phase: list[TelemetryLatencyBucket] = []
    tool_type: list[TelemetryLatencyBucket] = []


class TelemetryTurnLatency(CamelModel):
    """Turn latency summary for a single job."""

    total_turns: int = 0
    peak_turn_ms: int = 0
    avg_turn_ms: int = 0
    first_half_ms: int = 0
    second_half_ms: int = 0
    turn_curve: list[TelemetryLatencyBucket] = []


class TelemetryTurnEconomics(CamelModel):
    total_turns: int = 0
    peak_turn_cost_usd: float = 0
    avg_turn_cost_usd: float = 0
    cost_first_half_usd: float = 0
    cost_second_half_usd: float = 0
    turn_curve: list[TelemetryCostBucket] = []


class TelemetryFileEntry(CamelModel):
    file_path: str = ""
    access_count: int = 0
    read_count: int = 0
    write_count: int = 0


class TelemetryFileStats(CamelModel):
    total_accesses: int = 0
    unique_files: int = 0
    total_reads: int = 0
    total_writes: int = 0
    reread_count: int = 0


class TelemetryFileAccess(CamelModel):
    stats: TelemetryFileStats = TelemetryFileStats()
    top_files: list[TelemetryFileEntry] = []


class TelemetryQuotaSnapshot(CamelModel):
    used_requests: float = 0
    entitlement_requests: float = 0
    remaining_percentage: float = 0
    overage: float = 0
    overage_allowed: bool = False
    is_unlimited: bool = False
    reset_date: str = ""


class TelemetryReviewSignals(CamelModel):
    test_co_modifications: list[object] = []


class TelemetryReviewComplexity(CamelModel):
    tier: str = "quick"
    signals: list[str] = []
    signal_details: dict[str, dict[str, int | float]] = {}


class SidecarSessionSummary(CamelModel):
    """Per-session-kind cost/token summary for sidecar sessions within a job."""

    session_kind: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0
    llm_call_count: int = 0
    tool_call_count: int = 0


class JobTelemetryResponse(CamelModel):
    available: bool = False
    job_id: str = ""
    sdk: str = ""
    model: str = ""
    main_model: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_cost: float = 0
    context_window_size: int = 0
    current_context_tokens: int = 0
    context_utilization: float = 0
    compactions: int = 0
    tokens_compacted: int = 0
    tool_call_count: int = 0
    total_tool_duration_ms: int = 0
    tool_calls: list[TelemetryToolCall] = []
    llm_call_count: int = 0
    total_llm_duration_ms: int = 0
    llm_calls: list[TelemetryLlmCall] = []
    approval_count: int = 0
    total_approval_wait_ms: int = 0
    agent_messages: int = 0
    operator_messages: int = 0
    premium_requests: float = 0
    cost_drivers: TelemetryCostDrivers = TelemetryCostDrivers()
    turn_economics: TelemetryTurnEconomics = TelemetryTurnEconomics()
    latency_drivers: TelemetryLatencyDrivers = TelemetryLatencyDrivers()
    turn_latency: TelemetryTurnLatency = TelemetryTurnLatency()
    parallelism_ratio: float = 0.0
    idle_ms: int = 0
    file_access: TelemetryFileAccess = TelemetryFileAccess()
    quota_snapshots: dict[str, TelemetryQuotaSnapshot] | None = None
    review_signals: TelemetryReviewSignals = TelemetryReviewSignals()
    review_complexity: TelemetryReviewComplexity = TelemetryReviewComplexity()
    sidecar_sessions: list[SidecarSessionSummary] = []


# ---------------------------------------------------------------------------
# Fleet-level analytics
# ---------------------------------------------------------------------------


class SidecarCostEntry(CamelModel):
    """Fleet-level cost breakdown for a single sidecar session kind."""

    session_kind: str = ""
    session_count: int = 0
    total_cost_usd: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0


class ModelStatsEntry(CamelModel, extra="allow"):
    model: str = ""
    sdk: str = ""
    job_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    avg_duration_ms: float = 0.0
    premium_requests: float = 0.0


class RepoStatsEntry(CamelModel, extra="allow"):
    repo: str = ""
    job_count: int = 0
    succeeded: int = 0
    failed: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    tool_calls: int = 0
    avg_duration_ms: float = 0.0
    premium_requests: float = 0.0


class ToolStatsEntry(CamelModel, extra="allow"):
    name: str = ""
    count: int = 0
    avg_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    failure_count: int = 0
    unknown_count: int = 0
    p50_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0


class ShellCommandEntry(CamelModel, extra="allow"):
    command: str = ""
    call_count: int = 0
    total_cost_usd: float = 0.0
    avg_duration_ms: float = 0.0
    job_count: int = 0


class CostDriverEntry(CamelModel, extra="allow"):
    bucket: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0
    job_count: int = 0


class FleetCostEntry(CamelModel, extra="allow"):
    dimension: str = ""
    bucket: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    job_count: int = 0
    avg_cost_per_job: float = 0.0
    confidence: str = ""


class FileAccessEntry(CamelModel, extra="allow"):
    file_path: str = ""
    access_count: int = 0
    read_count: int = 0
    write_count: int = 0
    job_count: int = 0


class ObservationEntry(CamelModel, extra="allow"):
    id: int = 0
    category: str = ""
    severity: str = ""
    title: str = ""
    detail: str = ""
    evidence: dict[str, Any] = {}
    job_count: int = 0
    total_waste_usd: float = 0.0
    first_seen_at: str | None = None
    last_seen_at: str | None = None


class AnalyticsOverviewResponse(CamelModel):
    period: int
    total_jobs: int = 0
    succeeded: int = 0
    review: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    running: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    avg_duration_ms: float = 0.0
    total_premium_requests: float = 0.0
    total_tool_calls: int = 0
    total_tool_failures: int = 0
    total_agent_errors: int = 0
    total_tool_errors: int = 0
    total_tool_unknown: int = 0
    tool_success_rate: float | None = Field(
        default=None, description="Percentage among known tool outcomes, or null when no known outcomes exist."
    )
    cache_hit_rate: float = 0.0
    cost_trend: list[CostTrendEntry] = []
    total_subagent_cost_usd: float = 0.0
    total_retry_cost_usd: float = 0.0
    total_retry_count: int = 0


class AnalyticsModelsResponse(CamelModel):
    period: int
    models: list[ModelStatsEntry] = []


class AnalyticsToolsResponse(CamelModel):
    period: int
    tools: list[ToolStatsEntry] = []


class AnalyticsReposResponse(CamelModel):
    period: int
    repos: list[RepoStatsEntry] = []


class AnalyticsJobsResponse(CamelModel):
    period: int
    jobs: list[dict[str, object]] = []


class CostDriversJobResponse(CamelModel):
    job_id: str
    dimensions: dict[str, list[CostDriverEntry]] = {}


class FleetCostDriversResponse(CamelModel):
    period: int
    dimension: str | None = None
    buckets: list[CostDriverEntry] | None = None
    summary: list[FleetCostEntry] | None = None


class FleetLatencyEntry(CamelModel):
    """A single row in fleet latency breakdown."""

    dimension: str = ""
    bucket: str = ""
    avg_wall_clock_ms: float = 0.0
    avg_sum_duration_ms: float = 0.0
    total_span_count: int = 0
    job_count: int = 0
    avg_pct_of_total: float = 0.0


class FleetLatencyDriversResponse(CamelModel):
    """Fleet-wide latency attribution response."""

    period: int
    dimension: str | None = None
    summary: list[FleetLatencyEntry] = []
    avg_job_duration_ms: float = 0.0
    p50_job_duration_ms: int = 0
    p95_job_duration_ms: int = 0


class FileAccessJobResponse(CamelModel):
    job_id: str
    stats: FileAccessStats = FileAccessStats()
    top_files: list[FileAccessEntry] = []


class FleetFileAccessResponse(CamelModel):
    period: int
    top_files: list[FileAccessEntry] = []


class TurnEconomicsResponse(CamelModel):
    job_id: str
    total_turns: int = 0
    peak_turn_cost_usd: float = 0.0
    avg_turn_cost_usd: float = 0.0
    cost_first_half_usd: float = 0.0
    cost_second_half_usd: float = 0.0
    turn_curve: list[TelemetryCostBucket] = []


class ObservationsListResponse(CamelModel):
    observations: list[ObservationEntry] = []


class DismissResponse(CamelModel):
    status: str


class TriggerAnalysisResponse(CamelModel):
    observations_written: int


class ShellCommandsResponse(CamelModel):
    period: int
    commands: list[ShellCommandEntry] = []


class RetryCostResponse(CamelModel):
    period: int
    retry_cost_usd: float = 0.0
    retry_count: int = 0
    total_spans: int = 0
    total_cost_usd: float = 0.0
    retry_pct: float = 0.0


class EditEfficiencyCategory(CamelModel):
    activity: str = ""
    edit_turns: int = 0
    one_shot_turns: int = 0
    retries: int = 0
    one_shot_rate: float = 0.0
    job_count: int = 0


class EditEfficiencyResponse(CamelModel):
    period: int
    categories: list[EditEfficiencyCategory] = []


class JobContextFlag(CamelModel):
    type: str
    message: str


class JobContextJob(CamelModel):
    cost: float = 0.0
    duration_ms: float = 0.0
    diff_lines_added: int = 0
    diff_lines_removed: int = 0
    sdk: str = ""
    model: str = ""
    total_turns: int = 0
    peak_turn_cost_usd: float = 0.0
    avg_turn_cost_usd: float = 0.0
    cost_first_half_usd: float = 0.0
    cost_second_half_usd: float = 0.0


class JobContextRepoAvg(CamelModel):
    job_count: int = 0
    avg_cost: float = 0.0
    avg_duration_ms: float = 0.0
    avg_diff_lines: float = 0.0


class JobContextResponse(CamelModel):
    job: JobContextJob
    repo_avg: JobContextRepoAvg | None = None
    flags: list[JobContextFlag] = []


class TestCoModification(CamelModel):
    """A step where test and source files were both written."""

    turn_id: str | None = None
    step_number: int | None = None
    step_title: str | None = None
    test_files: list[str] = []
    source_files: list[str] = []


class ReviewSignals(CamelModel):
    """Risk signals surfaced during review."""

    test_co_modifications: list[TestCoModification] = []


class ReviewComplexity(CamelModel):
    """Review complexity tier for a job."""

    tier: str = "standard"
    signals: list[str] = []


class JobTelemetryReport(CamelModel):
    """Per-job telemetry report for future Hub telemetry push."""

    instance_id: str
    job_id: str
    sdk: str
    model: str = ""
    repo: str = ""
    status: str = ""
    resolution: str = ""
    total_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    premium_requests: int = 0
    duration_ms: float = 0.0
    total_turns: int = 0
    tool_call_count: int = 0
    diff_lines_added: int = 0
    diff_lines_removed: int = 0
    subagent_cost_usd: float = 0.0
    created_at: datetime
    completed_at: datetime | None = None


class SidecarSessionGlobalMetrics(CamelModel):
    total_calls: int
    avg_latency_ms: float
    active_jobs: int
    pool_size: int
    warm_tokens: int


class SidecarSessionJobMetrics(CamelModel):
    call_count: int
    avg_latency_ms: float
    total_latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


class SidecarSessionMetricsResponse(CamelModel):
    global_metrics: SidecarSessionGlobalMetrics = Field(alias="global")
    jobs: dict[str, SidecarSessionJobMetrics]


# ---------------------------------------------------------------------------
# Items 13-18: New analytics response models
# ---------------------------------------------------------------------------


class FileCostEntry(CamelModel):
    file_path: str = ""
    total_cost_usd: float = 0.0
    total_read_cost: float = 0.0
    total_write_cost: float = 0.0
    total_turns: int = 0
    job_count: int = 0


class FileCostResponse(CamelModel):
    files: list[FileCostEntry] = []
    period_days: int = 30


class OutcomeMatrixCell(CamelModel):
    activity: str = ""
    resolution: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    job_count: int = 0


class OutcomeMatrixResponse(CamelModel):
    cells: list[OutcomeMatrixCell] = []
    period_days: int = 30
    total_waste_usd: float = 0.0


class ActivityPhaseCell(CamelModel):
    activity: str = ""
    phase: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0
    job_count: int = 0


class ActivityPhaseMatrixResponse(CamelModel):
    cells: list[ActivityPhaseCell] = []
    period_days: int = 30


class ActionPurposeCell(CamelModel):
    action: str
    purpose: str
    cost_usd: float = 0.0
    call_count: int = 0


class ActionPurposeMatrixResponse(CamelModel):
    cells: list[ActionPurposeCell] = []
    period_days: int = 30


class WasteBreakdown(CamelModel):
    retry_usd: float = 0.0
    failed_jobs_usd: float = 0.0
    compaction_usd: float = 0.0
    rereads_usd: float = 0.0


class ExecutiveSummaryResponse(CamelModel):
    building_usd: float = 0.0
    thinking_usd: float = 0.0
    wasted_usd: float = 0.0
    total_usd: float = 0.0
    building_pct: float = 0.0
    thinking_pct: float = 0.0
    wasted_pct: float = 0.0
    waste_breakdown: WasteBreakdown = WasteBreakdown()
    period_days: int = 30
