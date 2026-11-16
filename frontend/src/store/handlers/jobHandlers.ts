/**
 * Job lifecycle SSE event handlers.
 */

import type { JobSummary, PlanStep } from "../types";
import type { SSEHandler, AppState } from "./types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MODEL_DOWNGRADE_RE = /^Model downgraded: requested (.+?) but received (.+)$/;

/** Finalize all active/pending plan steps to a terminal status. */
export function finalizePlanSteps(plan: PlanStep[] | undefined, finalStatus: "done" | "skipped"): PlanStep[] | undefined {
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
// Handlers
// ---------------------------------------------------------------------------

export function handleJobStateChanged(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  if (typeof payload.attemptId === "string") return null;
  const jobId = payload.jobId as string;
  const newState = payload.newState as string;
  const existing = state.jobs[jobId];
  if (existing) {
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

    // Finalize plan steps on cancel
    const isCanceled = newState === "canceled";
    const existingPlan = isCanceled ? state.plans[jobId] : undefined;
    const finalPlan = finalizePlanSteps(existingPlan, "skipped");

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

export function handleJobSetupProgress(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const step = payload.step as string;
  const existing = state.jobs[jobId];
  if (existing) {
    return {
      jobs: {
        ...state.jobs,
        [jobId]: { ...existing, setupStep: step },
      },
    };
  }
  return null;
}

export function handleJobReview(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  if (typeof payload.attemptId === "string") return null;
  const jobId = payload.jobId as string;
  const prUrl = (payload.prUrl as string | null) ?? null;
  const resolution = (payload.resolution as string | null) ?? null;
  const mergeStatus = (payload.mergeStatus as string | null) ?? null;
  const modelDowngraded = (payload.modelDowngraded as boolean) ?? false;
  const requestedModel = (payload.requestedModel as string | null) ?? null;
  const actualModel = (payload.actualModel as string | null) ?? null;
  const existing = state.jobs[jobId];
  if (existing) {
    const finalPlan = finalizePlanSteps(state.plans[jobId], "done");
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

export function handleJobCompleted(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  if (typeof payload.attemptId === "string") return null;
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

export function handleJobFailed(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  if (typeof payload.attemptId === "string") return null;
  const jobId = payload.jobId as string;
  const reason = (payload.reason as string | null) ?? "Unknown error";
  const existing = state.jobs[jobId];
  if (existing) {
    const finalPlan = finalizePlanSteps(state.plans[jobId], "skipped");
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

export function handleJobArchived(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
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

export function handleJobResolved(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
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

export function handleJobTitleUpdated(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const title = (payload.title as string | null) ?? null;
  const branch = (payload.branch as string | null) ?? null;
  const description = (payload.description as string | null) ?? null;
  const existing = state.jobs[jobId];
  if (existing) {
    return {
      jobs: {
        ...state.jobs,
        [jobId]: {
          ...existing,
          ...(title && { title }),
          ...(branch && { branch }),
          ...(description && { description }),
        },
      },
    };
  }
  return null;
}

export function handleModelDowngraded(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  if (typeof payload.attemptId === "string") return null;
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

export function handleMergeCompleted(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
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

export function handleMergeConflict(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
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

// SSEHandler type assertion to keep signatures compatible with the lookup table
export const jobHandlers: Record<string, SSEHandler> = {
  "job.state_changed": handleJobStateChanged,
  "job.setup_progress": handleJobSetupProgress,
  "job.review": handleJobReview,
  "job.completed": handleJobCompleted,
  "job.failed": handleJobFailed,
  "job.archived": handleJobArchived,
  "job.resolved": handleJobResolved,
  "job.title_updated": handleJobTitleUpdated,
  "model.downgraded": handleModelDowngraded,
  "merge.completed": handleMergeCompleted,
  "merge.conflict": handleMergeConflict,
};
