/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";

vi.mock("../../api/client", () => ({
  fetchJobs: vi.fn(),
}));

import { fetchJobs } from "../../api/client";
import { HistoryScreen } from "../HistoryScreen";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/repos/test",
    prompt: "Original archived task",
    state: "completed",
    baseRef: "main",
    worktreePath: null,
    branch: "fix/archived-task",
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    completedAt: "2025-01-02T00:00:00Z",
    archivedAt: "2025-01-03T00:00:00Z",
    resolution: "merged",
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

describe("HistoryScreen", () => {
  it("loads archived jobs on mount", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [makeJob()], cursor: null, hasMore: false } as any);

    render(
      <MemoryRouter>
        <HistoryScreen />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(fetchJobs).toHaveBeenCalledWith({ state: "review,completed,failed,canceled", limit: 100, archived: true });
    });
  });

  it("does not render an unarchive action for archived jobs", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [makeJob()], cursor: null, hasMore: false } as any);

    render(
      <MemoryRouter>
        <HistoryScreen />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Original archived task")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /unarchive/i })).not.toBeInTheDocument();
  });
});
