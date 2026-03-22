import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";
import { JobCard } from "../JobCard";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/home/user/repos/my-project",
    prompt: "Fix the authentication bug",
    state: "running",
    baseRef: "main",
    worktreePath: "/repos/test",
    branch: "fix/auth-bug",
    createdAt: new Date(Date.now() - 120_000).toISOString(), // 2 minutes ago
    updatedAt: new Date().toISOString(),
    completedAt: null,
    prUrl: null,
    ...overrides,
  };
}

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

beforeEach(() => {
  mockNavigate.mockReset();
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

describe("JobCard", () => {
  it("renders job title when present", () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob({ title: "Fix auth" })} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Fix auth")).toBeInTheDocument();
  });

  it("renders job id when no title", () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob({ title: null })} />
      </MemoryRouter>,
    );
    expect(screen.getByText("job-1")).toBeInTheDocument();
  });

  it("renders state badge", () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob({ state: "succeeded" })} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Succeeded")).toBeInTheDocument();
  });

  it("shows repo name (last path segment)", () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob()} />
      </MemoryRouter>,
    );
    expect(screen.getByText("my-project")).toBeInTheDocument();
  });

  it("shows failure reason for failed jobs", () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob({ state: "failed", failureReason: "Timeout after 10m" })} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Timeout after 10m")).toBeInTheDocument();
  });

  it("navigates on click", async () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob()} />
      </MemoryRouter>,
    );
    const button = screen.getByRole("button");
    fireEvent.click(button);
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/jobs/job-1");
    });
  });

  it("shows model downgrade warning", () => {
    render(
      <MemoryRouter>
        <JobCard
          job={makeJob({
            modelDowngraded: true,
            requestedModel: "gpt-4",
            actualModel: "gpt-3.5",
          })}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Model downgraded/)).toBeInTheDocument();
    expect(screen.getByText("gpt-4")).toBeInTheDocument();
    expect(screen.getByText("gpt-3.5")).toBeInTheDocument();
  });

  it("shows branch name for queued jobs", () => {
    render(
      <MemoryRouter>
        <JobCard job={makeJob({ state: "queued", branch: "fix/auth-bug" })} />
      </MemoryRouter>,
    );
    expect(screen.getByText("fix/auth-bug")).toBeInTheDocument();
  });

  it("shows persisted preview for succeeded jobs", () => {
    render(
      <MemoryRouter>
        <JobCard
          job={makeJob({
            state: "succeeded",
            progressHeadline: "Audit and improve keyboard shortcuts",
            progressSummary: "Reviewed the shortcut map and captured follow-up changes for the final pass.",
          })}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Audit and improve keyboard shortcuts")).toBeInTheDocument();
    expect(screen.getByText(/Reviewed the shortcut map/)).toBeInTheDocument();
  });
});
