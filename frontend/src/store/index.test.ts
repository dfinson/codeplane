import { describe, it, expect, beforeEach } from "vitest";
import {
  useTowerStore,
  selectJobs,
  selectConnectionStatus,
  selectApprovals,
  selectJobLogs,
  selectJobTranscript,
} from "./index";
import type { JobSummary, ApprovalRequest } from "./index";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/repos/test",
    prompt: "Fix the bug",
    state: "running",
    strategy: "single_agent",
    baseRef: "main",
    worktreePath: "/repos/test",
    branch: "fix/bug",
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    completedAt: null,
    ...overrides,
  };
}

describe("TowerStore", () => {
  beforeEach(() => {
    // Reset the store before each test
    useTowerStore.setState({
      jobs: {},
      approvals: {},
      logs: {},
      transcript: {},
      connectionStatus: "disconnected",
    });
  });

  describe("setConnectionStatus", () => {
    it("updates connection status", () => {
      useTowerStore.getState().setConnectionStatus("connected");
      expect(selectConnectionStatus(useTowerStore.getState())).toBe(
        "connected"
      );
    });
  });

  describe("applySnapshot", () => {
    it("replaces jobs and approvals", () => {
      const jobs = [makeJob({ id: "job-1" }), makeJob({ id: "job-2" })];
      const approvals: ApprovalRequest[] = [
        {
          id: "apr-1",
          jobId: "job-1",
          description: "Approve?",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
        },
      ];

      useTowerStore.getState().applySnapshot(jobs, approvals);
      const state = useTowerStore.getState();

      expect(Object.keys(selectJobs(state))).toHaveLength(2);
      expect(Object.keys(selectApprovals(state))).toHaveLength(1);
    });
  });

  describe("dispatchSSEEvent", () => {
    it("handles job_state_changed for existing job", () => {
      useTowerStore.setState({
        jobs: { "job-1": makeJob() },
      });

      useTowerStore.getState().dispatchSSEEvent("job_state_changed", {
        jobId: "job-1",
        newState: "succeeded",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(selectJobs(useTowerStore.getState())["job-1"].state).toBe(
        "succeeded"
      );
    });

    it("ignores job_state_changed for unknown job", () => {
      useTowerStore.getState().dispatchSSEEvent("job_state_changed", {
        jobId: "job-999",
        newState: "succeeded",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(Object.keys(selectJobs(useTowerStore.getState()))).toHaveLength(0);
    });

    it("handles log_line", () => {
      useTowerStore.getState().dispatchSSEEvent("log_line", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        level: "info",
        message: "Hello world",
        context: null,
      });

      const logs = selectJobLogs("job-1")(useTowerStore.getState());
      expect(logs).toHaveLength(1);
      expect(logs[0].message).toBe("Hello world");
    });

    it("appends log lines to existing logs", () => {
      useTowerStore.getState().dispatchSSEEvent("log_line", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        level: "info",
        message: "first",
        context: null,
      });
      useTowerStore.getState().dispatchSSEEvent("log_line", {
        jobId: "job-1",
        seq: 2,
        timestamp: "2025-01-01T00:00:01Z",
        level: "info",
        message: "second",
        context: null,
      });

      expect(selectJobLogs("job-1")(useTowerStore.getState())).toHaveLength(2);
    });

    it("handles transcript_update", () => {
      useTowerStore.getState().dispatchSSEEvent("transcript_update", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        role: "agent",
        content: "I fixed the bug",
      });

      const transcript = selectJobTranscript("job-1")(
        useTowerStore.getState()
      );
      expect(transcript).toHaveLength(1);
      expect(transcript[0].content).toBe("I fixed the bug");
    });

    it("handles approval_requested", () => {
      useTowerStore.getState().dispatchSSEEvent("approval_requested", {
        approvalId: "apr-1",
        jobId: "job-1",
        description: "Delete file?",
        proposedAction: "rm -rf",
        timestamp: "2025-01-01T00:00:00Z",
      });

      const approvals = selectApprovals(useTowerStore.getState());
      expect(approvals["apr-1"]).toBeDefined();
      expect(approvals["apr-1"].description).toBe("Delete file?");
    });

    it("handles approval_resolved", () => {
      // Set up an existing approval
      useTowerStore.setState({
        approvals: {
          "apr-1": {
            id: "apr-1",
            jobId: "job-1",
            description: "Delete file?",
            proposedAction: null,
            requestedAt: "2025-01-01T00:00:00Z",
            resolvedAt: null,
            resolution: null,
          },
        },
      });

      useTowerStore.getState().dispatchSSEEvent("approval_resolved", {
        approvalId: "apr-1",
        resolution: "approved",
        timestamp: "2025-01-01T01:00:00Z",
      });

      const approval = selectApprovals(useTowerStore.getState())["apr-1"];
      expect(approval.resolution).toBe("approved");
      expect(approval.resolvedAt).toBe("2025-01-01T01:00:00Z");
    });

    it("ignores approval_resolved for unknown approval", () => {
      useTowerStore.getState().dispatchSSEEvent("approval_resolved", {
        approvalId: "apr-999",
        resolution: "approved",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(
        Object.keys(selectApprovals(useTowerStore.getState()))
      ).toHaveLength(0);
    });

    it("handles snapshot", () => {
      useTowerStore.getState().dispatchSSEEvent("snapshot", {
        jobs: [makeJob({ id: "job-1" }), makeJob({ id: "job-2" })],
        pendingApprovals: [],
      });

      expect(
        Object.keys(selectJobs(useTowerStore.getState()))
      ).toHaveLength(2);
    });

    it("ignores unknown event types", () => {
      const beforeState = useTowerStore.getState();
      useTowerStore.getState().dispatchSSEEvent("unknown_event", {});
      const afterState = useTowerStore.getState();

      expect(selectJobs(afterState)).toEqual(selectJobs(beforeState));
    });
  });

  describe("selectors", () => {
    it("selectJobLogs returns empty array for unknown job", () => {
      expect(selectJobLogs("unknown")(useTowerStore.getState())).toEqual([]);
    });

    it("selectJobTranscript returns empty array for unknown job", () => {
      expect(
        selectJobTranscript("unknown")(useTowerStore.getState())
      ).toEqual([]);
    });
  });
});
