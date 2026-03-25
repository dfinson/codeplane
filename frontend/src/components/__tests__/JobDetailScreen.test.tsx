/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";

vi.mock("../../api/client", () => ({
  fetchJob: vi.fn(),
  cancelJob: vi.fn(),
  rerunJob: vi.fn(),
  resumeJob: vi.fn(),
  fetchJobTranscript: vi.fn().mockResolvedValue([]),
  fetchJobTimeline: vi.fn().mockResolvedValue([]),
  fetchJobDiff: vi.fn().mockResolvedValue([]),
  fetchApprovals: vi.fn().mockResolvedValue([]),
  resolveJob: vi.fn(),
  fetchArtifacts: vi.fn().mockResolvedValue({ items: [] }),
  createTerminalSession: vi.fn(),
}));

vi.mock("../../hooks/useSSE", () => ({
  useSSE: () => ({ reconnect: vi.fn() }),
}));

vi.mock("../../hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../TranscriptPanel", () => ({
  TranscriptPanel: () => <div data-testid="transcript-panel" />,
}));

vi.mock("../MetricsPanel", () => ({
  MetricsPanel: () => <div data-testid="metrics-panel" />,
}));

vi.mock("../ExecutionTimeline", () => ({
  ExecutionTimeline: () => <div data-testid="timeline-panel" />,
}));

vi.mock("../PlanPanel", () => ({
  PlanPanel: () => <div data-testid="plan-panel" />,
}));

vi.mock("../CompleteJobDialog", () => ({
  CompleteJobDialog: () => null,
}));

vi.mock("../StateBadge", () => ({
  StateBadge: ({ state }: { state: string }) => <span>{state}</span>,
}));

vi.mock("../SdkBadge", () => ({
  SdkBadge: () => <span>sdk</span>,
}));

vi.mock("../ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("../ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}));

import { toast } from "sonner";
import { fetchJob, fetchJobDiff, resolveJob, rerunJob, resumeJob } from "../../api/client";
import { JobDetailScreen } from "../JobDetailScreen";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/repos/test",
    prompt: "Fix the bug",
    title: "Fix bug",
    state: "review",
    baseRef: "main",
    worktreePath: "/repos/test/.cpl-worktrees/job-1",
    branch: "fix/bug",
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    completedAt: "2025-01-01T01:00:00Z",
    prUrl: null,
    resolution: "conflict",
    mergeStatus: "conflict",
    archivedAt: null,
    sdk: "copilot",
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(fetchJob).mockReset();
  vi.mocked(fetchJobDiff).mockReset();
  vi.mocked(resolveJob).mockReset();
  vi.mocked(fetchJobDiff).mockResolvedValue([]);
  useStore.setState({
    jobs: {},
    approvals: {},
    logs: {},
    transcript: {},
    diffs: {},
    timelines: {},
    plans: {},
    telemetryVersions: {},
    terminalSessions: {},
    activeTerminalTab: null,
    terminalDrawerOpen: false,
    terminalDrawerHeight: 320,
    connectionStatus: "connected",
    reconnectAttempt: 0,
  } as any);

  class ResizeObserverMock {
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
});

describe("JobDetailScreen", () => {
  it("re-fetches the job even when a cached copy already exists", async () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ resolution: "conflict", updatedAt: "2025-01-01T00:00:00Z" }),
      },
    });

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ resolution: "unresolved", mergeStatus: "not_merged", updatedAt: "2025-01-01T02:00:00Z" }) as any,
    );

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(fetchJob).toHaveBeenCalledWith("job-1");
    });

    await waitFor(() => {
      expect(useStore.getState().jobs["job-1"]?.resolution).toBe("unresolved");
      expect(useStore.getState().jobs["job-1"]?.mergeStatus).toBe("not_merged");
    });
  });

  it("uses page scrolling for live panels outside the transcript", async () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ state: "running", resolution: null }),
      },
    });

    vi.mocked(fetchJob).mockResolvedValueOnce(makeJob({ state: "running", resolution: null }) as any);

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    const transcriptPanel = await screen.findByTestId("transcript-panel");
    expect(transcriptPanel.parentElement).toHaveClass("h-[80dvh]", "min-h-[22rem]");
    expect(transcriptPanel.parentElement?.parentElement).toHaveClass("flex", "flex-col", "gap-4");
    expect(transcriptPanel.parentElement?.nextElementSibling).toHaveClass("space-y-4");
    expect(transcriptPanel.parentElement?.nextElementSibling).not.toHaveClass("overflow-y-auto");
  });

  it("reconciles the canonical job after merge so resolution controls disappear", async () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ resolution: "unresolved", mergeStatus: "not_merged" }),
      },
    });

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ resolution: "unresolved", mergeStatus: "not_merged" }) as any,
    );
    vi.mocked(fetchJobDiff).mockResolvedValueOnce([
      { path: "feature.ts", status: "modified", additions: 3, deletions: 1, hunks: [] },
    ] as any);
    vi.mocked(resolveJob).mockResolvedValueOnce({ resolution: "merged", conflictFiles: null, prUrl: null } as any);
    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ state: "completed", resolution: "merged", mergeStatus: "merged", updatedAt: "2025-01-01T03:00:00Z" }) as any,
    );

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Merge" }));

    await waitFor(() => {
      expect(resolveJob).toHaveBeenCalledWith("job-1", "smart_merge");
    });

    await waitFor(() => {
      expect(useStore.getState().jobs["job-1"]?.resolution).toBe("merged");
      expect(screen.queryByRole("button", { name: "Merge" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Complete & Archive" })).toBeInTheDocument();
    });
  });

  it("surfaces unresolved smart-merge results instead of reporting a false success", async () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ resolution: "unresolved", mergeStatus: "not_merged" }),
      },
    });

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ resolution: "unresolved", mergeStatus: "not_merged" }) as any,
    );
    vi.mocked(fetchJobDiff).mockResolvedValueOnce([
      { path: "feature.ts", status: "modified", additions: 3, deletions: 1, hunks: [] },
    ] as any);
    vi.mocked(resolveJob).mockResolvedValueOnce({ resolution: "unresolved", conflictFiles: null, prUrl: null } as any);
    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ resolution: "unresolved", mergeStatus: "not_merged", updatedAt: "2025-01-01T03:00:00Z" }) as any,
    );

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Merge" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Merge did not complete");
      expect(useStore.getState().jobs["job-1"]?.resolution).toBe("unresolved");
      expect(screen.getByRole("button", { name: "Merge" })).toBeInTheDocument();
    });
  });

  it("surfaces unresolved smart-merge error details in the toast and banner", async () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ resolution: "unresolved", mergeStatus: "not_merged" }),
      },
    });

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ resolution: "unresolved", mergeStatus: "not_merged" }) as any,
    );
    vi.mocked(fetchJobDiff).mockResolvedValueOnce([
      { path: "feature.ts", status: "modified", additions: 3, deletions: 1, hunks: [] },
    ] as any);
    vi.mocked(resolveJob).mockResolvedValueOnce({
      resolution: "unresolved",
      conflictFiles: null,
      prUrl: null,
      error: "Cherry-pick failed without conflict markers; check git configuration or hooks",
    } as any);
    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ resolution: "unresolved", mergeStatus: "not_merged", updatedAt: "2025-01-01T03:00:00Z" }) as any,
    );

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Merge" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Cherry-pick failed without conflict markers; check git configuration or hooks");
      expect(screen.getByText("Automatic merge failed: Cherry-pick failed without conflict markers; check git configuration or hooks")).toBeInTheDocument();
      expect(useStore.getState().jobs["job-1"]?.resolutionError).toBe("Cherry-pick failed without conflict markers; check git configuration or hooks");
    });
  });

  it("shows conflict resolution controls when merge metadata indicates a conflict", async () => {
    vi.mocked(fetchJobDiff).mockResolvedValueOnce([
      { path: "README.md", status: "modified", additions: 1, deletions: 1, hunks: [] },
    ] as any);

    useStore.setState({
      jobs: {
        "job-1": makeJob({
          resolution: "unresolved",
          mergeStatus: "conflict",
          conflictFiles: ["README.md"],
        }),
      },
      diffs: {
        "job-1": [
          { path: "README.md", status: "modified", additions: 1, deletions: 1, hunks: [] },
        ],
      },
    } as any);

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({
        resolution: "unresolved",
        mergeStatus: "conflict",
        conflictFiles: ["README.md"],
      }) as any,
    );

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Resolve with Agent" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Merge" })).not.toBeInTheDocument();
    expect(screen.getByText("Merge conflict — user input required")).toBeInTheDocument();
  });

  it("does not show conflict text when resolution is merged but stale conflict indicators remain", async () => {
    vi.mocked(fetchJobDiff).mockResolvedValueOnce([
      { path: "README.md", status: "modified", additions: 1, deletions: 1, hunks: [] },
    ] as any);

    // Simulate stale state: resolution is "merged" but mergeStatus/conflictFiles still indicate a past conflict
    useStore.setState({
      jobs: {
        "job-1": makeJob({
          state: "completed",
          resolution: "merged",
          mergeStatus: "conflict",
          conflictFiles: ["README.md"],
        }),
      },
      diffs: {
        "job-1": [
          { path: "README.md", status: "modified", additions: 1, deletions: 1, hunks: [] },
        ],
      },
    } as any);

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({
        state: "completed",
        resolution: "merged",
        mergeStatus: "conflict",
        conflictFiles: ["README.md"],
      }) as any,
    );

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    // The subtitle should show the merged message, NOT concatenate conflict text
    expect(await screen.findByText(/Changes merged into base branch/)).toBeInTheDocument();
    expect(screen.queryByText(/Merge conflict detected/)).not.toBeInTheDocument();
  });

  it("resumes the existing failed job instead of rerunning a new one", async () => {
    useStore.setState({
      jobs: {
        "job-1": makeJob({ state: "failed", resolution: null, mergeStatus: "not_merged" }),
      },
    });

    vi.mocked(fetchJob).mockResolvedValueOnce(
      makeJob({ state: "failed", resolution: null, mergeStatus: "not_merged" }) as any,
    );
    vi.mocked(resumeJob).mockResolvedValueOnce({
      id: "job-1",
      state: "running",
      branch: "fix/bug",
      worktreePath: "/repos/test/.cpl-worktrees/job-1",
      createdAt: "2025-01-01T00:00:00Z",
      updatedAt: "2025-01-01T02:00:00Z",
    } as any);

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));

    await waitFor(() => {
      expect(resumeJob).toHaveBeenCalledWith("job-1");
      expect(rerunJob).not.toHaveBeenCalled();
      expect(useStore.getState().jobs["job-1"]?.state).toBe("running");
    });
  });
});