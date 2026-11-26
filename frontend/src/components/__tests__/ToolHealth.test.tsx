import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { AnalyticsTools } from "../../api/client";
import { ToolHealth } from "../analytics/ToolHealth";

function showTool(overrides: Partial<AnalyticsTools["tools"][number]>) {
  render(<ToolHealth tools={[{
    name: "sample-tool",
    count: 0,
    failureCount: 0,
    unknownCount: 0,
    avgDurationMs: 0,
    totalDurationMs: 0,
    p50DurationMs: 0,
    p95DurationMs: 0,
    p99DurationMs: 0,
    ...overrides,
  }]} />);
  return screen.getByRole("row", { name: /sample-tool/ });
}

describe("ToolHealth outcome rates", () => {
  it("shows unknown-only outcomes without invented success or failure rates", () => {
    const row = showTool({ count: 2, unknownCount: 2 });
    expect(within(row).getByText("(2 unknown)")).toHaveClass("text-muted-foreground");
    const outcomes = within(row).getAllByText("Unknown");
    expect(outcomes).toHaveLength(2);
    outcomes.forEach((outcome) => expect(outcome).toHaveClass("text-muted-foreground"));
    expect(within(row).queryByText(/%/)).not.toBeInTheDocument();
  });

  it("uses only known outcomes in mixed rates", () => {
    const row = showTool({ count: 4, failureCount: 1, unknownCount: 2 });
    expect(within(row).getByText("50%")).toHaveClass("text-red-400");
    expect(within(row).getByText("1")).toHaveClass("text-red-400");
    expect(within(row).getByText("(2 unknown)")).toHaveClass("text-muted-foreground");
    expect(within(row).queryByText("75%")).not.toBeInTheDocument();
  });

  it.each([
    { failureCount: 0, rate: "100%", color: "text-green-400" },
    { failureCount: 2, rate: "0%", color: "text-red-400" },
  ])("preserves the known-outcome $rate visual", ({ failureCount, rate, color }) => {
    const row = showTool({ count: 2, failureCount });
    expect(within(row).getByText(rate)).toHaveClass(color);
    expect(within(row).queryByText(/unknown/i)).not.toBeInTheDocument();
  });

  it("does not invent 100% success when no calls were observed", () => {
    const row = showTool({});
    within(row).getAllByText("Unknown").forEach((outcome) => {
      expect(outcome).toHaveClass("text-muted-foreground");
    });
    expect(within(row).queryByText(/%/)).not.toBeInTheDocument();
  });
});
