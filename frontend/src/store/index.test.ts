import { describe, it, expect, beforeEach } from "vitest";
import {
  useStore,
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

      useStore.getState().applySnapshot(jobs, approvals);
      const state = useStore.getState();

      expect(Object.keys(selectJobs(state))).toHaveLength(2);
      expect(Object.keys(selectApprovals(state))).toHaveLength(1);
    });
  });

  describe("dispatchSSEEvent", () => {
    it("handles job_state_changed for existing job", () => {
      useStore.setState({
        jobs: { "job-1": makeJob() },
      });

      useStore.getState().dispatchSSEEvent("job_state_changed", {
        jobId: "job-1",
        newState: "succeeded",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(selectJobs(useStore.getState())["job-1"]!.state).toBe(
        "succeeded"
      );
    });

    it("ignores job_state_changed for unknown job", () => {
      useStore.getState().dispatchSSEEvent("job_state_changed", {
        jobId: "job-999",
        newState: "succeeded",
        timestamp: "2025-01-01T01:00:00Z",
      });

      expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
    });

    it("handles log_line", () => {
      useStore.getState().dispatchSSEEvent("log_line", {
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
      useStore.getState().dispatchSSEEvent("log_line", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        level: "info",
        message: "first",
        context: null,
      });
      useStore.getState().dispatchSSEEvent("log_line", {
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
      useStore.getState().dispatchSSEEvent("transcript_update", {
        jobId: "job-1",
        seq: 1,
        timestamp: "2025-01-01T00:00:00Z",
        role: "agent",
        content: "I fixed the bug",
      });

      const transcript = selectJobTranscript("job-1")(
        useStore.getState()
      );
      expect(transcript).toHaveLength(1);
      expect(transcript[0]!.content).toBe("I fixed the bug");
    });

    it("handles approval_requested", () => {
      useStore.getState().dispatchSSEEvent("approval_requested", {
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
      useStore.getState().dispatchSSEEvent("approval_requested", {
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
          },
        },
      });

      useStore.getState().dispatchSSEEvent("approval_resolved", {
        approvalId: "apr-1",
        resolution: "approved",
        timestamp: "2025-01-01T01:00:00Z",
      });

      const approval = selectApprovals(useStore.getState())["apr-1"]!;
      expect(approval.resolution).toBe("approved");
      expect(approval.resolvedAt).toBe("2025-01-01T01:00:00Z");
    });

    it("ignores approval_resolved for unknown approval", () => {
      useStore.getState().dispatchSSEEvent("approval_resolved", {
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

      useStore.getState().dispatchSSEEvent("session_heartbeat", {
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

      useStore.getState().dispatchSSEEvent("session_heartbeat", {
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
      useStore.getState().dispatchSSEEvent("diff_update", {
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
            state: "succeeded",
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

      useStore.getState().dispatchSSEEvent("session_resumed", {
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
            state: "succeeded",
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
              seq: -99,
              timestamp: "2025-06-02T00:00:00Z",
              role: "divider",
              content: "Session",
            },
          ],
        },
      });

      // Same timestamp triggers dedup branch
      useStore.getState().dispatchSSEEvent("session_resumed", {
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
