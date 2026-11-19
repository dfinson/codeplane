import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { BudgetCard } from "../analytics/BudgetCard";
import { CostEvidence } from "../CostEvidence";
import { SidecarSessionsSection } from "../MetricsPanelSections";
import { MetricViz } from "../metrics/VizTemplates";
import type { ScorecardResponse } from "../../api/client";
import type { UsageAmount } from "../../api/types";

const native: UsageAmount = {
  amount: "2.31", unit: "copilot_cost_unit", producer: "copilot", model: "fixture",
  job_id: "job", execution_epoch: "epoch", role: "primary", source: "native",
  verified: false, currency: null,
};

function scorecard(cost: number | null): ScorecardResponse {
  return {
    period: 7, budget: [{ sdk: "copilot", totalCostUsd: cost, jobCount: 1, premiumRequests: 0, avgCostPerJob: cost, avgDurationMs: 0 }],
    activity: { totalJobs: 1, running: 0, inReview: 0, merged: 0, prCreated: 0, discarded: 0, failed: 0, cancelled: 0 },
    quotaJson: null, costTrend: [{ date: "2026-11-19", cost, jobs: 1 }],
    dailySpendLimitUsd: 0, monthlyBudgetUsd: 0, monthSpendUsd: cost, projectedMonthEndUsd: cost,
    dailyAvgUsd: cost, daysElapsed: 1, daysInMonth: 30, pctMonthlyBudgetUsed: 0,
    avgCostPerMtok: null, modelCostMix: [], costUsage: [native],
    costPerDiffLine: null, totalDiffLines: 0, compactionCostUsd: null, compactionTokens: 0,
  };
}

describe("currency provenance analytics", () => {
  it("shows native units and estimates separately without inventing measured zero USD", () => {
    const data = { ...scorecard(null), estimatedCostUsd: 0.12 };
    const { container } = render(<BudgetCard scorecard={data} />);
    expect(screen.getByText("Unknown USD")).toBeInTheDocument();
    expect(screen.getByText("copilot primary cost units: 2.31")).toBeInTheDocument();
    expect(screen.getByText(/Estimated API-equivalent cost: \$0.12/)).toBeInTheDocument();
    expect(container.textContent).not.toContain("$2.31");
    expect(container.textContent).not.toContain("$0.00");
  });

  it.each([0, 0.5])("preserves explicitly measured USD %s alongside native evidence", cost => {
    const { container } = render(<BudgetCard scorecard={scorecard(cost)} />);
    expect(screen.getAllByText(`$${cost.toFixed(2)}`).length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain("$2.31");
    expect(screen.queryByText("Unknown USD")).not.toBeInTheDocument();
  });

  it("retains producer roles rather than combining native usage into a dollar total", () => {
    render(<CostEvidence usage={[native, { ...native, amount: "8", role: "scout" }]} />);
    expect(screen.getByText("copilot primary cost units: 2.31")).toBeInTheDocument();
    expect(screen.getByText("copilot scout cost units: 8")).toBeInTheDocument();
    expect(screen.queryByText("$10.31")).not.toBeInTheDocument();
  });

  it("keeps historical sidecar currency unknown", () => {
    const { container } = render(<SidecarSessionsSection sessions={[
      { sessionKind: "preflight", totalCostUsd: null, inputTokens: 10, outputTokens: 5, llmCallCount: 1, toolCallCount: 0 },
    ]} />);
    expect(container.textContent).toContain("Unknown USD");
    expect(container.textContent).not.toContain("$0");
  });

  it("does not turn a nullable custom currency query into a zero heatmap cell", () => {
    render(<MetricViz viz="heatmap" config={{}} data={[
      { x: "legacy", y: "USD", value: null }, { x: "verified", y: "USD", value: 0 },
    ]} />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
