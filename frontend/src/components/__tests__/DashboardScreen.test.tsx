/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";

// Mock the API client
vi.mock("../../api/client", () => ({
  fetchJobs: vi.fn(),
}));

import { fetchJobs } from "../../api/client";
import { DashboardScreen } from "../DashboardScreen";

// Mock child components that have heavy deps (KanbanBoard, MobileJobList)
vi.mock("../KanbanBoard", () => ({
  KanbanBoard: () => <div data-testid="kanban-board">KanbanBoard</div>,
}));
vi.mock("../MobileJobList", () => ({
  MobileJobList: () => <div data-testid="mobile-job-list">MobileJobList</div>,
}));

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

beforeEach(() => {
  vi.mocked(fetchJobs).mockReset();
  useStore.setState({
    jobs: {},
    approvals: {},
    logs: {},
    transcript: {},
    diffs: {},
    timelines: {},
    plans: {},
  });
});

describe("DashboardScreen", () => {
  it("renders Jobs heading", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    render(
      <MemoryRouter>
        <DashboardScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("Jobs")).toBeInTheDocument());
  });

  it("renders New Job button", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    render(
      <MemoryRouter>
        <DashboardScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("New Job")).toBeInTheDocument());
  });

  it("renders KanbanBoard", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    render(
      <MemoryRouter>
        <DashboardScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("kanban-board")).toBeInTheDocument());
  });

  it("fetches jobs on mount", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [makeJob()], cursor: null } as any);
    render(
      <MemoryRouter>
        <DashboardScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(fetchJobs).toHaveBeenCalledWith({ limit: 100, archived: false });
    });
  });

  it("populates store with fetched jobs", async () => {
    const job = makeJob({ id: "j-fetched" });
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [job], cursor: null } as any);
    render(
      <MemoryRouter>
        <DashboardScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      const storeJobs = useStore.getState().jobs;
      expect(storeJobs["j-fetched"]).toBeDefined();
    });
  });
});
