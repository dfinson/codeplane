import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";
import { JobCard } from "../JobCard";
import { MetadataChipStrip } from "../MetadataChipStrip";
import type { UsageAmount } from "../../api/types";

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
  });
});

describe("JobCard", () => {
  it.each(["card", "header"])("does not label unqualified historical cost as USD in the %s", (component) => {
    const job = makeJob({ totalCostUsd: 2.31, totalTokens: 116678, sdk: "codeplane" });
    const { container } = render(<MemoryRouter>
      {component === "card" ? <JobCard job={job} /> : <MetadataChipStrip job={job} hasMergeConflict={false} />}
    </MemoryRouter>);
    expect(container.textContent).not.toContain("$2.31");
    expect(screen.getByText("2.31 legacy units (unverified) · 117k tok")).toBeInTheDocument();
  });

  it.each(["card", "header"])("retains native units and verified USD without conversion in the %s", (component) => {
    const native: UsageAmount = {
      amount: "2.31", unit: "copilot_cost_unit", producer: "copilot", role: "primary",
      verified: false, currency: null, model: "fixture", job_id: "job-1", execution_epoch: "epoch",
      source: "assistant.usage",
    };
    const usd: UsageAmount = {
      ...native, amount: "0.12", unit: "USD", producer: "claude", verified: true, currency: "USD",
    };
    const job = makeJob({ totalCostUsd: 2.43, totalTokens: 116678, sdk: "codeplane", costUsage: [native, usd] });
    const { container } = render(<MemoryRouter>
      {component === "card" ? <JobCard job={job} /> : <MetadataChipStrip job={job} hasMergeConflict={false} />}
    </MemoryRouter>);
    expect(screen.getByText("2.31 Copilot cost units + $0.12 USD · 117k tok")).toBeInTheDocument();
    expect(container.textContent).not.toContain("$2.43");
    expect(container.textContent).not.toContain("$2.31");
    if (component === "header") {
      fireEvent.mouseEnter(screen.getByRole("button", { name: "View cost breakdown" }));
      expect(screen.getByRole("tooltip")).toHaveTextContent("Legacy aggregate (unit unverified)2.43");
      expect(screen.getByRole("tooltip")).toHaveTextContent("Native units are not dollars");
      expect(screen.getByRole("tooltip")).not.toHaveTextContent("$2.43");
    }
  });

  it("preserves verified zero USD and does not infer a currency from the provider name", () => {
    const usd: UsageAmount = {
      amount: "0", unit: "USD", producer: "claude", role: "primary",
      verified: true, currency: "USD", model: null, job_id: "job-1", execution_epoch: "epoch",
      source: "native-response",
    };
    const { rerender } = render(<MemoryRouter>
      <MetadataChipStrip job={makeJob({ totalCostUsd: 0, costUsage: [usd] })} hasMergeConflict={false} />
    </MemoryRouter>);
    expect(screen.getByText("$0.00 USD")).toBeInTheDocument();
    rerender(<MemoryRouter>
      <MetadataChipStrip job={makeJob({ totalCostUsd: 2.31, sdk: "claude", costUsage: [] })} hasMergeConflict={false} />
    </MemoryRouter>);
    expect(screen.getByText("2.31 legacy units (unverified)")).toBeInTheDocument();
    expect(screen.queryByText("$2.31")).not.toBeInTheDocument();
  });

  it.each(["card", "header"])("shows the captured Copilot cost unit without any dollars in the %s", (component) => {
    const job = makeJob({
      sdk: "codeplane", totalCostUsd: 2.31, totalTokens: 116678,
      costUsage: [{
        amount: "2.31", unit: "copilot_cost_unit", verified: false, currency: null, producer: "copilot",
        model: null, job_id: "job-1", execution_epoch: "epoch", role: "primary", source: "native-response",
      }],
    });
    const { container } = render(<MemoryRouter>
      {component === "card" ? <JobCard job={job} /> : <MetadataChipStrip job={job} hasMergeConflict={false} />}
    </MemoryRouter>);
    expect(screen.getByText("2.31 Copilot cost units · 117k tok")).toBeInTheDocument();
    expect(container.textContent).not.toContain("$");
  });

  it("renders nullable observation history without a fictional running agent or branch", () => {
    render(<MemoryRouter><JobCard job={makeJob({
      repo: null, baseRef: null, worktreePath: null, branch: null,
      observationOnly: true, source: "integration", sdk: "native:copilot",
    })} /></MemoryRouter>);
    expect(screen.getByText("External observation")).toBeInTheDocument();
    expect(screen.getByText("Observation")).toBeInTheDocument();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
    expect(screen.queryByText("main")).not.toBeInTheDocument();
  });

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
        <JobCard job={makeJob({ state: "review" })} />
      </MemoryRouter>,
    );
    expect(screen.getByText("In Review")).toBeInTheDocument();
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

  it("shows persisted preview for review jobs", () => {
    render(
      <MemoryRouter>
        <JobCard
          job={makeJob({
            state: "review",
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
