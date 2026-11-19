import type { CurrencyValue, JobTelemetryResponse } from "../api/types";
// ---------------------------------------------------------------------------
// Types shared across MetricsPanel and its section sub-components.
// ---------------------------------------------------------------------------

export interface ToolCall {
  name: string;
  displayLabel?: string;
  activity?: string;
  toolCategory?: string;
  durationMs: number;
  success: boolean;
  offsetSec?: number;
}

export interface LLMCall {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  cost?: CurrencyValue;
  durationMs: number;
  offsetSec?: number;
  isSubagent: boolean;
  callCount?: number;
}

export interface SidecarSession {
  sessionKind: string;
  inputTokens: number;
  outputTokens: number;
  totalCostUsd: CurrencyValue;
  llmCallCount: number;
  toolCallCount: number;
}

export interface TelemetryData {
  costUsage?: JobTelemetryResponse["costUsage"];
  estimatedCostUsd?: JobTelemetryResponse["estimatedCostUsd"];
  legacyCostUnverified?: JobTelemetryResponse["legacyCostUnverified"];
  available: boolean;
  sdk?: string;
  model?: string;
  mainModel?: string;
  durationMs?: number;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  totalCost?: CurrencyValue;
  contextWindowSize?: number;
  currentContextTokens?: number;
  contextUtilization?: number;
  compactions?: number;
  tokensCompacted?: number;
  toolCallCount?: number;
  totalToolDurationMs?: number;
  toolCalls?: ToolCall[];
  llmCallCount?: number;
  totalLlmDurationMs?: number;
  llmCalls?: LLMCall[];
  approvalCount?: number;
  totalApprovalWaitMs?: number;
  agentMessages?: number;
  operatorMessages?: number;
  premiumRequests?: number;
  quotaSnapshots?: Record<string, QuotaSnapshotData>;
  costDrivers?: CostDriversData;
  turnEconomics?: TurnEconomicsData;
  latencyDrivers?: LatencyDriversData;
  turnLatency?: TurnLatencyData;
  parallelismRatio?: number;
  idleMs?: number;
  fileAccess?: FileAccessData;
  reviewComplexity?: { tier: string; signals: string[] };
  reviewSignals?: { testCoModifications: unknown[] };
  sidecarSessions?: SidecarSession[];
}

export interface TurnAction {
  text: string;
  activity: string;
}

export interface CostDriverBucket {
  dimension: string;
  bucket: string;
  costUsd: CurrencyValue;
  inputTokens: number;
  outputTokens: number;
  callCount: number;
  activity?: string;
  tools?: string[];
  intent?: string;
  actions?: TurnAction[];
}

export interface CostDriversData {
  activity?: CostDriverBucket[];
  action?: CostDriverBucket[];
  purpose?: CostDriverBucket[];
  actionPurpose?: CostDriverBucket[];
  phase?: CostDriverBucket[];
  activityPhase?: CostDriverBucket[];
  editEfficiency?: CostDriverBucket[];
  motivation?: CostDriverBucket[];
}

export interface TurnEconomicsData {
  totalTurns: number;
  peakTurnCostUsd: CurrencyValue;
  avgTurnCostUsd: CurrencyValue;
  costFirstHalfUsd: CurrencyValue;
  costSecondHalfUsd: CurrencyValue;
  turnCurve: CostDriverBucket[];
}

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

export interface LatencyDriversData {
  category?: LatencyBucket[];
  activity?: LatencyBucket[];
  action?: LatencyBucket[];
  phase?: LatencyBucket[];
  toolType?: LatencyBucket[];
}

export interface TurnLatencyData {
  totalTurns: number;
  peakTurnMs: number;
  avgTurnMs: number;
  firstHalfMs: number;
  secondHalfMs: number;
  turnCurve: LatencyBucket[];
}

export interface FileAccessData {
  stats: {
    totalAccesses: number;
    uniqueFiles: number;
    totalReads: number;
    totalWrites: number;
    rereadCount: number;
  };
  topFiles: Array<{
    filePath: string;
    accessCount: number;
    readCount: number;
    writeCount: number;
  }>;
}

export interface QuotaSnapshotData {
  usedRequests: number;
  entitlementRequests: number;
  remainingPercentage: number;
  overage: number;
  overageAllowed: boolean;
  isUnlimited: boolean;
  usageAllowedWithExhaustedQuota: boolean;
  resetDate: string;
}

export interface SummaryAccomplished {
  what: string;
  files_affected?: string[];
}

export interface SummaryInProgress {
  description: string;
  file?: string;
}

export interface SummaryVerification {
  tests_run: boolean;
  tests_passed: boolean | null;
  build_run: boolean;
  build_passed: boolean | null;
}

export interface SessionSummaryJson {
  session_number?: number;
  accomplished?: SummaryAccomplished[];
  in_progress?: SummaryInProgress[] | null;
  resume_instructions?: string;
  verification_state?: SummaryVerification | null;
}

export interface SessionCheckpoint {
  sessionNumber: number;
  artifactId: string;
  createdAt: string;
  summary: SessionSummaryJson | null;
}

export type SortField = "name" | "count" | "avgMs" | "totalMs" | "fails";
export type SortDir = "asc" | "desc";

export interface ToolAggregate {
  name: string;
  count: number;
  totalMs: number;
  avgMs: number;
  fails: number;
}

// ---------------------------------------------------------------------------
// Helpers (pure functions)
// ---------------------------------------------------------------------------

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export { formatUsd } from "../lib/currency";

export function formatActivityBucket(bucket: string): string {
  switch (bucket) {
    // Intent-based categories (8 canonical)
    case "implementation":
      return "Implementation";
    case "investigation":
      return "Investigation";
    case "verification":
      return "Verification";
    case "git_ops":
      return "Commit & Push";
    case "setup":
      return "Setup";
    case "overhead":
      return "Overhead";
    case "reasoning":
      return "Reasoning";
    case "communication":
      return "Communication";
    case "idle":
      return "Idle / Overhead";
    // Legacy categories (older jobs before migration)
    case "code_changes":
      return "Implementation";
    case "delegation":
      return "Investigation";
    case "refactoring":
      return "Implementation";
    case "feature_dev":
      return "Implementation";
    case "code_reading":
    case "search_discovery":
    case "command_execution":
      return "Investigation";
    case "testing":
      return "Verification";
    case "build_deploy":
      return "Setup";
    case "bookkeeping":
    case "other_tools":
      return "Overhead";
    case "user_communication":
      return "Communication";
    // Phase labels (used in phase breakdown)
    case "environment_setup":
      return "Setup";
    case "agent_reasoning":
      return "Active";
    case "finalization":
      return "Finalization";
    case "post_completion":
      return "Post-completion";
    default:
      return bucket.replace(/_/g, " ");
  }
}

// ---------------------------------------------------------------------------
// Activity descriptions — explains what each cost category actually means
// ---------------------------------------------------------------------------

export const ACTIVITY_DESCRIPTIONS: Record<string, string> = {
  // 8 canonical categories
  implementation: "Turns where the agent edited or created files \u2014 the actual coding work",
  investigation: "Turns where the agent read code, searched, browsed, checked diffs/status, or delegated to sub-agents",
  verification: "Turns where the agent ran tests to validate changes",
  git_ops: "Turns where the agent committed, pushed, merged, or managed branches",
  setup: "Turns where the agent installed dependencies or set up the environment",
  overhead: "Turns spent on internal housekeeping — todos, memory, intent tracking",
  reasoning: "Turns of explicit thinking with no user-facing output",
  communication: "Turns where the agent composed a message to you (no tool calls)",
  // Legacy descriptions for older jobs
  delegation: "Turns where the agent delegated work to sub-agents",
  command_execution: "Turns where the agent ran shell commands",
  code_reading: "Turns where the agent read files or checked diffs",
  code_changes: "Turns where the agent edited/created files",
  search_discovery: "Turns where the agent searched code or fetched URLs",
  user_communication: "Turns where the agent composed a message to you",
  bookkeeping: "Turns spent on internal housekeeping",
  other_tools: "Turns using unclassified or custom tools",
  refactoring: "Turns where the agent restructured or renamed code",
  feature_dev: "Turns where the agent built new features",
  testing: "Turns where the agent ran or wrote tests",
  build_deploy: "Turns where the agent ran build or deploy commands",
};


const _PHASE_COLORS: Record<string, string> = {
  environment_setup: "bg-cyan-500",
  agent_reasoning: "bg-blue-500",
  verification: "bg-amber-500",
  finalization: "bg-purple-500",
  post_completion: "bg-slate-400",
};

const _PHASE_SHORT_LABELS: Record<string, string> = {
  environment_setup: "Setup",
  agent_reasoning: "Active",
  verification: "Verify",
  finalization: "Final",
  post_completion: "Post",
};

export function phaseColor(phase: string): string {
  return _PHASE_COLORS[phase] ?? "bg-gray-400";
}

export function phaseShortLabel(phase: string): string {
  return _PHASE_SHORT_LABELS[phase] ?? phase.replace(/_/g, " ");
}

export function estimateCostWithoutCache(
  pricing: { input: number; output: number },
  inputTokens: number,
  outputTokens: number,
  cacheReadTokens: number,
): number {
  return ((inputTokens + cacheReadTokens) * pricing.input + outputTokens * pricing.output) / 1_000_000;
}

// ---------------------------------------------------------------------------
// Activity colors — used by tool mix and cost breakdown visualisations
// ---------------------------------------------------------------------------

export const ACTIVITY_COLORS: Record<string, string> = {
  implementation: "bg-emerald-500",
  investigation: "bg-blue-500",
  verification: "bg-amber-500",
  git_ops: "bg-purple-500",
  setup: "bg-cyan-500",
  reasoning: "bg-indigo-400",
  overhead: "bg-gray-400",
  communication: "bg-violet-500",
  // Legacy fallbacks
  delegation: "bg-blue-500",
  feature_dev: "bg-emerald-400",
  refactoring: "bg-emerald-400",
};
