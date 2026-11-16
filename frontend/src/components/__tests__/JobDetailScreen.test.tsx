/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";

vi.mock("../../api/client", () => ({
  fetchJob: vi.fn(),
  fetchJobSnapshot: vi.fn().mockResolvedValue(null),
  cancelJob: vi.fn(),
  rerunJob: vi.fn(),
  resumeJob: vi.fn(),
  fetchJobTranscript: vi.fn().mockResolvedValue([]),
  fetchJobDiff: vi.fn().mockResolvedValue([]),
  fetchJobSteps: vi.fn().mockResolvedValue([]),
  fetchApprovals: vi.fn().mockResolvedValue([]),
  resolveJob: vi.fn(),
  fetchArtifacts: vi.fn().mockResolvedValue({
    items: [],
    collectionStatus: "completed",
    collectionError: null,
    collectionUpdatedAt: null,
  }),
  createTerminalSession: vi.fn(),
  archiveJob: vi.fn(),
  fetchMultiSession: vi.fn().mockResolvedValue(null),
  fetchReviewStory: vi.fn().mockResolvedValue(null),
  fetchProjects: vi.fn().mockResolvedValue({ items: [] }),
  fetchProject: vi.fn(),
  fetchProjectTaskLinks: vi.fn().mockResolvedValue({ items: [] }),
  fetchCurrentRecipeRun: vi.fn().mockResolvedValue({ run: null }),
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

vi.mock("../CompleteJobDialog", () => ({
  CompleteJobDialog: () => null,
}));

vi.mock("../StateBadge", () => ({
  StateBadge: ({ state }: { state: string }) => <span>{state}</span>,
}));

vi.mock("../SdkBadge", () => ({
  SdkBadge: () => <span>sdk</span>,
  SdkIcon: () => <span>icon</span>,
}));

vi.mock("../ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("../ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}));

import { toast } from "sonner";
import {
  fetchArtifacts,
  fetchJob,
  fetchJobDiff,
  fetchProject,
  fetchProjectTaskLinks,
  resolveJob,
  rerunJob,
  resumeJob,
} from "../../api/client";
import { JobDetailScreen } from "../JobDetailScreen";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/repos/test",
    prompt: "Fix the bug",
    projectId: "project-1",
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
  vi.mocked(fetchArtifacts).mockReset();
  vi.mocked(fetchProject).mockReset();
  vi.mocked(fetchProjectTaskLinks).mockReset();
  vi.mocked(fetchArtifacts).mockResolvedValue({
    items: [],
    collectionStatus: "completed",
    collectionError: null,
    collectionUpdatedAt: null,
  });
  vi.mocked(fetchJobDiff).mockResolvedValue([]);
  vi.mocked(fetchProject).mockRejectedValue(new Error("missing project"));
  vi.mocked(fetchProjectTaskLinks).mockResolvedValue({ items: [] } as any);
  useStore.setState({
    jobs: {},
    approvals: {},
    logs: {},
    transcript: {},
    diffs: {},
    timelines: {},
    plans: {},
    telemetryVersions: {},
    artifactVersions: {},
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
  it("shows an unavailable-project breadcrumb for legacy jobs without projectId", async () => {
    useStore.setState({ jobs: { "job-1": makeJob({ projectId: null }) } });
    vi.mocked(fetchJob).mockResolvedValueOnce(makeJob({ projectId: null }) as any);

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Original project is unavailable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/projects");
  });

  it("renders Project, repository, Task, and Job breadcrumbs with deep links", async () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    vi.mocked(fetchJob).mockResolvedValueOnce(makeJob() as any);
    vi.mocked(fetchProject).mockResolvedValueOnce({
      id: "project-1",
      name: "Payments",
      repoPaths: ["/repos/test"],
    } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValueOnce({
      items: [{
        id: "task-1",
        projectId: "project-1",
        repoPath: "/repos/test",
        storyNodeId: "5-1-chat",
        jobId: "job-1",
      }],
    } as any);

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "Payments" })).toHaveAttribute(
      "href",
      "/projects/id/project-1/board",
    );
    expect(screen.getByRole("link", { name: "test" })).toHaveAttribute(
      "href",
      "/projects/id/project-1/repos/%2Frepos%2Ftest/jobs",
    );
    expect(await screen.findByRole("link", { name: "5-1-chat" })).toHaveAttribute(
      "href",
      "/projects/id/project-1/board/task/task-1",
    );
  });

  it("keeps the Artifacts tab visible and refetches after artifacts.updated", async () => {
    useStore.setState({ jobs: { "job-1": makeJob() } });
    vi.mocked(fetchJob).mockResolvedValueOnce(makeJob() as any);

    render(
      <MemoryRouter initialEntries={["/jobs/job-1"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: /Artifacts/i })).toBeInTheDocument();
    await waitFor(() => expect(fetchArtifacts).toHaveBeenCalledTimes(1));

    act(() => {
      useStore.getState().dispatchSSEEvent("artifacts.updated", {
        jobId: "job-1",
        collectionStatus: "completed",
      });
    });
    await waitFor(() => expect(fetchArtifacts).toHaveBeenCalledTimes(2));
  });

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

  it.skip("uses page scrolling for live panels outside the transcript", async () => {
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

    expect(await screen.findByRole("button", { name: "Resolve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Merge" })).not.toBeInTheDocument();
    expect(screen.getByText("Conflict")).toBeInTheDocument();
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

    // When resolution is merged, conflict indicators should NOT appear
    // Wait for component to render
    await screen.findAllByText("Fix bug");
    expect(screen.queryByText("Conflict")).not.toBeInTheDocument();
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