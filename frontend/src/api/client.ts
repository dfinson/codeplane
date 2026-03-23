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
  ApprovalRequest,
  DiffFileModel,
  HealthResponse,
  Job,
  JobListResponse,
  RepoDetailResponse,
  RepoListResponse,
  SDKListResponse,
  Settings,
  WorkspaceListResponse,
} from "./types";

const BASE = "/api";

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (init?.body) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...headers,
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    let detail: string;
    if (body == null) {
      detail = res.statusText || `HTTP ${res.status}`;
    } else if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body.detail)) {
      // FastAPI 422 validation errors: [{loc, msg, type}, ...]
      detail = body.detail
        .map((e: { loc?: string[]; msg?: string }) =>
          [e.loc?.slice(1).join("."), e.msg].filter(Boolean).join(": "),
        )
        .join("; ");
    } else {
      detail = res.statusText || `HTTP ${res.status}`;
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// --- Health ---

export function fetchHealth(): Promise<HealthResponse> {
  return request("/health");
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
  return request(`/jobs/${encodeURIComponent(jobId)}/logs?level=${encodeURIComponent(level)}&limit=${limit}`);
}

export function fetchJobTranscript(jobId: string, limit = 2000): Promise<import("../store").TranscriptEntry[]> {
  return request(`/jobs/${encodeURIComponent(jobId)}/transcript?limit=${limit}`);
}

export function fetchJobTimeline(jobId: string, limit = 200): Promise<import("../store").TimelineEntry[]> {
  return request<Array<{ headline: string; headlinePast: string; summary?: string; timestamp: string }>>(
    `/jobs/${encodeURIComponent(jobId)}/timeline?limit=${limit}`,
  ).then((entries) =>
    entries.map((e) => ({
      headline: e.headline,
      headlinePast: e.headlinePast,
      summary: e.summary ?? "",
      timestamp: e.timestamp,
      active: false, // historical entries are never active; live SSE manages active state
    })),
  );
}

export function fetchJobDiff(jobId: string): Promise<DiffFileModel[]> {
  return request(`/jobs/${encodeURIComponent(jobId)}/diff`);
}

/** Full state hydration for a single job — used after reconnect or page refresh. */
export function fetchJobSnapshot(jobId: string): Promise<{
  job: import("../store").JobSummary;
  logs: import("../store").LogLine[];
  transcript: import("../store").TranscriptEntry[];
  diff: DiffFileModel[];
  approvals: import("../store").ApprovalRequest[];
  timeline: import("../store").TimelineEntry[];
}> {
  return request(`/jobs/${encodeURIComponent(jobId)}/snapshot`);
}

export function createJob(body: CreateJobRequest): Promise<CreateJobResponse> {
  return request("/jobs", {
    method: "POST",
    body: JSON.stringify(body),
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

export function rerunJob(jobId: string): Promise<CreateJobResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/rerun`, {
    method: "POST",
  });
}

export function fetchModels(sdk?: string): Promise<{ id?: string; name?: string; [key: string]: unknown }[]> {
  const qs = sdk ? `?sdk=${encodeURIComponent(sdk)}` : "";
  return request(`/models${qs}`);
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
  totalCost?: number;
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
}> {
  return request(`/jobs/${encodeURIComponent(jobId)}/telemetry`);
}

// --- Analytics ---

export interface AnalyticsOverview {
  period: number;
  totalJobs: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  running: number;
  totalCostUsd: number;
  totalTokens: number;
  avgDurationMs: number;
  totalPremiumRequests: number;
  totalToolCalls: number;
  totalToolFailures: number;
  toolSuccessRate: number;
  cacheHitRate: number;
  costTrend: { date: string; cost: number; jobs: number }[];
}

export interface AnalyticsModels {
  period: number;
  models: {
    model: string;
    sdk: string;
    job_count: number;
    total_cost_usd: number;
    total_tokens: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    avg_duration_ms: number;
    premium_requests: number;
  }[];
}

export interface AnalyticsTools {
  period: number;
  tools: {
    name: string;
    count: number;
    avg_duration_ms: number;
    total_duration_ms: number;
    failure_count: number;
  }[];
}

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
    total_cost_usd: number;
    tool_call_count: number;
    llm_call_count: number;
    premium_requests: number;
  }[];
}

export interface AnalyticsRepos {
  period: number;
  repos: {
    repo: string;
    job_count: number;
    succeeded: number;
    failed: number;
    total_cost_usd: number;
    total_tokens: number;
    tool_calls: number;
    avg_duration_ms: number;
    premium_requests: number;
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

export function fetchAnalyticsJobs(params?: {
  period?: number;
  sdk?: string;
  model?: string;
  status?: string;
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
  if (params?.sort) sp.set("sort", params.sort);
  if (params?.desc !== undefined) sp.set("desc", String(params.desc));
  if (params?.limit) sp.set("limit", String(params.limit));
  if (params?.offset) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return request(`/analytics/jobs${qs ? `?${qs}` : ""}`);
}

// --- Repos ---

export function fetchRepos(): Promise<RepoListResponse> {
  return request("/settings/repos");
}

export function fetchRepoDetail(repoPath: string): Promise<RepoDetailResponse> {
  return request(`/settings/repos/${encodeURIComponent(repoPath)}`);
}

export function registerRepo(source: string, cloneTo?: string): Promise<{ path: string; source: string; cloned: boolean }> {
  return request("/settings/repos", {
    method: "POST",
    body: JSON.stringify({ source, clone_to: cloneTo }),
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

// --- Approvals ---

export function fetchApprovals(jobId: string): Promise<ApprovalRequest[]> {
  return request(`/jobs/${encodeURIComponent(jobId)}/approvals`);
}

export function resolveApproval(
  approvalId: string,
  resolution: "approved" | "rejected",
): Promise<ApprovalRequest> {
  return request(`/approvals/${encodeURIComponent(approvalId)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution }),
  });
}

export function trustJob(jobId: string): Promise<{ resolved: number }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/approvals/trust`, {
    method: "POST",
  });
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

export function unarchiveJob(jobId: string): Promise<void> {
  return request(`/jobs/${encodeURIComponent(jobId)}/unarchive`, {
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

export async function createTerminalSession(
  cwd: string,
  jobId: string,
): Promise<{ id: string }> {
  return request<{ id: string }>("/terminal/sessions", {
    method: "POST",
    body: JSON.stringify({ cwd, jobId }),
  });
}

export { ApiError };
