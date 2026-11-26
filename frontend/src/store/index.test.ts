import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  useStore,
  selectJobs,
  selectConnectionStatus,
  selectApprovals,
  selectJobLogs,
  selectJobTranscript,
  selectTrackerOperationsForProject,
} from "./index";
import type { JobSummary, ApprovalRequest } from "./index";
import type { TrackerOperationResponse } from "../api/types";
import * as apiClient from "../api/client";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/repos/test",
    prompt: "Fix the bug",
    state: "running",
    sessionCount: 1,
    baseRef: "main",
    worktreePath: "/repos/test",
    branch: "fix/bug",
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    completedAt: null,
    prUrl: null,
    ...overrides,
  };
}

describe("AppStore", () => {
  beforeEach(() => {
    // Reset the store before each test
    useStore.setState({
      jobs: {},
      approvals: {},
      logs: {},
      transcript: {},
      connectionStatus: "disconnected",
    });
  });

    describe("tracker operation REST hydration", () => {
      function operation(overrides: Partial<TrackerOperationResponse> = {}): TrackerOperationResponse {
        return {
          id: "op-1", operationId: "op-1", sourceKey: "source-1",
          projectId: "project-1", projectRef: "project-1", jobId: "job-1", jobRef: "job-1",
          taskLinkId: null, taskLinkRef: null, trackerLinkId: "link-1", trackerLinkRef: "link-1",
          provider: "github", baseUrl: "https://github.com", externalRef: "org/project",
          credentialIdRef: "credential-1", ticketRef: "org/repo#1",
          action: "comment", targetKind: "comment", value: "Exact comment", normalizedValue: null, normalizedTarget: null,
          approvalId: null, approvalRef: null, approvalSnapshot: null,
          state: "awaiting_approval", version: 0, retryOfId: null, providerReceipt: null,
          errorCode: null, errorDetail: null, reconciliation: null,
          createdAt: "2026-11-11T12:00:00Z", updatedAt: "2026-11-11T12:00:00Z",
          dispatchStartedAt: null, completedAt: null, ...overrides,
        };
      }

      beforeEach(() => useStore.setState({ trackerOperations: {}, trackerOperationLoads: {} }));
      afterEach(() => vi.restoreAllMocks());

      it("does not let a delayed list overwrite a newer SSE state or erase another project", async () => {
        let finish!: (value: { items: TrackerOperationResponse[] }) => void;
        vi.spyOn(apiClient, "fetchTrackerOperations").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
        const pending = useStore.getState().loadTrackerOperations("project-1");
        useStore.getState().hydrateTrackerOperations([
          operation({ state: "applied", version: 5 }),
          operation({ id: "other", operationId: "other", projectRef: "project-2" }),
        ]);
        finish({ items: [operation({ state: "approved", version: 2 })] });
        await pending;
        expect(useStore.getState().trackerOperations["op-1"]?.state).toBe("applied");
        expect(useStore.getState().trackerOperations["other"]?.projectRef).toBe("project-2");
        expect(useStore.getState().trackerOperationLoads["project-1"]?.loaded).toBe(true);
      });

      it("retains history after a load error and rehydrates every tracked project on reconnect", async () => {
        const fetch = vi.spyOn(apiClient, "fetchTrackerOperations");
        fetch.mockResolvedValueOnce({ items: [operation()] });
        await useStore.getState().loadTrackerOperations("project-1");
        fetch.mockRejectedValueOnce(new Error("Unavailable"));
        await useStore.getState().loadTrackerOperations("project-1");
        expect(useStore.getState().trackerOperationLoads["project-1"]?.error).toBe("Unavailable");
        expect(useStore.getState().trackerOperations["op-1"]?.state).toBe("awaiting_approval");
        useStore.getState().hydrateTrackerOperations([operation({ id: "other", operationId: "other", projectRef: "project-2" })]);
        fetch.mockImplementation(async (projectRef) => ({ items: projectRef === "project-1"
          ? [operation({ state: "uncertain", version: 3 })]
          : [operation({ id: "other", operationId: "other", projectRef: "project-2", state: "failed", version: 2 })],
        }));
        await useStore.getState().refreshTrackerOperations();
        expect(fetch).toHaveBeenCalledWith("project-2");
        expect(useStore.getState().trackerOperations["op-1"]?.state).toBe("uncertain");
        expect(useStore.getState().trackerOperationLoads["project-1"]?.error).toBeNull();
        expect(useStore.getState().trackerOperations["other"]?.state).toBe("failed");
      });

      it("does not let an older request failure replace the latest load status", async () => {
        let rejectOld!: (reason: Error) => void;
        vi.spyOn(apiClient, "fetchTrackerOperations")
          .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; }))
          .mockResolvedValueOnce({ items: [operation({ state: "failed", version: 3 })] });
        const old = useStore.getState().loadTrackerOperations("project-1");
        await useStore.getState().loadTrackerOperations("project-1");
        rejectOld(new Error("Late failure"));
        await old;
        expect(useStore.getState().trackerOperationLoads["project-1"]).toMatchObject({ loaded: true, loading: false, error: null });
      });

      it("keeps project selectors stable and filters a mismatched project response", async () => {
        const selector = selectTrackerOperationsForProject("project-1");
        expect(selector(useStore.getState())).toBe(selector(useStore.getState()));
        vi.spyOn(apiClient, "fetchTrackerOperations").mockResolvedValue({
          items: [operation(), operation({ id: "other", operationId: "other", projectRef: "project-2" })],
        });
        await useStore.getState().loadTrackerOperations("project-1");
        expect(selector(useStore.getState()).map((item) => item.id)).toEqual(["op-1"]);
        expect(selector(useStore.getState())).toBe(selector(useStore.getState()));
        expect(useStore.getState().trackerOperations["other"]).toBeUndefined();
      });
    });

  describe("setConnectionStatus", () => {
    it("updates connection status", () => {
      useStore.getState().setConnectionStatus("connected");
      expect(selectConnectionStatus(useStore.getState())).toBe(
        "connected"
      );
    });
  });

  describe("applySnapshot", () => {
    it("replaces jobs and approvals", () => {
      const jobs = [makeJob({ id: "job-1", state: "waiting_for_approval" }), makeJob({ id: "job-2" })];
      const approvals: ApprovalRequest[] = [
        {
          id: "apr-1",
          jobId: "job-1",
          description: "Approve?",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
          notes: null,
        },
      ];

      useStore.getState().applySnapshot(jobs, approvals);
      const state = useStore.getState();

      expect(Object.keys(selectJobs(state))).toHaveLength(2);
      expect(Object.keys(selectApprovals(state))).toHaveLength(1);
    });

    it("drops approvals whose job is not in waiting_for_approval", () => {
      const jobs = [makeJob({ id: "job-1", state: "running" })];
      const approvals: ApprovalRequest[] = [
        {
          id: "apr-1",
          jobId: "job-1",
          description: "Stale approval",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
          notes: null,
        },
      ];

      useStore.getState().applySnapshot(jobs, approvals);
      const state = useStore.getState();

      expect(Object.keys(selectJobs(state))).toHaveLength(1);
      expect(Object.keys(selectApprovals(state))).toHaveLength(0);
    });
  });

  describe("dispatchSSEEvent", () => {
    it("handles job_state_changed for existing job", () => {
      useStore.setState({
        jobs: { "job-1": makeJob() },
      });

      useStore.getState().dispatchSSEEvent("job.state_changed", {
        jobId: "job-1",
        newState: "review",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(selectJobs(useStore.getState())["job-1"]!.state).toBe(
        "review"
      );
    });

    it("ignores job_state_changed for unknown job", () => {
      useStore.getState().dispatchSSEEvent("job.state_changed", {
        jobId: "job-999",
        newState: "review",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
    });

    it("handles log_line", () => {
      useStore.getState().dispatchSSEEvent("log", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        level: "info",
        message: "Hello world",
        context: null,
      });

      const logs = selectJobLogs("job-1")(useStore.getState());
      expect(logs).toHaveLength(1);
      expect(logs[0]!.message).toBe("Hello world");
    });

    it("appends log lines to existing logs", () => {
      useStore.getState().dispatchSSEEvent("log", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        level: "info",
        message: "first",
        context: null,
      });
      useStore.getState().dispatchSSEEvent("log", {
        jobId: "job-1",
        seq: 2,
        timestamp: "2025-01-01T00:00:01Z",
        level: "info",
        message: "second",
        context: null,
      });

      expect(selectJobLogs("job-1")(useStore.getState())).toHaveLength(2);
    });

    it("handles transcript_update", () => {
      useStore.getState().dispatchSSEEvent("message.assistant", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        kind: "message.assistant",
        content: "I fixed the bug",
      });

      const transcript = selectJobTranscript("job-1")(
        useStore.getState()
      );
      expect(transcript).toHaveLength(1);
      expect(transcript[0]!.content).toBe("I fixed the bug");
    });

    it("handles approval_requested", () => {
      useStore.getState().dispatchSSEEvent("permission.requested", {
        approvalId: "apr-1",
        jobId: "job-1",
        description: "Delete file?",
        proposedAction: "rm -rf",
        timestamp: "2025-01-01T00:00:00Z",
      });

      const approvals = selectApprovals(useStore.getState());
      expect(approvals["apr-1"]).toBeDefined();
      expect(approvals["apr-1"]!.description).toBe("Delete file?");
      expect(approvals["apr-1"]!.requestedAt).toBe("2025-01-01T00:00:00Z");
    });

    it("approval_requested falls back to now when timestamp missing", () => {
      useStore.getState().dispatchSSEEvent("permission.requested", {
        approvalId: "apr-2",
        jobId: "job-1",
        description: "No timestamp",
        proposedAction: null,
      });

      const approvals = selectApprovals(useStore.getState());
      expect(approvals["apr-2"]).toBeDefined();
      // requestedAt should be a valid ISO string, not undefined
      expect(approvals["apr-2"]!.requestedAt).toBeDefined();
      expect(new Date(approvals["apr-2"]!.requestedAt).getTime()).not.toBeNaN();
    });

    it("handles approval_resolved", () => {
      // Set up an existing approval
      useStore.setState({
        approvals: {
          "apr-1": {
            id: "apr-1",
            jobId: "job-1",
            description: "Delete file?",
            proposedAction: null,
            requestedAt: "2025-01-01T00:00:00Z",
            resolvedAt: null,
            resolution: null,
            requiresExplicitApproval: false,
            notes: null,
          },
        },
      });

      useStore.getState().dispatchSSEEvent("permission.resolved", {
        approvalId: "apr-1",
        resolution: "approved",
        timestamp: "2025-01-01T01:00:00Z",
      });

      const approval = selectApprovals(useStore.getState())["apr-1"]!;
      expect(approval.resolution).toBe("approved");
      expect(approval.resolvedAt).toBe("2025-01-01T01:00:00Z");
    });

    it("ignores approval_resolved for unknown approval", () => {
      useStore.getState().dispatchSSEEvent("permission.resolved", {
        approvalId: "apr-999",
        resolution: "approved",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(
        Object.keys(selectApprovals(useStore.getState()))
      ).toHaveLength(0);
    });

    it("handles snapshot", () => {
      useStore.getState().dispatchSSEEvent("snapshot", {
        jobs: [makeJob({ id: "job-1" }), makeJob({ id: "job-2" })],
        pendingApprovals: [],
      });

      expect(
        Object.keys(selectJobs(useStore.getState()))
      ).toHaveLength(2);
    });

    it("handles session_heartbeat sets connected", () => {
      expect(selectConnectionStatus(useStore.getState())).toBe(
        "disconnected"
      );

      useStore.getState().dispatchSSEEvent("session.heartbeat", {
        jobId: "job-1",
        sessionId: "sess-1",
        timestamp: "2025-01-01T00:00:00Z",
      });

      expect(selectConnectionStatus(useStore.getState())).toBe(
        "connected"
      );
    });

    it("session_heartbeat is no-op when already connected", () => {
      useStore.getState().setConnectionStatus("connected");

      useStore.getState().dispatchSSEEvent("session.heartbeat", {
        jobId: "job-1",
        sessionId: "sess-1",
        timestamp: "2025-01-01T00:00:00Z",
      });

      expect(selectConnectionStatus(useStore.getState())).toBe(
        "connected"
      );
    });

    it("handles diff_update without error", () => {
      const beforeState = useStore.getState();
      useStore.getState().dispatchSSEEvent("diff.updated", {
        jobId: "job-1",
        changedFiles: ["src/app.ts"],
      });
      const afterState = useStore.getState();

      // diff_update is a no-op placeholder for now
      expect(selectJobs(afterState)).toEqual(selectJobs(beforeState));
    });

    it("ignores unknown event types", () => {
      const beforeState = useStore.getState();
      useStore.getState().dispatchSSEEvent("unknown_event", {});
      const afterState = useStore.getState();

      expect(selectJobs(afterState)).toEqual(selectJobs(beforeState));
    });

    it("session_resumed clears all stale badge fields", () => {
      // Set up a job with all badge fields populated (e.g. after succeed + discard + archive + model downgrade)
      useStore.setState({
        jobs: {
          "job-1": makeJob({
            state: "review",
            resolution: "discarded",
            conflictFiles: ["src/app.ts"],
            failureReason: "old failure",
            archivedAt: "2025-06-01T00:00:00Z",
            modelDowngraded: true,
            requestedModel: "gpt-4",
            actualModel: "gpt-3.5",
            prUrl: "https://github.com/org/repo/pull/1",
            mergeStatus: "not_merged",
            completedAt: "2025-06-01T00:00:00Z",
          }),
        },
      });

      useStore.getState().dispatchSSEEvent("session.resumed", {
        jobId: "job-1",
        timestamp: "2025-06-02T00:00:00Z",
        session_number: 2,
      });

      const job = selectJobs(useStore.getState())["job-1"]!;
      expect(job.state).toBe("running");
      expect(job.resolution).toBeNull();
      expect(job.conflictFiles).toBeNull();
      expect(job.failureReason).toBeNull();
      expect(job.archivedAt).toBeNull();
      expect(job.modelDowngraded).toBe(false);
      expect(job.requestedModel).toBeNull();
      expect(job.actualModel).toBeNull();
      expect(job.prUrl).toBeNull();
      expect(job.mergeStatus).toBeNull();
      expect(job.completedAt).toBeNull();
    });

    it("session_resumed dedup path also clears all badge fields", () => {
      // Pre-populate transcript with matching divider to trigger dedup path
      useStore.setState({
        jobs: {
          "job-1": makeJob({
            state: "review",
            resolution: "conflict",
            conflictFiles: ["a.ts"],
            modelDowngraded: true,
            requestedModel: "gpt-4",
            actualModel: "gpt-3.5",
          }),
        },
        transcript: {
          "job-1": [
            {
              jobId: "job-1",
              timestamp: "2025-06-02T00:00:00Z",
              kind: "divider",
              content: "Session",
            },
          ],
        },
      });

      // Same timestamp triggers dedup branch
      useStore.getState().dispatchSSEEvent("session.resumed", {
        jobId: "job-1",
        timestamp: "2025-06-02T00:00:00Z",
        session_number: 2,
      });

      const job = selectJobs(useStore.getState())["job-1"]!;
      expect(job.state).toBe("running");
      expect(job.resolution).toBeNull();
      expect(job.conflictFiles).toBeNull();
      expect(job.modelDowngraded).toBe(false);
    });
  });

  describe("selectors", () => {
    it("selectJobLogs returns empty array for unknown job", () => {
      expect(selectJobLogs("unknown")(useStore.getState())).toEqual([]);
    });

    it("selectJobTranscript returns empty array for unknown job", () => {
      expect(
        selectJobTranscript("unknown")(useStore.getState())
      ).toEqual([]);
    });
  });
});
