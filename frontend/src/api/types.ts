/**
 * Friendly type aliases re-exported from the generated OpenAPI schema.
 *
 * All component code imports from this file, never from schema.d.ts directly.
 */

import type { components } from "./schema";

export type Job = components["schemas"]["JobResponse"];
export type JobState = Job["state"];
export type CreateJobRequest = components["schemas"]["CreateJobRequest"];
export type CreateJobResponse = components["schemas"]["CreateJobResponse"];
export type JobListResponse = components["schemas"]["JobListResponse"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type RegisterRepoRequest = components["schemas"]["RegisterRepoRequest"];
export type RegisterRepoResponse = components["schemas"]["RegisterRepoResponse"];
export type RepoListResponse = components["schemas"]["RepoListResponse"];
export type PermissionMode = components["schemas"]["PermissionMode"];
export type CompletionStrategy = "auto_merge" | "pr_only" | "manual";

export interface Settings {
  maxConcurrentJobs: number;
  permissionMode: string;
  autoPush: boolean;
  cleanupWorktree: boolean;
  deleteBranchAfterMerge: boolean;
  artifactRetentionDays: number;
  maxArtifactSizeMb: number;
  autoArchiveDays: number;
  verify: boolean;
  selfReview: boolean;
  maxTurns: number;
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

export interface TranscriptEntry {
  jobId: string;
  seq: number;
  timestamp: string;
  role: "agent" | "operator";
  content: string;
}

export interface ApprovalRequest {
  id: string;
  jobId: string;
  description: string;
  proposedAction: string | null;
  requestedAt: string;
  resolvedAt: string | null;
  resolution: string | null;
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
}

export interface DiffUpdatePayload {
  jobId: string;
  changedFiles: DiffFileModel[];
}

// --- Resolve types ---

export interface ResolveJobResponse {
  resolution: string;
  prUrl?: string | null;
  conflictFiles?: string[] | null;
}

// --- Artifact types ---

export type ArtifactType = "diff_snapshot" | "agent_summary" | "session_snapshot" | "session_log" | "custom";

export interface ArtifactResponse {
  id: string;
  jobId: string;
  name: string;
  type: ArtifactType;
  mimeType: string;
  sizeBytes: number;
  phase: string;
  createdAt: string;
}

export interface ArtifactListResponse {
  items: ArtifactResponse[];
}

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
