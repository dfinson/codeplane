/**
 * Approval SSE event handlers.
 */

import type { ApprovalRequest, BatchApproval } from "../types";
import type { SSEHandler, AppState } from "./types";

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

export function handleApprovalRequested(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  if (payload.batchManaged === true) return null;
  const approval: ApprovalRequest = {
    id: payload.approvalId as string,
    jobId: payload.jobId as string,
    description: payload.description as string,
    proposedAction: (payload.proposedAction as string | null) ?? null,
    requestedAt: (payload.timestamp as string) ?? new Date().toISOString(),
    resolvedAt: null,
    resolution: null,
    requiresExplicitApproval: (payload.requiresExplicitApproval as boolean) ?? false,
    notes: null,
  };
  return {
    approvals: { ...state.approvals, [approval.id]: approval },
  };
}

export function handleApprovalResolved(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
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

export function handleBatchApprovalRequested(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const actions = (payload.actions as Array<Record<string, unknown>>) ?? [];
  const batch: BatchApproval = {
    batchId: payload.batchId as string,
    jobId: payload.jobId as string,
    actions: actions.map((a) => ({
      id: a.id as string,
      kind: a.kind as string,
      recommendedAction: a.recommendedAction as BatchApproval["actions"][number]["recommendedAction"],
      reasonCode: a.reasonCode as string,
      reason: a.reason as string,
      riskScore: typeof a.riskScore === "number" ? a.riskScore : null,
      riskBand: a.riskBand as string,
      effect: (a.effect as string | null) ?? null,
      checkpointRef: (a.checkpointRef as string | null) ?? null,
      description: a.description as string,
    })),
    summary: (payload.summary as string) ?? "",
    requestedAt: (payload.timestamp as string) ?? new Date().toISOString(),
    resolvedAt: null,
    resolution: null,
  };
  return {
    batchApprovals: { ...state.batchApprovals, [batch.batchId]: batch },
  };
}

export function handleBatchApprovalResolved(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const batchId = payload.batchId as string;
  const existing = state.batchApprovals[batchId];
  if (existing) {
    return {
      batchApprovals: {
        ...state.batchApprovals,
        [batchId]: {
          ...existing,
          resolution: payload.resolution as string,
          resolvedAt: (payload.timestamp as string) ?? new Date().toISOString(),
        },
      },
    };
  }
  return null;
}

export const approvalHandlers: Record<string, SSEHandler> = {
  "permission.requested": handleApprovalRequested,
  "permission.resolved": handleApprovalResolved,
  "permission.batch.requested": handleBatchApprovalRequested,
  "permission.batch.resolved": handleBatchApprovalResolved,
};
