import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { toast } from "sonner";
import { SessionIdentityPanel } from "../SessionIdentityPanel";
import { useSessionCoordination } from "../../store/sessionCoordination";
import { fetchPeerAgents, fetchPeerMessages, fetchSessionDirectory, relayPeerMessage } from "../../api/sessionCoordination";
import type { Job, PeerMessageResponse, SessionAgentDirectoryResponse, SessionDirectoryResponse } from "../../api/types";

vi.mock("../../api/sessionCoordination", () => ({
  fetchPeerMessages: vi.fn(),
  fetchSessionDirectory: vi.fn(),
  relayPeerMessage: vi.fn(),
  fetchPeerAgents: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const job: Job = {
  id: "job-1", repo: "/repo", prompt: "Fix bug", state: "running",
  sdk: "copilot", source: "managed", sessionCount: 1, mode: "standard",
  observationOnly: false,
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

async function selectRecipient(id = "peer") {
  fireEvent.change(screen.getByLabelText("Recipient in this project"), { target: { value: id } });
  fireEvent.click(await screen.findByRole("radio", { name: `Agent for ${id} running` }));
}

function agentMenu(target = "peer"): SessionAgentDirectoryResponse {
  return {
    jobId: target, observedAt: "2026-01-01", items: [{
      agentId: "primary", jobId: target, name: `Agent for ${target}`, description: "Implement API",
      sdk: "copilot", state: "running", executionToken: `${target}-epoch`, acceptsPeerMessages: true,
      updatedAt: "2026-01-01",
    }],
  };
}

describe("SessionIdentityPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionCoordination.setState({ directories: {}, messages: {}, errors: {}, loading: {} });
    vi.mocked(fetchSessionDirectory).mockResolvedValue(directory);
    vi.mocked(fetchPeerMessages).mockResolvedValue({ items: [], hasMore: false });
    vi.mocked(fetchPeerAgents).mockImplementation(async (_source, target) => agentMenu(target));
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
    await selectRecipient();
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

  it("offers recipe-agent purposes and sends only to the explicitly selected live attempt", async () => {
    const menu = agentMenu();
    menu.items[0] = { ...menu.items[0]!, name: "Reviewer", attemptId: "review", executionToken: "review-token" };
    menu.items.push({ ...menu.items[0]!, agentId: "build", name: "Builder", attemptId: "build", executionToken: "build-token" });
    menu.items.push({ ...menu.items[0]!, agentId: "done", name: "Finished", state: "succeeded", acceptsPeerMessages: false });
    vi.mocked(fetchPeerAgents).mockResolvedValue(menu);
    vi.mocked(relayPeerMessage).mockRejectedValue(new Error("Lost response"));
    await openPanel();
    fireEvent.change(screen.getByLabelText("Recipient in this project"), { target: { value: "peer" } });
    expect(await screen.findByRole("radio", { name: "Finished succeeded" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Review this API" } });
    expect(screen.getByRole("button", { name: "Send peer note" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "Builder running" }));
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(vi.mocked(relayPeerMessage).mock.calls[0]?.[1]).toMatchObject({
      recipientJobId: "peer", recipientAttemptId: "build", recipientExecutionToken: "build-token",
    });
  });

  it("requires reselection after execution changes but can recover the old receipt without losing edited text", async () => {
    vi.mocked(relayPeerMessage).mockRejectedValueOnce(new Error("Lost response")).mockResolvedValueOnce({
      messageId: "receipt", senderJobId: job.id, recipientJobId: "peer", content: "Original",
      senderSessionId: null, recipientSessionId: "sdk-peer", replyTo: null,
      origin: "operator", status: "accepted", detail: "Queued", createdAt: "2026-01-01",
    });
    await openPanel();
    await selectRecipient();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Original" } });
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    const menu = agentMenu();
    menu.items[0]!.executionToken = "replacement";
    vi.mocked(fetchPeerAgents).mockResolvedValue(menu);
    fireEvent.click(screen.getByRole("button", { name: "Refresh agent cards" }));
    await waitFor(() => expect(screen.getByRole("radio")).not.toBeChecked());
    expect(screen.getByRole("button", { name: "Send peer note" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "New unsent draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Check previous delivery receipt" }));
    await screen.findByText("accepted: Queued (message receipt)");
    expect(vi.mocked(relayPeerMessage).mock.calls[1]?.[1]).toEqual(vi.mocked(relayPeerMessage).mock.calls[0]?.[1]);
    expect(screen.getByLabelText("Note")).toHaveValue("New unsent draft");
    fireEvent.click(screen.getByRole("radio"));
    expect(screen.getByRole("button", { name: "Send peer note" })).toBeEnabled();
  });

  it("does not display a late agent response for a previous recipient", async () => {
    let resolveOld!: (value: SessionAgentDirectoryResponse) => void;
    vi.mocked(fetchPeerAgents).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    vi.mocked(fetchSessionDirectory).mockResolvedValue({
      ...directory, peers: [...directory.peers!, { ...job, id: "peer-2" }],
    });
    await openPanel();
    fireEvent.change(screen.getByLabelText("Recipient in this project"), { target: { value: "peer" } });
    await waitFor(() => expect(fetchPeerAgents).toHaveBeenCalledTimes(1));
    await selectRecipient("peer-2");
    await act(async () => { resolveOld(agentMenu()); });
    expect(screen.getByRole("radio", { name: "Agent for peer-2 running" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "Agent for peer running" })).not.toBeInTheDocument();
  });

  it.each([
    { status: "accepted", edit: "content" },
    { status: "rejected", edit: "content" },
    { status: "accepted", edit: "recipient" },
    { status: "accepted", edit: "restore" },
  ] as const)("preserves a $edit draft edit after a delayed $status response", async ({ status, edit }) => {
    let resolveSend: ((value: PeerMessageResponse) => void) | undefined;
    vi.mocked(relayPeerMessage).mockImplementationOnce(() => new Promise((resolve) => { resolveSend = resolve; }));
    vi.mocked(fetchSessionDirectory).mockResolvedValue({
      ...directory, peers: [...(directory.peers ?? []), { ...job, id: "peer-2", title: "Other task" }],
    });
    await openPanel();
    await selectRecipient();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "API ready" } });
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await waitFor(() => expect(relayPeerMessage).toHaveBeenCalledTimes(1));

    if (edit === "recipient") {
      await selectRecipient("peer-2");
    } else {
      fireEvent.change(screen.getByLabelText("Note"), { target: { value: "New unsent note" } });
      if (edit === "restore") {
        fireEvent.change(screen.getByLabelText("Note"), { target: { value: "API ready" } });
      }
    }
    const receipt: PeerMessageResponse = {
      messageId: "receipt", senderJobId: "job-1", recipientJobId: "peer",
      senderSessionId: null, recipientSessionId: "peer-sdk", origin: "operator",
      content: "API ready", replyTo: null, status, detail: "Delivery finished", createdAt: "2026-01-01",
    };
    if (!resolveSend) throw new Error("Send request was not started");
    resolveSend(receipt);
    await screen.findByText(`${status}: Delivery finished (message receipt)`);
    await waitFor(() => expect(screen.getByRole("button", { name: "Send peer note" })).toBeEnabled());
    const draftContent = edit === "content" ? "New unsent note" : "API ready";
    const draftRecipient = edit === "recipient" ? "peer-2" : "peer";
    expect(screen.getByLabelText("Note")).toHaveValue(draftContent);
    expect(screen.getByLabelText("Recipient in this project")).toHaveValue(draftRecipient);

    vi.mocked(relayPeerMessage).mockResolvedValueOnce({
      ...receipt, content: draftContent, recipientJobId: draftRecipient, messageId: "next-receipt",
    });
    fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    await waitFor(() => expect(relayPeerMessage).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(relayPeerMessage).mock.calls;
    expect(calls[1]?.[1]).toMatchObject({ content: draftContent, recipientJobId: draftRecipient });
    expect(calls[1]?.[1].messageId).not.toBe(calls[0]?.[1].messageId);
  });

  it("preserves the reserved message ID when retrying an unknown delivery", async () => {
    vi.mocked(relayPeerMessage).mockResolvedValue({
      messageId: "receipt", senderJobId: "job-1", recipientJobId: "peer",
      senderSessionId: null, recipientSessionId: "peer-sdk", origin: "operator",
      content: "API ready", replyTo: null, status: "unknown",
      detail: "Delivery could not be confirmed", createdAt: "2026-01-01",
    });
    await openPanel();
    await selectRecipient();
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
    const sendNote = async () => {
      await selectRecipient();
      fireEvent.change(screen.getByLabelText("Note"), { target: { value: "API ready" } });
      fireEvent.click(screen.getByRole("button", { name: "Send peer note" }));
    };
    await sendNote();
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    const nextJob = { ...job, id: "job-2" };
    vi.mocked(fetchSessionDirectory).mockResolvedValue({ ...directory, job: nextJob });
    rerender(renderPanel(nextJob));
    expect(screen.getByRole("button", { name: "Session details and peers" })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "Session details and peers" }));
    expect(await screen.findByLabelText("Note")).toHaveValue("");
    expect(screen.getByLabelText("Recipient in this project")).toHaveValue("");
    await sendNote();
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
