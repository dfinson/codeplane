/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useStore } from "../../store";
import type { ApprovalRequest } from "../../store";

// Mock the API client
vi.mock("../../api/client", () => ({
  resolveApproval: vi.fn(),
  trustJob: vi.fn(),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { resolveApproval, trustJob } from "../../api/client";
import { ApprovalBanner } from "../ApprovalBanner";

function makeApproval(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: "apr-1",
    jobId: "job-1",
    description: "Delete important file?",
    proposedAction: "rm -rf /tmp/file",
    requestedAt: "2025-01-01T00:00:00Z",
    resolvedAt: null,
    resolution: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(resolveApproval).mockReset();
  vi.mocked(trustJob).mockReset();
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

describe("ApprovalBanner", () => {
  it("renders nothing when no pending approvals", () => {
    const { container } = render(<ApprovalBanner jobId="job-1" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders approval description", () => {
    useStore.setState({ approvals: { "apr-1": makeApproval() } });
    render(<ApprovalBanner jobId="job-1" />);
    expect(screen.getByText("Delete important file?")).toBeInTheDocument();
  });

  it("renders proposed action in pre block", () => {
    useStore.setState({ approvals: { "apr-1": makeApproval() } });
    render(<ApprovalBanner jobId="job-1" />);
    expect(screen.getByText("rm -rf /tmp/file")).toBeInTheDocument();
  });

  it("shows Approve and Reject buttons", () => {
    useStore.setState({ approvals: { "apr-1": makeApproval() } });
    render(<ApprovalBanner jobId="job-1" />);
    expect(screen.getByText("Approve")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });

  it("calls resolveApproval on Approve click", async () => {
    vi.mocked(resolveApproval).mockResolvedValueOnce({} as any);
    useStore.setState({ approvals: { "apr-1": makeApproval() } });
    render(<ApprovalBanner jobId="job-1" />);
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => {
      expect(resolveApproval).toHaveBeenCalledWith("apr-1", "approved");
    });
  });

  it("calls resolveApproval on Reject click", async () => {
    vi.mocked(resolveApproval).mockResolvedValueOnce({} as any);
    useStore.setState({ approvals: { "apr-1": makeApproval() } });
    render(<ApprovalBanner jobId="job-1" />);
    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() => {
      expect(resolveApproval).toHaveBeenCalledWith("apr-1", "rejected");
    });
  });

  it("calls trustJob on Approve All click", async () => {
    vi.mocked(trustJob).mockResolvedValueOnce({ resolved: 1 });
    useStore.setState({ approvals: { "apr-1": makeApproval() } });
    render(<ApprovalBanner jobId="job-1" />);
    fireEvent.click(screen.getByText("Approve All"));
    await waitFor(() => {
      expect(trustJob).toHaveBeenCalledWith("job-1");
    });
  });

  it("shows pending count", () => {
    useStore.setState({
      approvals: {
        "apr-1": makeApproval({ id: "apr-1" }),
        "apr-2": makeApproval({ id: "apr-2", description: "Another?" }),
      },
    });
    render(<ApprovalBanner jobId="job-1" />);
    expect(screen.getByText("2 pending approvals")).toBeInTheDocument();
  });

  it("does not show resolved approvals", () => {
    useStore.setState({
      approvals: {
        "apr-1": makeApproval({ resolvedAt: "2025-01-01T01:00:00Z", resolution: "approved" }),
      },
    });
    const { container } = render(<ApprovalBanner jobId="job-1" />);
    expect(container.innerHTML).toBe("");
  });
});
