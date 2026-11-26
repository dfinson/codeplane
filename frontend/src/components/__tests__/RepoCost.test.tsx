import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ModelComparisonRow } from "../../api/client-analytics";
import { fetchModelComparison, fetchYield } from "../../api/client";
import { RepoCost } from "../RepoCost";

vi.mock("../../api/client", () => ({
  fetchModelComparison: vi.fn(),
  fetchYield: vi.fn(),
}));

function model(name: string, cost: number | null): ModelComparisonRow {
  return {
    model: name, sdk: "copilot", jobCount: 2, totalCostUsd: cost,
    avgCost: cost == null ? null : cost / 2, avgDurationMs: 100,
    premiumRequests: 2, merged: 0, prCreated: 0, discarded: 0, failed: 0,
    avgVerifyTurns: null, verifyJobCount: 0, avgDiffLines: 0, cacheHitRate: 0,
    costPerJob: null, costPerMinute: null, costPerTurn: null,
    costPerToolCall: null, costPerDiffLine: null,
  };
}

function renderCost(models: ModelComparisonRow[]) {
  vi.mocked(fetchModelComparison).mockResolvedValue({ period: 30, repo: "repo", models });
  render(
    <MemoryRouter initialEntries={["/projects/id/project-1/repos/repo/cost"]}>
      <Routes>
        <Route path="/projects/id/:projectId/repos/:repoPath/cost" element={<RepoCost />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchYield).mockResolvedValue({
    period: 30, categories: [], costPerMergeUsd: null, totalCostUsd: null, totalJobs: 0,
  });
});

describe("RepoCost currency evidence", () => {
  it.each([false, true])("keeps unknown rows without discarding a verified subtotal: %s", async (mixed) => {
    renderCost([model("unreported", null), ...(mixed ? [model("verified", 4)] : [])]);
    const row = await screen.findByRole("row", { name: /unreported/ });
    expect(within(row).getAllByText("Unknown USD")).toHaveLength(2);
    expect(screen.getAllByText("Unknown USD")).toHaveLength(mixed ? 2 : 4);
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
    if (mixed) {
      expect(within(screen.getByRole("row", { name: /^verified / })).getByText("$4.00")).toBeInTheDocument();
      expect(screen.getByText("Verified USD subtotal (30d)").parentElement).toHaveTextContent("$4.00");
      expect(screen.getByText("Verified USD per job").parentElement).toHaveTextContent("$1.00");
    }
  });

  it.each([false, true])("keeps the subtotal and all-job rate invariant to model grouping: %s", async (split) => {
    const known = { ...model("known", 4), jobCount: 1, avgCost: 4 };
    renderCost(split
      ? [known, { ...model("unknown", null), jobCount: 1 }]
      : [{ ...known, jobCount: 2 }]);
    await screen.findByRole("row", { name: /^known / });
    expect(screen.getByText("Verified USD subtotal (30d)").parentElement).toHaveTextContent("$4.00");
    expect(screen.getByText("Verified USD per job").parentElement).toHaveTextContent("$2.00");
    expect(within(screen.getByRole("row", { name: /^known / })).getAllByText("$4.00")).toHaveLength(2);
  });

  it.each([0, 4])("preserves measured costs and averages for %s USD", async (cost) => {
    renderCost([model("verified", cost)]);
    const row = await screen.findByRole("row", { name: /^verified / });
    expect(within(row).getAllByText(cost === 0 ? "$0" : "$4.00")).toHaveLength(cost === 0 ? 2 : 1);
    expect(screen.getAllByText(cost === 0 ? "$0" : "$2.00")).toHaveLength(cost === 0 ? 4 : 2);
    expect(screen.queryByText("Unknown USD")).not.toBeInTheDocument();
  });
});
