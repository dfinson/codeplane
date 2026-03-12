/**
 * Zustand store — single source of truth for application state.
 *
 * SSE events are processed through a central event dispatcher that
 * updates the store. Components read from the store via selectors.
 */

import { create } from "zustand";

// ---------------------------------------------------------------------------
// Types (inline until schema.d.ts generation is wired up)
// ---------------------------------------------------------------------------

/** Connection status exposed to UI components. */
export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";

/** Minimal job shape matching JobResponse from the backend. */
export interface JobSummary {
  id: string;
  repo: string;
  prompt: string;
  state: string;
  strategy: string;
  baseRef: string;
  worktreePath: string | null;
  branch: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
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

  // UI state
  connectionStatus: ConnectionStatus;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  dispatchSSEEvent: (eventType: string, data: unknown) => void;
  applySnapshot: (jobs: JobSummary[], approvals: ApprovalRequest[]) => void;
}

export const useTowerStore = create<TowerState>((set) => ({
  jobs: {},
  approvals: {},
  logs: {},
  transcript: {},
  connectionStatus: "disconnected",

  setConnectionStatus: (status) => set({ connectionStatus: status }),

  applySnapshot: (jobs, approvals) =>
    set({
      jobs: Object.fromEntries(jobs.map((j) => [j.id, j])),
      approvals: Object.fromEntries(approvals.map((a) => [a.id, a])),
    }),

  dispatchSSEEvent: (eventType, data) =>
    set((state) => {
      const payload = data as Record<string, unknown>;

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
          return state;
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
          return {
            logs: { ...state.logs, [jobId]: [...existing, entry] },
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
          return {
            transcript: { ...state.transcript, [jobId]: [...existing, entry] },
          };
        }

        case "approval_requested": {
          const approval: ApprovalRequest = {
            id: payload.approvalId as string,
            jobId: payload.jobId as string,
            description: payload.description as string,
            proposedAction: (payload.proposedAction as string | null) ?? null,
            requestedAt: payload.timestamp as string,
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
          return state;
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

        default:
          return state;
      }
    }),
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
