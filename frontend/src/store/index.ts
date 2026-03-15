/**
 * Zustand store — single source of truth for application state.
 *
 * SSE events are processed through a central event dispatcher that
 * updates the store. Components read from the store via selectors.
 */

import { create } from "zustand";

// ---------------------------------------------------------------------------
// Types — inline until schema generation (npm run generate:api) is wired up.
// These mirror the CamelModel shapes from the backend and MUST be replaced
// by imports from ../api/types once that module is populated.
// See: frontend/src/api/types.ts for the planned generated aliases.
// ---------------------------------------------------------------------------

import type { DiffFileModel } from "../api/types";

/** Connection status exposed to UI components. */
export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";

/** Minimal job shape matching JobResponse from the backend. */
export interface JobSummary {
  id: string;
  repo: string;
  prompt: string;
  title?: string | null;
  state: string;
  strategy: string;
  baseRef: string;
  worktreePath: string | null;
  branch: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  prUrl?: string | null;
  resolution?: string | null;
  archivedAt?: string | null;
  completionStrategy?: string | null;
  mergeStatus?: string | null;
  conflictFiles?: string[] | null;
  failureReason?: string | null;
  progressHeadline?: string | null;
  model?: string | null;
  modelDowngraded?: boolean;
  requestedModel?: string | null;
  actualModel?: string | null;
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

export interface LogLine {
  jobId: string;
  seq: number;
  timestamp: string;
  level: string;
  message: string;
  context: Record<string, unknown> | null;
}

export interface TranscriptEntry {
  jobId: string;
  seq: number;
  timestamp: string;
  role: string;
  content: string;
  // Rich fields — only present for specific roles
  title?: string;        // agent messages: optional annotation title
  turnId?: string;       // groups reasoning + tool_calls + message into one turn
  toolName?: string;     // tool_call: identifier
  toolArgs?: string;     // tool_call: JSON-serialised arguments
  toolResult?: string;   // tool_call: text output
  toolSuccess?: boolean; // tool_call: success flag
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MODEL_DOWNGRADE_RE = /^Model downgraded: requested (.+) but received (.+)$/;

/** Enrich a job loaded from the REST API with parsed model downgrade info. */
export function enrichJob(job: JobSummary): JobSummary {
  if (job.modelDowngraded) return job; // already enriched (e.g. from SSE)
  if (!job.failureReason) return job;
  const m = MODEL_DOWNGRADE_RE.exec(job.failureReason);
  if (!m) return job;
  return { ...job, modelDowngraded: true, requestedModel: m[1], actualModel: m[2] };
}

// ---------------------------------------------------------------------------
// Store shape
// ---------------------------------------------------------------------------

interface TowerState {
  // Data slices
  jobs: Record<string, JobSummary>;
  approvals: Record<string, ApprovalRequest>;
  logs: Record<string, LogLine[]>; // keyed by jobId
  transcript: Record<string, TranscriptEntry[]>; // keyed by jobId
  diffs: Record<string, DiffFileModel[]>; // keyed by jobId

  // UI state
  connectionStatus: ConnectionStatus;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  dispatchSSEEvent: (eventType: string, data: unknown) => void;
  applySnapshot: (jobs: JobSummary[], approvals: ApprovalRequest[]) => void;
}

export const useTowerStore = create<TowerState>((set, get) => ({
  jobs: {},
  approvals: {},
  logs: {},
  transcript: {},
  diffs: {},
  connectionStatus: "reconnecting",

  setConnectionStatus: (status) => set({ connectionStatus: status }),

  applySnapshot: (jobs, approvals) =>
    set({
      jobs: Object.fromEntries(jobs.map((j) => [j.id, enrichJob(j)])),
      approvals: Object.fromEntries(approvals.map((a) => [a.id, a])),
    }),

  dispatchSSEEvent: (eventType, data) => {
    // Process the event and only call set() if we have an actual state change.
    // Zustand's set() always creates a new state reference even when returning
    // the same state object, which causes unnecessary re-renders.
    const state = get();
    const payload = data as Record<string, unknown>;
    const update = (() => {
      switch (eventType) {
        case "job_state_changed": {
          const jobId = payload.jobId as string;
          const newState = payload.newState as string;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: newState,
                  updatedAt: (payload.timestamp as string) ?? existing.updatedAt,
                },
              },
            };
          }
          return null;
        }

        case "log_line": {
          const jobId = payload.jobId as string;
          const entry: LogLine = {
            jobId,
            seq: payload.seq as number,
            timestamp: payload.timestamp as string,
            level: payload.level as string,
            message: payload.message as string,
            context: (payload.context as Record<string, unknown> | null) ?? null,
          };
          const existing = state.logs[jobId] ?? [];
          const updated = [...existing, entry];
          return {
            logs: { ...state.logs, [jobId]: updated.length > 10_000 ? updated.slice(-10_000) : updated },
          };
        }

        case "transcript_update": {
          const jobId = payload.jobId as string;
          const entry: TranscriptEntry = {
            jobId,
            seq: payload.seq as number,
            timestamp: payload.timestamp as string,
            role: payload.role as string,
            content: payload.content as string,
            title: payload.title as string | undefined,
            turnId: payload.turnId as string | undefined,
            toolName: payload.toolName as string | undefined,
            toolArgs: payload.toolArgs as string | undefined,
            toolResult: payload.toolResult as string | undefined,
            toolSuccess: payload.toolSuccess as boolean | undefined,
          };
          const existing = state.transcript[jobId] ?? [];
          // Deduplicate: two SSE connections (global + job-scoped) may deliver
          // the same event; skip if identical role+content+timestamp already present.
          if (existing.some((e) => e.timestamp === entry.timestamp && e.role === entry.role && e.content === entry.content)) {
            return null;
          }
          const updated = [...existing, entry];
          return {
            transcript: { ...state.transcript, [jobId]: updated.length > 10_000 ? updated.slice(-10_000) : updated },
          };
        }

        case "approval_requested": {
          const approval: ApprovalRequest = {
            id: payload.approvalId as string,
            jobId: payload.jobId as string,
            description: payload.description as string,
            proposedAction: (payload.proposedAction as string | null) ?? null,
            requestedAt: (payload.timestamp as string) ?? new Date().toISOString(),
            resolvedAt: null,
            resolution: null,
          };
          return {
            approvals: { ...state.approvals, [approval.id]: approval },
          };
        }

        case "approval_resolved": {
          const approvalId = payload.approvalId as string;
          const existing = state.approvals[approvalId];
          if (existing) {
            return {
              approvals: {
                ...state.approvals,
                [approvalId]: {
                  ...existing,
                  resolution: payload.resolution as string,
                  resolvedAt: payload.timestamp as string,
                },
              },
            };
          }
          return null;
        }

        case "snapshot": {
          const jobs = (payload.jobs as JobSummary[]) ?? [];
          const approvals =
            (payload.pendingApprovals as ApprovalRequest[]) ?? [];
          return {
            jobs: Object.fromEntries(jobs.map((j) => [j.id, enrichJob(j)])),
            approvals: Object.fromEntries(approvals.map((a) => [a.id, a])),
          };
        }

        case "session_heartbeat": {
          if (state.connectionStatus !== "connected") {
            return { connectionStatus: "connected" as ConnectionStatus };
          }
          return null;
        }

        case "job_succeeded": {
          const jobId = payload.jobId as string;
          const prUrl = (payload.prUrl as string | null) ?? null;
          const resolution = (payload.resolution as string | null) ?? null;
          const mergeStatus = (payload.mergeStatus as string | null) ?? null;
          const modelDowngraded = (payload.modelDowngraded as boolean) ?? false;
          const requestedModel = (payload.requestedModel as string | null) ?? null;
          const actualModel = (payload.actualModel as string | null) ?? null;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: "succeeded",
                  ...(prUrl && { prUrl }),
                  ...(resolution && { resolution }),
                  ...(mergeStatus && { mergeStatus }),
                  failureReason: null,
                  progressHeadline: null,
                  ...(modelDowngraded && { modelDowngraded, requestedModel, actualModel }),
                },
              },
            };
          }
          return null;
        }

        case "job_failed": {
          const jobId = payload.jobId as string;
          const reason = (payload.reason as string | null) ?? "Unknown error";
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: "failed",
                  failureReason: reason,
                  progressHeadline: null,
                },
              },
            };
          }
          return null;
        }

        case "job_resolved": {
          const jobId = payload.jobId as string;
          const resolution = payload.resolution as string;
          const prUrl = (payload.prUrl as string | null) ?? null;
          const conflictFiles = (payload.conflictFiles as string[] | null) ?? null;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  resolution,
                  prUrl: prUrl ?? existing.prUrl,
                  conflictFiles,
                  updatedAt: (payload.timestamp as string) ?? existing.updatedAt,
                },
              },
            };
          }
          return null;
        }

        case "job_archived": {
          const jobId = payload.jobId as string;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  archivedAt: new Date().toISOString(),
                },
              },
            };
          }
          return null;
        }

        case "diff_update": {
          const jobId = payload.jobId as string;
          const changedFiles = (payload.changedFiles as DiffFileModel[]) ?? [];
          return {
            diffs: { ...state.diffs, [jobId]: changedFiles },
          };
        }

        case "session_resumed": {
          const jobId = payload.jobId as string;
          const sessionNumber = payload.sessionNumber as number;
          const timestamp = payload.timestamp as string;
          const divider: TranscriptEntry = {
            jobId,
            seq: -99,
            timestamp,
            role: "divider",
            content: `Session ${sessionNumber}`,
          };
          const existing = state.transcript[jobId] ?? [];
          // Deduplicate: two SSE connections may deliver the same event
          if (existing.some((e) => e.role === "divider" && e.content === divider.content && e.timestamp === divider.timestamp)) {
            return { jobs: state.jobs[jobId] ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], state: "running" } } : state.jobs };
          }
          return {
            transcript: { ...state.transcript, [jobId]: [...existing, divider] },
            // Also update job state back to running
            jobs: state.jobs[jobId]
              ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], state: "running" } }
              : state.jobs,
          };
        }

        case "job_title_updated": {
          const jobId = payload.jobId as string;
          const title = (payload.title as string | null) ?? null;
          const branch = (payload.branch as string | null) ?? null;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  ...(title && { title }),
                  ...(branch && { branch }),
                },
              },
            };
          }
          return null;
        }

        case "progress_headline": {
          const jobId = payload.jobId as string;
          const headline = payload.headline as string;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  progressHeadline: headline,
                },
              },
            };
          }
          return null;
        }

        case "model_downgraded": {
          const jobId = payload.jobId as string;
          const requestedModel = payload.requestedModel as string;
          const actualModel = payload.actualModel as string;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  modelDowngraded: true,
                  requestedModel,
                  actualModel,
                },
              },
            };
          }
          return null;
        }

        default:
          return null;
      }
    })();
    // Only call set() if the handler returned an actual update
    if (update !== null) {
      set(update);
    }
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectJobs = (state: TowerState) => state.jobs;
export const selectConnectionStatus = (state: TowerState) =>
  state.connectionStatus;
export const selectApprovals = (state: TowerState) => state.approvals;

// Stable empty-array sentinels — MUST NOT be inline `?? []` because a new
// array literal is a new reference on every call, causing useSyncExternalStore
// to see a changed snapshot every render → infinite re-render loop (#185).
const EMPTY_LOGS: LogLine[] = [];
const EMPTY_TRANSCRIPT: TranscriptEntry[] = [];
const EMPTY_DIFFS: DiffFileModel[] = [];

export const selectJobLogs = (jobId: string) => (state: TowerState) =>
  state.logs[jobId] ?? EMPTY_LOGS;
export const selectJobTranscript = (jobId: string) => (state: TowerState) =>
  state.transcript[jobId] ?? EMPTY_TRANSCRIPT;
export const selectJobDiffs = (jobId: string) => (state: TowerState) =>
  state.diffs[jobId] ?? EMPTY_DIFFS;

// Per-column selectors — only recompute when jobs in that column change
function sortByUpdatedDesc(jobs: JobSummary[]): JobSummary[] {
  return jobs.sort(
    (a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

export const selectActiveJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) => j.state === "queued" || j.state === "running",
    ),
  );

/** Sign-off: everything that needs operator attention before archival.
 *  - waiting_for_approval
 *  - succeeded (any resolution) — not archived
 *  - canceled — not archived
 */
export const selectSignoffJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) =>
        !j.archivedAt &&
        (j.state === "waiting_for_approval" ||
          j.state === "succeeded" ||
          j.state === "canceled"),
    ),
  );

/** @deprecated Use selectSignoffJobs instead */
export const selectReviewJobs = (state: TowerState): JobSummary[] =>
  selectSignoffJobs(state);

/** Attention: failed jobs that haven't been archived. */
export const selectAttentionJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) => !j.archivedAt && j.state === "failed",
    ),
  );

/** @deprecated Use selectAttentionJobs instead */
export const selectFailedJobs = (state: TowerState): JobSummary[] =>
  selectAttentionJobs(state);

/** Archived jobs loaded into the store (for the history browser). */
export const selectArchivedJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter((j) => !!j.archivedAt),
  );

/** Count of archived jobs known to the store (badge hint). */
export const selectArchivedCount = (state: TowerState): number =>
  Object.values(state.jobs).filter((j) => !!j.archivedAt).length;
