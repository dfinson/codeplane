/**
 * Zustand store selectors — stable selector functions for components.
 *
 * Using named selectors (instead of inline arrow functions in components)
 * prevents unnecessary re-renders and makes state dependencies explicit.
 */

import type { DiffFileModel, StoryResponse } from "../api/types";
import type {
  AppState,
  JobSummary,
  LogLine,
  TranscriptEntry,
  PlanStep,
  TimelineEntry,
  ActivityTimelineState,
} from "./types";

// Stable empty-array sentinels — MUST NOT be inline `?? []` because a new
// array literal is a new reference on every call, causing useSyncExternalStore
// to see a changed snapshot every render → infinite re-render loop (#185).
const EMPTY_LOGS: LogLine[] = [];
const EMPTY_TRANSCRIPT: TranscriptEntry[] = [];
const EMPTY_DIFFS: DiffFileModel[] = [];

export const selectJobs = (state: AppState) => state.jobs;
export const selectConnectionStatus = (state: AppState) =>
  state.connectionStatus;
export const selectReconnectAttempt = (state: AppState) =>
  state.reconnectAttempt;
export const selectApprovals = (state: AppState) => state.approvals;
export const selectBatchApprovals = (state: AppState) => state.batchApprovals;

export const selectJobLogs = (jobId: string) => (state: AppState) =>
  state.logs[jobId] ?? EMPTY_LOGS;
export const selectJobTranscript = (jobId: string) => (state: AppState) =>
  state.transcript[jobId] ?? EMPTY_TRANSCRIPT;
export const selectJobDiffs = (jobId: string) => (state: AppState) =>
  state.diffs[jobId] ?? EMPTY_DIFFS;

const EMPTY_STORY: StoryResponse | null = null;
export const selectJobStory = (jobId: string) => (state: AppState) =>
  state.stories[jobId] ?? EMPTY_STORY;

// Structural analysis selectors

/** Select accumulated streaming tool output for a job, keyed by toolCallId. */
export const selectStreamingToolOutput = (jobId: string) => (state: AppState) => {
  const prefix = `${jobId}:`;
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(state.streamingToolOutput)) {
    if (key.startsWith(prefix)) {
      result[key.slice(prefix.length)] = value;
    }
  }
  return result;
};

/** Select accumulated streaming reasoning for a job, keyed by turnId. */
export const selectStreamingReasoning = (jobId: string) => (state: AppState) => {
  const prefix = `${jobId}:`;
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(state.streamingReasoning)) {
    if (key.startsWith(prefix)) {
      result[key.slice(prefix.length)] = value;
    }
  }
  return result;
};

// Per-column selectors — only recompute when jobs in that column change
function sortByUpdatedDesc(jobs: JobSummary[]): JobSummary[] {
  return jobs.sort(
    (a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

// Classifier predicates (AD-1: one shared job-status classifier, reused by both the
// flat cross-repo selectors below and the repo-scoped `*ForRepo` variants used by
// RepoBoard — never re-implemented at a second call site).
function isActiveJob(j: JobSummary): boolean {
  return !j.archivedAt && (j.state === "preparing" || j.state === "queued" || j.state === "running");
}

/** Sign-off: everything that needs operator attention before archival.
 *  - waiting_for_approval
 *  - review (agent done, awaiting operator decision) — not archived
 *  - completed but still unresolved (operator hasn't decided yet)
 */
function isSignoffJob(j: JobSummary): boolean {
  return (
    !j.archivedAt &&
    (j.state === "waiting_for_approval" ||
      j.state === "review" ||
      (j.state === "completed" && (!j.resolution || j.resolution === "unresolved")))
  );
}

/** Attention: failed jobs that haven't been archived. */
function isAttentionJob(j: JobSummary): boolean {
  return !j.archivedAt && j.state === "failed";
}

export const selectActiveJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter(isActiveJob));

export const selectSignoffJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter(isSignoffJob));

export const selectAttentionJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter(isAttentionJob));

// Repo-scoped variants (CAP-1 / Story 2.3, RepoBoard.tsx). AD-2: repo scoping travels
// via the URL route param — `repoPath` is passed in by the caller, not read from
// store or client-only state. A single-repo Project reduces to `job.repo === repoPath`.
export const selectActiveJobsForRepo = (repoPath: string) => (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter((j) => j.repo === repoPath && isActiveJob(j)));

export const selectSignoffJobsForRepo = (repoPath: string) => (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter((j) => j.repo === repoPath && isSignoffJob(j)));

export const selectAttentionJobsForRepo = (repoPath: string) => (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter((j) => j.repo === repoPath && isAttentionJob(j)));

// Project-scoped variants — true multi-repo aggregation across every member
// repo of a Project (not just a single reduced repo path). Used by the
// project-identity-routed board so a multi-repo Project's board genuinely
// shows jobs from all its member repos, not only the first one.
//
// Every Job has an owning Project (AD-5) — there is no legacy "no project"
// state to fall back to a repo-path match for, so these match on
// `projectId` alone.
export const selectActiveJobsForProject = (projectId: string) => (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter((j) => j.projectId === projectId && isActiveJob(j)));

export const selectSignoffJobsForProject = (projectId: string) => (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter((j) => j.projectId === projectId && isSignoffJob(j)));

export const selectAttentionJobsForProject = (projectId: string) => (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(Object.values(state.jobs).filter((j) => j.projectId === projectId && isAttentionJob(j)));

/** Archived jobs loaded into the store (for the history browser). */
export const selectArchivedJobs = (state: AppState): JobSummary[] =>
  sortByUpdatedDesc(
    Object.values(state.jobs).filter((j) => !!j.archivedAt),
  );

/** Count of archived jobs known to the store (badge hint). */
export const selectArchivedCount = (state: AppState): number =>
  Object.values(state.jobs).filter((j) => !!j.archivedAt).length;

const EMPTY_TIMELINE: TimelineEntry[] = [];
export const selectJobTimeline = (jobId: string) => (state: AppState) =>
  state.timelines[jobId] ?? EMPTY_TIMELINE;

const EMPTY_PLAN: PlanStep[] = [];
export const selectJobPlan = (jobId: string) => (state: AppState) =>
  state.plans[jobId] ?? EMPTY_PLAN;

const EMPTY_ACTIVITY_TIMELINE: ActivityTimelineState = { activities: [] };
export const selectActivityTimeline = (jobId: string) => (state: AppState) =>
  state.activityTimelines[jobId] ?? EMPTY_ACTIVITY_TIMELINE;
export const selectHoveredPlanItemId = (state: AppState) => state.hoveredPlanItemId;

const EMPTY_SECONDARY_SESSIONS: Record<string, import("./types").SecondarySession> = {};
export const selectSecondarySessions = (jobId: string) => (state: AppState) =>
  state.secondarySessions[jobId] ?? EMPTY_SECONDARY_SESSIONS;

const EMPTY_CONTEXT_HANDOFFS: import("./types").ContextHandoff[] = [];
export const selectContextHandoffs = (jobId: string) => (state: AppState) =>
  state.contextHandoffs[jobId] ?? EMPTY_CONTEXT_HANDOFFS;
