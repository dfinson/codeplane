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
  GlobalConfigResponse,
  HealthResponse,
  Job,
  JobListResponse,
  RepoListResponse,
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
}): Promise<JobListResponse> {
  const qs = new URLSearchParams();
  if (params?.state) qs.set("state", params.state);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.cursor) qs.set("cursor", params.cursor);
  const query = qs.toString();
  return request(`/jobs${query ? `?${query}` : ""}`);
}

export function fetchJob(jobId: string): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(jobId)}`);
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

// --- Repos ---

export function fetchRepos(): Promise<RepoListResponse> {
  return request("/settings/repos");
}

export function registerRepo(source: string): Promise<{ path: string; source: string; cloned: boolean }> {
  return request("/settings/repos", {
    method: "POST",
    body: JSON.stringify({ source }),
  });
}

export function unregisterRepo(repoPath: string): Promise<void> {
  return request(`/settings/repos/${encodeURIComponent(repoPath)}`, {
    method: "DELETE",
  });
}

// --- Settings ---

export function fetchGlobalConfig(): Promise<GlobalConfigResponse> {
  return request("/settings/global");
}

export function updateGlobalConfig(configYaml: string): Promise<GlobalConfigResponse> {
  return request("/settings/global", {
    method: "PUT",
    body: JSON.stringify({ config_yaml: configYaml }),
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
