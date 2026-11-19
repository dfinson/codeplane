/**
 * Analytics API client module.
 *
 * Centralizes all analytics-related HTTP calls (scorecard, cost drivers,
 * model comparison, observations, fleet metrics, etc.).
 */

import { request } from "./client";
import type { AnalyticsOverviewResponse, AnalyticsToolsResponse, CurrencyValue, JobTelemetryResponse, RetryCostResponse as GeneratedRetryCostResponse } from "./types";

// ---------------------------------------------------------------------------
// Analytics Overview / Models / Tools / Repos / Jobs
// ---------------------------------------------------------------------------

export type AnalyticsOverview = AnalyticsOverviewResponse;

export interface AnalyticsModels {
  period: number;
  models: {
    model: string;
    sdk: string;
    job_count: number;
    total_cost_usd: CurrencyValue;
    total_tokens: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    avg_duration_ms: number;
    premium_requests: number;
    // Normalized metrics (from enhanced cost_by_model)
    total_turns?: number;
    total_tool_calls?: number;
    total_diff_lines?: number;
    cost_per_job?: CurrencyValue;
    cost_per_minute?: CurrencyValue;
    cost_per_turn?: CurrencyValue;
    cost_per_tool_call?: CurrencyValue;
    cost_per_diff_line?: CurrencyValue;
    cost_per_mtok?: CurrencyValue;
    cache_hit_rate?: number;
    [key: string]: unknown;
  }[];
}

export type AnalyticsTools = AnalyticsToolsResponse;

export interface AnalyticsJobs {
  period: number;
  jobs: {
    job_id: string;
    sdk: string;
    model: string;
    repo: string;
    branch: string;
    status: string;
    created_at: string;
    completed_at: string | null;
    duration_ms: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    total_cost_usd: CurrencyValue;
    tool_call_count: number;
    llm_call_count: number;
    premium_requests: number;
  }[];
}

export interface AnalyticsRepos {
  period: number;
  repos: {
    repo: string;
    jobCount: number;
    succeeded: number;
    failed: number;
    totalCostUsd: CurrencyValue;
    totalTokens: number;
    toolCalls: number;
    avgDurationMs: number;
    premiumRequests: number;
  }[];
}

export function fetchAnalyticsOverview(period = 7): Promise<AnalyticsOverview> {
  return request(`/analytics/overview?period=${period}`);
}

export function fetchAnalyticsModels(period = 7): Promise<AnalyticsModels> {
  return request(`/analytics/models?period=${period}`);
}

export function fetchAnalyticsTools(period = 30): Promise<AnalyticsTools> {
  return request(`/analytics/tools?period=${period}`);
}

export function fetchAnalyticsRepos(period = 7): Promise<AnalyticsRepos> {
  return request(`/analytics/repos?period=${period}`);
}

// Model pricing — keyed by model name, null if not found
export interface ModelPricing {
  provider: string;
  input: number;       // $/MTok
  output: number;      // $/MTok
  cache_read: number;  // $/MTok
  cache_write: number; // $/MTok
  max_input_tokens: number;
  max_output_tokens: number;
}

export function fetchModelPricing(models: string[]): Promise<Record<string, ModelPricing | null>> {
  return request(`/analytics/pricing?models=${encodeURIComponent(models.join(","))}`);
}

export function fetchAnalyticsJobs(params?: {
  period?: number;
  sdk?: string;
  model?: string;
  status?: string;
  repo?: string;
  sort?: string;
  desc?: boolean;
  limit?: number;
  offset?: number;
}): Promise<AnalyticsJobs> {
  const sp = new URLSearchParams();
  if (params?.period) sp.set("period", String(params.period));
  if (params?.sdk) sp.set("sdk", params.sdk);
  if (params?.model) sp.set("model", params.model);
  if (params?.status) sp.set("status", params.status);
  if (params?.repo) sp.set("repo", params.repo);
  if (params?.sort) sp.set("sort", params.sort);
  if (params?.desc !== undefined) sp.set("desc", String(params.desc));
  if (params?.limit) sp.set("limit", String(params.limit));
  if (params?.offset) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return request(`/analytics/jobs${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
// Cost Analytics API
// ---------------------------------------------------------------------------

export interface CostDriversResponse {
  jobId: string;
  dimensions: Record<string, CostAttributionBucket[]>;
}

export interface CostAttributionBucket {
  dimension: string;
  bucket: string;
  costUsd: CurrencyValue;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  callCount: number;
  confidence: "exact" | "approximate";
}

export interface FleetCostDriversResponse {
  period: number;
  summary?: Array<CostAttributionBucket & {
    jobCount?: number;
    avgCostPerJob?: CurrencyValue;
    confidence?: "exact" | "approximate";
  }>;
  dimension?: string;
  buckets?: Array<CostAttributionBucket & {
    jobCount?: number;
  }>;
}

// ---------------------------------------------------------------------------
// Latency attribution
// ---------------------------------------------------------------------------

export interface LatencyBucket {
  dimension: string;
  bucket: string;
  wallClockMs: number;
  sumDurationMs: number;
  spanCount: number;
  p50Ms: number;
  p95Ms: number;
  maxMs: number;
  pctOfTotal: number;
}

export interface FleetLatencyEntry {
  dimension: string;
  bucket: string;
  avgWallClockMs: number;
  avgSumDurationMs: number;
  totalSpanCount: number;
  jobCount: number;
  avgPctOfTotal: number;
}

export interface FleetLatencyDriversResponse {
  period: number;
  dimension?: string;
  summary: FleetLatencyEntry[];
  avgJobDurationMs: number;
  p50JobDurationMs: number;
  p95JobDurationMs: number;
}

export interface FileAccessResponse {
  jobId: string;
  stats: {
    total_accesses: number;
    unique_files: number;
    total_reads: number;
    total_writes: number;
    reread_count: number;
  };
  topFiles: Array<{
    file_path: string;
    access_count: number;
    read_count: number;
    write_count: number;
    job_count?: number;
  }>;
}

export interface TurnEconomicsResponse {
  jobId: string;
  totalTurns: number;
  peakTurnCostUsd: CurrencyValue;
  avgTurnCostUsd: CurrencyValue;
  costFirstHalfUsd: CurrencyValue;
  costSecondHalfUsd: CurrencyValue;
  turnCurve: CostAttributionBucket[];
}

export function fetchCostDrivers(jobId: string): Promise<CostDriversResponse> {
  return request(`/analytics/cost-drivers/${jobId}`);
}

export function fetchFleetCostDrivers(
  period = 30,
  dimension?: string,
): Promise<FleetCostDriversResponse> {
  const params = new URLSearchParams({ period: String(period) });
  if (dimension) params.set("dimension", dimension);
  return request(`/analytics/cost-drivers?${params}`);
}

export function fetchFleetLatencyDrivers(
  period = 30,
  dimension?: string,
): Promise<FleetLatencyDriversResponse> {
  const params = new URLSearchParams({ period: String(period) });
  if (dimension) params.set("dimension", dimension);
  return request(`/analytics/latency-drivers?${params}`);
}

export function fetchFileAccess(jobId: string): Promise<FileAccessResponse> {
  return request(`/analytics/file-access/${jobId}`);
}

export function fetchFleetFileAccess(
  period = 30,
): Promise<{ period: number; topFiles: FileAccessResponse["topFiles"] }> {
  return request(`/analytics/file-access?period=${period}`);
}

export function fetchTurnEconomics(jobId: string): Promise<TurnEconomicsResponse> {
  return request(`/analytics/turn-economics/${jobId}`);
}

// ---------------------------------------------------------------------------
// Shell command breakdown, retry cost, edit efficiency
// ---------------------------------------------------------------------------

export interface ShellCommandRow {
  command: string;
  call_count: number;
  total_cost_usd: CurrencyValue;
  avg_duration_ms: number;
  job_count: number;
}

export interface ShellCommandsResponse {
  period: number;
  commands: ShellCommandRow[];
}

export function fetchShellCommands(period = 30): Promise<ShellCommandsResponse> {
  return request(`/analytics/shell-commands?period=${period}`);
}

export type RetryCostResponse = GeneratedRetryCostResponse;

export function fetchRetryCost(period = 30): Promise<RetryCostResponse> {
  return request(`/analytics/retry-cost?period=${period}`);
}

export interface EditEfficiencyRow {
  activity: string;
  editTurns: number;
  oneShotTurns: number;
  retries: number;
  oneShotRate: number;
  jobCount: number;
}

export interface EditEfficiencyResponse {
  period: number;
  categories: EditEfficiencyRow[];
}

export function fetchEditEfficiency(period = 30): Promise<EditEfficiencyResponse> {
  return request(`/analytics/edit-efficiency?period=${period}`);
}

// ---------------------------------------------------------------------------
// Scorecard / Redesigned Analytics
// ---------------------------------------------------------------------------

export interface ScorecardBudget {
  sdk: string;
  totalCostUsd: CurrencyValue;
  premiumRequests: number;
  jobCount: number;
  avgCostPerJob: CurrencyValue;
  avgDurationMs: number;
}

export interface ScorecardActivity {
  totalJobs: number;
  running: number;
  inReview: number;
  merged: number;
  prCreated: number;
  discarded: number;
  failed: number;
  cancelled: number;
}

export interface ModelCostMixEntry {
  model: string;
  costPerMtok: CurrencyValue;
  totalTokens: number;
  totalCostUsd: CurrencyValue;
  pctOfTokens: number;
}

export interface ScorecardResponse {
  costUsage?: JobTelemetryResponse["costUsage"];
  estimatedCostUsd?: JobTelemetryResponse["estimatedCostUsd"];
  period: number;
  activity: ScorecardActivity;
  budget: ScorecardBudget[];
  quotaJson: string | null;
  costTrend: AnalyticsOverviewResponse["costTrend"];
  dailySpendLimitUsd: number;
  // Average cost per MTok + model mix
  avgCostPerMtok: CurrencyValue;
  modelCostMix: ModelCostMixEntry[];
  // Monthly budget (Item 5)
  monthlyBudgetUsd: number;
  monthSpendUsd: CurrencyValue;
  projectedMonthEndUsd: CurrencyValue;
  daysElapsed: number;
  daysInMonth: number;
  dailyAvgUsd: CurrencyValue;
  pctMonthlyBudgetUsed: CurrencyValue;
  // Cost-per-line (Item 9)
  costPerDiffLine: CurrencyValue;
  totalDiffLines: number;
  // Compaction cost (Item 13)
  compactionCostUsd: CurrencyValue;
  compactionTokens: number;
}

export interface ModelComparisonRow {
  model: string;
  sdk: string;
  jobCount: number;
  avgCost: CurrencyValue;
  avgDurationMs: number;
  totalCostUsd: CurrencyValue;
  premiumRequests: number;
  merged: number;
  prCreated: number;
  discarded: number;
  failed: number;
  avgVerifyTurns: number | null;
  verifyJobCount: number;
  avgDiffLines: number;
  cacheHitRate: number;
  costPerJob: CurrencyValue;
  costPerMinute: CurrencyValue;
  costPerTurn: CurrencyValue;
  costPerToolCall: CurrencyValue;
  costPerDiffLine: CurrencyValue;
}

export interface ModelComparisonResponse {
  period: number;
  repo: string | null;
  models: ModelComparisonRow[];
}

export interface JobContextFlag {
  type: string;
  message: string;
}

export interface JobContextResponse {
  job: {
    cost: CurrencyValue;
    durationMs: number;
    diffLinesAdded: number;
    diffLinesRemoved: number;
    sdk: string;
    model: string;
    totalTurns: number;
    peakTurnCostUsd: CurrencyValue;
    avgTurnCostUsd: CurrencyValue;
    costFirstHalfUsd: CurrencyValue;
    costSecondHalfUsd: CurrencyValue;
  };
  repoAvg: {
    jobCount: number;
    avgCost: CurrencyValue;
    avgDurationMs: number;
    avgDiffLines: number;
  } | null;
  flags: JobContextFlag[];
}

export interface Observation {
  id: number;
  category: string;
  severity: string;
  title: string;
  detail: string;
  evidence: Record<string, unknown>;
  jobCount: number;
  totalWasteUsd: CurrencyValue;
  firstSeenAt: string;
  lastSeenAt: string;
}

export interface ObservationsResponse {
  observations: Observation[];
}

export function fetchScorecard(period = 7): Promise<ScorecardResponse> {
  return request(`/analytics/scorecard?period=${period}`);
}

export function fetchModelComparison(
  period = 30,
  repo?: string,
): Promise<ModelComparisonResponse> {
  const params = new URLSearchParams({ period: String(period) });
  if (repo) params.set("repo", repo);
  return request(`/analytics/model-comparison?${params}`);
}

export function fetchJobContext(jobId: string): Promise<JobContextResponse> {
  return request(`/analytics/job-context/${jobId}`);
}

export function fetchObservations(
  category?: string,
  severity?: string,
): Promise<ObservationsResponse> {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (severity) params.set("severity", severity);
  const qs = params.toString();
  return request(`/analytics/observations${qs ? `?${qs}` : ""}`);
}

export function dismissObservation(observationId: number): Promise<{ status: string }> {
  return request(`/analytics/observations/${observationId}/dismiss`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Yield / ROI (Item 2)
// ---------------------------------------------------------------------------

export interface YieldCategoryRow {
  category: string;
  jobCount: number;
  totalCostUsd: CurrencyValue;
  avgCostUsd: CurrencyValue;
  pctOfTotal: CurrencyValue;
}

export interface YieldResponse {
  period: number;
  categories: YieldCategoryRow[];
  costPerMergeUsd: CurrencyValue;
  totalCostUsd: CurrencyValue;
  totalJobs: number;
}

export function fetchYield(period = 30, repo?: string): Promise<YieldResponse> {
  const params = new URLSearchParams({ period: String(period) });
  if (repo) params.set("repo", repo);
  return request(`/analytics/yield?${params}`);
}

// ---------------------------------------------------------------------------
// Model efficiency (Item 6)
// ---------------------------------------------------------------------------

export interface ModelEfficiencyRow {
  model: string;
  editTurns: number;
  oneShotTurns: number;
  retries: number;
  oneShotRate: number;
  retryRate: number;
  jobCount: number;
}

export interface ModelEfficiencyResponse {
  period: number;
  models: ModelEfficiencyRow[];
}

export function fetchModelEfficiency(period = 30): Promise<ModelEfficiencyResponse> {
  return request(`/analytics/model-efficiency?period=${period}`);
}

// ---------------------------------------------------------------------------
// Cache efficiency (Item 7)
// ---------------------------------------------------------------------------

export interface CacheEfficiencyRow {
  bucket: string;
  totalInputTokens: number;
  totalCacheReadTokens: number;
  totalCacheWriteTokens: number;
  cacheHitRate: number;
  jobCount: number;
}

export interface CacheEfficiencyResponse {
  period: number;
  dimension: string;
  buckets: CacheEfficiencyRow[];
}

export function fetchCacheEfficiency(
  period = 30,
  dimension = "phase",
): Promise<CacheEfficiencyResponse> {
  return request(`/analytics/cache-efficiency?period=${period}&dimension=${dimension}`);
}

// ---------------------------------------------------------------------------
// Per-repo cost drivers (Item 4)
// ---------------------------------------------------------------------------

export interface RepoCostBreakdown {
  repo: string;
  totalCostUsd: CurrencyValue;
  buckets: CostAttributionBucket[];
}

export interface RepoCostDriversResponse {
  period: number;
  dimension: string;
  repos: RepoCostBreakdown[];
}

export function fetchRepoCostDrivers(
  period = 30,
  dimension = "activity",
): Promise<RepoCostDriversResponse> {
  return request(`/analytics/cost-drivers?period=${period}&dimension=${dimension}&group_by=repo`);
}

// ---------------------------------------------------------------------------
// CSV/JSON export (Item 10)
// ---------------------------------------------------------------------------

export function exportCostDrivers(
  period = 30,
  fmt: "csv" | "json" = "csv",
): string {
  return `/api/analytics/export?period=${period}&fmt=${fmt}`;
}

// ---------------------------------------------------------------------------
// File cost (Item 14)
// ---------------------------------------------------------------------------

export interface FileCostEntry {
  filePath: string;
  totalCostUsd: CurrencyValue;
  totalReadCost: CurrencyValue;
  totalWriteCost: CurrencyValue;
  totalTurns: number;
  jobCount: number;
}

export interface FileCostResponse {
  files: FileCostEntry[];
  periodDays: number;
}

export function fetchFileCost(period = 30, limit = 30): Promise<FileCostResponse> {
  return request(`/analytics/file-cost?period=${period}&limit=${limit}`);
}

// ---------------------------------------------------------------------------
// Outcome matrix (Item 15)
// ---------------------------------------------------------------------------

export interface OutcomeMatrixCell {
  activity: string;
  resolution: string;
  costUsd: CurrencyValue;
  inputTokens: number;
  outputTokens: number;
  jobCount: number;
}

export interface OutcomeMatrixResponse {
  cells: OutcomeMatrixCell[];
  periodDays: number;
  totalWasteUsd: CurrencyValue;
}

export function fetchOutcomeMatrix(period = 30): Promise<OutcomeMatrixResponse> {
  return request(`/analytics/outcome-matrix?period=${period}`);
}

// ---------------------------------------------------------------------------
// Activity × Phase matrix (Item 16)
// ---------------------------------------------------------------------------

export interface ActivityPhaseCell {
  activity: string;
  phase: string;
  costUsd: CurrencyValue;
  inputTokens: number;
  outputTokens: number;
  callCount: number;
  jobCount: number;
}

export interface ActivityPhaseMatrixResponse {
  cells: ActivityPhaseCell[];
  periodDays: number;
}

export function fetchActivityPhaseMatrix(period = 30): Promise<ActivityPhaseMatrixResponse> {
  return request(`/analytics/activity-phase-matrix?period=${period}`);
}

// ---------------------------------------------------------------------------
// Action × Purpose matrix (Item 19)
// ---------------------------------------------------------------------------

export interface ActionPurposeCell {
  action: string;
  purpose: string;
  costUsd: CurrencyValue;
  callCount: number;
}

export interface ActionPurposeMatrixResponse {
  cells: ActionPurposeCell[];
  periodDays: number;
}

export function fetchActionPurposeMatrix(period = 30): Promise<ActionPurposeMatrixResponse> {
  return request(`/analytics/action-purpose-matrix?period=${period}`);
}

// ---------------------------------------------------------------------------
// Executive summary (Item 18)
// ---------------------------------------------------------------------------

export interface WasteBreakdown {
  retryUsd: CurrencyValue;
  failedJobsUsd: CurrencyValue;
  compactionUsd: CurrencyValue;
  rereadsUsd: CurrencyValue;
}

export interface ExecutiveSummaryResponse {
  buildingUsd: CurrencyValue;
  thinkingUsd: CurrencyValue;
  wastedUsd: CurrencyValue;
  totalUsd: CurrencyValue;
  buildingPct: number;
  thinkingPct: number;
  wastedPct: number;
  wasteBreakdown: WasteBreakdown;
  periodDays: number;
}

export function fetchExecutiveSummary(period = 30): Promise<ExecutiveSummaryResponse> {
  return request(`/analytics/executive-summary?period=${period}`);
}
