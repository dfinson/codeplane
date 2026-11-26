import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCall } from "../MetricsPanelTypes";

vi.mock("../../api/client", () => ({
  fetchJobTelemetry: vi.fn(),
  fetchJobContext: vi.fn().mockResolvedValue({
    job: {
      cost: 0, durationMs: 0, diffLinesAdded: 0, diffLinesRemoved: 0, sdk: "", model: "",
      totalTurns: 0, peakTurnCostUsd: 0, avgTurnCostUsd: 0, costFirstHalfUsd: 0, costSecondHalfUsd: 0,
    },
    repoAvg: null,
    flags: [],
  }),
  fetchArtifacts: vi.fn().mockResolvedValue({ items: [] }),
  fetchArtifactContent: vi.fn(),
  fetchSidecarSessionMetrics: vi.fn().mockResolvedValue({
    global: { totalCalls: 0, avgLatencyMs: 0, activeJobs: 0, poolSize: 0, warmTokens: 0 },
    jobs: {},
  }),
}));

import { fetchJobTelemetry } from "../../api/client";
import { MetricsPanel } from "../MetricsPanel";

async function showToolOutcomes(outcomes: ToolCall["success"][]) {
  vi.mocked(fetchJobTelemetry).mockResolvedValue({
    available: true,
    jobId: "observed-job",
    toolCallCount: outcomes.length,
    totalToolDurationMs: outcomes.length * 100,
    toolCalls: outcomes.map((success) => ({
      name: "sample-tool", durationMs: 100, success,
      activity: "overhead", toolCategory: "other", offsetSec: 0,
    })),
  });
  render(<MetricsPanel jobId="observed-job" />);
  fireEvent.click(await screen.findByRole("button", { name: /Tool Breakdown/ }));
  return screen.getByRole("row", { name: /sample-tool/ });
}

describe("MetricsPanel tool outcomes", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows unknown-only outcomes neutrally rather than counting them as failures", async () => {
    const row = await showToolOutcomes([null, null]);
    expect(screen.getByText("2 (2 unknown)")).toBeInTheDocument();
    const outcome = within(row).getByText("Unknown (2)");
    expect(outcome).toHaveClass("text-muted-foreground");
    expect(row.lastElementChild).not.toHaveClass("text-red-400");
    expect(row.lastElementChild).toHaveTextContent(/^Unknown \(2\)$/);
    expect(screen.queryByText(/2 fail/)).not.toBeInTheDocument();
  });

  it("counts only explicit failures while retaining mixed unknown outcomes", async () => {
    const row = await showToolOutcomes([true, false, null]);
    expect(screen.getByText("3 (1 fail) (1 unknown)")).toBeInTheDocument();
    expect(row.lastElementChild).toHaveClass("text-red-400");
    expect(row.lastElementChild).toHaveTextContent(/^1 \(1 unknown\)$/);
    expect(within(row).getByText("(1 unknown)")).toHaveClass("text-muted-foreground");
  });

  it("preserves known success and failure counts and visuals", async () => {
    const row = await showToolOutcomes([true, false, false]);
    expect(screen.getByText("3 (2 fail)")).toBeInTheDocument();
    expect(row.lastElementChild).toHaveTextContent(/^2$/);
    expect(row.lastElementChild).toHaveClass("text-red-400");
    expect(within(row).queryByText(/unknown/i)).not.toBeInTheDocument();
  });
});
