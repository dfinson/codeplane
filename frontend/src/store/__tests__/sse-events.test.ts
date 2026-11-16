import { describe, it, expect, beforeEach } from "vitest";
import {
  useStore,
  selectJobs,
  selectApprovals,
  selectJobLogs,
  selectJobTranscript,
  selectJobDiffs,
  selectJobTimeline,
  selectJobPlan,
  selectActivityTimeline,
  selectStreamingToolOutput,
  selectStreamingReasoning,
  selectSecondarySessions,
  selectContextHandoffs,
  selectActiveJobs,
  selectSignoffJobs,
  selectAttentionJobs,
  selectActiveJobsForRepo,
  selectSignoffJobsForRepo,
  selectAttentionJobsForRepo,
  selectArchivedJobs,
  selectArchivedCount,
} from "../index";
import type { DiffFileModel, TrackerOperationResponse } from "../../api/types";
import { normalizeTFEvent } from "../sseNormalize";
import type {
  AppState,
  ActivityTimelineState,
  ApprovalRequest,
  ContextHandoff,
  JobSummary,
  LogLine,
  PlanStep,
  SecondarySession,
  TimelineEntry,
  TranscriptEntry,
} from "../index";

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

type HydratedSnapshot = Parameters<AppState["hydrateJob"]>[0];

it.each([
  "job.failed", "job.completed", "job.review", "job.state_changed",
  "permission.requested", "permission.resolved", "model.downgraded",
])("keeps %s from an attributed attempt out of Job-level state", (kind) => {
  const job = makeJob({ model: "chosen-model" });
  const sibling: ApprovalRequest = {
    id: "sibling", jobId: job.id, description: "Other pending decision", proposedAction: null,
    requestedAt: "2026-01-01T00:00:00Z", resolvedAt: null, resolution: null,
    requiresExplicitApproval: true, notes: null,
  };
  useStore.setState({ jobs: { [job.id]: job }, approvals: { sibling } });
  useStore.getState().dispatchSSEEvent(kind, normalizeTFEvent({
    id: `scoped-${kind}`, kind, session_id: job.id, timestamp: "2026-01-01T00:00:00Z",
    payload: {
      reason: "Attempt outcome", new_state: "failed", resolution: "rejected",
      approval_id: "attempt-gate", description: "Scoped decision",
      requested_model: "chosen-model", actual_model: "reported-model",
    },
    metadata: { attempt_id: "attempt", recipe_run_id: "run", execution_epoch: "epoch" },
  }));
  expect(useStore.getState().jobs[job.id]).toBe(job);
  expect(useStore.getState().approvals.sibling).toBe(sibling);
});

it("retains identical prompt text from two authoritative sibling attempts", () => {
  for (const attempt of ["first", "second"]) {
    useStore.getState().dispatchSSEEvent("message.user", normalizeTFEvent({
      id: `event-${attempt}`, kind: "message.user", session_id: "job-1",
      payload: { content: "Identical prompt" },
      metadata: { attempt_id: attempt, run_id: `session-${attempt}`, execution_epoch: "epoch" },
    }));
  }
  expect(useStore.getState().transcript["job-1"]?.map((entry) => entry.eventId)).toEqual([
    "event-first", "event-second",
  ]);
});

function makeHydratedSnapshot(job: JobSummary, overrides: Partial<HydratedSnapshot> = {}): HydratedSnapshot {
  return {
    job,
    logs: [],
    transcript: [],
    diff: [],
    approvals: [],
    timeline: [],
    ...overrides,
  };
}

function makeCurrentJobSlice(jobId: string) {
  const approvals: Record<string, ApprovalRequest> = {
    "approval-current": {
      id: "approval-current",
      jobId,
      description: "Current approval",
      proposedAction: null,
      requestedAt: "2025-01-01T00:00:00Z",
      resolvedAt: null,
      resolution: null,
      requiresExplicitApproval: false,
      notes: null,
    },
  };
  const logs: LogLine[] = [
    {
      jobId,
      seq: 1,
      timestamp: "2025-01-01T00:00:00Z",
      level: "info",
      message: "Current log",
      context: null,
    },
  ];
  const transcript: TranscriptEntry[] = [
    {
      jobId,
      timestamp: "2025-01-01T00:00:00Z",
      kind: "message.assistant",
      content: "Current transcript",
    },
  ];
  const diffs: DiffFileModel[] = [
    { path: "current.ts", status: "modified", additions: 1, deletions: 0, hunks: [] },
  ];
  const plans: PlanStep[] = [
    {
      planStepId: "plan-current",
      label: "Current plan",
      status: "active",
      summary: "Keep this plan",
    },
  ];
  const timelines: TimelineEntry[] = [
    {
      headline: "Current headline",
      headlinePast: "Current headline",
      summary: "Current summary",
      timestamp: "2025-01-01T00:00:00Z",
      active: true,
    },
  ];
  const activityTimelines: Record<string, ActivityTimelineState> = {
    [jobId]: {
      activities: [
        {
          activityId: "activity-current",
          label: "Current activity",
          status: "active",
          steps: [
            {
              turnId: "turn-current",
              title: "Current turn",
              activityId: "activity-current",
            },
          ],
        },
      ],
    },
  };
  const secondarySessions: Record<string, Record<string, SecondarySession>> = {
    [jobId]: {
      "secondary-current": {
        id: "secondary-current",
        jobId,
        kind: "sidecar",
        name: "Current secondary",
        icon: "box",
        status: "running",
        startedAt: "2025-01-01T00:00:00Z",
        entries: [],
      },
    },
  };
  const contextHandoffs: Record<string, ContextHandoff[]> = {
    [jobId]: [
      {
        jobId,
        source: "resume",
        sourceSessionId: "session-current",
        summary: "Current handoff",
        content: "Current content",
        timestamp: "2025-01-01T00:00:00Z",
      },
    ],
  };

  return {
    jobs: {
      [jobId]: makeJob({
        id: jobId,
        state: "review",
        updatedAt: "2025-01-01T00:00:20Z",
      }),
    },
    approvals,
    logs: { [jobId]: logs },
    transcript: { [jobId]: transcript },
    diffs: { [jobId]: diffs },
    plans: { [jobId]: plans },
    timelines: { [jobId]: timelines },
    activityTimelines,
    secondarySessions,
    streamingMessages: { [`${jobId}:turn-current`]: "Current streaming message" },
    streamingToolOutput: { [`${jobId}:tool-current`]: "Current streaming tool output" },
    streamingReasoning: { [`${jobId}:turn-current`]: "Current streaming reasoning" },
    contextHandoffs,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  useStore.setState({
    trackerOperations: {},
    trackerOperationLoads: {},
    jobs: {},
    approvals: {},
    logs: {},
    transcript: {},
    diffs: {},
    plans: {},
    timelines: {},
    activityTimelines: {},
    secondarySessions: {},
    streamingMessages: {},
    streamingToolOutput: {},
    streamingReasoning: {},
    contextHandoffs: {},
    telemetryVersions: {},
    artifactVersions: {},
    jobHeartbeats: {},
    terminalSessions: {},
    connectionStatus: "disconnected",
  });
});

describe("hydrateJob freshness guard", () => {
  it("preserves the current job slice when an older reconnect snapshot arrives", () => {
    const jobId = "job-1";
    useStore.setState(makeCurrentJobSlice(jobId));

    useStore.getState().hydrateJob(
      makeHydratedSnapshot(
        makeJob({
          id: jobId,
          state: "running",
          updatedAt: "2025-01-01T00:00:10Z",
        }),
        {
          logs: [
            {
              jobId,
              seq: 9,
              timestamp: "2025-01-01T00:00:10Z",
              level: "warn",
              message: "Stale log",
              context: null,
            },
          ],
          transcript: [
            {
              jobId,
              timestamp: "2025-01-01T00:00:10Z",
              kind: "message.assistant",
              content: "Stale transcript",
            },
          ],
          diff: [{ path: "stale.ts", status: "modified", additions: 9, deletions: 0, hunks: [] }],
          approvals: [
            {
              id: "approval-stale",
              jobId,
              description: "Stale approval",
              proposedAction: null,
              requestedAt: "2025-01-01T00:00:10Z",
              resolvedAt: null,
              resolution: null,
              requiresExplicitApproval: false,
              notes: null,
            },
          ],
          timeline: [
            {
              headline: "Stale headline",
              headlinePast: "Stale headline",
              summary: "Stale summary",
              timestamp: "2025-01-01T00:00:10Z",
              active: false,
            },
          ],
          steps: [
            {
              planStepId: "plan-stale",
              label: "Stale plan",
              status: "pending",
              summary: "Replace this plan",
            },
          ],
          turnSummaries: [
            {
              turnId: "turn-stale",
              title: "Stale turn",
              activityId: "activity-stale",
              activityLabel: "Stale activity",
              isNewActivity: true,
              activityStatus: "done",
            },
          ],
          secondarySessions: [
            {
              id: "secondary-stale",
              kind: "sidecar",
              name: "Stale secondary",
              icon: "circle",
              status: "completed",
              startedAt: "2025-01-01T00:00:10Z",
              completedAt: "2025-01-01T00:00:11Z",
              output: "stale output",
              entries: [],
            },
          ],
          contextHandoffs: [
            {
              source: "followup",
              sourceSessionId: "session-stale",
              summary: "Stale handoff",
              content: "stale content",
              timestamp: "2025-01-01T00:00:10Z",
            },
          ],
        },
      ),
    );

    const state = useStore.getState();
    expect(selectJobs(state)[jobId]?.state).toBe("review");
    expect(selectJobs(state)[jobId]?.updatedAt).toBe("2025-01-01T00:00:20Z");
    expect(selectApprovals(state)).toEqual(makeCurrentJobSlice(jobId).approvals);
    expect(selectJobLogs(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).logs[jobId]);
    expect(selectJobTranscript(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).transcript[jobId]);
    expect(selectJobDiffs(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).diffs[jobId]);
    expect(selectJobTimeline(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).timelines[jobId]);
    expect(selectJobPlan(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).plans[jobId]);
    expect(selectActivityTimeline(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).activityTimelines[jobId]);
    expect(selectStreamingToolOutput(jobId)(state)).toEqual({ "tool-current": "Current streaming tool output" });
    expect(selectStreamingReasoning(jobId)(state)).toEqual({ "turn-current": "Current streaming reasoning" });
    expect(selectSecondarySessions(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).secondarySessions[jobId]);
    expect(selectContextHandoffs(jobId)(state)).toEqual(makeCurrentJobSlice(jobId).contextHandoffs[jobId]);
    expect(state.streamingMessages[`${jobId}:turn-current`]).toBe("Current streaming message");
  });

  it("accepts a newer resume snapshot that returns a terminal job to running", () => {
    const jobId = "job-1";
    useStore.setState({
      jobs: {
        [jobId]: makeJob({
          id: jobId,
          state: "completed",
          updatedAt: "2025-01-01T00:00:10Z",
        }),
      },
    });

    useStore.getState().hydrateJob(
      makeHydratedSnapshot(
        makeJob({
          id: jobId,
          state: "running",
          updatedAt: "2025-01-01T00:00:20Z",
        }),
        {
          logs: [],
          transcript: [],
          diff: [],
          approvals: [],
          timeline: [],
        },
      ),
    );

    const job = selectJobs(useStore.getState())[jobId]!;
    expect(job.state).toBe("running");
    expect(job.updatedAt).toBe("2025-01-01T00:00:20Z");
  });

  it("treats tied timestamps as fresh when no version evidence exists", () => {
    const jobId = "job-1";
    useStore.setState({
      jobs: {
        [jobId]: makeJob({
          id: jobId,
          state: "review",
          updatedAt: "2025-01-01T00:00:00Z",
        }),
      },
    });

    useStore.getState().hydrateJob(
      makeHydratedSnapshot(
        makeJob({
          id: jobId,
          state: "running",
          updatedAt: "2025-01-01T00:00:00Z",
        }),
        {
          transcript: [
            {
              jobId,
              timestamp: "2025-01-01T00:00:00Z",
              kind: "message.assistant",
              content: "Tie snapshot transcript",
            },
          ],
          logs: [],
          diff: [],
          approvals: [],
          timeline: [],
        },
      ),
    );

    const state = useStore.getState();
    expect(selectJobs(state)[jobId]?.state).toBe("running");
    expect(selectJobTranscript(jobId)(state)).toEqual([
      {
        jobId,
        timestamp: "2025-01-01T00:00:00Z",
        kind: "message.assistant",
        content: "Tie snapshot transcript",
      },
    ]);
  });

  it("treats missing timestamps as fresh when no version evidence exists", () => {
    const jobId = "job-1";
    useStore.setState({
      jobs: {
        [jobId]: makeJob({
          id: jobId,
          state: "review",
          updatedAt: "",
        }),
      },
    });

    useStore.getState().hydrateJob(
      makeHydratedSnapshot(
        makeJob({
          id: jobId,
          state: "running",
          updatedAt: "",
        }),
        {
          logs: [],
          transcript: [
            {
              jobId,
              timestamp: "",
              kind: "message.assistant",
              content: "Missing timestamp snapshot transcript",
            },
          ],
          diff: [],
          approvals: [],
          timeline: [],
        },
      ),
    );

    const state = useStore.getState();
    expect(selectJobs(state)[jobId]?.state).toBe("running");
    expect(selectJobTranscript(jobId)(state)).toEqual([
      {
        jobId,
        timestamp: "",
        kind: "message.assistant",
        content: "Missing timestamp snapshot transcript",
      },
    ]);
  });
});

// ---- Additional SSE event types -------------------------------------------

describe("dispatchSSEEvent — additional events", () => {
  it("handles job_review with prUrl and resolution", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ progressHeadline: "Audit", progressSummary: "Reviewing shortcuts" }) } });
    useStore.getState().dispatchSSEEvent("job.review", {
      jobId: "job-1",
      prUrl: "https://github.com/pr/1",
      resolution: "merged",
      mergeStatus: "merged",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.state).toBe("review");
    expect(job.prUrl).toBe("https://github.com/pr/1");
    expect(job.resolution).toBe("merged");
    expect(job.failureReason).toBeNull();
    expect(job.progressHeadline).toBe("Audit");
    expect(job.progressSummary).toBe("Reviewing shortcuts");
  });

  it("handles job_review with model downgrade", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("job.review", {
      jobId: "job-1",
      modelDowngraded: true,
      requestedModel: "gpt-4",
      actualModel: "gpt-3.5",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.modelDowngraded).toBe(true);
    expect(job.requestedModel).toBe("gpt-4");
  });

  it("ignores job_review for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job.review", {
      jobId: "unknown",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles job_failed", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ progressHeadline: "Audit", progressSummary: "Reviewing shortcuts" }) } });
    useStore.getState().dispatchSSEEvent("job.failed", {
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
    useStore.getState().dispatchSSEEvent("job.failed", {
      jobId: "job-1",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.failureReason).toBe("Unknown error");
  });

  it("ignores job_failed for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job.failed", {
      jobId: "unknown",
      reason: "Oops",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles job_resolved", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "review" }) } });
    useStore.getState().dispatchSSEEvent("job.resolved", {
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
    useStore.setState({ jobs: { "job-1": makeJob({ state: "review" }) } });
    useStore.getState().dispatchSSEEvent("job.resolved", {
      jobId: "job-1",
      resolution: "conflict",
      conflictFiles: ["a.ts", "b.ts"],
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.conflictFiles).toEqual(["a.ts", "b.ts"]);
  });

  it("stores unresolved job_resolved errors", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "review" }) } });
    useStore.getState().dispatchSSEEvent("job.resolved", {
      jobId: "job-1",
      resolution: "unresolved",
      error: "Cherry-pick failed without conflict markers; check git configuration or hooks",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.resolution).toBe("unresolved");
    expect(job.resolutionError).toBe("Cherry-pick failed without conflict markers; check git configuration or hooks");
  });

  it("ignores job_resolved for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job.resolved", {
      jobId: "unknown",
      resolution: "merged",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles job_archived", () => {
    useStore.setState({ jobs: { "job-1": makeJob({ state: "review" }) } });
    useStore.getState().dispatchSSEEvent("job.archived", {
      jobId: "job-1",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.archivedAt).toBeDefined();
    expect(typeof job.archivedAt).toBe("string");
  });

  it("ignores job_archived for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job.archived", { jobId: "unknown" });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles diff_update and stores diffs", () => {
    const files = [{ path: "a.ts", status: "modified", additions: 1, deletions: 0, hunks: [] }];
    useStore.getState().dispatchSSEEvent("diff.updated", {
      jobId: "job-1",
      changedFiles: files,
    });
    expect(selectJobDiffs("job-1")(useStore.getState())).toEqual(files);
  });

  it("handles job_title_updated", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("job.title_updated", {
      jobId: "job-1",
      title: "New Title",
      branch: "feat/new",
    });
    const job = selectJobs(useStore.getState())["job-1"]!;
    expect(job.title).toBe("New Title");
    expect(job.branch).toBe("feat/new");
  });

  it("ignores job_title_updated for unknown job", () => {
    useStore.getState().dispatchSSEEvent("job.title_updated", {
      jobId: "unknown",
      title: "Title",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("handles agent_plan_updated — creates plan steps", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("plan.updated", {
      jobId: "job-1",
      steps: [
        { label: "Analyze code", status: "active" },
        { label: "Fix bugs", status: "pending" },
      ],
    });
    const plans = useStore.getState().plans["job-1"] ?? [];
    expect(plans).toHaveLength(2);
    expect(plans[0]?.label).toBe("Analyze code");
    expect(plans[0]?.status).toBe("active");
  });

  it("handles model_downgraded", () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    useStore.getState().dispatchSSEEvent("model.downgraded", {
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
    useStore.getState().dispatchSSEEvent("model.downgraded", {
      jobId: "unknown",
      requestedModel: "gpt-4",
      actualModel: "gpt-3.5",
    });
    expect(Object.keys(selectJobs(useStore.getState()))).toHaveLength(0);
  });

  it("transcript_update deduplicates", () => {
    useStore.getState().dispatchSSEEvent("message.assistant", {
      jobId: "job-1",
      eventId: "evt-1",
      sequence: 1,
      timestamp: "2025-01-01T00:00:00Z",
      kind: "message.assistant",
      content: "Hello",
    });
    useStore.getState().dispatchSSEEvent("message.assistant", {
      jobId: "job-1",
      eventId: "evt-1",
      sequence: 1,
      timestamp: "2025-01-01T00:00:00Z",
      kind: "message.assistant",
      content: "Hello",
    });
    expect(selectJobTranscript("job-1")(useStore.getState())).toHaveLength(1);
  });

  it("keeps distinct events that share a legacy sequence value", () => {
    useStore.getState().dispatchSSEEvent("message.assistant", {
      jobId: "job-1",
      eventId: "evt-1",
      sequence: 0,
      timestamp: "2025-01-01T00:00:00Z",
      kind: "message.assistant",
      content: "First",
    });
    useStore.getState().dispatchSSEEvent("message.assistant", {
      jobId: "job-1",
      eventId: "evt-2",
      sequence: 0,
      timestamp: "2025-01-01T00:00:01Z",
      kind: "message.assistant",
      content: "Second",
    });
    expect(selectJobTranscript("job-1")(useStore.getState())).toHaveLength(2);
  });

  it("bumps artifact versions after collection completes", () => {
    useStore.getState().dispatchSSEEvent("artifacts.updated", {
      jobId: "job-1",
      collectionStatus: "completed",
    });
    expect(useStore.getState().artifactVersions["job-1"]).toBe(1);
  });
});

// ---- Column selectors -----------------------------------------------------

describe("column selectors", () => {
  it("selectActiveJobs returns queued and running, not archived", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "queued" }),
        "j-2": makeJob({ id: "j-2", state: "running" }),
        "j-3": makeJob({ id: "j-3", state: "review" }),
        "j-4": makeJob({ id: "j-4", state: "running", archivedAt: "2025-01-01" }),
      },
    });
    const active = selectActiveJobs(useStore.getState());
    expect(active.map((j) => j.id).sort()).toEqual(["j-1", "j-2"]);
  });

  it("selectSignoffJobs returns waiting_for_approval and review, not canceled", () => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", state: "waiting_for_approval" }),
        "j-2": makeJob({ id: "j-2", state: "review" }),
        "j-3": makeJob({ id: "j-3", state: "canceled" }),
        "j-4": makeJob({ id: "j-4", state: "running" }),
        "j-5": makeJob({ id: "j-5", state: "review", archivedAt: "2025-01-01" }),
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
        "j-1": makeJob({ id: "j-1", state: "review", archivedAt: "2025-01-01" }),
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

// ---- Repo-scoped column selectors (Story 2.3 / CAP-1 RepoBoard) -----------

describe("repo-scoped column selectors", () => {
  beforeEach(() => {
    useStore.setState({
      jobs: {
        "j-1": makeJob({ id: "j-1", repo: "/repos/a", state: "running" }),
        "j-2": makeJob({ id: "j-2", repo: "/repos/b", state: "running" }),
        "j-3": makeJob({ id: "j-3", repo: "/repos/a", state: "waiting_for_approval" }),
        "j-4": makeJob({ id: "j-4", repo: "/repos/b", state: "waiting_for_approval" }),
        "j-5": makeJob({ id: "j-5", repo: "/repos/a", state: "failed" }),
        "j-6": makeJob({ id: "j-6", repo: "/repos/b", state: "failed" }),
        "j-7": makeJob({ id: "j-7", repo: "/repos/a", state: "running", archivedAt: "2025-01-01" }),
      },
    });
  });

  it("selectActiveJobsForRepo only returns active jobs for the given repo", () => {
    const active = selectActiveJobsForRepo("/repos/a")(useStore.getState());
    expect(active.map((j) => j.id)).toEqual(["j-1"]);
  });

  it("selectSignoffJobsForRepo only returns signoff jobs for the given repo", () => {
    const signoff = selectSignoffJobsForRepo("/repos/a")(useStore.getState());
    expect(signoff.map((j) => j.id)).toEqual(["j-3"]);
  });

  it("selectAttentionJobsForRepo only returns failed jobs for the given repo", () => {
    const attention = selectAttentionJobsForRepo("/repos/a")(useStore.getState());
    expect(attention.map((j) => j.id)).toEqual(["j-5"]);
  });

  it("excludes archived jobs from the repo-scoped active selector", () => {
    const active = selectActiveJobsForRepo("/repos/a")(useStore.getState());
    expect(active.map((j) => j.id)).not.toContain("j-7");
  });

  it("returns an empty array for a repo with no matching jobs", () => {
    const active = selectActiveJobsForRepo("/repos/nonexistent")(useStore.getState());
    expect(active).toEqual([]);
  });
});

// ---- Stale approval eviction on job_state_changed -------------------------

describe("job_state_changed evicts stale approvals", () => {
  it("clears unresolved approvals when job leaves waiting_for_approval (server-restart recovery)", () => {
    useStore.setState({
      jobs: { "job-1": makeJob({ state: "waiting_for_approval" }) },
      approvals: {
        "a-1": {
          id: "a-1",
          jobId: "job-1",
          description: "Approve action",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
          notes: null,
        },
      },
    });

    // Server recovery sends job_state_changed back to running, no approval_resolved
    useStore.getState().dispatchSSEEvent("job.state_changed", {
      jobId: "job-1",
      newState: "running",
      timestamp: "2025-01-01T00:01:00Z",
    });

    const state = useStore.getState();
    expect(state.jobs["job-1"]?.state).toBe("running");
    expect(Object.keys(state.approvals)).toHaveLength(0);
  });

  it("keeps resolved approvals intact when job transitions state", () => {
    useStore.setState({
      jobs: { "job-1": makeJob({ state: "waiting_for_approval" }) },
      approvals: {
        "a-1": {
          id: "a-1",
          jobId: "job-1",
          description: "Approve action",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: "2025-01-01T00:01:00Z",
          resolution: "approved",
          requiresExplicitApproval: false,
          notes: null,
        },
      },
    });

    useStore.getState().dispatchSSEEvent("job.state_changed", {
      jobId: "job-1",
      newState: "running",
      timestamp: "2025-01-01T00:01:00Z",
    });

    const state = useStore.getState();
    expect(state.jobs["job-1"]?.state).toBe("running");
    // Already-resolved approval is not removed
    expect(state.approvals["a-1"]).toBeDefined();
  });

  it("does not evict approvals for other jobs", () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ id: "job-1", state: "waiting_for_approval" }),
        "job-2": makeJob({ id: "job-2", state: "waiting_for_approval" }),
      },
      approvals: {
        "a-1": {
          id: "a-1",
          jobId: "job-1",
          description: "job-1 approval",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
          notes: null,
        },
        "a-2": {
          id: "a-2",
          jobId: "job-2",
          description: "job-2 approval",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
          notes: null,
        },
      },
    });

    useStore.getState().dispatchSSEEvent("job.state_changed", {
      jobId: "job-1",
      newState: "running",
      timestamp: "2025-01-01T00:01:00Z",
    });

    const state = useStore.getState();
    expect(state.approvals["a-1"]).toBeUndefined();
    expect(state.approvals["a-2"]).toBeDefined();
  });
});

// ---- Snapshot SSE event filters stale approvals ---------------------------

describe("snapshot SSE event", () => {
  it("drops approvals whose job is not in waiting_for_approval (snapshot-only reconnect)", () => {
    useStore.getState().dispatchSSEEvent("snapshot", {
      jobs: [{ ...makeJob({ id: "job-1", state: "running" }) }],
      pendingApprovals: [
        {
          id: "a-1",
          jobId: "job-1",
          description: "Stale approval",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
        },
      ],
    });

    const state = useStore.getState();
    expect(state.jobs["job-1"]?.state).toBe("running");
    expect(Object.keys(state.approvals)).toHaveLength(0);
  });

  it("keeps approvals for jobs in waiting_for_approval", () => {
    useStore.getState().dispatchSSEEvent("snapshot", {
      jobs: [{ ...makeJob({ id: "job-1", state: "waiting_for_approval" }) }],
      pendingApprovals: [
        {
          id: "a-1",
          jobId: "job-1",
          description: "Live approval",
          proposedAction: null,
          requestedAt: "2025-01-01T00:00:00Z",
          resolvedAt: null,
          resolution: null,
          requiresExplicitApproval: false,
        },
      ],
    });

    const state = useStore.getState();
    expect(state.approvals["a-1"]).toBeDefined();
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

  describe("tracker operation central dispatcher", () => {
    function operation(overrides: Partial<TrackerOperationResponse> = {}): TrackerOperationResponse {
      return {
        id: "op-1", operationId: "op-1", sourceKey: "source-1",
        projectId: "project-1", projectRef: "project-1", jobId: "job-1", jobRef: "job-1",
        taskLinkId: null, taskLinkRef: null, trackerLinkId: "link-1", trackerLinkRef: "link-1",
        provider: "github", baseUrl: "https://github.com", externalRef: "org/project",
        credentialIdRef: "credential-1", ticketRef: "org/repo#1",
        action: "comment", targetKind: "comment", value: "  Exact body\n\n ", normalizedValue: null, normalizedTarget: null,
        approvalId: "approval-1", approvalRef: "approval-1", approvalSnapshot: null,
        state: "uncertain", version: 3, retryOfId: null, providerReceipt: null,
        errorCode: "response_lost", errorDetail: "Response lost", reconciliation: null,
        createdAt: "2026-11-11T12:00:00Z", updatedAt: "2026-11-11T12:01:00Z",
        dispatchStartedAt: "2026-11-11T12:00:30Z", completedAt: null, ...overrides,
      };
    }

    it("preserves orphaned camelCase operation payloads through the TF boundary", () => {
      const incoming = operation({ jobId: null, trackerLinkId: null });
      const payload = normalizeTFEvent({
        kind: "tracker.operation_updated", session_id: "",
        payload: { operation: incoming },
      });
      useStore.getState().dispatchSSEEvent("tracker.operation_updated", payload);
      expect(useStore.getState().trackerOperations["op-1"]).toEqual(incoming);
      expect(useStore.getState().jobs).toEqual({});
    });

    it("rejects older and equal-version events after a newer reconciliation", () => {
      const applied = operation({ state: "applied", version: 6 });
      useStore.getState().dispatchSSEEvent("tracker.operation_updated", { operation: applied });
      const currentMap = useStore.getState().trackerOperations;
      for (const version of [2, 5, 6]) {
        useStore.getState().dispatchSSEEvent("tracker.operation_updated", {
          operation: operation({ version }),
        });
      }
      expect(useStore.getState().trackerOperations).toBe(currentMap);
      expect(useStore.getState().trackerOperations["op-1"]).toEqual(applied);
    });

    it("does not overwrite an SSE-created row with an older REST list or an empty list", () => {
      useStore.getState().dispatchSSEEvent("tracker.operation_updated", { operation: operation({ version: 5 }) });
      useStore.getState().hydrateTrackerOperations([operation({ state: "approved", version: 2 })]);
      useStore.getState().hydrateTrackerOperations([]);
      expect(useStore.getState().trackerOperations["op-1"]?.state).toBe("uncertain");
      expect(useStore.getState().trackerOperations["op-1"]?.version).toBe(5);
    });

    it.each(["rest", "sse"] as const)("accepts equal-version FK deletion through %s without replacing outcome evidence or resurrecting references", (source) => {
      const current = operation({
        state: "applied", version: 6,
        taskLinkId: "task-1", taskLinkRef: "task-1",
        providerReceipt: { id: "receipt-1", httpStatus: 200 },
        reconciliation: {
          resolution: "applied", evidence: "Verified receipt-1", actor: "operator",
          recordedAt: "2026-11-11T12:05:00Z",
        },
      });
      useStore.getState().hydrateTrackerOperations([current]);
      const deleted = {
        ...current,
        projectId: null, jobId: null, taskLinkId: null, trackerLinkId: null, approvalId: null,
        state: "uncertain" as const, value: "Stale body", providerReceipt: null, reconciliation: null,
      };
      if (source === "rest") {
        useStore.getState().hydrateTrackerOperations([deleted]);
      } else {
        useStore.getState().dispatchSSEEvent("tracker.operation_updated", { operation: deleted });
      }
      const expected = {
        ...current,
        projectId: null, jobId: null, taskLinkId: null, trackerLinkId: null, approvalId: null,
      };
      expect(useStore.getState().trackerOperations["op-1"]).toEqual(expected);

      const deletedMap = useStore.getState().trackerOperations;
      for (const version of [5, 6]) {
        useStore.getState().hydrateTrackerOperations([{ ...current, version }]);
        useStore.getState().dispatchSSEEvent("tracker.operation_updated", { operation: { ...current, version } });
      }
      expect(useStore.getState().trackerOperations).toBe(deletedMap);

      // Newer outcome evidence is valid, but cannot undo the deletion already
      // observed for the same immutable correlation references.
      useStore.getState().hydrateTrackerOperations([{
        ...current, version: 7, providerReceipt: { id: "receipt-2", httpStatus: 200 },
      }]);
      expect(useStore.getState().trackerOperations["op-1"]).toEqual({
        ...expected, version: 7, providerReceipt: { id: "receipt-2", httpStatus: 200 },
      });
    });

    it("allows first canonical approval binding but retains a later equal-version approval deletion", () => {
      const unbound = operation({ state: "awaiting_approval", version: 0, approvalId: null, approvalRef: null });
      useStore.getState().hydrateTrackerOperations([unbound]);
      const bound = { ...unbound, version: 1, approvalId: "approval-1", approvalRef: "approval-1" };
      useStore.getState().dispatchSSEEvent("tracker.operation_updated", { operation: bound });
      expect(useStore.getState().trackerOperations["op-1"]?.approvalId).toBe("approval-1");

      useStore.getState().hydrateTrackerOperations([{ ...bound, approvalId: null }]);
      useStore.getState().dispatchSSEEvent("tracker.operation_updated", { operation: bound });
      useStore.getState().hydrateTrackerOperations([unbound]);
      expect(useStore.getState().trackerOperations["op-1"]).toEqual({ ...bound, approvalId: null });
    });

    it("does not interpret missing, older or differently referenced correlations as equal-version deletion", () => {
      const current = operation({ version: 4 });
      useStore.getState().hydrateTrackerOperations([current]);
      const currentMap = useStore.getState().trackerOperations;
      useStore.getState().hydrateTrackerOperations([
        { ...current, jobId: null, version: 3 },
        { ...current, jobId: null, jobRef: "other-job" },
        { ...current, approvalId: null, approvalRef: null },
        { ...current, approvalId: undefined },
      ]);
      expect(useStore.getState().trackerOperations).toBe(currentMap);
      expect(useStore.getState().trackerOperations["op-1"]).toEqual(current);
    });

    it("ignores malformed versions and mismatched immutable ownership", () => {
      const dispatch = useStore.getState().dispatchSSEEvent;
      dispatch("tracker.operation_updated", { operation: operation() });
      dispatch("tracker.operation_updated", { operation: operation({ projectRef: "other-project", version: 4 }) });
      for (const version of [-1, Number.NaN, 1.5]) {
        dispatch("tracker.operation_updated", { operation: operation({ version }) });
      }
      dispatch("tracker.operation_updated", { operation: null });
      expect(useStore.getState().trackerOperations["op-1"]?.projectRef).toBe("project-1");
      expect(useStore.getState().trackerOperations["op-1"]?.version).toBe(3);
    });
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
});
