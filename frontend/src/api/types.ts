/**
 * Friendly type aliases re-exported from the generated OpenAPI schema.
 *
 * All component code imports from this file, never from schema.d.ts directly.
 */

import type { components } from "./schema";

export type Job = components["schemas"]["JobResponse"];
export type UsageAmount = components["schemas"]["UsageAmount"];
export type JobState = Job["state"];
export type CreateJobRequest = components["schemas"]["CreateJobRequest"];
export type CreateJobResponse = components["schemas"]["CreateJobResponse"];
export type JobListResponse = components["schemas"]["JobListResponse"];
export type RecipeDefinition = components["schemas"]["RecipeDefinition"];
export type RecipeDraft = components["schemas"]["RecipeDraftResponse"];
export type RecipeRevision = components["schemas"]["RecipeRevisionResponse"];
export type RecipeDraftList = components["schemas"]["RecipeDraftListResponse"];
export type RecipeRevisionList = components["schemas"]["RecipeRevisionListResponse"];
export type RecipeValidationResult = components["schemas"]["RecipeValidationResult"];
export type RecipeStep = components["schemas"]["RecipeStep"];
export type RecipeInput = components["schemas"]["RecipeInput"];
export type RecipeOutput = components["schemas"]["RecipeOutput"];
export type RecipeParameter = components["schemas"]["RecipeParameter"];
export type RecipeRoute = components["schemas"]["RecipeRoute"];
export type RecipeCommandArgument = components["schemas"]["RecipeCommandArgument"];
export type RecipeRunner = RecipeStep["runner"];
export type RecipeCapability = NonNullable<RecipeStep["capabilities"]>[number];
export type RecipeValueType = RecipeOutput["type"];
export type RecipeRun = components["schemas"]["RecipeRunResponse"];
export type RecipeRunList = components["schemas"]["RecipeRunListResponse"];
export type RecipeCurrentRun = components["schemas"]["RecipeCurrentRunResponse"];
export type RecipeStepList = components["schemas"]["RecipeStepListResponse"];
export type RecipeStepInspection = components["schemas"]["RecipeStepInspection"];
export type RecipeAttemptInspection = components["schemas"]["RecipeAttemptInspection"];
export type RecipeConsumedInputPage = components["schemas"]["RecipeConsumedInputPage"];
export type RecipeSelection = components["schemas"]["RecipeSelection"];
export type RecipeAttemptControl = components["schemas"]["RecipeAttemptControl"];
export type RecipeMessageReceipt = components["schemas"]["RecipeMessageReceipt"];
export type RecipeOperationReceipt = components["schemas"]["RecipeOperationReceipt"];
export type RecipeReconcileRequest = components["schemas"]["RecipeReconcileRequest"];
export type RecipeResumeResponse = components["schemas"]["RecipeResumeResponse"];
export type RecipeGateDecisionRequest = components["schemas"]["RecipeGateDecisionRequest"];
export type JobPendingDecisionPage = components["schemas"]["JobPendingDecisionPage"];
export type ReviewPreparation = components["schemas"]["ReviewPreparation"];
export type ReviewRecord = components["schemas"]["ReviewRecord"];
export type ReviewHistoryPage = components["schemas"]["ReviewHistoryPage"];
export type PrepareReviewRequest = components["schemas"]["PrepareReviewRequest"];
export type RecordReviewRequest = components["schemas"]["RecordReviewRequest"];
export type TranscriptPayload = components["schemas"]["TranscriptPayload"];
export type TranscriptSearchResult = components["schemas"]["TranscriptSearchResult"];
export type TelemetryToolCall = components["schemas"]["TelemetryToolCall"];
export type AnalyticsOverviewResponse = components["schemas"]["AnalyticsOverviewResponse"];
export type AnalyticsToolsResponse = components["schemas"]["AnalyticsToolsResponse"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type RegisterRepoRequest = components["schemas"]["RegisterRepoRequest"];
export type RegisterRepoResponse = components["schemas"]["RegisterRepoResponse"];
export type RepoListResponse = components["schemas"]["RepoListResponse"];
export type RepoDetailResponse = components["schemas"]["RepoDetailResponse"];
export type ProjectSummaryResponse = components["schemas"]["ProjectSummaryResponse"];
export type ProjectListSummaryResponse = components["schemas"]["ProjectListSummaryResponse"];
export type ProjectResponse = components["schemas"]["ProjectResponse"];
export type ProjectListResponse = components["schemas"]["ProjectListResponse"];
export type Chat = components["schemas"]["ChatResponse"];
export type ChatListResponse = components["schemas"]["ChatListResponse"];
export type ChatMessage = components["schemas"]["ChatMessageResponse"];
export type ChatTurnResponse = components["schemas"]["ChatTurnResponse"];
export type CreateChatRequest = components["schemas"]["CreateChatRequest"];
export type LaunchJobFromChatRequest = components["schemas"]["LaunchJobFromChatRequest"];
export type AttachChatToChainRequest = components["schemas"]["AttachChatToChainRequest"];
export type TaskLinkResponse = components["schemas"]["TaskLinkResponse"];
export type TaskLinkInspection = components["schemas"]["TaskLinkInspectionResponse"];
export type ProjectBoard = components["schemas"]["ProjectBoardResponse"];
export type ProjectBoardPage = components["schemas"]["ProjectBoardPage"];
export type ProjectBoardItem = components["schemas"]["ProjectBoardItem"];
export type ProjectBoardColumn = ProjectBoardItem["column"];
export type ProjectApprovalPage = components["schemas"]["ProjectApprovalPage"];
export type ProjectDeliveryPage = components["schemas"]["ProjectDeliveryPage"];
export type TaskLinkListResponse = components["schemas"]["TaskLinkListResponse"];
export type CreateManualTaskLinkRequest = components["schemas"]["CreateManualTaskLinkRequest"];
export type TrackerTicketResponse = components["schemas"]["TrackerTicketResponse"];
export type TrackerSummaryResponse = components["schemas"]["TrackerSummaryResponse"];
export type TrackerLinkResponse = components["schemas"]["TrackerLinkResponse"];
export type TrackerLinkListResponse = components["schemas"]["TrackerLinkListResponse"];
export type TrackerOperationResponse = components["schemas"]["TrackerOperationResponse"];
export type TrackerOperationListResponse = components["schemas"]["TrackerOperationListResponse"];
export type ReconcileTrackerOperationRequest = components["schemas"]["ReconcileTrackerOperationRequest"];
export type RetryTrackerOperationRequest = components["schemas"]["RetryTrackerOperationRequest"];
export type CredentialResponse = components["schemas"]["CredentialResponse"];
export type CreateCredentialRequest = components["schemas"]["CreateCredentialRequest"];
export type CredentialListResponse = components["schemas"]["CredentialListResponse"];
export type CompletionStrategy = "auto_merge" | "pr_only" | "manual";

// Sidecar template types
export type SidecarTemplate = components["schemas"]["SidecarTemplateResponse"];
export type SidecarTemplateListResponse = components["schemas"]["SidecarTemplateListResponse"];
export type CreateSidecarTemplateRequest = components["schemas"]["CreateSidecarTemplateRequest"];
export type UpdateSidecarTemplateRequest = components["schemas"]["UpdateSidecarTemplateRequest"];
export type GenerateSidecarRequest = components["schemas"]["GenerateSidecarRequest"];
export type GenerateSidecarResponse = components["schemas"]["GenerateSidecarResponse"];

export interface Settings {
  maxConcurrentJobs: number;
  autoPush: boolean;
  cleanupWorktree: boolean;
  deleteBranchAfterMerge: boolean;
  artifactRetentionDays: number;
  maxArtifactSizeMb: number;
  autoArchiveDays: number;
  maxTurns: number;
  verify: boolean;
  selfReview: boolean;
  verifyPrompt: string;
  selfReviewPrompt: string;
}

// SSE payload types — not in the OpenAPI schema since they're sent via SSE,
// so we define them here matching the backend CamelModel shapes.
export interface LogLine {
  jobId: string;
  seq: number;
  timestamp: string;
  level: "debug" | "info" | "warn" | "error";
  message: string;
  context: Record<string, unknown> | null;
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

// --- Action Policy batch types ---

/** TraceForge governance verdict — surfaced natively (replaces the retired CP tier). */
export type RecommendedAction = "allow" | "warn" | "escalate" | "deny" | "transform";

export interface BatchAction {
  id: string;
  kind: string;
  toolName: string | null;
  path: string | null;
  command: string | null;
  recommendedAction: RecommendedAction;
  reasonCode: string;
  riskScore: number | null;
  riskBand: string;
  effect: string | null;
  reason: string;
  checkpointRef: string | null;
}

export interface BatchApprovalRequest {
  batchId: string;
  jobId: string;
  actions: BatchAction[];
  requestedAt: string;
}

export interface ResolveBatchRequest {
  batchId: string;
  resolution: "approved" | "rejected" | "partial";
  approvedIds?: string[];
  trustGrantId?: string;
}

export interface JobStateChangedPayload {
  jobId: string;
  previousState: string | null;
  newState: string;
  timestamp: string;
}

// --- Diff types ---

export type DiffLineType = "context" | "addition" | "deletion";
export type DiffFileStatus = "added" | "modified" | "deleted" | "renamed";

export interface DiffLineModel {
  type: DiffLineType;
  content: string;
}

export interface DiffHunkModel {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLineModel[];
}


export interface DiffFileModel {
  path: string;
  status: DiffFileStatus;
  additions: number;
  deletions: number;
  hunks: DiffHunkModel[];
  truncated?: boolean;
  rawSize?: number | null;
  writeCount?: number | null;
  retryCount?: number | null;
}

// --- Motivation types for intent-annotated diff review ---

export interface HunkMotivation {
  editKey: string;
  title: string;
  why: string;
}

export interface FileMotivation {
  title: string;
  why: string;
  unmatchedEdits: HunkMotivation[];
}

export interface StepDiffResponse {
  stepId: string;
  diff: string;
  filesChanged: number;
  changedFiles: DiffFileModel[];
  stepContext?: string | null;
  fileMotivations?: Record<string, FileMotivation>;
  hunkMotivations?: Record<string, HunkMotivation>;
}





export interface JobMotivationsResponse {
  jobId: string;
  fileMotivations: Record<string, FileMotivation>;
  hunkMotivations: Record<string, HunkMotivation>;
}

export interface DiffUpdatePayload {
  jobId: string;
  changedFiles: DiffFileModel[];
}

// --- Story types ---

export interface StoryBlock {
  type: "narrative" | "reference" | "beat" | "heading";
  // narrative / beat fields
  text?: string | null;
  // heading fields
  level?: number | null;
  // beat fields
  beatKind?: "decide" | "backtrack" | "insight" | "verify" | null;
  // reference fields
  spanId?: number | null;
  stepNumber?: number | null;
  stepTitle?: string | null;
  file?: string | null;
  why?: string | null;
  turnId?: string | null;
  editCount?: number | null;
  snippet?: string | null;
  action?: "created" | "modified" | "read" | null;
}

export interface StoryResponse {
  jobId: string;
  blocks: StoryBlock[];
  cached: boolean;
  beatCount: number;
  hasDecisions: boolean;
  hasBacktracks: boolean;
  pending?: boolean;
}

// --- Review signal types ---

export interface TestCoModification {
  turnId: string | null;
  stepNumber: number | null;
  stepTitle: string | null;
  testFiles: string[];
  sourceFiles: string[];
}

export interface ReviewSignals {
  testCoModifications: TestCoModification[];
}

export interface ReviewComplexity {
  tier: "quick" | "standard" | "deep";
  signals: string[];
}

// --- Resolve types ---

export interface ResolveJobResponse {
  resolution: string;
  prUrl?: string | null;
  conflictFiles?: string[] | null;
  error?: string | null;
}

// --- Artifact types ---

export type ArtifactType = components["schemas"]["ArtifactType"];
export type ExecutionPhase = components["schemas"]["ExecutionPhase"];
export type GitMergeOutcome = components["schemas"]["GitMergeOutcome"];

export type ArtifactResponse = components["schemas"]["ArtifactResponse"];
export type ArtifactListResponse = components["schemas"]["ArtifactListResponse"];

// --- Workspace types ---

export type WorkspaceEntryType = "file" | "directory";

export interface WorkspaceEntry {
  path: string;
  type: WorkspaceEntryType;
  sizeBytes: number | null;
}

export interface WorkspaceListResponse {
  items: WorkspaceEntry[];
  cursor: string | null;
  hasMore: boolean;
}

// --- SDK types ---

export interface SDKInfo {
  id: string;
  name: string;
  enabled: boolean;
  status: "ready" | "not_installed" | "not_configured";
  authenticated: boolean | null;
  hint: string;
}

export interface SDKListResponse {
  default: string;
  sdks: SDKInfo[];
}

// --- Naming suggestion types ---

export interface SuggestNamesResponse {
  title: string;
  description: string;
  branchName: string;
  worktreeName: string;
}

export type JobTelemetryResponse = components["schemas"]["JobTelemetryResponse"];
export type CurrencyValue = JobTelemetryResponse["totalCost"];
export type RetryCostResponse = components["schemas"]["RetryCostResponse"];
