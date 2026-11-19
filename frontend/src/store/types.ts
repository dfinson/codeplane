/**
 * Store type definitions — domain models used across the application.
 *
 * These mirror the CamelModel shapes from the backend. Components, hooks,
 * and services import these types to stay in sync with the store schema.
 */

import type {
  DiffFileModel, Job, SDKInfo, StoryResponse, TrackerOperationResponse, TranscriptPayload,
  RecipeStepInspection, RecipeAttemptInspection, RecipeRun, RecipeRunList, RecipeStepList,
  TaskLinkInspection, ProjectBoard, ProjectBoardPage, ProjectApprovalPage, ProjectDeliveryPage,
  RecipeAttemptControl, ReviewPreparation, ReviewHistoryPage, JobPendingDecisionPage,
} from "../api/types";

/** Connection status exposed to UI components. */
export type ConnectionStatus = "connected" | "connecting" | "reconnecting" | "disconnected";

/** Minimal job shape matching JobResponse from the backend. */
export interface JobSummary extends Omit<Job, "state" | "mode" | "resolution" | "mergeStatus" | "sdk" | "source" | "observationOnly"> {
  state: string;
  resolution?: string | null;
  mergeStatus?: string | null;
  conflictFiles?: string[] | null;
  resolutionError?: string | null;
  modelDowngraded?: boolean;
  requestedModel?: string | null;
  actualModel?: string | null;
  /** Current setup step for jobs in 'preparing' state (e.g. "creating_workspace"). */
  setupStep?: string | null;
  mode?: string;
  sdk?: Job["sdk"];
  source?: Job["source"];
  observationOnly?: Job["observationOnly"];
}

export interface ApprovalRequest {
  id: string;
  jobId: string;
  description: string;
  proposedAction: string | null;
  requestedAt: string;
  resolvedAt: string | null;
  resolution: string | null;
  requiresExplicitApproval: boolean;
  notes: string | null;
}

/** TraceForge governance verdict — surfaced natively (replaces the retired CP tier). */
export type RecommendedAction = "allow" | "warn" | "escalate" | "deny" | "transform";

/** A batch of actions awaiting operator approval (escalate/deny recommendations). */
export interface BatchApprovalAction {
  id: string;
  kind: string;
  recommendedAction: RecommendedAction;
  reasonCode: string;
  reason: string;
  riskScore: number | null;
  riskBand: string;
  effect: string | null;
  checkpointRef: string | null;
  description: string;
}

export interface BatchApproval {
  batchId: string;
  jobId: string;
  actions: BatchApprovalAction[];
  summary: string;
  requestedAt: string;
  resolvedAt: string | null;
  resolution: string | null;
}

export interface LogLine {
  jobId: string;
  seq: number;
  timestamp: string;
  level: string;
  message: string;
  context: Record<string, unknown> | null;
}

export interface TranscriptEntry extends Pick<TranscriptPayload,
  "metadata" | "pairingStatus" | "pairingReason" | "admissionStatus" | "executionStatus" | "operatorContent"> {
  jobId: string;
  eventId?: string;
  sequence?: number;
  timestamp: string;
  kind: string;
  content: string;
  // Rich fields — only present for specific transcript kinds
  title?: string;        // agent messages: optional annotation title
  turnId?: string;       // groups reasoning + tool_calls + message into one turn
  toolCallId?: string;   // pairs tool.call.started/completed
  toolName?: string;     // tool_call: identifier
  arguments?: string;    // tool_call: JSON-serialised arguments
  result?: string;       // tool_call: text output
  success?: boolean;     // tool_call: success flag
  toolIssue?: string;    // tool_call: short issue summary when attention is needed
  toolIntent?: string;   // tool_call: SDK-provided intent string (deterministic label)
  toolTitle?: string;    // tool_call: SDK-provided display title
  toolDisplay?: string;  // tool_call: deterministic per-tool label (e.g. "$ ls -la", "Read src/main.py")
  toolDisplayFull?: string;  // tool_call: same label without char truncation (for CSS-based responsive truncation)
  toolDurationMs?: number;  // tool_call: execution time in milliseconds
  toolVisibility?: string;  // tool_call: "hidden" | "collapsed" | "visible"
  // AI-generated group summary — patched in asynchronously via tool_group_summary SSE
  toolGroupSummary?: string;
  // Sidecar fields — present for sidecar transcript kinds
  sidecarName?: string;
  sidecarIcon?: string;
  sidecarDescription?: string;
  sidecarTemplateId?: string;
}

export interface PlanStep {
  planStepId?: string;
  label: string;
  status: "done" | "active" | "pending" | "skipped";
  summary?: string;
  toolCount?: number;
  filesWritten?: string[];
  durationMs?: number;
}

export interface TimelineEntry {
  headline: string;
  headlinePast: string;
  summary: string;
  timestamp: string;
  active: boolean;
}

/** A single step in the activity timeline — one visible agent turn. */
export interface ActivityTimelineStep {
  turnId: string;
  title: string;
  activityId: string;
  planItemId?: string | null;
  /** Highest-severity governance recommendation seen during this turn. */
  recommendedAction?: RecommendedAction | null;
}

/** A retrospective grouping of steps in the activity timeline. */
export interface ActivityTimelineActivity {
  activityId: string;
  label: string;
  status: "active" | "done";
  steps: ActivityTimelineStep[];
  planItemId?: string | null;
}

/** Per-job activity timeline state. */
export interface ActivityTimelineState {
  activities: ActivityTimelineActivity[];
}

/** A single entry within a secondary session (reasoning, tool call, output, error). */
export interface SecondarySessionEntry {
  seq: number;
  kind: "reasoning" | "tool_call" | "output" | "error";
  content: string;
  toolName?: string | null;
  toolArgs?: string | null;
  durationMs?: number | null;
  toolResult?: string | null;
  toolDisplay?: string | null;
  toolDisplayFull?: string | null;
  toolSuccess?: boolean | null;
  toolIssue?: string | null;
  toolVisibility?: string | null;
}

/** A unified secondary session (preflight, sidecar, monitor, extractor). */
export interface SecondarySession {
  id: string;
  jobId: string;
  kind: "preflight" | "sidecar" | "monitor" | "extractor";
  name: string;
  icon: string;
  status: "running" | "completed" | "failed" | "timeout";
  startedAt: string;
  completedAt?: string | null;
  output?: string | null;
  inputTokens?: number;
  outputTokens?: number;
  costUsd?: number;
  entries: SecondarySessionEntry[];
}

/** Terminal session metadata tracked in the store. */
export interface TerminalSession {
  id: string;
  label: string;
  cwd?: string;
  jobId?: string | null;
}

/** Real-time index progress for a repo being onboarded. */

/** Structural warning emitted at step boundaries during execution. */

/** Context handoff — emitted when context crosses a session boundary. */
export interface ContextHandoff {
  jobId: string;
  source: "preflight" | "resume" | "resume_native" | "followup";
  sourceSessionId?: string | null;
  summary: string;
  content?: string | null;
  timestamp: string;
}

export interface AppState {
  jobPendingDecisionPages: Record<string, JobPendingDecisionPage>;
  reviewPreparations: Record<string, ReviewPreparation>;
  reviewHistoryPages: Record<string, ReviewHistoryPage>;
  recipeAttemptControls: Record<string, RecipeAttemptControl>;
  projectBoards: Record<string, ProjectBoard>;
  projectBoardPages: Record<string, ProjectBoardPage>;
  projectApprovalPages: Record<string, ProjectApprovalPage>;
  projectDeliveryPages: Record<string, ProjectDeliveryPage>;
  projectBoardVersion: number;
  taskLinkInspections: Record<string, TaskLinkInspection>;
  recipeCurrentRuns: Record<string, RecipeRun | null>;
  recipeRuns: Record<string, RecipeRun>;
  recipeRunHistoryPages: Record<string, RecipeRunList>;
  recipeStepPages: Record<string, RecipeStepList>;
  recipeStepInspections: Record<string, RecipeStepInspection>;
  recipeAttemptInspections: Record<string, RecipeAttemptInspection>;
  recipeVersions: Record<string, number>;
  // Data slices
  trackerOperations: Record<string, TrackerOperationResponse>;
  trackerOperationLoads: Record<string, {
    loading: boolean;
    loaded: boolean;
    error: string | null;
    requestVersion: number;
  }>;
  jobs: Record<string, JobSummary>;
  approvals: Record<string, ApprovalRequest>;
  batchApprovals: Record<string, BatchApproval>; // keyed by batchId
  logs: Record<string, LogLine[]>; // keyed by jobId
  transcript: Record<string, TranscriptEntry[]>; // keyed by jobId
  diffs: Record<string, DiffFileModel[]>; // keyed by jobId
  stories: Record<string, StoryResponse>; // keyed by jobId
  plans: Record<string, PlanStep[]>; // keyed by jobId
  timelines: Record<string, TimelineEntry[]>; // keyed by jobId
  activityTimelines: Record<string, ActivityTimelineState>; // keyed by jobId
  /** Unified secondary sessions — keyed by jobId, then sessionId. */
  secondarySessions: Record<string, Record<string, SecondarySession>>;
  /** Accumulated streaming text for in-progress agent messages, keyed by
   * "${jobId}:${turnId}" (or "${jobId}:__default__" when turnId is absent).
   * Cleared when the complete agent message arrives for that turn. */
  streamingMessages: Record<string, string>;
  /** Accumulated streaming tool output for in-progress tool execution, keyed by
   * "${jobId}:${toolCallId}" (or "${jobId}:${toolName}" as fallback).
   * Cleared when the tool_call completion arrives. */
  streamingToolOutput: Record<string, string>;
  /** Accumulated streaming reasoning text for in-progress thinking, keyed by
   * "${jobId}:${turnId}" (or "${jobId}:__default__" when turnId is absent).
   * Cleared when the complete reasoning message arrives for that turn. */
  streamingReasoning: Record<string, string>;
  /** Monotonically-increasing counter per job, bumped on each telemetry_updated
   * SSE event. Components watching this trigger a telemetry re-fetch. */
  telemetryVersions: Record<string, number>; // keyed by jobId
  /** Bumped after terminal artifact collection finishes so panels re-fetch. */
  artifactVersions: Record<string, number>; // keyed by jobId

  /** Context handoffs per job — shows what was passed between sessions. */
  contextHandoffs: Record<string, ContextHandoff[]>;

  // Terminal state
  terminalDrawerOpen: boolean;
  terminalDrawerHeight: number;
  terminalSessions: Record<string, TerminalSession>;
  activeTerminalTab: string | null;

  // SDK + model catalogue (loaded once at app startup)
  sdks: SDKInfo[];
  defaultSdk: string | null;
  sdksLoading: boolean;
  modelsBySdk: Record<string, { value: string; label: string }[]>;
  defaultModelBySdk: Record<string, string | null>;
  modelsLoadingBySdk: Record<string, boolean>;

  // UI state
  connectionStatus: ConnectionStatus;
  reconnectAttempt: number;
  /** Plan item ID being hovered — used to highlight linked activities. */
  hoveredPlanItemId: string | null;
  /** Incremented when policy settings change via SSE — triggers re-fetch. */
  policySettingsVersion: number;
  /** Per-job heartbeat info — last activity timestamp and active tool. */
  jobHeartbeats: Record<string, { lastActivityAt: string; activeToolName?: string; activeToolSince?: string }>;

  // Actions
  hydrateTrackerOperations: (operations: TrackerOperationResponse[]) => void;
  loadTrackerOperations: (projectId: string) => Promise<void>;
  refreshTrackerOperations: () => Promise<void>;
  setConnectionStatus: (status: ConnectionStatus) => void;
  setReconnectAttempt: (attempt: number) => void;
  /** Fetches SDK list + models for the default SDK. Called once on app mount. */
  initSdksAndModels: () => Promise<void>;
  /** Fetches models for a specific SDK (no-op if already loaded). */
  loadModelsForSdk: (sdkId: string) => Promise<void>;
  dispatchSSEEvent: (eventType: string, data: unknown) => void;
  applySnapshot: (jobs: JobSummary[], approvals: ApprovalRequest[]) => void;
  /** Bulk-apply a full job snapshot from the hydration endpoint. */
  hydrateJob: (snapshot: {
    job: JobSummary;
    logs: LogLine[];
    transcript: TranscriptEntry[];
    diff: DiffFileModel[];
    approvals: ApprovalRequest[];
    timeline: TimelineEntry[];
    steps?: Array<{ planStepId?: string; label: string; status: string; summary?: string; toolCount?: number; filesWritten?: string[]; durationMs?: number }>;
    turnSummaries?: Array<Record<string, unknown>>;
    secondarySessions?: Array<{ id: string; kind: string; name: string; icon: string; status: string; startedAt: string; completedAt?: string | null; output?: string | null; inputTokens?: number; outputTokens?: number; costUsd?: number; entries?: Array<{ seq: number; kind: string; content: string; toolName?: string | null; toolArgs?: string | null; durationMs?: number | null; toolResult?: string | null; toolDisplay?: string | null; toolDisplayFull?: string | null; toolSuccess?: boolean | null; toolIssue?: string | null; toolVisibility?: string | null }> }>;
    contextHandoffs?: Array<{ source: string; sourceSessionId?: string | null; summary: string; content?: string | null; timestamp: string }>;
  }) => void;

  // Terminal actions
  toggleTerminalDrawer: () => void;
  setTerminalDrawerHeight: (height: number) => void;
  setActiveTerminalTab: (id: string) => void;
  addTerminalSession: (session: TerminalSession) => void;
  removeTerminalSession: (id: string) => void;
  createTerminalSession: (opts?: { cwd?: string; jobId?: string; label?: string }) => void;
  setHoveredPlanItemId: (id: string | null) => void;
  setStory: (jobId: string, story: StoryResponse) => void;

}
