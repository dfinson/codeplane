import { describe, it, expect, beforeEach } from "vitest";
import {
  useStore,
  selectJobs,
  selectJobLogs,
  selectJobTranscript,
  selectJobDiffs,
  selectJobTimeline,
  selectJobPlan,
  selectActiveJobs,
  selectSignoffJobs,
  selectAttentionJobs,
  selectArchivedJobs,
  selectArchivedCount,
} from "../index";
import type { JobSummary } from "../index";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  useStore.setState({
    jobs: {},
    approvals: {},
    logs: {},
    transcript: {},
    diffs: {},
    timelines: {},
    plans: {},
    connectionStatus: "disconnected",
  });
});

// ---- Additional SSE event types -------------------------------------------

describe("dispatchSSEEvent — additional events", () => {
  it("handles job_succeeded with prUrl and resolution", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ progressHeadline: "Audit", progressSummary: "Reviewing shortcuts" }) } });
    useStore.getState().dispatchSSEEvent("job_succeeded", {
      jobId: "job-1",
      prUrl: "https://github.com/pr/1",
      resolution: "merged",
      mergeStatus: "merged",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.state).toBe("succeeded");
    expect(job.prUrl).toBe("https://github.com/pr/1");
    expect(job.resolution).toBe("merged");
    expect(job.failureReason).toBeNull();
    expect(job.progressHeadline).toBe("Audit");
    expect(job.progressSummary).toBe("Reviewing shortcuts");
  });

  it("handles job_succeeded with model downgrade", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("job_succeeded", {
      jobId: "job-1",
      modelDowngraded: true,
      requestedModel: "gpt-4",
      actualModel: "gpt-3.5",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.modelDowngraded).toBe(true);
    expect(job.requestedModel).toBe("gpt-4");
  });

  it("ignores job_succeeded for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job_succeeded", {
      jobId: "unknown",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles job_failed", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ progressHeadline: "Audit", progressSummary: "Reviewing shortcuts" }) } });
    useStore.getState().dispatchSSEEvent("job_failed", {
      jobId: "job-1",
      reason: "Timeout",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.state).toBe("failed");
    expect(job.failureReason).toBe("Timeout");
    expect(job.progressHeadline).toBe("Audit");
    expect(job.progressSummary).toBe("Reviewing shortcuts");
  });

  it("handles job_failed with default reason", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("job_failed", {
      jobId: "job-1",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.failureReason).toBe("Unknown error");
  });

  it("ignores job_failed for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job_failed", {
      jobId: "unknown",
      reason: "Oops",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles job_resolved", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "succeeded" }) } });
    useStore.getState().dispatchSSEEvent("job_resolved", {
      jobId: "job-1",
      resolution: "merged",
      prUrl: "https://github.com/pr/1",
      conflictFiles: null,
      timestamp: "2025-01-01T02:00:00Z",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.resolution).toBe("merged");
    expect(job.prUrl).toBe("https://github.com/pr/1");
  });

  it("handles job_resolved with conflict", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "succeeded" }) } });
    useStore.getState().dispatchSSEEvent("job_resolved", {
      jobId: "job-1",
      resolution: "conflict",
      conflictFiles: ["a.ts", "b.ts"],
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.conflictFiles).toEqual(["a.ts", "b.ts"]);
  });

  it("stores unresolved job_resolved errors", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "succeeded" }) } });
    useStore.getState().dispatchSSEEvent("job_resolved", {
      jobId: "job-1",
      resolution: "unresolved",
      error: "Cherry-pick failed without conflict markers; check git configuration or hooks",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.resolution).toBe("unresolved");
    expect(job.resolutionError).toBe("Cherry-pick failed without conflict markers; check git configuration or hooks");
  });

  it("ignores job_resolved for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job_resolved", {
      jobId: "unknown",
      resolution: "merged",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles job_archived", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "succeeded" }) } });
    useStore.getState().dispatchSSEEvent("job_archived", {
      jobId: "job-1",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.archivedAt).toBeDefined();
    expect(typeof job.archivedAt).toBe("string");
  });

  it("ignores job_archived for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job_archived", { jobId: "unknown" });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles diff_update and stores diffs", () => {
    const files = [{ path: "a.ts", status: "modified", additions: 1, deletions: 0, hunks: [] }];
    useStore.getState().dispatchSSEEvent("diff_update", {
      jobId: "job-1",
      changedFiles: files,
    });
    expect(selectJobDiffs("job-1")(useStore.getState())).toEqual(files);
  });

  it("handles job_title_updated", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("job_title_updated", {
      jobId: "job-1",
      title: "New Title",
      branch: "feat/new",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.title).toBe("New Title");
    expect(job.branch).toBe("feat/new");
  });

  it("ignores job_title_updated for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job_title_updated", {
      jobId: "unknown",
      title: "Title",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles progress_headline for existing job", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("progress_headline", {
      jobId: "job-1",
      headline: "Analyzing code",
      headlinePast: "Analyzed code",
      timestamp: "2025-01-01T00:01:00Z",
      summary: "Looking at files",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.progressHeadline).toBe("Analyzing code");
    expect(job.progressSummary).toBe("Looking at files");
    const timeline = selectJobTimeline("job-1")(useStore.getState());
    expect(timeline).toHaveLength(1);
    const firstEntry = timeline[0];
    expect(firstEntry).toBeDefined();
    expect(firstEntry?.active).toBe(true);
  });

  it("progress_headline deactivates previous entries", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("progress_headline", {
      jobId: "job-1",
      headline: "First",
      timestamp: "2025-01-01T00:01:00Z",
    });
    useStore.getState().dispatchSSEEvent("progress_headline", {
      jobId: "job-1",
      headline: "Second",
      timestamp: "2025-01-01T00:02:00Z",
    });
    const timeline = selectJobTimeline("job-1")(useStore.getState());
    expect(timeline).toHaveLength(2);
    const firstEntry = timeline[0];
    const secondEntry = timeline[1];
    expect(firstEntry).toBeDefined();
    expect(secondEntry).toBeDefined();
    expect(firstEntry?.active).toBe(false);
    expect(secondEntry?.active).toBe(true);
  });

  it("handles model_downgraded", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("model_downgraded", {
      jobId: "job-1",
      requestedModel: "gpt-4",
      actualModel: "gpt-3.5",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.modelDowngraded).toBe(true);
    expect(job.requestedModel).toBe("gpt-4");
    expect(job.actualModel).toBe("gpt-3.5");
  });

  it("ignores model_downgraded for unknown job", () => {
    useStore.getState().dispatchSSEEvent("model_downgraded", {
      jobId: "unknown",
      requestedModel: "gpt-4",
      actualModel: "gpt-3.5",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles agent_plan_updated", () => {
    useStore.getState().dispatchSSEEvent("agent_plan_updated", {
      jobId: "job-1",
      steps: [
        { label: "Analyze", status: "done" },
        { label: "Implement", status: "active" },
        { label: "Test", status: "pending" },
      ],
    });
    const plan = selectJobPlan("job-1")(useStore.getState());
    expect(plan).toHaveLength(3);
    const firstStep = plan[0];
    const secondStep = plan[1];
    expect(firstStep).toBeDefined();
    expect(secondStep).toBeDefined();
    expect(firstStep?.status).toBe("done");
    expect(secondStep?.status).toBe("active");
  });

  it("transcript_update deduplicates", () => {
    useStore.getState().dispatchSSEEvent("transcript_update", {
      jobId: "job-1",
      seq: 1,
      timestamp: "2025-01-01T00:00:00Z",
      role: "agent",
      content: "Hello",
    });
    useStore.getState().dispatchSSEEvent("transcript_update", {
      jobId: "job-1",
      seq: 1,
      timestamp: "2025-01-01T00:00:00Z",
      role: "agent",
      content: "Hello",
    });
    expect(selectJobTranscript("job-1")(useStore.getState())).toHaveLength(1);
  });
});

// ---- Column selectors -----------------------------------------------------

describe("column selectors", () => {
  it("selectActiveJobs returns queued and running, not archived", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "queued" }),
        "j-2": makeJob({ id: "j-2", state: "running" }),
        "j-3": makeJob({ id: "j-3", state: "succeeded" }),
        "j-4": makeJob({ id: "j-4", state: "running", archivedAt: "2025-01-01" }),
      },
    });
    const active = selectActiveJobs(useStore.getState());
    expect(active.map((j) => j.id).sort()).toEqual(["j-1", "j-2"]);
  });

  it("selectSignoffJobs returns waiting_for_approval and succeeded, not canceled", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "waiting_for_approval" }),
        "j-2": makeJob({ id: "j-2", state: "succeeded" }),
        "j-3": makeJob({ id: "j-3", state: "canceled" }),
        "j-4": makeJob({ id: "j-4", state: "running" }),
        "j-5": makeJob({ id: "j-5", state: "succeeded", archivedAt: "2025-01-01" }),
      },
    });
    const signoff = selectSignoffJobs(useStore.getState());
    expect(signoff.map((j) => j.id).sort()).toEqual(["j-1", "j-2"]);
  });

  it("selectAttentionJobs returns failed, not archived", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "failed" }),
        "j-2": makeJob({ id: "j-2", state: "failed", archivedAt: "2025-01-01" }),
        "j-3": makeJob({ id: "j-3", state: "running" }),
      },
    });
    const attention = selectAttentionJobs(useStore.getState());
    expect(attention.map((j) => j.id)).toEqual(["j-1"]);
  });

  it("selectArchivedJobs returns only archived", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "succeeded", archivedAt: "2025-01-01" }),
        "j-2": makeJob({ id: "j-2", state: "running" }),
      },
    });
    const archived = selectArchivedJobs(useStore.getState());
    expect(archived.map((j) => j.id)).toEqual(["j-1"]);
  });

  it("selectArchivedCount returns count of archived", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", archivedAt: "2025-01-01" }),
        "j-2": makeJob({ id: "j-2", archivedAt: "2025-02-01" }),
        "j-3": makeJob({ id: "j-3" }),
      },
    });
    expect(selectArchivedCount(useStore.getState())).toBe(2);
  });

  it("selectActiveJobs sorted by updatedAt descending", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "running", updatedAt: "2025-01-01T00:00:00Z" }),
        "j-2": makeJob({ id: "j-2", state: "running", updatedAt: "2025-01-02T00:00:00Z" }),
      },
    });
    const active = selectActiveJobs(useStore.getState());
    const firstJob = active[0];
    const secondJob = active[1];
    expect(firstJob).toBeDefined();
    expect(secondJob).toBeDefined();
    expect(firstJob?.id).toBe("j-2");
    expect(secondJob?.id).toBe("j-1");
  });
});

// ---- Empty selector sentinels ---------------------------------------------

describe("selector sentinels", () => {
  it("selectJobLogs returns stable empty array", () => {
    const a = selectJobLogs("unknown")(useStore.getState());
    const b = selectJobLogs("unknown")(useStore.getState());
    expect(a).toBe(b); // Same reference
    expect(a).toEqual([]);
  });

  it("selectJobTranscript returns stable empty array", () => {
    const a = selectJobTranscript("unknown")(useStore.getState());
    const b = selectJobTranscript("unknown")(useStore.getState());
    expect(a).toBe(b);
  });

  it("selectJobDiffs returns stable empty array", () => {
    const a = selectJobDiffs("unknown")(useStore.getState());
    const b = selectJobDiffs("unknown")(useStore.getState());
    expect(a).toBe(b);
  });

  it("selectJobTimeline returns stable empty array", () => {
    const a = selectJobTimeline("unknown")(useStore.getState());
    const b = selectJobTimeline("unknown")(useStore.getState());
    expect(a).toBe(b);
  });

  it("selectJobPlan returns stable empty array", () => {
    const a = selectJobPlan("unknown")(useStore.getState());
    const b = selectJobPlan("unknown")(useStore.getState());
    expect(a).toBe(b);
  });
});
