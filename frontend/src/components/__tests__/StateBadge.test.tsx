import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StateBadge } from "../StateBadge";

describe("StateBadge", () => {
  it("shows Queued state", () => {
    render(<StateBadge state="queued" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("shows Running state", () => {
    render(<StateBadge state="running" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("shows In Review state", () => {
    render(<StateBadge state="review" />);
    expect(screen.getByText("In Review")).toBeInTheDocument();
  });

  it("shows Failed state", () => {
    render(<StateBadge state="failed" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("shows Canceled state", () => {
    render(<StateBadge state="canceled" />);
    expect(screen.getByText("Canceled")).toBeInTheDocument();
  });

  it("shows Approval state for waiting_for_approval", () => {
    render(<StateBadge state="waiting_for_approval" />);
    expect(screen.getByText("Approval")).toBeInTheDocument();
  });

  it("shows Unknown for unrecognized state", () => {
    render(<StateBadge state="some_unknown_state" />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });

  it("renders as a span with correct CSS classes", () => {
    const { container } = render(<StateBadge state="running" />);
    const badge = container.querySelector("span");
    expect(badge).not.toBeNull();
    expect(badge!.className).toContain("bg-blue-900/30");
    expect(badge!.className).toContain("text-blue-400");
  });
});
