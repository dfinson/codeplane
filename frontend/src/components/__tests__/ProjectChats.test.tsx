/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../api/client", () => ({
  attachChatToChain: vi.fn(),
  createChat: vi.fn(),
  detachChatFromChain: vi.fn(),
  fetchChatMessages: vi.fn(),
  fetchChats: vi.fn(),
  fetchProjectTaskLinks: vi.fn(),
  fetchProjects: vi.fn(),
  launchJobFromChat: vi.fn(),
  sendChatTurn: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  attachChatToChain,
  detachChatFromChain,
  fetchChatMessages,
  fetchChats,
  fetchProjectTaskLinks,
  fetchProjects,
  launchJobFromChat,
  sendChatTurn,
} from "../../api/client";
import { ProjectChats } from "../ProjectChats";

const chat = {
  id: "chat-1",
  projectId: "project-1",
  title: "Architecture",
  createdAt: "2026-08-17T10:00:00Z",
  lastMessageAt: "2026-08-17T10:00:00Z",
  status: "open",
  taskLinkId: null,
};

function renderChats() {
  return render(
    <MemoryRouter initialEntries={["/projects/id/project-1/chats/chat-1"]}>
      <Routes>
        <Route path="/projects/id/:projectId/chats/:chatId" element={<ProjectChats />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderChatsAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chats/:chatId" element={<ProjectChats />} />
        <Route path="/projects/id/:projectId/chats/:chatId" element={<ProjectChats />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchChats).mockResolvedValue({ items: [chat] } as any);
  vi.mocked(fetchProjects).mockResolvedValue({
    items: [{ id: "project-1", name: "Payments", repoPaths: ["/repo/payments"] }],
  } as any);
  vi.mocked(fetchChatMessages).mockResolvedValue([]);
  vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
    items: [{
      id: "task-1",
      projectId: "project-1",
      repoPath: "/repo/payments",
      storyNodeId: "5-2-launch",
      dependsOn: [],
      jobId: null,
      trackerTicketRef: null,
      promptOverride: null,
      epicId: "5",
      state: "ready",
      trackerLinkId: null,
      createdAt: "2026-08-17T10:00:00Z",
      updatedAt: "2026-08-17T10:00:00Z",
    }],
  } as any);
});

describe("ProjectChats", () => {
  it.each([false, true])("reconciles a submitted draft after returning before completion, newer edit=%s", async (newerEdit) => {
    const other = { ...chat, id: "chat-2", title: "Other conversation" };
    vi.mocked(fetchChats).mockResolvedValue({ items: [chat, other] });
    let finish!: (value: Awaited<ReturnType<typeof sendChatTurn>>) => void;
    const pending = new Promise<Awaited<ReturnType<typeof sendChatTurn>>>((resolve) => { finish = resolve; });
    vi.mocked(sendChatTurn).mockReturnValueOnce(pending);
    renderChats();
    const first = await screen.findByPlaceholderText("Write a message…");
    await waitFor(() => expect(first).toBeEnabled());
    fireEvent.change(first, { target: { value: "Submitted draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(sendChatTurn).toHaveBeenCalledWith("chat-1", "Submitted draft"));
    fireEvent.click(screen.getByRole("link", { name: /Other conversation/i }));
    await screen.findByRole("heading", { name: "Other conversation" });
    fireEvent.click(screen.getByRole("link", { name: /Architecture/i }));
    await screen.findByRole("heading", { name: "Architecture" });
    const returned = screen.getByPlaceholderText("Write a message…");
    await waitFor(() => expect(returned).toBeEnabled());
    expect(returned).toHaveValue("Submitted draft");
    if (newerEdit) {
      fireEvent.change(returned, { target: { value: "Newer edit" } });
      fireEvent.change(returned, { target: { value: "Submitted draft" } });
    }
    await act(async () => {
      finish({
        userMessage: { id: "sent-1", chatId: "chat-1", role: "user", content: "Submitted draft", createdAt: chat.createdAt },
        assistantMessage: { id: "reply-1", chatId: "chat-1", role: "assistant", content: "Old assistant reply", createdAt: chat.createdAt },
        state: "assistant", error: null,
      });
      await pending;
    });
    expect(returned).toHaveValue(newerEdit ? "Submitted draft" : "");
    expect(screen.queryByText("Old assistant reply")).not.toBeInTheDocument();
    expect(screen.queryByText("Assistant replied")).not.toBeInTheDocument();
  });

  it("does not let a delayed attachment replace the newly selected Chat", async () => {
    const other = { ...chat, id: "chat-2", title: "Other conversation" };
    vi.mocked(fetchChats).mockResolvedValue({ items: [chat, other] });
    let finish!: (value: Awaited<ReturnType<typeof attachChatToChain>>) => void;
    const pending = new Promise<Awaited<ReturnType<typeof attachChatToChain>>>((resolve) => { finish = resolve; });
    vi.mocked(attachChatToChain).mockReturnValueOnce(pending);
    renderChats();
    await screen.findByRole("button", { name: /Attach chain/i });
    fireEvent.change(screen.getByLabelText("Task Recipe chain"), { target: { value: "task-1" } });
    fireEvent.click(screen.getByRole("button", { name: /Attach chain/i }));
    fireEvent.click(screen.getByRole("link", { name: /Other conversation/i }));
    await screen.findByRole("heading", { name: "Other conversation" });
    await act(async () => { finish({ ...chat, taskLinkId: "task-1" }); await pending; });
    expect(screen.getByRole("heading", { name: "Other conversation" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Detach chain/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Task Recipe chain")).toHaveValue("");
  });

  it("does not apply a failed send's delayed history recovery to another Chat", async () => {
    const other = { ...chat, id: "chat-2", title: "Other conversation" };
    vi.mocked(fetchChats).mockResolvedValue({ items: [chat, other] });
    let finish!: (value: Awaited<ReturnType<typeof fetchChatMessages>>) => void;
    const pending = new Promise<Awaited<ReturnType<typeof fetchChatMessages>>>((resolve) => { finish = resolve; });
    let reads = 0;
    vi.mocked(fetchChatMessages).mockImplementation(async (id) => {
      if (id === "chat-1" && ++reads > 1) return pending;
      return [];
    });
    vi.mocked(sendChatTurn).mockRejectedValueOnce(new Error("Old request failed"));
    renderChats();
    const input = await screen.findByPlaceholderText("Write a message…");
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "For the first Chat" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(reads).toBe(2));
    fireEvent.click(screen.getByRole("link", { name: /Other conversation/i }));
    await screen.findByRole("heading", { name: "Other conversation" });
    await act(async () => { finish([]); await pending; });
    expect(screen.queryByText("Old request failed")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Write a message…")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  });

  it("keeps unsent drafts scoped to their exact Chat", async () => {
    const other = { ...chat, id: "chat-2", title: "Other conversation" };
    vi.mocked(fetchChats).mockResolvedValue({ items: [chat, other] });
    renderChats();
    const first = await screen.findByPlaceholderText("Write a message…");
    await waitFor(() => expect(first).toBeEnabled());
    fireEvent.change(first, { target: { value: "First draft" } });
    fireEvent.click(screen.getByRole("link", { name: /Other conversation/i }));
    await screen.findByRole("heading", { name: "Other conversation" });
    const second = screen.getByPlaceholderText("Write a message…");
    await waitFor(() => expect(second).toBeEnabled());
    expect(second).toHaveValue("");
    fireEvent.change(second, { target: { value: "Second draft" } });
    fireEvent.click(screen.getByRole("link", { name: /Architecture/i }));
    await screen.findByRole("heading", { name: "Architecture" });
    expect(screen.getByPlaceholderText("Write a message…")).toHaveValue("First draft");
  });

  it("retries a failed read without sending another conversation command", async () => {
    vi.mocked(fetchChatMessages).mockRejectedValueOnce(new Error("Messages unavailable"));
    renderChats();
    expect(await screen.findByText("Messages unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No messages yet.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry loading messages" }));
    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
    expect(sendChatTurn).not.toHaveBeenCalled();
    expect(launchJobFromChat).not.toHaveBeenCalled();
  });

  it("loads a deep-linked Chat within its owning Project", async () => {
    renderChats();

    expect(await screen.findAllByText("Architecture")).toHaveLength(2);
    expect(fetchChats).toHaveBeenCalledWith("project-1");
    expect(fetchChatMessages).toHaveBeenCalledWith("chat-1");
    expect(screen.getAllByText("Payments")).toHaveLength(2);
  });

  it("renders the persisted assistant response after sending", async () => {
    vi.mocked(sendChatTurn).mockResolvedValue({
      userMessage: {
        id: "message-1",
        chatId: "chat-1",
        role: "user",
        content: "What should we do?",
        createdAt: "2026-08-17T10:01:00Z",
      },
      assistantMessage: {
        id: "message-2",
        chatId: "chat-1",
        role: "assistant",
        content: "Compare the tradeoffs first.",
        createdAt: "2026-08-17T10:01:01Z",
      },
      state: "assistant",
      error: null,
    });
    renderChats();

    const input = await screen.findByPlaceholderText("Write a message…");
    fireEvent.change(input, { target: { value: "What should we do?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Compare the tradeoffs first.")).toBeInTheDocument();
    expect(screen.getByText("Assistant replied")).toBeInTheDocument();
  });

  it("shows an explicit assistant failure and keeps the draft for retry", async () => {
    vi.mocked(sendChatTurn).mockResolvedValue({
      userMessage: {
        id: "message-1",
        chatId: "chat-1",
        role: "user",
        content: "Try this",
        createdAt: "2026-08-17T10:01:00Z",
      },
      assistantMessage: null,
      state: "error",
      error: "Assistant unavailable",
    });
    renderChats();

    const input = await screen.findByPlaceholderText("Write a message…");
    fireEvent.change(input, { target: { value: "Try this" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Assistant unavailable")).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveValue("Try this"));
  });

  it("requires an explicit repository and launches a Job from the Chat", async () => {
    vi.mocked(launchJobFromChat).mockResolvedValue({ id: "job-1" } as any);
    renderChats();

    const launch = await screen.findByRole("button", { name: /Launch Job/i });
    expect(launch).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Repository for launched Job"), {
      target: { value: "/repo/payments" },
    });
    fireEvent.click(launch);

    await waitFor(() => {
      expect(launchJobFromChat).toHaveBeenCalledWith("chat-1", { repo: "/repo/payments" });
    });
  });

  it("attaches and detaches the selected Task Recipe chain", async () => {
    vi.mocked(attachChatToChain).mockResolvedValue({ ...chat, taskLinkId: "task-1" } as any);
    vi.mocked(detachChatFromChain).mockResolvedValue(chat as any);
    renderChats();

    await screen.findByRole("button", { name: /Attach chain/i });
    fireEvent.change(screen.getByLabelText("Task Recipe chain"), {
      target: { value: "task-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Attach chain/i }));

    await waitFor(() => expect(attachChatToChain).toHaveBeenCalledWith("chat-1", "task-1"));
    fireEvent.click(screen.getByRole("button", { name: /Detach chain/i }));
    await waitFor(() => expect(detachChatFromChain).toHaveBeenCalledWith("chat-1"));
  });

  it("exposes scoped actions for an unscoped Chat after explicit Project selection", async () => {
    const unscoped = { ...chat, projectId: null };
    vi.mocked(fetchChats).mockResolvedValue({ items: [unscoped] } as any);
    vi.mocked(launchJobFromChat).mockResolvedValue({ id: "job-1" } as any);

    renderChatsAt("/chats/chat-1");

    const projectSelect = await screen.findByLabelText("Project for Chat action");
    expect(screen.getByRole("button", { name: /Launch Job/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Attach chain/i })).toBeDisabled();

    fireEvent.change(projectSelect, { target: { value: "project-1" } });
    await waitFor(() =>
      expect(fetchProjectTaskLinks).toHaveBeenCalledWith("project-1"),
    );
    fireEvent.change(screen.getByLabelText("Repository for launched Job"), {
      target: { value: "/repo/payments" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Launch Job/i }));

    await waitFor(() =>
      expect(launchJobFromChat).toHaveBeenCalledWith("chat-1", {
        repo: "/repo/payments",
      }),
    );
  });

  it("ignores a stale message fetch after selecting another Chat", async () => {
    const chatTwo = {
      ...chat,
      id: "chat-2",
      title: "Second chat",
    };
    vi.mocked(fetchChats).mockResolvedValue({ items: [chat, chatTwo] } as any);
    let resolveFirst!: (messages: any[]) => void;
    vi.mocked(fetchChatMessages).mockImplementation((id) => {
      if (id === "chat-1") {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve([{
        id: "new-message",
        chatId: "chat-2",
        role: "user",
        content: "Current selection",
        createdAt: "2026-08-17T10:01:00Z",
      }] as any);
    });

    renderChats();
    await screen.findByText("Second chat");
    fireEvent.click(screen.getByRole("link", { name: /Second chat/i }));
    expect(await screen.findByText("Current selection")).toBeInTheDocument();

    await act(async () => {
      resolveFirst([{
        id: "stale-message",
        chatId: "chat-1",
        role: "user",
        content: "Stale selection",
        createdAt: "2026-08-17T10:00:00Z",
      }]);
    });

    expect(screen.queryByText("Stale selection")).not.toBeInTheDocument();
    expect(screen.getByText("Current selection")).toBeInTheDocument();
  });
});
