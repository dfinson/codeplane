import type { CurrencyValue } from "./types";
/**
 * REST API client module.
 *
 * Centralizes all HTTP calls to the backend. Components should import
 * functions from here rather than calling fetch() directly.
 */

import type {
  ArtifactListResponse,
  CreateJobRequest,
  CreateJobResponse,
  CreateSidecarTemplateRequest,
  RecipeDefinition,
  RecipeDraft,
  RecipeRevision,
  RecipeDraftList,
  RecipeRevisionList,
  RecipeValidationResult,
  RecipeRun,
  RecipeRunList,
  RecipeCurrentRun,
  RecipeStepList,
  RecipeStepInspection,
  RecipeAttemptInspection,
  RecipeConsumedInputPage,
  RecipeAttemptControl,
  RecipeMessageReceipt,
  RecipeOperationReceipt,
  RecipeReconcileRequest,
  RecipeResumeResponse,
  RecipeGateDecisionRequest,
  JobPendingDecisionPage,
  ReviewPreparation,
  ReviewRecord,
  ReviewHistoryPage,
  PrepareReviewRequest,
  RecordReviewRequest,
  ApprovalRequest,
  DiffFileModel,
  GenerateSidecarResponse,
  HealthResponse,
  Job,
  JobListResponse,
  RepoDetailResponse,
  RepoListResponse,
  RegisterRepoResponse,
  ProjectListSummaryResponse,
  ProjectListResponse,
  ProjectResponse,
  Chat,
  ChatListResponse,
  ChatMessage,
  CreateChatRequest,
  TaskLinkListResponse,
  TaskLinkInspection,
  ProjectBoard,
  ProjectBoardPage,
  ProjectBoardColumn,
  ProjectApprovalPage,
  ProjectDeliveryPage,
  TrackerLinkListResponse,
  TrackerLinkResponse,
  TrackerSummaryResponse,
  TrackerOperationResponse,
  TrackerOperationListResponse,
  ReconcileTrackerOperationRequest,
  RetryTrackerOperationRequest,
  SDKListResponse,
  Settings,
  SidecarTemplate,
  SidecarTemplateListResponse,
  UpdateSidecarTemplateRequest,
  WorkspaceListResponse,
} from "./types";

const BASE = "/api";
const REQUEST_TIMEOUT_MS = 30_000;
const MAX_RETRIES = 2;
const RETRY_DELAY_MS = 1000;

export function fetchTaskLinkInspection(projectId: string, taskId: string): Promise<TaskLinkInspection> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/task-links/${encodeURIComponent(taskId)}`);
}

export function fetchProjectBoard(projectId: string, signal?: AbortSignal): Promise<ProjectBoard> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/board`, { signal });
}

export function fetchProjectBoardPage(
  projectId: string, column: ProjectBoardColumn, after: string, signal?: AbortSignal,
): Promise<ProjectBoardPage> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/board/columns/${column}?${new URLSearchParams({ after })}`, { signal });
}

export function fetchProjectBoardApprovals(projectId: string, after = "", signal?: AbortSignal): Promise<ProjectApprovalPage> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/board/approvals?${new URLSearchParams({ after })}`, { signal });
}

export function fetchProjectBoardDeliveries(projectId: string, after = "", signal?: AbortSignal): Promise<ProjectDeliveryPage> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/board/deliveries?${new URLSearchParams({ after })}`, { signal });
}

function recipeRunPath(jobId: string, runId: string): string {
  return `/jobs/${encodeURIComponent(jobId)}/recipe/runs/${encodeURIComponent(runId)}`;
}

export function fetchCurrentRecipeRun(jobId: string): Promise<RecipeCurrentRun> {
  return request(`/jobs/${encodeURIComponent(jobId)}/recipe/current`);
}

export function fetchJobPendingDecisions(jobId: string, after = ""): Promise<JobPendingDecisionPage> {
  return request(`/jobs/${encodeURIComponent(jobId)}/recipe/decisions?${new URLSearchParams({ after })}`);
}

export function resumeSelectedRecipeRun(jobId: string, expectedRunId: string, startRequestKey: string, instruction: string): Promise<RecipeResumeResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: "POST", body: JSON.stringify({ expectedRunId, startRequestKey, instruction }),
  });
}

export function prepareReview(jobId: string, selection: PrepareReviewRequest): Promise<ReviewPreparation> {
  return request(`/jobs/${encodeURIComponent(jobId)}/reviews/prepare`, { method: "POST", body: JSON.stringify(selection) });
}

export function recordReview(jobId: string, body: RecordReviewRequest): Promise<ReviewRecord> {
  return request(`/jobs/${encodeURIComponent(jobId)}/reviews`, { method: "POST", body: JSON.stringify(body) });
}

export function fetchReviewHistory(jobId: string, after = 0, highWater?: number): Promise<ReviewHistoryPage> {
  const query = new URLSearchParams({ after: String(after), limit: "10" });
  if (highWater !== undefined) query.set("highWater", String(highWater));
  return request(`/jobs/${encodeURIComponent(jobId)}/reviews?${query}`);
}

export function fetchRecipeRun(jobId: string, runId: string): Promise<RecipeRun> {
  return request(recipeRunPath(jobId, runId));
}

export function fetchRecipeRunHistory(
  jobId: string, params: { after?: number; highWater?: number; limit?: number } = {},
): Promise<RecipeRunList> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined) query.set(key, String(value));
  return request(`/jobs/${encodeURIComponent(jobId)}/recipe/runs?${query}`);
}

export function fetchRecipeRunSteps(
  jobId: string, runId: string, after = "",
): Promise<RecipeStepList> {
  const query = new URLSearchParams({ after, limit: "30" });
  return request(`${recipeRunPath(jobId, runId)}/steps?${query}`);
}

export function fetchRecipeStep(jobId: string, runId: string, stepId: string): Promise<RecipeStepInspection> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}`);
}

export function fetchRecipeAttempt(
  jobId: string, runId: string, stepId: string, attemptId: string,
): Promise<RecipeAttemptInspection> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}`);
}

export function fetchRecipeConsumers(jobId: string, runId: string, stepId: string, attemptId: string, after = ""): Promise<RecipeConsumedInputPage> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/consumers?${new URLSearchParams({ after })}`);
}

export function fetchRecipeAttemptControl(
  jobId: string, runId: string, stepId: string, attemptId: string,
): Promise<RecipeAttemptControl> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/control`);
}

export function stopRecipeAttempt(jobId: string, runId: string, stepId: string, attemptId: string, expectedToken: string): Promise<RecipeOperationReceipt> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/stop`, {
    method: "POST", body: JSON.stringify({ expectedToken }),
  });
}

export function resumeRecipeAttempt(jobId: string, runId: string, stepId: string, attemptId: string, expectedVersion: number): Promise<RecipeOperationReceipt> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/resume`, {
    method: "POST", body: JSON.stringify({ expectedVersion }),
  });
}

export function reconcileRecipeAttempt(jobId: string, runId: string, stepId: string, attemptId: string, body: RecipeReconcileRequest): Promise<RecipeOperationReceipt> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/reconcile`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export function decideRecipeAttempt(jobId: string, runId: string, stepId: string, attemptId: string, body: RecipeGateDecisionRequest): Promise<RecipeOperationReceipt> {
  return request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/decision`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export async function sendRecipeAttemptMessage(
  jobId: string, runId: string, stepId: string, attemptId: string,
  expectedExecution: string, content: string,
): Promise<RecipeMessageReceipt> {
  try {
    return await request(`${recipeRunPath(jobId, runId)}/steps/${encodeURIComponent(stepId)}/attempts/${encodeURIComponent(attemptId)}/messages`, {
      method: "POST", body: JSON.stringify({ expectedExecution, content }),
    });
  } catch (error) {
    if (error instanceof ApiError && [400, 404, 409, 422].includes(error.status)) {
      return {
        jobId, runId, stepInstanceId: stepId, attemptId, status: error.status === 409 ? "conflict" : "failed", executionToken: null,
        executionStatus: "unconfirmed",
        reason: typeof error.body?.reason === "string" ? error.body.reason : error.detail,
      };
    }
    throw error;
  }
}

class ApiError extends Error {
  /** Parsed response body (if JSON), for structured error inspection. */
  body: Record<string, unknown> | null;
  constructor(
    public status: number,
    public detail: string,
    body?: Record<string, unknown> | null,
  ) {
    super(detail);
    this.name = "ApiError";
    this.body = body ?? null;
  }
}

/** Strip HTML tags to prevent XSS when error details are rendered in UI. */
function sanitize(text: string): string {
  return text.replace(/<[^>]*>/g, "");
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (init?.body) {
    headers["Content-Type"] = "application/json";
  }

  let lastError: unknown;
  const isIdempotent = !init?.method || init.method === "GET" || init.method === "HEAD";

  for (let attempt = 0; attempt <= (isIdempotent ? MAX_RETRIES : 0); attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, RETRY_DELAY_MS * attempt));
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const res = await fetch(`${BASE}${path}`, {
        ...init,
        headers: { ...headers, ...init?.headers },
        signal: init?.signal ?? controller.signal,
      });
      clearTimeout(timeout);

      if (!res.ok) {
        // Don't retry client errors (4xx)
        if (res.status >= 400 && res.status < 500) {
          throw await buildApiError(res);
        }
        // Retry server errors (5xx) for idempotent requests
        lastError = await buildApiError(res);
        if (attempt < (isIdempotent ? MAX_RETRIES : 0)) continue;
        throw lastError;
      }
      if (res.status === 204) return undefined as T;
      return res.json() as Promise<T>;
    } catch (e) {
      clearTimeout(timeout);
      if (e instanceof ApiError) throw e;
      if ((e as Error).name === "AbortError") {
        throw new ApiError(0, "Request timed out");
      }
      lastError = e;
      if (attempt < (isIdempotent ? MAX_RETRIES : 0)) continue;
      throw e;
    }
  }
  throw lastError;
}

async function buildApiError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  let detail: string;
  if (body == null) {
    detail = res.statusText || `HTTP ${res.status}`;
  } else if (typeof body.detail === "string") {
    detail = sanitize(body.detail);
  } else if (typeof body.detail === "object" && body.detail !== null && !Array.isArray(body.detail)) {
    // Structured detail object (e.g. {reason, message, blockers})
    detail = sanitize(body.detail.message ?? body.detail.reason ?? (res.statusText || `HTTP ${res.status}`));
  } else if (Array.isArray(body.detail)) {
    detail = body.detail
      .map((e: { loc?: string[]; msg?: string }) =>
        [e.loc?.slice(1).join("."), e.msg].filter(Boolean).join(": "),
      )
      .join("; ");
  } else {
    detail = res.statusText || `HTTP ${res.status}`;
  }
  return new ApiError(res.status, detail, body);
}

// --- Health ---

export function fetchHealth(): Promise<HealthResponse> {
  return request("/health");
}

export interface SidecarSessionMetrics {
  global: {
    totalCalls: number;
    avgLatencyMs: number;
    activeJobs: number;
    poolSize: number;
    warmTokens: number;
  };
  jobs: Record<string, {
    callCount: number;
    avgLatencyMs: number;
    totalLatencyMs: number;
    inputTokens: number;
    outputTokens: number;
    costUsd: CurrencyValue;
  }>;
}

export function fetchSidecarSessionMetrics(): Promise<SidecarSessionMetrics> {
  return request("/sidecar-sessions/metrics");
}

// --- Jobs ---

export function fetchJobs(params?: {
  state?: string;
  limit?: number;
  cursor?: string;
  archived?: boolean;
}): Promise<JobListResponse> {
  const qs = new URLSearchParams();
  if (params?.state) qs.set("state", params.state);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.cursor) qs.set("cursor", params.cursor);
  if (params?.archived !== undefined) qs.set("archived", String(params.archived));
  const query = qs.toString();
  return request(`/jobs${query ? `?${query}` : ""}`);
}

export function fetchJob(jobId: string): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(jobId)}`);
}

export function fetchJobLogs(jobId: string, level: string = "debug", limit = 2000): Promise<import("../store").LogLine[]> {
  return request<{ items: import("../store").LogLine[] }>(`/jobs/${encodeURIComponent(jobId)}/logs?level=${encodeURIComponent(level)}&limit=${limit}`).then((r) => r.items);
}

export function fetchJobTranscript(jobId: string, limit = 2000): Promise<import("../store").TranscriptEntry[]> {
  return request<{ items: import("../store").TranscriptEntry[] }>(`/jobs/${encodeURIComponent(jobId)}/transcript?limit=${limit}`).then((r) => r.items);
}

export function fetchJobDiff(jobId: string): Promise<DiffFileModel[]> {
  return request<{ items: DiffFileModel[] }>(`/jobs/${encodeURIComponent(jobId)}/diff`).then((r) => r.items);
}

/** Fetch the full (non-truncated) diff for a single file within a job. */
export function fetchDiffFile(jobId: string, filePath: string): Promise<DiffFileModel> {
  return request<DiffFileModel>(`/jobs/${encodeURIComponent(jobId)}/diff-file?path=${encodeURIComponent(filePath)}`);
}

/** Fetch the diff for a single step/turn (uses turn_id as the step lookup key). */
export function fetchStepDiff(jobId: string, turnId: string): Promise<import("../api/types").StepDiffResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/steps/${encodeURIComponent(turnId)}/diff`);
}

export function fetchJobTimeline(jobId: string, limit = 200): Promise<import("../store").TimelineEntry[]> {
  return request<{ items: Array<{ headline: string; headlinePast: string; summary?: string; timestamp: string }> }>(
    `/jobs/${encodeURIComponent(jobId)}/timeline?limit=${limit}`,
  ).then((r) =>
    r.items.map((e) => ({
      headline: e.headline,
      headlinePast: e.headlinePast,
      summary: e.summary ?? "",
      timestamp: e.timestamp,
      active: false,
    })),
  );
}

export function fetchTranscriptSearch(
  jobId: string,
  q: string,
  opts?: { kinds?: string[]; stepId?: string; limit?: number },
): Promise<Array<{ seq: number; kind: string; content: string; toolName: string | null; stepId: string | null; stepNumber: number | null; timestamp: string }>> {
  const params = new URLSearchParams({ q });
  if (opts?.kinds) opts.kinds.forEach((k) => params.append("kinds", k));
  if (opts?.stepId) params.set("step_id", opts.stepId);
  if (opts?.limit) params.set("limit", String(opts.limit));
  return request<{ items: Array<{ seq: number; kind: string; content: string; toolName: string | null; stepId: string | null; stepNumber: number | null; timestamp: string }> }>(`/jobs/${encodeURIComponent(jobId)}/transcript/search?${params}`).then((r) => r.items);
}

export function restoreToSha(jobId: string, sha: string): Promise<{ restored: boolean; sha: string }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sha }),
  });
}

/** Full state hydration for a single job — used after reconnect or page refresh. */
export function fetchJobSnapshot(jobId: string): Promise<{
  job: import("../store").JobSummary;
  logs: import("../store").LogLine[];
  transcript: import("../store").TranscriptEntry[];
  diff: DiffFileModel[];
  approvals: import("../store").ApprovalRequest[];
  timeline: import("../store").TimelineEntry[];
  steps?: Array<{ planStepId?: string; label: string; status: string; summary?: string; toolCount?: number; filesWritten?: string[]; durationMs?: number }>;
  turnSummaries?: Array<Record<string, unknown>>;
}> {
  return request(`/jobs/${encodeURIComponent(jobId)}/snapshot`);
}

export function fetchRecipes(after = 0, highWater?: number): Promise<RecipeDraftList> {
  const query = new URLSearchParams({ after: String(after), limit: "30" });
  if (highWater !== undefined) query.set("highWater", String(highWater));
  return request(`/recipes?${query}`);
}

export function fetchRecipe(id: string): Promise<RecipeDraft> {
  return request(`/recipes/${encodeURIComponent(id)}`);
}

export function fetchRecipeRevisions(id: string, after = 0, highWater?: number): Promise<RecipeRevisionList> {
  const query = new URLSearchParams({ after: String(after), limit: "30" });
  if (highWater !== undefined) query.set("highWater", String(highWater));
  return request(`/recipes/${encodeURIComponent(id)}/revisions?${query}`);
}

export function fetchRecipeRevision(id: string): Promise<RecipeRevision> {
  return request(`/recipes/revisions/${encodeURIComponent(id)}`);
}

export function resolveDefaultRecipe(planFirst: boolean): Promise<RecipeRevision> {
  return request("/recipes/resolve-default", { method: "POST", body: JSON.stringify({ planFirst }) });
}

export function createRecipeDraft(definition: RecipeDefinition): Promise<RecipeDraft> {
  return request("/recipes", { method: "POST", body: JSON.stringify(definition) });
}

export function saveRecipeDraft(id: string, expectedVersion: number, definition: RecipeDefinition): Promise<RecipeDraft> {
  return request(`/recipes/${encodeURIComponent(id)}`, {
    method: "PUT", body: JSON.stringify({ expectedVersion, definition }),
  });
}

export function validateRecipe(definition: RecipeDefinition): Promise<RecipeValidationResult> {
  return request("/recipes/validate", { method: "POST", body: JSON.stringify(definition) });
}

export function publishRecipe(id: string, expectedVersion: number): Promise<RecipeRevision> {
  return request(`/recipes/${encodeURIComponent(id)}/publish`, {
    method: "POST", body: JSON.stringify({ expectedVersion }),
  });
}

export function copyRecipeRevision(id: string, name: string): Promise<RecipeDraft> {
  return request(`/recipes/revisions/${encodeURIComponent(id)}/copy`, { method: "POST", body: JSON.stringify({ name }) });
}

export function createJob(body: CreateJobRequest): Promise<CreateJobResponse> {
  return request("/jobs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function warmUtilitySession(): Promise<string> {
  const resp: { sessionToken: string } = await request("/utility-sessions/warm", {
    method: "POST",
  });
  return resp.sessionToken;
}

export async function releaseUtilitySession(token: string): Promise<void> {
  await request(`/utility-sessions/${encodeURIComponent(token)}`, {
    method: "DELETE",
  });
}

export function suggestNames(prompt: string): Promise<import("./types").SuggestNamesResponse> {
  return request("/jobs/suggest-names", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}

export function cancelJob(jobId: string): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
}

export function interruptJob(jobId: string): Promise<void> {
  return request(`/jobs/${encodeURIComponent(jobId)}/interrupt`, {
    method: "POST",
  });
}

export function rerunJob(jobId: string): Promise<CreateJobResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/rerun`, {
    method: "POST",
  });
}

export function fetchModels(sdk?: string): Promise<{ id?: string; name?: string; [key: string]: unknown }[]> {
  const qs = sdk ? `?sdk=${encodeURIComponent(sdk)}` : "";
  return request<{ items: { id?: string; name?: string; [key: string]: unknown }[] }>(`/models${qs}`).then((r) => r.items);
}

export function fetchSDKs(): Promise<SDKListResponse> {
  return request("/sdks");
}

export function fetchJobTelemetry(jobId: string): Promise<{
  available: boolean;
  jobId: string;
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
  toolCalls?: { name: string; durationMs: number; success: boolean }[];
  llmCallCount?: number;
  totalLlmDurationMs?: number;
  llmCalls?: { model: string; inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; cost: number; durationMs: number; isSubagent: boolean }[];
  approvalCount?: number;
  totalApprovalWaitMs?: number;
  agentMessages?: number;
  operatorMessages?: number;
  premiumRequests?: number;
  quotaSnapshots?: Record<string, {
    usedRequests: number;
    entitlementRequests: number;
    remainingPercentage: number;
    overage: number;
    overageAllowed: boolean;
    isUnlimited: boolean;
    usageAllowedWithExhaustedQuota: boolean;
    resetDate: string;
  }>;
  costDrivers?: {
    activity?: Array<{
      dimension: string;
      bucket: string;
      costUsd: CurrencyValue;
      inputTokens: number;
      outputTokens: number;
      callCount: number;
    }>;
    phase?: Array<{
      dimension: string;
      bucket: string;
      costUsd: CurrencyValue;
      inputTokens: number;
      outputTokens: number;
      callCount: number;
    }>;
    editEfficiency?: Array<{
      dimension: string;
      bucket: string;
      costUsd: CurrencyValue;
      inputTokens: number;
      outputTokens: number;
      callCount: number;
    }>;
  };
  turnEconomics?: {
    totalTurns: number;
    peakTurnCostUsd: CurrencyValue;
    avgTurnCostUsd: CurrencyValue;
    costFirstHalfUsd: CurrencyValue;
    costSecondHalfUsd: CurrencyValue;
    turnCurve: Array<{
      dimension: string;
      bucket: string;
      costUsd: CurrencyValue;
      inputTokens: number;
      outputTokens: number;
      callCount: number;
      activity?: string;
      intent?: string;
      actions?: Array<{ text: string; activity: string }>;
    }>;
  };
  fileAccess?: {
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
  };
}> {
  return request(`/jobs/${encodeURIComponent(jobId)}/telemetry`);
}

// --- Repos ---

export function fetchRepos(): Promise<RepoListResponse> {
  return request("/settings/repos");
}

export function fetchProjectsSummary(): Promise<ProjectListSummaryResponse> {
  return request("/settings/projects/summary");
}

/** List every registered Project, including its member repo paths (Story 2.1 / CAP-6). */
export function fetchProjects(): Promise<ProjectListResponse> {
  return request("/settings/projects");
}

export function fetchProject(projectId: string): Promise<ProjectResponse> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}`);
}

export function createProject(body: { name: string; repoPaths: string[] }): Promise<ProjectResponse> {
  return request("/settings/projects", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateProject(
  projectId: string,
  body: { name?: string; repoPaths?: string[]; confirmRepoRemoval?: boolean },
): Promise<ProjectResponse> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** List a Project's currently persisted TaskLinks (Story 4.2 / CAP-9). */
export function fetchProjectTaskLinks(projectId: string): Promise<TaskLinkListResponse> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/task-links`);
}

export function createManualTaskLink(
  projectId: string,
  body: import("./types").CreateManualTaskLinkRequest,
): Promise<import("./types").TaskLinkResponse> {
  return request(`/settings/projects/${encodeURIComponent(projectId)}/task-links`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function startTaskLink(
  projectId: string,
  taskLinkId: string,
): Promise<import("./types").TaskLinkResponse> {
  return request(
    `/settings/projects/${encodeURIComponent(projectId)}/task-links/${encodeURIComponent(taskLinkId)}/start`,
    { method: "POST" },
  );
}

export function fetchChats(projectId?: string): Promise<ChatListResponse> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return request(`/chats${query}`);
}

export function createChat(body: CreateChatRequest): Promise<Chat> {
  return request("/chats", { method: "POST", body: JSON.stringify(body) });
}

export function fetchChat(chatId: string): Promise<Chat> {
  return request(`/chats/${encodeURIComponent(chatId)}`);
}

export function addChatMessage(chatId: string, content: string): Promise<ChatMessage> {
  return request(`/chats/${encodeURIComponent(chatId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function sendChatTurn(chatId: string, content: string): Promise<import("./types").ChatTurnResponse> {
  return request(`/chats/${encodeURIComponent(chatId)}/turns`, {
    method: "POST",
    body: JSON.stringify({ role: "user", content }),
  });
}

export function fetchChatMessages(chatId: string): Promise<ChatMessage[]> {
  return request(`/chats/${encodeURIComponent(chatId)}/messages`);
}

export function launchJobFromChat(
  chatId: string,
  body: import("./types").LaunchJobFromChatRequest,
): Promise<import("./types").CreateJobResponse> {
  return request(`/chats/${encodeURIComponent(chatId)}/launch-job`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function attachChatToChain(
  chatId: string,
  taskLinkId: string,
): Promise<Chat> {
  return request(`/chats/${encodeURIComponent(chatId)}/attach-chain`, {
    method: "POST",
    body: JSON.stringify({ taskLinkId }),
  });
}

export function detachChatFromChain(chatId: string): Promise<Chat> {
  return request(`/chats/${encodeURIComponent(chatId)}/detach-chain`, {
    method: "POST",
  });
}

export function fetchRepoDetail(repoPath: string): Promise<RepoDetailResponse> {
  return request(`/settings/repos/${encodeURIComponent(repoPath)}`);
}

export function registerRepo(source: string, cloneTo?: string, mode?: "register" | "clone"): Promise<RegisterRepoResponse> {
  return request("/settings/repos", {
    method: "POST",
    body: JSON.stringify({ source, clone_to: cloneTo, mode }),
  });
}

export function createRepo(path: string, name?: string): Promise<{ path: string; name: string }> {
  return request("/settings/repos/create", {
    method: "POST",
    body: JSON.stringify({ path, name: name || undefined }),
  });
}

export function unregisterRepo(repoPath: string): Promise<void> {
  return request(`/settings/repos/${encodeURIComponent(repoPath)}`, {
    method: "DELETE",
  });
}

export function browseDirectories(path?: string): Promise<{
  current: string;
  parent: string | null;
  items: { name: string; path: string; isGitRepo: boolean }[];
}> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  return request(`/settings/browse${qs}`);
}

// --- Settings ---

export function fetchSettings(): Promise<Settings> {
  return request("/settings");
}

export function updateSettings(settings: Partial<Settings>): Promise<Settings> {
  return request("/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

// --- Artifacts ---

export function fetchArtifacts(jobId: string): Promise<ArtifactListResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/artifacts`);
}

export function downloadArtifactUrl(artifactId: string): string {
  return `${BASE}/artifacts/${encodeURIComponent(artifactId)}`;
}

export async function fetchArtifactContent(artifactId: string): Promise<unknown> {
  const url = downloadArtifactUrl(artifactId);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`artifact fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchArtifactText(artifactId: string): Promise<string> {
  const url = downloadArtifactUrl(artifactId);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`artifact fetch failed: ${res.status}`);
  return res.text();
}

// --- Workspace ---

export function fetchWorkspaceFiles(
  jobId: string,
  params?: { path?: string; cursor?: string; limit?: number },
): Promise<WorkspaceListResponse> {
  const qs = new URLSearchParams();
  if (params?.path) qs.set("path", params.path);
  if (params?.cursor) qs.set("cursor", params.cursor);
  if (params?.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return request(`/jobs/${encodeURIComponent(jobId)}/workspace${query ? `?${query}` : ""}`);
}

export function fetchWorkspaceFile(
  jobId: string,
  path: string,
): Promise<{ path: string; content: string }> {
  const qs = new URLSearchParams({ path });
  return request(`/jobs/${encodeURIComponent(jobId)}/workspace/file?${qs.toString()}`);
}

export function workspaceFileRawUrl(jobId: string, path: string): string {
  const qs = new URLSearchParams({ path });
  return `${BASE}/jobs/${encodeURIComponent(jobId)}/workspace/file/raw?${qs.toString()}`;
}

// --- Approvals ---

export function fetchApprovals(jobId: string): Promise<ApprovalRequest[]> {
  return request<{ items: ApprovalRequest[] }>(`/jobs/${encodeURIComponent(jobId)}/approvals`).then((r) => r.items);
}

export function resolveApproval(
  approvalId: string,
  resolution: "approved" | "rejected",
  notes?: string,
): Promise<ApprovalRequest> {
  const body: Record<string, string> = { resolution };
  if (notes) body.notes = notes;
  return request(`/approvals/${encodeURIComponent(approvalId)}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function trustJob(jobId: string): Promise<{ resolved: number }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/approvals/trust`, {
    method: "POST",
  });
}

// --- Action Policy Batches ---

export function resolveBatch(
  jobId: string,
  batchId: string,
  resolution: "approved" | "rejected" | "partial",
  approvedIds?: string[],
  trustGrantId?: string,
): Promise<{ resolved: boolean }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/batches/resolve`, {
    method: "POST",
    body: JSON.stringify({ batchId, resolution, approvedIds, trustGrantId }),
  });
}

// --- Policy Settings ---

export interface PolicyConfig {
  preset: string;
  batchWindowSeconds: number;
}

export interface UsdCeiling {
  warnUsd: number | null;
  ceilingUsd: number | null;
}

export interface PolicyState {
  config: PolicyConfig;
  usdCeilings: Record<string, UsdCeiling>;
  mcpServers: Array<{ name: string; command: string; contained: boolean; reversible: boolean; trusted: boolean; createdAt: string }>;
}

export function fetchPolicySettings(): Promise<PolicyState> {
  return request("/settings/policy");
}

export function updatePolicyPreset(preset: string): Promise<PolicyConfig> {
  return request("/settings/policy/preset", {
    method: "PUT",
    body: JSON.stringify({ preset }),
  });
}

export function updatePolicyConfig(config: Partial<PolicyConfig>): Promise<PolicyConfig> {
  return request("/settings/policy/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export function updateUsdCeilings(
  ceilings: Record<string, UsdCeiling>,
): Promise<{ ceilings: Record<string, UsdCeiling> }> {
  return request("/settings/policy/usd-ceilings", {
    method: "PUT",
    body: JSON.stringify({ ceilings }),
  });
}

// --- Credentials (Story 3.1, global Settings > Integrations) ---

export type CredentialProvider = "github" | "jira" | "azure_devops";

export interface Credential {
  id: string;
  provider: CredentialProvider;
  label: string;
  baseUrl: string;
  email: string | null;
  requiresEmailUpdate: boolean;
  createdAt: string;
}

export interface CreateCredentialRequest {
  provider: CredentialProvider;
  label: string;
  baseUrl: string;
  pat: string;
  email?: string | null;
}

export function fetchCredentials(): Promise<{ credentials: Credential[] }> {
  return request("/settings/credentials");
}

export function fetchCredentialGuidance(): Promise<{ guidance: Record<string, string> }> {
  return request("/settings/credentials/guidance");
}

export function createCredential(body: CreateCredentialRequest): Promise<Credential> {
  return request("/settings/credentials", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteCredential(credentialId: string): Promise<void> {
  return request(`/settings/credentials/${encodeURIComponent(credentialId)}`, {
    method: "DELETE",
  });
}

export function updateJiraCredentialEmail(credentialId: string, email: string): Promise<Credential> {
  return request(`/settings/credentials/${encodeURIComponent(credentialId)}/jira-email`, {
    method: "PATCH",
    body: JSON.stringify({ email }),
  });
}

// --- Project tracker state (Story 3.3) ---

export function fetchTrackerOperations(
  projectId: string,
  filters?: { job_ref?: string; tracker_link_ref?: string },
): Promise<TrackerOperationListResponse> {
  const params = new URLSearchParams();
  if (filters?.job_ref) params.set("job_ref", filters.job_ref);
  if (filters?.tracker_link_ref) params.set("tracker_link_ref", filters.tracker_link_ref);
  const query = params.toString();
  return request(`/projects/${encodeURIComponent(projectId)}/tracker-links/operations${query ? `?${query}` : ""}`);
}

export function fetchTrackerOperation(projectId: string, operationId: string): Promise<TrackerOperationResponse> {
  return request(`/projects/${encodeURIComponent(projectId)}/tracker-links/operations/${encodeURIComponent(operationId)}`);
}

export function reconcileTrackerOperation(
  projectId: string,
  operationId: string,
  body: ReconcileTrackerOperationRequest,
): Promise<TrackerOperationResponse> {
  return request(`/projects/${encodeURIComponent(projectId)}/tracker-links/operations/${encodeURIComponent(operationId)}/reconcile`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function retryTrackerOperation(
  projectId: string,
  operationId: string,
  body: RetryTrackerOperationRequest,
): Promise<TrackerOperationResponse> {
  return request(`/projects/${encodeURIComponent(projectId)}/tracker-links/operations/${encodeURIComponent(operationId)}/retry`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchTrackerLinks(projectId: string): Promise<TrackerLinkListResponse> {
  return request(`/projects/${encodeURIComponent(projectId)}/tracker-links`);
}

export function createTrackerLink(
  projectId: string,
  body: { credentialId: string; externalRef: string },
): Promise<TrackerLinkResponse> {
  return request(`/projects/${encodeURIComponent(projectId)}/tracker-links`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function detachTrackerLink(projectId: string, linkId: string): Promise<void> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/tracker-links/${encodeURIComponent(linkId)}`,
    { method: "DELETE" },
  );
}

export function refreshTrackerLink(projectId: string, linkId: string): Promise<TrackerSummaryResponse> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/tracker-links/${encodeURIComponent(linkId)}/refresh`,
    { method: "POST" },
  );
}

// --- Operator Messages ---

export function sendOperatorMessage(
  jobId: string,
  content: string,
): Promise<{ seq: number; timestamp: string }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function pauseJob(jobId: string): Promise<void> {
  return request(`/jobs/${encodeURIComponent(jobId)}/pause`, {
    method: "POST",
  });
}

export function continueJob(
  jobId: string,
  instruction: string,
): Promise<{ id: string; state: string; branch: string | null; worktreePath: string | null; createdAt: string }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/continue`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

export function resumeJob(
  jobId: string,
  instruction?: string,
): Promise<{ id: string; state: string; branch: string | null; worktreePath: string | null; createdAt: string; updatedAt: string }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: "POST",
    body: JSON.stringify(instruction?.trim() ? { instruction } : {}),
  });
}

// --- Job Resolution ---

export function resolveJob(
  jobId: string,
  action: "merge" | "smart_merge" | "create_pr" | "discard" | "agent_merge",
): Promise<{ resolution: string; prUrl?: string | null; conflictFiles?: string[] | null; error?: string | null }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
}

export function archiveJob(jobId: string): Promise<void> {
  return request(`/jobs/${encodeURIComponent(jobId)}/archive`, {
    method: "POST",
  });
}

// --- Voice ---

export async function transcribeAudio(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, "recording.webm");
  const res = await fetch(`${BASE}/voice/transcribe`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body != null && typeof body.detail === "string"
      ? body.detail
      : res.statusText || `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  const data = (await res.json()) as { text: string };
  return data.text;
}

export async function createTerminalSession(opts: {
  cwd?: string | null;
  jobId?: string | null;
  promptLabel?: string | null;
}): Promise<{ id: string; cwd: string; jobId?: string | null }> {
  return request<{ id: string; cwd: string; jobId?: string | null }>("/terminal/sessions", {
    method: "POST",
    body: JSON.stringify({
      cwd: opts.cwd ?? null,
      jobId: opts.jobId ?? null,
      promptLabel: opts.promptLabel ?? null,
    }),
  });
}

export async function deleteTerminalSession(id: string): Promise<void> {
  await request<unknown>(`/terminal/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Push notifications
// ---------------------------------------------------------------------------

export function fetchVapidKey(): Promise<{ publicKey: string }> {
  return request("/notifications/vapid-key");
}

export function subscribePush(subscription: PushSubscriptionJSON): Promise<void> {
  return request("/notifications/subscribe", {
    method: "POST",
    body: JSON.stringify(subscription),
  });
}

export function unsubscribePush(endpoint: string): Promise<void> {
  return request("/notifications/unsubscribe", {
    method: "POST",
    body: JSON.stringify({ endpoint }),
  });
}

// ---------------------------------------------------------------------------
// Job sharing
// ---------------------------------------------------------------------------

export function createShareLink(jobId: string): Promise<{ token: string; jobId: string; url: string }> {
  return request(`/jobs/${jobId}/share`, { method: "POST" });
}

export function fetchSharedSnapshot(token: string): Promise<{
  job: import("../store").JobSummary;
  logs: import("../store").LogLine[];
  transcript: import("../store").TranscriptEntry[];
  diff: DiffFileModel[];
  approvals: import("../store").ApprovalRequest[];
  timeline: import("../store").TimelineEntry[];
  turnSummaries?: Array<Record<string, unknown>>;
}> {
  return request(`/share/${encodeURIComponent(token)}/snapshot`);
}

export function fetchSharedTelemetry(token: string): Promise<Record<string, unknown>> {
  return request(`/share/${encodeURIComponent(token)}/telemetry`);
}

// ---------------------------------------------------------------------------
// Job Story
// ---------------------------------------------------------------------------

export function fetchJobStory(
  jobId: string,
  regenerate = false,
): Promise<import("./types").StoryResponse> {
  const params = new URLSearchParams();
  if (regenerate) params.set("regenerate", "true");
  const qs = params.toString() ? `?${params.toString()}` : "";
  return request(`/jobs/${encodeURIComponent(jobId)}/story${qs}`);
}

// Diff motivations
// ---------------------------------------------------------------------------

export function fetchJobMotivations(jobId: string): Promise<import("./types").JobMotivationsResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/motivations`);
}

export { ApiError };
export * from "./client-analytics";

// Repository summary
// ---------------------------------------------------------------------------
export interface RepoJobSummary {
  id: string;
  title: string | null;
  state: string;
  createdAt: string;
  completedAt: string | null;
  totalCostUsd: number | null;
  model: string | null;
}

export interface RepoCostSummary {
  totalCostUsd: CurrencyValue;
  totalJobs: number;
  totalTokens: number;
}


export interface RepoSummaryResponse {
  path: string;
  originUrl: string | null;
  baseBranch: string | null;
  currentBranch: string | null;
  platform: string | null;
  recentJobs: RepoJobSummary[];
  activeJobCount: number;
  cost: RepoCostSummary;
}

export function fetchRepoSummary(repoPath: string): Promise<RepoSummaryResponse> {
  return request(`/settings/repos/${encodeURIComponent(repoPath)}/summary`);
}

// --- Sidecar Templates ---

export function fetchSidecarTemplates(): Promise<SidecarTemplateListResponse> {
  return request("/sidecar-templates");
}

export function fetchSidecarTemplate(id: string): Promise<SidecarTemplate> {
  return request(`/sidecar-templates/${encodeURIComponent(id)}`);
}

export function createSidecarTemplate(req: CreateSidecarTemplateRequest): Promise<SidecarTemplate> {
  return request("/sidecar-templates", { method: "POST", body: JSON.stringify(req) });
}

export function updateSidecarTemplate(id: string, req: UpdateSidecarTemplateRequest): Promise<SidecarTemplate> {
  return request(`/sidecar-templates/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(req) });
}

export function deleteSidecarTemplate(id: string): Promise<void> {
  return request(`/sidecar-templates/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function generateSidecarDefinition(description: string): Promise<GenerateSidecarResponse> {
  return request("/sidecar-templates/generate", { method: "POST", body: JSON.stringify({ description }) });
}
