import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";

vi.mock("../../api/client", () => ({
  createManualTaskLink: vi.fn(),
  fetchProjects: vi.fn(),
  fetchCredentials: vi.fn(),
  fetchTrackerLinks: vi.fn(),
  refreshTrackerLink: vi.fn(),
  fetchTrackerOperations: vi.fn(),
  reconcileTrackerOperation: vi.fn(),
  retryTrackerOperation: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  createManualTaskLink,
  fetchCredentials,
  fetchProjects,
  fetchTrackerLinks,
  refreshTrackerLink,
  fetchTrackerOperations,
  reconcileTrackerOperation,
  retryTrackerOperation,
} from "../../api/client";
import type { TrackerOperationResponse } from "../../api/types";
import { useStore } from "../../store";
import { useSSE } from "../../hooks/useSSE";
import { TrackerSyncPanel } from "../TrackerSyncPanel";

function operation(overrides: Partial<TrackerOperationResponse> = {}): TrackerOperationResponse {
  return {
    id: "op-1", operationId: overrides.id ?? "op-1", sourceKey: "source-1",
    projectId: "project-1", projectRef: "project-1",
    jobId: "job-1", jobRef: "job-1",
    taskLinkId: "task-1", taskLinkRef: "task-1",
    trackerLinkId: "link-1", trackerLinkRef: "link-1",
    provider: "github", baseUrl: "https://github.com",
    externalRef: "org/project", credentialIdRef: "credential-1", ticketRef: "org/repo#42",
    action: "comment", targetKind: "comment", value: "  Exact comment\n\nBody with trailing space. ",
    normalizedValue: null, normalizedTarget: overrides.normalizedValue ?? null,
    approvalId: "approval-1", approvalRef: "approval-1", approvalSnapshot: null,
    state: "uncertain", version: 3, retryOfId: null, providerReceipt: null,
    errorCode: "response_lost", errorDetail: "The provider response was lost.",
    reconciliation: null, createdAt: "2026-11-11T12:00:00Z", updatedAt: "2026-11-11T12:01:00Z",
    dispatchStartedAt: "2026-11-11T12:00:30Z", completedAt: null,
    ...overrides,
  };
}

beforeEach(() => {
  useStore.setState({ trackerOperations: {}, trackerOperationLoads: {} });
  vi.mocked(fetchTrackerOperations).mockReset().mockResolvedValue({ items: [] });
  vi.mocked(reconcileTrackerOperation).mockReset();
  vi.mocked(retryTrackerOperation).mockReset();
  vi.mocked(fetchProjects).mockResolvedValue({
    items: [
      {
        id: "project-1",
        name: "Payments",
        repoPaths: ["/repos/payments"],
        createdAt: "2026-08-10T12:00:00Z",
        updatedAt: "2026-08-10T12:00:00Z",
      },
    ],
  });
  vi.mocked(fetchCredentials).mockResolvedValue({
    credentials: [
      {
        id: "credential-1",
        provider: "jira",
        label: "Acme Jira",
        baseUrl: "https://acme.atlassian.net",
        email: "dev@example.com",
        requiresEmailUpdate: false,
        createdAt: "2026-08-10T12:00:00Z",
      },
    ],
  });
  vi.mocked(fetchTrackerLinks).mockResolvedValue({
    trackerLinks: [
      {
        id: "link-1",
        projectId: "project-1",
        credentialId: "credential-1",
        externalRef: "PAY",
        createdAt: "2026-08-10T12:00:00Z",
        summary: {
          trackerLinkId: "link-1",
          tickets: [
            {
              id: "PAY-42",
              title: "Retry settlement",
              status: "In Progress",
              url: "https://acme.atlassian.net/browse/PAY-42",
            },
          ],
          lastSyncedAt: "2026-08-10T12:05:00Z",
          lastError: null,
        },
      },
    ],
  });
  vi.mocked(refreshTrackerLink).mockReset();
  vi.mocked(createManualTaskLink).mockReset();
});

describe("TrackerSyncPanel", () => {
  it("renders project, provider, link, and normalized ticket state", async () => {
    render(<TrackerSyncPanel />);

    expect(await screen.findByText("Payments")).toBeInTheDocument();
    expect(screen.getByText("Acme Jira")).toBeInTheDocument();
    expect(screen.getByText("PAY")).toBeInTheDocument();
    expect(screen.getByText("Retry settlement")).toBeInTheDocument();
    expect(screen.getByText("In Progress")).toBeInTheDocument();
  });

  it("renders never-synced and error states", async () => {
    vi.mocked(fetchTrackerLinks).mockResolvedValue({
      trackerLinks: [
        {
          id: "link-1",
          projectId: "project-1",
          credentialId: "credential-1",
          externalRef: "PAY",
          createdAt: "2026-08-10T12:00:00Z",
          summary: {
            trackerLinkId: "link-1",
            tickets: [],
            lastSyncedAt: null,
            lastError: "Tracker provider request failed",
          },
        },
      ],
    });

    render(<TrackerSyncPanel />);

    expect(await screen.findByText("Tracker provider request failed")).toBeInTheDocument();
    expect(screen.getByText("Never synced")).toBeInTheDocument();
  });

  it("manually refreshes one link and renders the returned state", async () => {
    vi.mocked(refreshTrackerLink).mockResolvedValue({
      trackerLinkId: "link-1",
      tickets: [
        {
          id: "PAY-43",
          title: "New ticket",
          status: "Ready",
          url: null,
        },
      ],
      lastSyncedAt: "2026-08-10T12:10:00Z",
      lastError: null,
    });

    render(<TrackerSyncPanel />);
    const refreshButton = await screen.findByRole("button", { name: "Refresh PAY" });
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(refreshTrackerLink).toHaveBeenCalledWith("project-1", "link-1");
    });
    expect(await screen.findByText("New ticket")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("shows an empty state when no Projects have tracker links", async () => {
    vi.mocked(fetchTrackerLinks).mockResolvedValue({ trackerLinks: [] });

    render(<TrackerSyncPanel />);

    expect(await screen.findByText("No tracker links attached yet.")).toBeInTheDocument();
  });

  it("assigns a synced ticket with its explicit TrackerLink", async () => {
    vi.mocked(createManualTaskLink).mockResolvedValue({ id: "task-1" } as never);
    render(<TrackerSyncPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Assign task for PAY-42" }));
    const createButton = screen.getByRole("button", { name: "Create TaskLink" });
    expect(createButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Task repository"), {
      target: { value: "/repos/payments" },
    });
    fireEvent.change(screen.getByLabelText("Task prompt"), {
      target: { value: "Implement payment retry" },
    });
    fireEvent.click(createButton);

    await waitFor(() => {
      expect(createManualTaskLink).toHaveBeenCalledWith("project-1", {
        repoPath: "/repos/payments",
        trackerLinkId: "link-1",
        trackerTicketRef: "PAY-42",
        promptOverride: "Implement payment retry",
        outputRoutes: [],
      });
    });
  });

  it("persists the explicit tracker-write output route when selected", async () => {
    vi.mocked(createManualTaskLink).mockResolvedValue({ id: "task-1" } as never);
    render(<TrackerSyncPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Assign task for PAY-42" }));
    fireEvent.change(screen.getByLabelText("Task repository"), {
      target: { value: "/repos/payments" },
    });
    fireEvent.change(screen.getByLabelText("Task prompt"), {
      target: { value: "Implement payment retry" },
    });
    fireEvent.click(screen.getByRole("checkbox", {
      name: /approved tracker comment/i,
    }));
    fireEvent.click(screen.getByRole("button", { name: "Create TaskLink" }));

    await waitFor(() => {
      expect(createManualTaskLink).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({ outputRoutes: ["tracker_write"] }),
      );
    });
  });

    it("hydrates pending, failed, applied and uncertain outcomes without changing exact comments", async () => {
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [
        operation(),
        operation({ id: "pending", state: "awaiting_approval", errorCode: null, errorDetail: null }),
        operation({ id: "failed", state: "failed", errorCode: "provider_rejected", errorDetail: "Provider denied this write." }),
        operation({ id: "applied", state: "applied", providerReceipt: { id: "comment-42", url: "https://github.com/org/repo/issues/42#issuecomment-42" } }),
      ] });
      const mounted = render(<TrackerSyncPanel />);
      expect(await screen.findByText("Delivery uncertain")).toBeInTheDocument();
      expect(screen.getByText("Pending approval")).toBeInTheDocument();
      expect(screen.getByText("Failed: not applied")).toBeInTheDocument();
      expect(screen.getByText("Applied")).toBeInTheDocument();
      expect(screen.getByText("Reason: Provider denied this write.")).toBeInTheDocument();
      expect(screen.getByText("comment-42")).toBeInTheDocument();
      const row = screen.getByText("Delivery uncertain").closest("article")!;
      expect(within(row).queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
      expect(row.querySelector("pre")?.textContent).toBe(operation().value);
      expect(within(row).getByRole("link", { name: "job-1" })).toHaveAttribute("href", "/jobs/job-1");
      expect(within(row).getByText("task-1")).toBeInTheDocument();
      mounted.unmount();
      act(() => useStore.setState({ trackerOperations: {}, trackerOperationLoads: {} }));
      render(<TrackerSyncPanel />);
      expect(await screen.findByText("Delivery uncertain")).toBeInTheDocument();
      expect(screen.getByText("Pending approval")).toBeInTheDocument();
    });

    it("distinguishes GitHub Project Status from the issue-state mutation without changing Jira labels", async () => {
      vi.mocked(fetchCredentials).mockResolvedValue({ credentials: [{
        id: "credential-1", provider: "github", label: "GitHub",
        baseUrl: "https://github.com", email: null, requiresEmailUpdate: false,
        createdAt: "2026-11-11T00:00:00Z",
      }] });
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [
        operation({ action: "transition", targetKind: "github_issue_state", value: "Done", normalizedValue: "closed" }),
      ] });
      render(<TrackerSyncPanel />);
      expect(await screen.findByText("Project Status: In Progress")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "org/repo#42: Issue state: closed" })).toBeInTheDocument();
      expect(screen.getByText("Done")).toBeInTheDocument();
      expect(screen.getByText(/This operation does not change Project Status/)).toBeInTheDocument();
    });

    it("retains detached operations and deleted references without broken Job links", async () => {
      vi.mocked(fetchTrackerLinks).mockResolvedValue({ trackerLinks: [] });
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [
        operation({ trackerLinkId: null, jobId: null, taskLinkId: null, approvalId: null }),
      ] });
      render(<TrackerSyncPanel />);
      expect(await screen.findByText("Delivery uncertain")).toBeInTheDocument();
      expect(screen.getByText("link-1 (detached or deleted)")).toBeInTheDocument();
      expect(screen.getByText("job-1 (deleted)")).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /job-1/ })).not.toBeInTheDocument();
    });

    it.each(["applied", "not_applied"] as const)("records %s only with evidence, uses the observed version, and focuses the resulting status", async (resolution) => {
      const current = operation();
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [current] });
      vi.mocked(reconcileTrackerOperation).mockResolvedValue(operation({ state: resolution, version: 4 }));
      render(<TrackerSyncPanel />);
      const evidence = await screen.findByRole("textbox", { name: "Reconciliation evidence" });
      const button = screen.getByRole("button", { name: resolution === "applied" ? "Record applied" : "Record not applied" });
      expect(button).toBeDisabled();
      fireEvent.change(evidence, { target: { value: "  Checked provider history and receipt #42.  " } });
      button.focus();
      expect(button).toHaveFocus();
      fireEvent.click(button);
      await waitFor(() => expect(reconcileTrackerOperation).toHaveBeenCalledWith("project-1", "op-1", {
        expectedVersion: 3, resolution, evidence: "  Checked provider history and receipt #42.  ",
      }));
      const status = await screen.findByText(resolution === "applied" ? "Applied" : "Confirmed not applied");
      await waitFor(() => expect(status).toHaveFocus());
      expect(screen.queryByRole("textbox", { name: "Reconciliation evidence" })).not.toBeInTheDocument();
      expect(retryTrackerOperation).not.toHaveBeenCalled();
    });

    it("keeps evidence on conflict, refreshes the version, and does not fabricate applied state", async () => {
      vi.mocked(fetchTrackerOperations)
        .mockResolvedValueOnce({ items: [operation()] })
        .mockResolvedValue({ items: [operation({ version: 4 })] });
      vi.mocked(reconcileTrackerOperation).mockRejectedValueOnce(new Error("Operation version conflict"));
      render(<TrackerSyncPanel />);
      const evidence = await screen.findByRole("textbox", { name: "Reconciliation evidence" });
      fireEvent.change(evidence, { target: { value: "Provider receipt #42" } });
      fireEvent.click(screen.getByRole("button", { name: "Record applied" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Operation version conflict");
      expect(evidence).toHaveValue("Provider receipt #42");
      expect(screen.getByText("Delivery uncertain")).toBeInTheDocument();
      expect(screen.queryByText("Applied")).not.toBeInTheDocument();
      vi.mocked(reconcileTrackerOperation).mockResolvedValue(operation({ state: "applied", version: 5 }));
      await waitFor(() => expect(screen.getByRole("button", { name: "Record applied" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Record applied" }));
      await waitFor(() => expect(reconcileTrackerOperation).toHaveBeenLastCalledWith("project-1", "op-1", {
        expectedVersion: 4, resolution: "applied", evidence: "Provider receipt #42",
      }));
    });

    it.each(["failed", "not_applied"] as const)("requests a fresh approved retry from %s without overwriting the original", async (state) => {
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [operation({ state })] });
      vi.mocked(retryTrackerOperation).mockResolvedValue(operation({
        id: "retry-1", retryOfId: "op-1", state: "awaiting_approval", version: 0,
      }));
      render(<TrackerSyncPanel />);
      fireEvent.click(await screen.findByRole("button", { name: "Request retry with fresh approval" }));
      await waitFor(() => expect(retryTrackerOperation).toHaveBeenCalledWith("project-1", "op-1", {
        expectedVersion: 3, requestId: expect.any(String),
      }));
      expect(await screen.findByText("Pending approval")).toBeInTheDocument();
      expect(useStore.getState().trackerOperations["op-1"]?.state).toBe(state);
      expect(useStore.getState().trackerOperations["retry-1"]?.retryOfId).toBe("op-1");
      expect(useStore.getState().trackerOperations["retry-1"]?.value).toBe(operation().value);
      expect(screen.queryByText("Applied")).not.toBeInTheDocument();
    });

    it("reuses the retry request identity after response loss and retains visible failure details", async () => {
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [operation({ state: "failed" })] });
      vi.mocked(retryTrackerOperation).mockRejectedValueOnce(new Error("Response lost"))
        .mockResolvedValue(operation({ id: "retry-1", state: "awaiting_approval", retryOfId: "op-1", version: 0 }));
      render(<TrackerSyncPanel />);
      fireEvent.click(await screen.findByRole("button", { name: "Request retry with fresh approval" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Response lost");
      const requestId = vi.mocked(retryTrackerOperation).mock.calls[0]![2].requestId;
      await waitFor(() => expect(screen.getByRole("button", { name: "Request retry with fresh approval" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Request retry with fresh approval" }));
      await waitFor(() => expect(retryTrackerOperation).toHaveBeenLastCalledWith("project-1", "op-1", {
        expectedVersion: 3, requestId,
      }));
    });

    it("keeps persisted outcomes visible after a failed reload", async () => {
      vi.mocked(fetchTrackerOperations).mockResolvedValueOnce({ items: [operation()] })
        .mockRejectedValue(new Error("History unavailable"));
      render(<TrackerSyncPanel />);
      expect(await screen.findByText("Delivery uncertain")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Reload operations for project-1" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
      expect(screen.getByText("Delivery uncertain")).toBeInTheDocument();
    });

    it("keeps history project-scoped and applies central SSE updates rather than component copies", async () => {
      vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [operation()] });
      render(<TrackerSyncPanel />);
      expect(await screen.findByText("Delivery uncertain")).toBeInTheDocument();
      act(() => {
        useStore.getState().dispatchSSEEvent("tracker.operation_updated", {
          operation: operation({ state: "applied", version: 4 }),
        });
      });
      expect(screen.getByText("Applied")).toBeInTheDocument();
      act(() => {
        useStore.getState().dispatchSSEEvent("tracker.operation_updated", {
          operation: operation({ state: "dispatching", version: 2 }),
        });
      });
      expect(screen.getByText("Applied")).toBeInTheDocument();
      expect(screen.queryByText("Dispatch in progress: delivery not confirmed")).not.toBeInTheDocument();
    });

  describe("tracker operation SSE reconnect hydration", () => {
    it("registers the dotted event and rehydrates on manual global reconnect", async () => {
      class FakeEventSource {
        static instances: FakeEventSource[] = [];
        onopen: (() => void) | null = null;
        onerror: (() => void) | null = null;
        listeners = new Map<string, (event: MessageEvent) => void>();
        close = vi.fn();
        constructor() { FakeEventSource.instances.push(this); }
        addEventListener(kind: string, listener: (event: MessageEvent) => void) {
          this.listeners.set(kind, listener);
        }
      }
      vi.stubGlobal("EventSource", FakeEventSource);
      const { result, unmount } = renderHook(() => useSSE());
      try {
        await act(async () => { await useStore.getState().loadTrackerOperations("project-1"); });
        act(() => { FakeEventSource.instances[0]!.onopen?.(); });
        await waitFor(() => expect(fetchTrackerOperations).toHaveBeenCalledTimes(2));
        expect(FakeEventSource.instances[0]!.listeners.has("tracker.operation_updated")).toBe(true);
        act(() => {
          FakeEventSource.instances[0]!.listeners.get("tracker.operation_updated")!(new MessageEvent("tracker.operation_updated", {
            data: JSON.stringify({ kind: "tracker.operation_updated", session_id: null, payload: { operation: operation() } }),
          }));
        });
        await waitFor(() => expect(useStore.getState().trackerOperations["op-1"]?.state).toBe("uncertain"));
        vi.mocked(fetchTrackerOperations).mockResolvedValue({ items: [operation({ state: "applied", version: 5 })] });
        act(() => result.current.reconnect());
        act(() => { FakeEventSource.instances[1]!.onopen?.(); });
        await waitFor(() => expect(useStore.getState().trackerOperations["op-1"]?.state).toBe("applied"));
        expect(fetchTrackerOperations).toHaveBeenCalledTimes(3);
      } finally {
        unmount();
        vi.unstubAllGlobals();
      }
    });
  });
});
