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
      jobs: Object.fromEntries(jobs.map((j) => [j.id, j])),
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
          };
          const existing = state.transcript[jobId] ?? [];
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
            jobs: Object.fromEntries(jobs.map((j) => [j.id, j])),
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
          const existing = state.jobs[jobId];
          if (existing && prUrl) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: { ...existing, prUrl },
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
export const selectJobLogs = (jobId: string) => (state: TowerState) =>
  state.logs[jobId] ?? [];
export const selectJobTranscript = (jobId: string) => (state: TowerState) =>
  state.transcript[jobId] ?? [];
export const selectJobDiffs = (jobId: string) => (state: TowerState) =>
  state.diffs[jobId] ?? [];

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

export const selectSignoffJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) => j.state === "waiting_for_approval",
    ),
  );

export const selectFailedJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter((j) => j.state === "failed"),
  );

export const selectHistoryJobs = (state: TowerState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) => j.state === "succeeded" || j.state === "canceled",
    ),
  );
