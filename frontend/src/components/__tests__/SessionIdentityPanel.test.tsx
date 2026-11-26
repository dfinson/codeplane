import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { toast } from "sonner";
import { SessionIdentityPanel } from "../SessionIdentityPanel";
import { useSessionCoordination } from "../../store/sessionCoordination";
import { fetchPeerMessages, fetchSessionDirectory, relayPeerMessage } from "../../api/sessionCoordination";
import type { Job, SessionDirectoryResponse } from "../../api/types";

vi.mock("../../api/sessionCoordination", () => ({
  fetchPeerMessages: vi.fn(),
  fetchSessionDirectory: vi.fn(),
  relayPeerMessage: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const job: Job = {
  id: "job-1", repo: "/repo", prompt: "Fix bug", state: "running",
  sdk: "copilot", source: "managed", sessionCount: 1, mode: "standard",
  baseRef: "main", branch: "fix/bug", worktreePath: "/worktree",
  createdAt: "2026-01-01", updatedAt: "2026-01-01", completedAt: null,
};
const directory: SessionDirectoryResponse = {
  job: { ...job, source: "managed", sdkSessionId: "sdk-new", sessionCount: 2 },
  parent: { ...job, id: "parent", title: "Original task" },
  children: [{ ...job, id: "child", title: "Follow-up task" }],
  peers: [{ ...job, id: "peer", title: "API task" }],
  childrenHasMore: false, peersHasMore: false,
};

async function openPanel() {
  render(<MemoryRouter><SessionIdentityPanel job={job} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Session details and peers" }));
  await screen.findByText("sdk-new");
}

describe("SessionIdentityPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionCoordination.setState({ directories: {}, messages: {}, errors: {}, loading: {} });
    vi.mocked(fetchSessionDirectory).mockResolvedValue(directory);
    vi.mocked(fetchPeerMessages).mockResolvedValue({ items: [], hasMore: false });
  });

  it("copies current SDK identity separately from internal job links and navigates follow-ups", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await openPanel();
    fireEvent.click(screen.getByRole("button", { name: "Copy Current SDK session ID" }));
    expect(writeText).toHaveBeenCalledWith("sdk-new");
    fireEvent.click(screen.getByRole("button", { name: "Copy Internal link" }));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/jobs/job-1`);
    expect(screen.getByRole("link", { name: "Original task" })).toHaveAttribute("href", "/jobs/parent");
    expect(screen.getByRole("link", { name: "Follow-up task" })).toHaveAttribute("href", "/jobs/child");
  });

  it("reports clipboard rejection instead of claiming success", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    await openPanel();
    fireEvent.click(screen.getByRole("button", { name: "Copy Job ID" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("keeps the message ID for retry after a lost response and reports SDK acceptance precisely", async () => {
    vi.mocked(relayPeerMessage).mockRejectedValueOnce(new Error("Lost response")).mockResolvedValueOnce({
      messageId: "receipt", senderJobId: "job-1", recipientJobId: "peer",
      senderSessionId: null, recipientSessionId: "peer-sdk", origin: "operator",
      content: "API ready", replyTo: null, status: "accepted",
      detail: "Queued by the recipient SDK; not confirmed read or acknowledged", createdAt: "2026-01-01",
    });
    await openPanel();
    fireEvent.change(screen.getByLabelText("Recipient in this project"), { target: { value: "peer" } });
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "API ready" } });
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Lost response"));
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await screen.findByText(/accepted: Queued by the recipient SDK/);
    expect(vi.mocked(relayPeerMessage).mock.calls[0]?.[1].messageId)
      .toBe(vi.mocked(relayPeerMessage).mock.calls[1]?.[1].messageId);
  });

  it("surfaces directory failures and offers an explicit refresh", async () => {
    vi.mocked(fetchSessionDirectory).mockRejectedValueOnce(new Error("Unavailable"));
    render(<MemoryRouter><SessionIdentityPanel job={job} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Session details and peers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Refresh session details" }));
    await screen.findByText("sdk-new");
  });

  it("preserves the reserved message ID when retrying an unknown delivery", async () => {
    vi.mocked(relayPeerMessage).mockResolvedValue({
      messageId: "receipt", senderJobId: "job-1", recipientJobId: "peer",
      senderSessionId: null, recipientSessionId: "peer-sdk", origin: "operator",
      content: "API ready", replyTo: null, status: "unknown",
      detail: "Delivery could not be confirmed", createdAt: "2026-01-01",
    });
    await openPanel();
    fireEvent.change(screen.getByLabelText("Recipient in this project"), { target: { value: "peer" } });
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "API ready" } });
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await screen.findByText(/unknown: Delivery could not be confirmed/);
    await waitFor(() => expect(screen.getByRole("button", { name: "Send peer note" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await waitFor(() => expect(relayPeerMessage).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(relayPeerMessage).mock.calls;
    expect(calls[0]?.[1].messageId).toBeTruthy();
    expect(calls[1]?.[1].messageId).toBe(calls[0]?.[1].messageId);
  });

  it("does not carry a pending note or retry identity into another job", async () => {
    vi.mocked(relayPeerMessage).mockRejectedValue(new Error("Lost response"));
    const renderPanel = (current: Job) => <MemoryRouter><SessionIdentityPanel job={current} /></MemoryRouter>;
    const { rerender } = render(renderPanel(job));
    fireEvent.click(screen.getByRole("button", { name: "Session details and peers" }));
    await screen.findByText("sdk-new");
    const sendNote = () => {
      fireEvent.change(screen.getByLabelText("Recipient in this project"), { target: { value: "peer" } });
      fireEvent.change(screen.getByLabelText("Note"), { target: { value: "API ready" } });
      fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    };
    sendNote();
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    const nextJob = { ...job, id: "job-2" };
    vi.mocked(fetchSessionDirectory).mockResolvedValue({ ...directory, job: nextJob });
    rerender(renderPanel(nextJob));
    expect(screen.getByRole("button", { name: "Session details and peers" })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "Session details and peers" }));
    expect(await screen.findByLabelText("Note")).toHaveValue("");
    expect(screen.getByLabelText("Recipient in this project")).toHaveValue("");
    sendNote();
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(relayPeerMessage).mock.calls;
    expect(calls[0]?.[0]).toBe("job-1");
    expect(calls[1]?.[0]).toBe("job-2");
    expect(calls[1]?.[1].messageId).not.toBe(calls[0]?.[1].messageId);
  });

  it("does not let an older page request replace a newer snapshot", async () => {
    let resolveFirst: ((value: SessionDirectoryResponse) => void) | undefined;
    vi.mocked(fetchSessionDirectory).mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    const nextPage = { ...directory, children: [], peers: [] };
    vi.mocked(fetchSessionDirectory).mockResolvedValueOnce(nextPage);
    const first = useSessionCoordination.getState().load(job.id, 0);
    await useSessionCoordination.getState().load(job.id, 50);
    if (!resolveFirst) throw new Error("First request was not started");
    resolveFirst(directory);
    await first;
    expect(useSessionCoordination.getState().directories[job.id]).toEqual(nextPage);
    expect(useSessionCoordination.getState().loading[job.id]).toBe(false);
  });
});
