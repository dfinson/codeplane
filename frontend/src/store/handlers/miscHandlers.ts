/**
 * Miscellaneous SSE event handlers: snapshot, heartbeat, diff, session, telemetry.
 */

import type { JobSummary, ApprovalRequest, TranscriptEntry, ConnectionStatus, ContextHandoff } from "../types";
import type { DiffFileModel, TrackerOperationResponse } from "../../api/types";
import type { SSEHandler, AppState } from "./types";
import { enrichJob } from "./jobHandlers";

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

const trackerCorrelations = [
  ["projectId", "projectRef"],
  ["jobId", "jobRef"],
  ["taskLinkId", "taskLinkRef"],
  ["trackerLinkId", "trackerLinkRef"],
  ["approvalId", "approvalRef"],
] as const;

/** FK deletion can null a live correlation without advancing operation version. */
export function mergeTrackerOperations(
  current: AppState["trackerOperations"],
  incoming: TrackerOperationResponse[],
): AppState["trackerOperations"] {
  let next = current;
  for (const operation of incoming) {
    if (!operation || typeof operation.id !== "string" ||
        typeof operation.projectRef !== "string" || typeof operation.version !== "number" ||
        !Number.isSafeInteger(operation.version) || operation.version < 0) continue;
    const previous = next[operation.id];
    if (previous && (previous.projectRef !== operation.projectRef ||
        (typeof previous.version === "number" && previous.version > operation.version))) continue;
    let merged = previous && previous.version === operation.version ? previous : operation;
    if (previous) {
      for (const [liveKey, refKey] of trackerCorrelations) {
        const reference = previous[refKey];
        // A null approvalId without an approvalRef is not a deletion: the
        // canonical approval can still be provisioned by a newer version.
        if (reference == null || reference !== operation[refKey]) continue;
        if ((previous[liveKey] === null || operation[liveKey] === null) && merged[liveKey] !== null) {
          merged = { ...merged, [liveKey]: null };
        }
      }
    }
    if (merged === previous) continue;
    if (next === current) next = { ...current };
    next[operation.id] = merged;
  }
  return next;
}

export function handleTrackerOperationUpdated(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const operation = payload.operation as TrackerOperationResponse | undefined;
  if (!operation) return null;
  const trackerOperations = mergeTrackerOperations(state.trackerOperations, [operation]);
  return trackerOperations === state.trackerOperations ? null : { trackerOperations };
}

export function handleSnapshot(_state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobs = (payload.jobs as JobSummary[]) ?? [];
  const rawApprovals =
    (payload.pendingApprovals as ApprovalRequest[]) ?? [];
  const jobMap = Object.fromEntries(jobs.map((j) => [j.id, enrichJob(j)]));
  // Drop approvals whose job is no longer in waiting_for_approval.
  // This covers the server-restart recovery path where the backend resets
  // the job to running without resolving its pending approval in the DB,
  // and the SSE gap is large enough that only a snapshot is sent (no
  // job_state_changed replay event to trigger the in-flight eviction).
  const approvals = rawApprovals.filter(
    (a) => jobMap[a.jobId]?.state === "waiting_for_approval",
  );
  // Merge snapshot jobs into existing state rather than replacing.
  // A job-scoped SSE connection sends snapshots containing only a single job;
  // replacing state.jobs wholesale would wipe out all other jobs from the store.
  const mergedJobs = { ..._state.jobs, ...jobMap };
  return {
    jobs: mergedJobs,
    approvals: { ..._state.approvals, ...Object.fromEntries(approvals.map((a) => [a.id, a])) },
  };
}

export function handleSessionHeartbeat(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string | undefined;
  const lastActivityAt = payload.lastActivityAt as string | undefined;
  const activeToolName = payload.activeToolName as string | undefined;
  const activeToolSince = payload.activeToolSince as string | undefined;

  const updates: Partial<AppState> = {};

  if (state.connectionStatus !== "connected") {
    updates.connectionStatus = "connected" as ConnectionStatus;
  }

  if (jobId && lastActivityAt) {
    const entry: { lastActivityAt: string; activeToolName?: string; activeToolSince?: string } = { lastActivityAt };
    if (activeToolName) entry.activeToolName = activeToolName;
    if (activeToolSince) entry.activeToolSince = activeToolSince;
    updates.jobHeartbeats = { ...state.jobHeartbeats, [jobId]: entry };
  }

  return Object.keys(updates).length > 0 ? updates : null;
}

export function handleDiffUpdate(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const changedFiles = (payload.changedFiles as DiffFileModel[]) ?? [];
  return {
    diffs: { ...state.diffs, [jobId]: changedFiles },
  };
}

export function handleSessionResumed(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const timestamp = payload.timestamp as string;
  const divider: TranscriptEntry = {
    jobId,
    eventId: payload.eventId as string | undefined,
    sequence: payload.sequence as number | undefined,
    timestamp,
    kind: "divider",
    content: "Session",
  };
  const existing = state.transcript[jobId] ?? [];
  // Deduplicate: two SSE connections may deliver the same event
  const resetFields = {
    state: "running",
    resolution: null,
    conflictFiles: null,
    failureReason: null,
    progressHeadline: null,
    progressSummary: null,
    archivedAt: null,
    modelDowngraded: false,
    requestedModel: null,
    actualModel: null,
    prUrl: null,
    mergeStatus: null,
    completedAt: null,
  };
  if (existing.some((e) => e.kind === "divider" && e.timestamp === divider.timestamp)) {
    return { jobs: state.jobs[jobId] ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], ...resetFields } } : state.jobs };
  }
  return {
    transcript: { ...state.transcript, [jobId]: [...existing, divider] },
    jobs: state.jobs[jobId]
      ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], ...resetFields } }
      : state.jobs,
  };
}

export function handleTelemetryUpdated(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  // Increment the per-job version counter so MetricsPanel re-fetches.
  const jobId = payload.jobId as string;
  const prev = state.telemetryVersions[jobId] ?? 0;

  // Also patch the job summary with live cost/token totals when present.
  const existingJob = state.jobs[jobId];
  let jobs = state.jobs;
  if (existingJob && (payload.totalCostUsd !== undefined || payload.totalTokens !== undefined)) {
    jobs = {
      ...state.jobs,
      [jobId]: {
        ...existingJob,
        ...(payload.totalCostUsd !== undefined ? { totalCostUsd: payload.totalCostUsd as number } : {}),
        ...(payload.totalTokens !== undefined ? { totalTokens: payload.totalTokens as number } : {}),
        ...(payload.inputTokens !== undefined ? { inputTokens: payload.inputTokens as number } : {}),
        ...(payload.outputTokens !== undefined ? { outputTokens: payload.outputTokens as number } : {}),
      },
    };
  }

  return {
    jobs,
    telemetryVersions: { ...state.telemetryVersions, [jobId]: prev + 1 },
  };
}

export function handleArtifactsUpdated(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string | undefined;
  if (!jobId) return null;
  return {
    artifactVersions: {
      ...state.artifactVersions,
      [jobId]: (state.artifactVersions[jobId] ?? 0) + 1,
    },
  };
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function handlePolicySettingsChanged(_state: AppState): Partial<AppState> | null {
  return {
    policySettingsVersion: Date.now(),
  };
}

export function handleRepoIndexProgress(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const repo = payload.repo as string;
  return {
    repoIndexState: {
      ...state.repoIndexState,
      [repo]: {
        repo,
        indexed: payload.indexed as number,
        total: payload.total as number,
        phase: payload.phase as string,
      },
    },
  };
}

export function handleRepoIndexComplete(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  // Remove from active indexing state — the repo is now ready.
  const repo = payload.repo as string;
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { [repo]: _done, ...rest } = state.repoIndexState;
  return { repoIndexState: rest };
}

export function handleContextHandoff(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string | undefined;
  if (!jobId) return null;
  const handoff: ContextHandoff = {
    jobId,
    source: (payload.source as ContextHandoff["source"]) || "preflight",
    sourceSessionId: (payload.sourceSessionId as string | null) ?? null,
    summary: (payload.summary as string) || "",
    content: (payload.content as string | null) ?? null,
    timestamp: (payload.timestamp as string) || new Date().toISOString(),
  };
  const existing = state.contextHandoffs[jobId] ?? [];
  return {
    contextHandoffs: {
      ...state.contextHandoffs,
      [jobId]: [...existing, handoff],
    },
  };
}

export const miscHandlers: Record<string, SSEHandler> = {
  "tracker.operation_updated": handleTrackerOperationUpdated,
  snapshot: handleSnapshot,
  "session.heartbeat": handleSessionHeartbeat,
  "diff.updated": handleDiffUpdate,
  "session.resumed": handleSessionResumed,
  "telemetry.updated": handleTelemetryUpdated,
  "artifacts.updated": handleArtifactsUpdated,
  "policy.settings_changed": handlePolicySettingsChanged,
  "repo.index_progress": handleRepoIndexProgress,
  "repo.index_complete": handleRepoIndexComplete,
  "context.handoff": handleContextHandoff,
  "structural.warning": (state, payload) => {
    const jobId = payload.jobId as string | undefined;
    if (!jobId) return {};
    const warning = {
      warningType: (payload.warningType as string) || "",
      detail: (payload.detail as string) || "",
      repo: (payload.repo as string) || "",
      timestamp: Date.now(),
    };
    const existing = state.structuralWarnings[jobId] ?? [];
    return {
      structuralWarnings: {
        ...state.structuralWarnings,
        [jobId]: [...existing, warning],
      },
    };
  },
};
