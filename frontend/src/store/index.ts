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

import type { DiffFileModel, SDKInfo } from "../api/types";
import { fetchSDKs, fetchModels } from "../api/client";

function pickDefaultModelId(models: Array<{ value: string; isDefault: boolean }>): string | null {
  const flagged = models.find((m) => m.isDefault);
  return flagged?.value ?? models[0]?.value ?? null;
}

/** Connection status exposed to UI components. */
export type ConnectionStatus = "connected" | "connecting" | "reconnecting" | "disconnected";

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
  resolutionError?: string | null;
  failureReason?: string | null;
  progressHeadline?: string | null;
  progressSummary?: string | null;
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
  requiresExplicitApproval: boolean;
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
  toolIssue?: string;    // tool_call: short issue summary when attention is needed
  toolIntent?: string;   // tool_call: SDK-provided intent string (deterministic label)
  toolTitle?: string;    // tool_call: SDK-provided display title
  toolDisplay?: string;  // tool_call: deterministic per-tool label (e.g. "$ ls -la", "Read src/main.py")
  toolDisplayFull?: string;  // tool_call: same label without char truncation (for CSS-based responsive truncation)
  toolDurationMs?: number;  // tool_call: execution time in milliseconds
  // AI-generated group summary — patched in asynchronously via tool_group_summary SSE
  toolGroupSummary?: string;
  stepId?: string;      // step this event belongs to (added by StepTracker)
  stepNumber?: number;  // sequential step number within the job
}

export interface Step {
  stepId: string;
  stepNumber: number;
  jobId: string;
  turnId: string | null;
  intent: string;
  title: string | null;
  status: "running" | "completed" | "failed" | "canceled";
  trigger: string;
  toolCount: number;
  durationMs: number | null;
  startedAt: string;
  completedAt: string | null;
  filesRead: string[] | null;
  filesWritten: string[] | null;
  startSha: string | null;
  endSha: string | null;
  artifactCount: number;
  agentMessage: string | null;
}

export interface TimelineEntry {
  headline: string;
  headlinePast: string;
  summary: string;
  timestamp: string;
  active: boolean;
}

export interface StepGroup {
  groupId: string;
  headline: string;
  headlinePast: string;
  stepIds: string[];
}

const HEADLINE_STOP_WORDS = new Set([
  "a",
  "an",
  "and",
  "the",
  "to",
  "for",
  "of",
  "in",
  "on",
  "with",
  "agent",
  "phase",
  "task",
  "tasks",
  "work",
  "working",
  "progress",
  "checking",
  "check",
  "investigating",
  "investigate",
  "debugging",
  "debug",
  "analyzing",
  "analyze",
  "exploring",
  "explore",
  "reviewing",
  "review",
  "fixing",
  "fix",
  "implementing",
  "implement",
  "updating",
  "update",
  "writing",
  "write",
  "running",
  "run",
  "editing",
  "edit",
  "refining",
  "refine",
]);

function normalizeHeadlineText(text: string): string {
  return (text.toLowerCase().match(/[a-z0-9]+/g) ?? []).join(" ");
}

function normalizeHeadlineTokens(text: string): Set<string> {
  return new Set((text.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((word) => !HEADLINE_STOP_WORDS.has(word)));
}

function headlinesAreSimilar(left: string, right: string): boolean {
  const leftNorm = normalizeHeadlineText(left);
  const rightNorm = normalizeHeadlineText(right);
  if (!leftNorm || !rightNorm) return false;
  if (leftNorm === rightNorm || leftNorm.includes(rightNorm) || rightNorm.includes(leftNorm)) return true;

  const leftTokens = normalizeHeadlineTokens(left);
  const rightTokens = normalizeHeadlineTokens(right);
  if (leftTokens.size === 0 || rightTokens.size === 0) return false;

  const shared = [...leftTokens].filter((token) => rightTokens.has(token));
  if (shared.length < 2) return false;
  return shared.length / Math.min(leftTokens.size, rightTokens.size) >= 0.67;
}

function countSimilarTrailingEntries(timeline: TimelineEntry[], headline: string): number {
  let count = 0;
  for (let index = timeline.length - 1; index >= 0; index -= 1) {
    const entry = timeline[index];
    if (!entry) continue;
    if (headlinesAreSimilar(entry.headline, headline) || headlinesAreSimilar(entry.headlinePast, headline)) {
      count += 1;
      continue;
    }
    break;
  }
  return count;
}

export interface PlanStep {
  label: string;
  status: "done" | "active" | "pending" | "skipped";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MODEL_DOWNGRADE_RE = /^Model downgraded: requested (.+) but received (.+)$/;

/** Finalize all active/pending plan steps to a terminal status. */
function finalizePlanSteps(plan: PlanStep[] | undefined, finalStatus: "done" | "skipped"): PlanStep[] | undefined {
  return plan?.map((s) => (s.status === "active" || s.status === "pending" ? { ...s, status: finalStatus } : s));
}

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
  steps: Record<string, Step[]>;           // keyed by jobId
  stepGroups: Record<string, StepGroup[]>;  // keyed by jobId
  transcriptByStep: Record<string, Record<string, TranscriptEntry[]>>;  // jobId → stepId → entries
  /** Accumulated streaming text for in-progress agent messages, keyed by
   * "${jobId}:${turnId}" (or "${jobId}:__default__" when turnId is absent).
   * Cleared when the complete agent message arrives for that turn. */
  streamingMessages: Record<string, string>;
  /** Monotonically-increasing counter per job, bumped on each telemetry_updated
   * SSE event. Components watching this trigger a telemetry re-fetch. */
  telemetryVersions: Record<string, number>; // keyed by jobId

  // Terminal state
  terminalDrawerOpen: boolean;
  terminalDrawerHeight: number;
  terminalSessions: Record<string, TerminalSession>;
  activeTerminalTab: string | null;

  // SDK + model catalogue (loaded once at app startup)
  sdks: SDKInfo[];
  defaultSdk: string | null;
  sdksLoading: boolean;
  modelsBySdk: Record<string, { value: string; label: string }[]>;
  defaultModelBySdk: Record<string, string | null>;
  modelsLoadingBySdk: Record<string, boolean>;

  // UI state
  connectionStatus: ConnectionStatus;
  reconnectAttempt: number;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  setReconnectAttempt: (attempt: number) => void;
  /** Fetches SDK list + models for the default SDK. Called once on app mount. */
  initSdksAndModels: () => Promise<void>;
  /** Fetches models for a specific SDK (no-op if already loaded). */
  loadModelsForSdk: (sdkId: string) => Promise<void>;
  dispatchSSEEvent: (eventType: string, data: unknown) => void;
  applySnapshot: (jobs: JobSummary[], approvals: ApprovalRequest[]) => void;
  /** Bulk-apply a full job snapshot from the hydration endpoint. */
  hydrateJob: (snapshot: {
    job: JobSummary;
    logs: LogLine[];
    transcript: TranscriptEntry[];
    diff: DiffFileModel[];
    approvals: ApprovalRequest[];
    timeline: TimelineEntry[];
    steps?: Step[];
  }) => void;

  // Terminal actions
  toggleTerminalDrawer: () => void;
  setTerminalDrawerHeight: (height: number) => void;
  setActiveTerminalTab: (id: string) => void;
  addTerminalSession: (session: TerminalSession) => void;
  removeTerminalSession: (id: string) => void;
  createTerminalSession: (opts?: { cwd?: string; jobId?: string; label?: string }) => void;
}

// Module-level singleton guard: ensures initSdksAndModels is only ever
// in-flight once, even if called concurrently from multiple components.
let _sdkInitPromise: Promise<void> | null = null;

/** Reset the SDK init guard — for use in tests only. */
export function _resetSdkInitForTesting() {
  _sdkInitPromise = null;
}

function buildTranscriptByStep(entries: TranscriptEntry[]): Record<string, TranscriptEntry[]> {
  const byStep: Record<string, TranscriptEntry[]> = {};
  for (const entry of entries) {
    if (entry.stepId) {
      (byStep[entry.stepId] ??= []).push(entry);
    }
  }
  return byStep;
}

export const useStore = create<AppState>((set, get) => ({
  jobs: {},
  approvals: {},
  logs: {},
  transcript: {},
  diffs: {},
  timelines: {},
  plans: {},
  steps: {},
  stepGroups: {},
  transcriptByStep: {},
  streamingMessages: {},
  telemetryVersions: {},
  connectionStatus: "reconnecting",
  reconnectAttempt: 0,

  // SDK + model catalogue
  sdks: [],
  defaultSdk: null,
  sdksLoading: true,
  modelsBySdk: {},
  defaultModelBySdk: {},
  modelsLoadingBySdk: {},

  // Terminal state
  terminalDrawerOpen: false,
  terminalDrawerHeight: 300,
  terminalSessions: {},
  activeTerminalTab: null,

  setConnectionStatus: (status) =>
    get().connectionStatus !== status && set({ connectionStatus: status }),

  setReconnectAttempt: (attempt) => set({ reconnectAttempt: attempt }),

  initSdksAndModels: async () => {
    // No-op if already done (success or failure)
    if (!get().sdksLoading) return;
    // Coalesce concurrent callers onto the same in-flight promise
    if (_sdkInitPromise) return _sdkInitPromise;
    _sdkInitPromise = (async () => {
      try {
        const r = await fetchSDKs();
        set({ sdks: r.sdks, defaultSdk: r.default, sdksLoading: false });
        // Pre-load models for the default SDK
        await get().loadModelsForSdk(r.default);
      } catch (err) {
        console.error("Failed to fetch SDKs", err);
        set({ sdksLoading: false });
      }
    })();
    return _sdkInitPromise;
  },

  loadModelsForSdk: async (sdkId: string) => {
    // Skip if already loaded or currently loading
    const state = get();
    if (state.modelsBySdk[sdkId] !== undefined || state.modelsLoadingBySdk[sdkId]) return;
    set((s) => ({ modelsLoadingBySdk: { ...s.modelsLoadingBySdk, [sdkId]: true } }));
    try {
      const models = await fetchModels(sdkId);
      const mapped = models
        .map((x) => ({
          value: String(x.id ?? x.name ?? ""),
          label: String(x.name ?? x.id ?? "unknown"),
          isDefault: Boolean(
            (typeof x.default === "boolean" && x.default) ||
            (typeof x.isDefault === "boolean" && x.isDefault) ||
            (typeof x.is_default === "boolean" && x.is_default),
          ),
        }))
        .filter((x) => x.value);
      set((s) => ({
        modelsBySdk: { ...s.modelsBySdk, [sdkId]: mapped.map(({ value, label }) => ({ value, label })) },
        defaultModelBySdk: { ...s.defaultModelBySdk, [sdkId]: pickDefaultModelId(mapped) },
        modelsLoadingBySdk: { ...s.modelsLoadingBySdk, [sdkId]: false },
      }));
    } catch (err) {
      console.error(`Failed to fetch models for SDK "${sdkId}"`, err);
      set((s) => ({
        modelsBySdk: { ...s.modelsBySdk, [sdkId]: [] },
        defaultModelBySdk: { ...s.defaultModelBySdk, [sdkId]: null },
        modelsLoadingBySdk: { ...s.modelsLoadingBySdk, [sdkId]: false },
      }));
    }
  },

  applySnapshot: (jobs, approvals) => {
    const jobMap = Object.fromEntries(jobs.map((j) => [j.id, enrichJob(j)]));
    const validApprovals = approvals.filter(
      (a) => jobMap[a.jobId]?.state === "waiting_for_approval",
    );
    set({
      jobs: jobMap,
      approvals: Object.fromEntries(validApprovals.map((a) => [a.id, a])),
    });
  },

  hydrateJob: (snapshot) => {
    const jobId = snapshot.job.id;
    set((s) => {
      // Remove stale approvals for this job before merging fresh ones
      const keptApprovals = Object.fromEntries(
        Object.entries(s.approvals).filter(([, a]) => a.jobId !== jobId),
      );
      // Drop any in-flight streaming state for this job
      const streamingMessages = Object.fromEntries(
        Object.entries(s.streamingMessages).filter(([k]) => !k.startsWith(`${jobId}:`)),
      );
      // Deduplicate transcript: remove tool_running entries whose tool has a
      // completed tool_call — both are persisted but only one should render.
      // Use turnId-scoped keys when available to avoid false-positive removal
      // of in-flight tool_running entries for the same tool name.
      const completedCallKeys = new Set<string>();
      for (const e of snapshot.transcript) {
        if (e.role === "tool_call" && e.toolName) {
          completedCallKeys.add(e.turnId ? `${e.toolName}::${e.turnId}` : e.toolName);
        }
      }
      const deduped = snapshot.transcript.filter((e) => {
        if (e.role !== "tool_running" || !e.toolName) return true;
        const key = e.turnId ? `${e.toolName}::${e.turnId}` : e.toolName;
        return !completedCallKeys.has(key);
      });
      return {
        jobs: { ...s.jobs, [jobId]: enrichJob(snapshot.job) },
        logs: { ...s.logs, [jobId]: snapshot.logs },
        transcript: { ...s.transcript, [jobId]: deduped },
        diffs: { ...s.diffs, [jobId]: snapshot.diff },
        timelines: {
          ...s.timelines,
          [jobId]: snapshot.timeline.map((t) => ({ ...t, active: false })),
        },
        approvals: {
          ...keptApprovals,
          ...Object.fromEntries(snapshot.approvals.map((a) => [a.id, a])),
        },
        streamingMessages,
        steps: { ...s.steps, [jobId]: snapshot.steps ?? [] },
        transcriptByStep: { ...s.transcriptByStep, [jobId]: buildTranscriptByStep(snapshot.transcript ?? []) },
      };
    });
  },

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
            const isCanceled = newState === "canceled";
            const existingPlan = isCanceled ? state.plans[jobId] : undefined;
            const finalPlan = finalizePlanSteps(existingPlan, "skipped");

            // If the job is leaving waiting_for_approval without an
            // approval_resolved event (e.g. server-restart recovery), evict any
            // stale unresolved approvals for this job so the mobile badge stays
            // in sync with the column content.
            let approvals = state.approvals;
            if (newState !== "waiting_for_approval") {
              const staleIds = Object.keys(state.approvals).filter(
                (id) => state.approvals[id]?.jobId === jobId && !state.approvals[id]?.resolvedAt,
              );
              if (staleIds.length > 0) {
                approvals = { ...state.approvals };
                for (const id of staleIds) delete approvals[id];
              }
            }

            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: newState,
                  updatedAt: (payload.timestamp as string) ?? existing.updatedAt,
                },
              },
              ...(finalPlan && { plans: { ...state.plans, [jobId]: finalPlan } }),
              ...(approvals !== state.approvals && { approvals }),
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
          const role = payload.role as string;

          // agent_delta: accumulate streaming text per turn, don't add to transcript
          if (role === "agent_delta") {
            const turnId = (payload.turnId as string | undefined) ?? "__default__";
            const key = `${jobId}:${turnId}`;
            const delta = (payload.content as string) ?? "";
            return {
              streamingMessages: {
                ...state.streamingMessages,
                [key]: (state.streamingMessages[key] ?? "") + delta,
              },
            };
          }

          const entry: TranscriptEntry = {
            jobId,
            seq: payload.seq as number,
            timestamp: payload.timestamp as string,
            role,
            content: payload.content as string,
            title: payload.title as string | undefined,
            turnId: payload.turnId as string | undefined,
            toolName: payload.toolName as string | undefined,
            toolArgs: payload.toolArgs as string | undefined,
            toolResult: payload.toolResult as string | undefined,
            toolSuccess: payload.toolSuccess as boolean | undefined,
            toolIssue: payload.toolIssue as string | undefined,
            toolIntent: payload.toolIntent as string | undefined,
            toolTitle: payload.toolTitle as string | undefined,
            toolDisplay: payload.toolDisplay as string | undefined,
            toolDisplayFull: payload.toolDisplayFull as string | undefined,
            toolDurationMs: payload.toolDurationMs as number | undefined,
            stepId: payload.stepId as string | undefined,
            stepNumber: payload.stepNumber as number | undefined,
          };
          const existing = state.transcript[jobId] ?? [];

          // When a tool_call arrives, replace any matching tool_running entry
          // (same toolName, and same turnId when both are present) so the
          // in-progress placeholder is superseded.
          let base = existing;
          if (entry.role === "tool_call") {
            const before = base.length;
            base = base.filter((e) => {
              if (e.role !== "tool_running" || e.toolName !== entry.toolName) return true;
              // If both entries have a turnId, they must match to be considered the same call.
              if (entry.turnId && e.turnId && entry.turnId !== e.turnId) return true;
              return false;
            });
            // If we replaced something, emit directly — no further dedup needed.
            if (base.length < before) {
              const updated = [...base, entry];
              return {
                transcript: { ...state.transcript, [jobId]: updated.length > 10_000 ? updated.slice(-10_000) : updated },
              };
            }
          }

          // Deduplicate: two SSE connections (global + job-scoped) may deliver
          // the same event; skip if identical role+content+timestamp already present.
          if (existing.some((e) => e.timestamp === entry.timestamp && e.role === entry.role && e.content === entry.content)) {
            return null;
          }
          const updated = [...existing, entry];

          // When a complete agent message arrives, clear streaming state for that turn.
          let streamingMessages = state.streamingMessages;
          if (entry.role === "agent") {
            const key = entry.turnId ? `${jobId}:${entry.turnId}` : `${jobId}:__default__`;
            if (key in streamingMessages) {
              streamingMessages = { ...streamingMessages };
              delete streamingMessages[key];
            }
          }

          // Index by step for O(1) lookup in StepContainer
          let transcriptByStep = state.transcriptByStep;
          if (entry.stepId) {
            const jobIndex = transcriptByStep[jobId] ?? {};
            const stepEntries = jobIndex[entry.stepId] ?? [];
            const capped = stepEntries.length >= 500 ? stepEntries.slice(-499) : stepEntries;
            transcriptByStep = {
              ...transcriptByStep,
              [jobId]: { ...jobIndex, [entry.stepId]: [...capped, entry] },
            };
          }

          return {
            transcript: { ...state.transcript, [jobId]: updated.length > 10_000 ? updated.slice(-10_000) : updated },
            streamingMessages,
            transcriptByStep,
          };
        }

        case "step_started": {
          const jobId = payload.jobId as string;
          const existing = get().steps[jobId] ?? [];
          // Dedup: skip if already hydrated from REST API
          const stepId = payload.stepId as string;
          if (existing.some((s) => s.stepId === stepId)) return {};
          const newStep: Step = {
            stepId: payload.stepId as string,
            stepNumber: payload.stepNumber as number,
            jobId,
            turnId: (payload.turnId as string | null) ?? null,
            intent: payload.intent as string,
            title: null,
            status: "running",
            trigger: payload.trigger as string,
            toolCount: 0,
            durationMs: null,
            startedAt: payload.startedAt as string,
            completedAt: null,
            filesRead: null,
            filesWritten: null,
            startSha: null,
            endSha: null,
            artifactCount: 0,
            agentMessage: null,
          };
          return { steps: { ...get().steps, [jobId]: [...existing, newStep] } };
        }

        case "step_completed": {
          const jobId = payload.jobId as string;
          const existing = get().steps[jobId] ?? [];
          return {
            steps: {
              ...get().steps,
              [jobId]: existing.map((s) =>
                s.stepId === (payload.stepId as string)
                  ? {
                      ...s,
                      status: payload.status as Step["status"],
                      toolCount: payload.toolCount as number,
                      durationMs: (payload.durationMs as number | null) ?? null,
                      completedAt: new Date().toISOString(),
                      agentMessage: (payload.agentMessage as string | null) ?? s.agentMessage ?? null,
                      filesRead: (payload.filesRead as string[] | null) ?? null,
                      filesWritten: (payload.filesWritten as string[] | null) ?? null,
                      startSha: (payload.startSha as string | null) ?? null,
                      endSha: (payload.endSha as string | null) ?? null,
                    }
                  : s
              ),
            },
          };
        }

        case "step_title": {
          const jobId = payload.jobId as string;
          const existing = get().steps[jobId] ?? [];
          return {
            steps: {
              ...get().steps,
              [jobId]: existing.map((s) =>
                s.stepId === (payload.stepId as string)
                  ? { ...s, title: payload.title as string }
                  : s
              ),
            },
          };
        }

        case "step_group_updated": {
          const jobId = payload.jobId as string;
          const group: StepGroup = {
            groupId: payload.groupId as string,
            headline: payload.headline as string,
            headlinePast: payload.headlinePast as string,
            stepIds: payload.stepIds as string[],
          };
          const existingGroups = get().stepGroups[jobId] ?? [];
          // Replace if same groupId, otherwise append
          const idx = existingGroups.findIndex((g) => g.groupId === group.groupId);
          const updatedGroups = idx >= 0
            ? existingGroups.map((g, i) => (i === idx ? group : g))
            : [...existingGroups, group];
          return {
            stepGroups: { ...get().stepGroups, [jobId]: updatedGroups },
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
            requiresExplicitApproval: (payload.requiresExplicitApproval as boolean) ?? false,
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
          return {
            jobs: jobMap,
            approvals: Object.fromEntries(approvals.map((a) => [a.id, a])),
          };
        }

        case "session_heartbeat": {
          if (state.connectionStatus !== "connected") {
            return { connectionStatus: "connected" as ConnectionStatus };
          }
          return null;
        }

        case "job_review": {
          const jobId = payload.jobId as string;
          const prUrl = (payload.prUrl as string | null) ?? null;
          const resolution = (payload.resolution as string | null) ?? null;
          const mergeStatus = (payload.mergeStatus as string | null) ?? null;
          const modelDowngraded = (payload.modelDowngraded as boolean) ?? false;
          const requestedModel = (payload.requestedModel as string | null) ?? null;
          const actualModel = (payload.actualModel as string | null) ?? null;
          const existing = state.jobs[jobId];
          if (existing) {
            const existingPlan = state.plans[jobId];
            const finalPlan = finalizePlanSteps(existingPlan, "done");
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: "review",
                  ...(prUrl && { prUrl }),
                  ...(resolution && { resolution }),
                  ...(mergeStatus && { mergeStatus }),
                  failureReason: null,
                  ...(modelDowngraded && { modelDowngraded, requestedModel, actualModel }),
                },
              },
              ...(finalPlan && { plans: { ...state.plans, [jobId]: finalPlan } }),
            };
          }
          return null;
        }

        case "job_completed": {
          const jobId = payload.jobId as string;
          const resolution = (payload.resolution as string | null) ?? null;
          const prUrl = (payload.prUrl as string | null) ?? null;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: "completed",
                  ...(resolution && { resolution }),
                  ...(prUrl && { prUrl }),
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
            const existingPlan = state.plans[jobId];
            const finalPlan = finalizePlanSteps(existingPlan, "skipped");
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  state: "failed",
                  failureReason: reason,
                },
              },
              ...(finalPlan && { plans: { ...state.plans, [jobId]: finalPlan } }),
            };
          }
          return null;
        }

        case "job_resolved": {
          const jobId = payload.jobId as string;
          const resolution = payload.resolution as string;
          const prUrl = (payload.prUrl as string | null) ?? null;
          const conflictFiles = (payload.conflictFiles as string[] | null) ?? null;
          const resolutionError = (payload.error as string | null) ?? null;
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
                  resolutionError,
                  updatedAt: (payload.timestamp as string) ?? existing.updatedAt,
                },
              },
            };
          }
          return null;
        }

        case "merge_completed": {
          const jobId = payload.jobId as string;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  mergeStatus: "merged",
                  updatedAt: (payload.timestamp as string) ?? existing.updatedAt,
                },
              },
            };
          }
          return null;
        }

        case "merge_conflict": {
          const jobId = payload.jobId as string;
          const conflictFiles = (payload.conflictFiles as string[] | null) ?? null;
          const prUrl = (payload.prUrl as string | null) ?? null;
          const existing = state.jobs[jobId];
          if (existing) {
            return {
              jobs: {
                ...state.jobs,
                [jobId]: {
                  ...existing,
                  mergeStatus: "conflict",
                  conflictFiles,
                  prUrl: prUrl ?? existing.prUrl,
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
          if (existing.some((e) => e.role === "divider" && e.timestamp === divider.timestamp)) {
            return { jobs: state.jobs[jobId] ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], ...resetFields } } : state.jobs };
          }
          // Also reset step state so stale steps from the previous session don't appear
          const { [jobId]: _s, ...restSteps } = state.steps;
          const { [jobId]: _t, ...restByStep } = state.transcriptByStep;
          const { [jobId]: _g, ...restGroups } = state.stepGroups;
          return {
            transcript: { ...state.transcript, [jobId]: [...existing, divider] },
            jobs: state.jobs[jobId]
              ? { ...state.jobs, [jobId]: { ...state.jobs[jobId], ...resetFields } }
              : state.jobs,
            steps: restSteps,
            transcriptByStep: restByStep,
            stepGroups: restGroups,
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
            if (e.role === "tool_call" && e.turnId === turnId && e.toolGroupSummary !== summary) {
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
          const requestedReplacesCount = (payload.replacesCount as number) || 0;
          const existing = state.jobs[jobId];

          // Accumulate timeline entry
          const prevTimeline = state.timelines[jobId] ?? [];
          const similarityReplacesCount = requestedReplacesCount > 0 ? 0 : countSimilarTrailingEntries(prevTimeline, headline);
          const replacesCount = Math.max(requestedReplacesCount, similarityReplacesCount);
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
                  progressSummary: summary,
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

        case "telemetry_updated": {
          // Increment the per-job version counter so MetricsPanel re-fetches.
          const jobId = payload.jobId as string;
          const prev = state.telemetryVersions[jobId] ?? 0;
          return {
            telemetryVersions: { ...state.telemetryVersions, [jobId]: prev + 1 },
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

  removeTerminalSession: (id) =>
    set((s) => {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { [id]: _removed, ...rest } = s.terminalSessions;
      // Delete the session on the backend (fire-and-forget)
      fetch(`/api/terminal/sessions/${id}`, { method: "DELETE" }).catch((err) => console.error("Failed to delete terminal session", err));
      const remaining = Object.keys(rest);
      return {
        terminalSessions: rest,
        activeTerminalTab:
          s.activeTerminalTab === id
            ? remaining.length > 0
              ? remaining[remaining.length - 1]
              : null
            : s.activeTerminalTab,
        // Auto-close the drawer when no sessions remain
        terminalDrawerOpen: remaining.length > 0 ? s.terminalDrawerOpen : false,
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
          promptLabel: opts?.label ?? null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        console.error("[terminal] Failed to create session:", err);
        return;
      }
      const data = await res.json();

      const baseLabel = opts?.label || data.cwd?.split("/").pop() || "Terminal";

      // Auto-number duplicate labels so tabs are distinguishable (e.g. "main ×2")
      const existingLabels = Object.values(get().terminalSessions).map((s) => s.label);
      const collision = existingLabels.filter(
        (l) => l === baseLabel || l?.startsWith(baseLabel + " ×"),
      ).length;
      const label = collision > 0 ? `${baseLabel} ×${collision + 1}` : baseLabel;

      const session: TerminalSession = {
        id: data.id,
        label,
        cwd: data.cwd,
        jobId: data.jobId ?? opts?.jobId,
      };

      // On mobile, auto-maximise the drawer when opening a job terminal
      const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
      const drawerHeight = isMobile
        ? Math.floor(window.innerHeight * 0.9)
        : get().terminalDrawerHeight;

      set((s) => ({
        terminalSessions: { ...s.terminalSessions, [session.id]: session },
        activeTerminalTab: session.id,
        terminalDrawerOpen: true,
        terminalDrawerHeight: drawerHeight,
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
export const selectReconnectAttempt = (state: AppState) =>
  state.reconnectAttempt;
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
 *  - review (agent done, awaiting operator decision) — not archived
 *  - completed (finished but not yet archived)
 */
export const selectSignoffJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter(
      (j) =>
        !j.archivedAt &&
        (j.state === "waiting_for_approval" ||
          j.state === "review" ||
          j.state === "completed"),
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

const EMPTY_STEPS: Step[] = [];
export const selectJobSteps = (jobId: string) => (state: AppState) =>
  state.steps[jobId] ?? EMPTY_STEPS;
export const selectActiveStep = (jobId: string) => (state: AppState) => {
  const steps = state.steps[jobId] ?? [];
  return steps.find((s) => s.status === "running");
};
const EMPTY_STEP_GROUPS: StepGroup[] = [];
export const selectStepGroups = (jobId: string) => (state: AppState) =>
  state.stepGroups[jobId] ?? EMPTY_STEP_GROUPS;
const EMPTY_STEP_ENTRIES: TranscriptEntry[] = [];
export const selectStepEntries = (jobId: string, stepId: string) => (state: AppState) =>
  state.transcriptByStep[jobId]?.[stepId] ?? EMPTY_STEP_ENTRIES;
