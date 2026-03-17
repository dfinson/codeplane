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
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, (body as { detail?: string }).detail ?? res.statusText);
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

export function fetchJobDiff(jobId: string): Promise<DiffFileModel[]> {
  return request(`/jobs/${encodeURIComponent(jobId)}/diff`);
}

export function createJob(body: CreateJobRequest): Promise<CreateJobResponse> {
  return request("/jobs", {
    method: "POST",
    body: JSON.stringify(body),
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

export function fetchModels(): Promise<{ id?: string; name?: string; [key: string]: unknown }[]> {
  return request("/models");
}

export function fetchSDKs(): Promise<SDKListResponse> {
  return request("/sdks");
}

export function fetchJobTelemetry(jobId: string): Promise<{
  available: boolean;
  jobId: string;
  model?: string;
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
  llmCalls?: { model: string; inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; cost: number; durationMs: number }[];
  approvalCount?: number;
  totalApprovalWaitMs?: number;
  agentMessages?: number;
  operatorMessages?: number;
}> {
  return request(`/jobs/${encodeURIComponent(jobId)}/telemetry`);
}

// --- Repos ---

export function fetchRepos(): Promise<RepoListResponse> {
  return request("/settings/repos");
}

export function fetchRepoDetail(repoPath: string): Promise<{
  path: string;
  originUrl: string | null;
  baseBranch: string | null;
  activeJobCount: number;
}> {
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
  items: { name: string; path: string; isGitRepo: string }[];
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

export function cleanupWorktrees(): Promise<{ removed: number }> {
  return request("/settings/cleanup-worktrees", { method: "POST" });
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
  instruction: string,
): Promise<{ id: string; state: string; branch: string | null; worktreePath: string | null; createdAt: string; updatedAt: string }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

// --- Job Resolution ---

export function resolveJob(
  jobId: string,
  action: "merge" | "smart_merge" | "create_pr" | "discard",
): Promise<{ resolution: string; prUrl?: string | null; conflictFiles?: string[] | null }> {
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
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, (body as { detail?: string }).detail ?? res.statusText);
  }
  const data = (await res.json()) as { text: string };
  return data.text;
}

export { ApiError };
