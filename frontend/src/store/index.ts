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
  baseRef: string;
  worktreePath: string | null;
  branch: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  prUrl?: string | null;
  resolution?: string | null;
  archivedAt?: string | null;
  mergeStatus?: string | null;
  worktreeName?: string | null;
  conflictFiles?: string[] | null;
  failureReason?: string | null;
  progressHeadline?: string | null;
  model?: string | null;
  modelDowngraded?: boolean;
  requestedModel?: string | null;
  actualModel?: string | null;
  sdk?: string;
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
  toolIntent?: string;   // tool_call: SDK-provided intent string (deterministic label)
  toolTitle?: string;    // tool_call: SDK-provided display title
  toolDisplay?: string;  // tool_call: deterministic per-tool label (e.g. "$ ls -la", "Read src/main.py")
  // AI-generated group summary — patched in asynchronously via tool_group_summary SSE
  toolGroupSummary?: string;
}

export interface TimelineEntry {
  headline: string;
  headlinePast: string;
  summary: string;
  timestamp: string;
  active: boolean;
}

export interface PlanStep {
  label: string;
  status: "done" | "active" | "pending" | "skipped";
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

/** Terminal session metadata tracked in the store. */
export interface TerminalSession {
  id: string;
  label: string;
  cwd?: string;
  jobId?: string | null;
}

interface AppState {
  // Data slices
  jobs: Record<string, JobSummary>;
  approvals: Record<string, ApprovalRequest>;
  logs: Record<string, LogLine[]>; // keyed by jobId
  transcript: Record<string, TranscriptEntry[]>; // keyed by jobId
  diffs: Record<string, DiffFileModel[]>; // keyed by jobId
  timelines: Record<string, TimelineEntry[]>; // keyed by jobId
  plans: Record<string, PlanStep[]>; // keyed by jobId

  // Terminal state
  terminalDrawerOpen: boolean;
  terminalDrawerHeight: number;
  terminalSessions: Record<string, TerminalSession>;
  activeTerminalTab: string | null;

  // UI state
  connectionStatus: ConnectionStatus;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  dispatchSSEEvent: (eventType: string, data: unknown) => void;
  applySnapshot: (jobs: JobSummary[], approvals: ApprovalRequest[]) => void;

  // Terminal actions
  toggleTerminalDrawer: () => void;
  setTerminalDrawerHeight: (height: number) => void;
  setActiveTerminalTab: (id: string) => void;
  addTerminalSession: (session: TerminalSession) => void;
  /** Register a job-scoped terminal without opening the global drawer. */
  addJobTerminalSession: (session: TerminalSession) => void;
  removeTerminalSession: (id: string) => void;
  createTerminalSession: (opts?: { cwd?: string; jobId?: string; label?: string }) => void;
}

export const useStore = create<AppState>((set, get) => ({
  jobs: {},
  approvals: {},
  logs: {},
  transcript: {},
  diffs: {},
  timelines: {},
  plans: {},
  connectionStatus: "reconnecting",

  // Terminal state
  terminalDrawerOpen: false,
  terminalDrawerHeight: 300,
  terminalSessions: {},
  activeTerminalTab: null,

  setConnectionStatus: (status) =>
    get().connectionStatus !== status && set({ connectionStatus: status }),

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
            toolIntent: payload.toolIntent as string | undefined,
            toolTitle: payload.toolTitle as string | undefined,
            toolDisplay: payload.toolDisplay as string | undefined,
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
          const timestamp = payload.timestamp as string;
          const divider: TranscriptEntry = {
            jobId,
            seq: -99,
            timestamp,
            role: "divider",
            content: "Session",
          };
          const existing = state.transcript[jobId] ?? [];
          // Deduplicate: two SSE connections may deliver the same event
          const resetFields = {
            state: "running",
            resolution: null,
            conflictFiles: null,
            failureReason: null,
            archivedAt: null,
            modelDowngraded: false,
            requestedModel: null,
            actualModel: null,
            prUrl: null,
            mergeStatus: null,
            completedAt: null,
          };
          if (existing.some((e) => e.role === "divider" && e.timestamp === divider.timestamp)) {
            return { jobs: state.jobs[jobId] ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], ...resetFields } } : state.jobs };
          }
          return {
            transcript: { ...state.transcript, [jobId]: [...existing, divider] },
            jobs: state.jobs[jobId]
              ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], ...resetFields } }
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

        case "tool_group_summary": {
          const jobId = payload.jobId as string;
          const turnId = payload.turnId as string;
          const summary = payload.summary as string;
          const entries = state.transcript[jobId];
          if (!entries) return null;
          let changed = false;
          const patched = entries.map((e) => {
            if (e.role === "tool_call" && e.turnId === turnId && !e.toolGroupSummary) {
              changed = true;
              return { ...e, toolGroupSummary: summary };
            }
            return e;
          });
          if (!changed) return null;
          return { transcript: { ...state.transcript, [jobId]: patched } };
        }

        case "progress_headline": {
          const jobId = payload.jobId as string;
          const headline = payload.headline as string;
          const headlinePast = (payload.headlinePast as string) || headline;
          const timestamp = (payload.timestamp as string) || new Date().toISOString();
          const replacesCount = (payload.replacesCount as number) || 0;
          const existing = state.jobs[jobId];

          // Accumulate timeline entry
          const prevTimeline = state.timelines[jobId] ?? [];
          // If collapsing, remove the last N entries first
          const base = replacesCount > 0 ? prevTimeline.slice(0, -replacesCount) : prevTimeline;
          // Mark all remaining previous entries as inactive
          const deactivated = base.map((e) =>
            e.active ? { ...e, active: false } : e,
          );
          const summary = (payload.summary as string) || "";
          const newTimeline = [
            ...deactivated,
            { headline, headlinePast, summary, timestamp, active: true },
          ];

          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  progressHeadline: headline,
                },
              },
              timelines: { ...state.timelines, [jobId]: newTimeline },
            };
          }
          return {
            timelines: { ...state.timelines, [jobId]: newTimeline },
          };
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

        case "agent_plan_updated": {
          const jobId = payload.jobId as string;
          const steps = (payload.steps as Array<{ label: string; status: string }>) || [];
          const typed: PlanStep[] = steps.map((s) => ({
            label: s.label,
            status: (s.status as PlanStep["status"]) || "pending",
          }));
          return {
            plans: { ...state.plans, [jobId]: typed },
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

  // ------------------------------------------------------------------
  // Terminal actions
  // ------------------------------------------------------------------

  toggleTerminalDrawer: () =>
    set((s) => ({ terminalDrawerOpen: !s.terminalDrawerOpen })),

  setTerminalDrawerHeight: (height) => set({ terminalDrawerHeight: height }),

  setActiveTerminalTab: (id) => set({ activeTerminalTab: id }),

  addTerminalSession: (session) =>
    set((s) => ({
      terminalSessions: { ...s.terminalSessions, [session.id]: session },
      activeTerminalTab: session.id,
      terminalDrawerOpen: true,
    })),

  addJobTerminalSession: (session) =>
    set((s) => ({
      terminalSessions: { ...s.terminalSessions, [session.id]: session },
    })),

  removeTerminalSession: (id) =>
    set((s) => {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { [id]: _removed, ...rest } = s.terminalSessions;
      // Delete the session on the backend (fire-and-forget)
      fetch(`/api/terminal/sessions/${id}`, { method: "DELETE" }).catch(() => {});
      const remaining = Object.keys(rest);
      const drawerSessions = Object.values(rest).filter((sess) => !sess.jobId);
      return {
        terminalSessions: rest,
        activeTerminalTab:
          s.activeTerminalTab === id
            ? remaining.length > 0
              ? remaining[remaining.length - 1]
              : null
            : s.activeTerminalTab,
        // Auto-close the drawer when no global sessions remain
        terminalDrawerOpen: drawerSessions.length > 0 ? s.terminalDrawerOpen : false,
      };
    }),

  createTerminalSession: async (opts) => {
    try {
      const res = await fetch("/api/terminal/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cwd: opts?.cwd ?? null,
          jobId: opts?.jobId ?? null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        console.error("[terminal] Failed to create session:", err);
        return;
      }
      const data = await res.json();
      const session: TerminalSession = {
        id: data.id,
        label: opts?.label || data.cwd?.split("/").pop() || "Terminal",
        cwd: data.cwd,
        jobId: data.jobId ?? opts?.jobId,
      };
      set((s) => ({
        terminalSessions: { ...s.terminalSessions, [session.id]: session },
        activeTerminalTab: session.id,
        terminalDrawerOpen: true,
      }));
    } catch (e) {
      console.error("[terminal] Error creating session:", e);
    }
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectJobs = (state: AppState) => state.jobs;
export const selectConnectionStatus = (state: AppState) =>
  state.connectionStatus;
export const selectApprovals = (state: AppState) => state.approvals;

// Stable empty-array sentinels — MUST NOT be inline `?? []` because a new
// array literal is a new reference on every call, causing useSyncExternalStore
// to see a changed snapshot every render → infinite re-render loop (#185).
const EMPTY_LOGS: LogLine[] = [];
const EMPTY_TRANSCRIPT: TranscriptEntry[] = [];
const EMPTY_DIFFS: DiffFileModel[] = [];

export const selectJobLogs = (jobId: string) => (state: AppState) =>
  state.logs[jobId] ?? EMPTY_LOGS;
export const selectJobTranscript = (jobId: string) => (state: AppState) =>
  state.transcript[jobId] ?? EMPTY_TRANSCRIPT;
export const selectJobDiffs = (jobId: string) => (state: AppState) =>
  state.diffs[jobId] ?? EMPTY_DIFFS;

const EMPTY_TIMELINE: TimelineEntry[] = [];
export const selectJobTimeline = (jobId: string) => (state: AppState) =>
  state.timelines[jobId] ?? EMPTY_TIMELINE;

const EMPTY_PLAN: PlanStep[] = [];
export const selectJobPlan = (jobId: string) => (state: AppState) =>
  state.plans[jobId] ?? EMPTY_PLAN;

// Per-column selectors — only recompute when jobs in that column change
function sortByUpdatedDesc(jobs: JobSummary[]): JobSummary[] {
  return jobs.sort(
    (a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

export const selectActiveJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) => !j.archivedAt && (j.state === "queued" || j.state === "running"),
    ),
  );

/** Sign-off: everything that needs operator attention before archival.
 *  - waiting_for_approval
 *  - succeeded (any resolution) — not archived
 *  - canceled — not archived
 */
export const selectSignoffJobs = (state: AppState): JobSummary[] =>
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
export const selectReviewJobs = (state: AppState): JobSummary[] =>
  selectSignoffJobs(state);

/** Attention: failed jobs that haven't been archived. */
export const selectAttentionJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) => !j.archivedAt && j.state === "failed",
    ),
  );

/** @deprecated Use selectAttentionJobs instead */
export const selectFailedJobs = (state: AppState): JobSummary[] =>
  selectAttentionJobs(state);

/** Archived jobs loaded into the store (for the history browser). */
export const selectArchivedJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter((j) => !!j.archivedAt),
  );

/** Count of archived jobs known to the store (badge hint). */
export const selectArchivedCount = (state: AppState): number =>
  Object.values(state.jobs).filter((j) => !!j.archivedAt).length;
