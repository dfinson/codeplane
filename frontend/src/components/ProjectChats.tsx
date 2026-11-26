import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertCircle, CheckCircle2, Link2, MessageSquare, Play, Send, Unlink } from "lucide-react";
import { toast } from "sonner";
import {
  attachChatToChain,
  createChat,
  detachChatFromChain,
  fetchChatMessages,
  fetchChats,
  fetchProjectTaskLinks,
  fetchProjects,
  launchJobFromChat,
  sendChatTurn,
} from "../api/client";
import type { Chat, ChatMessage, ProjectResponse, TaskLinkResponse } from "../api/types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";

type SendState = "idle" | "sending" | "awaiting-assistant" | "assistant" | "error";
type ChatAction = "idle" | "launching" | "attaching" | "detaching";

export function ProjectChats() {
  const { projectId, chatId } = useParams<{ projectId?: string; chatId?: string }>();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [selected, setSelected] = useState<Chat | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [taskLinks, setTaskLinks] = useState<TaskLinkResponse[]>([]);
  const [newChatProjectId, setNewChatProjectId] = useState(projectId ?? "");
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [sendState, setSendState] = useState<SendState>("idle");
  const [sendError, setSendError] = useState<string | null>(null);
  const [launchRepo, setLaunchRepo] = useState("");
  const [actionProjectId, setActionProjectId] = useState(projectId ?? "");
  const [chainTaskLinkId, setChainTaskLinkId] = useState("");
  const [chatAction, setChatAction] = useState<ChatAction>("idle");
  const [actionError, setActionError] = useState<string | null>(null);
  const selectionRequest = useRef(0);
  const listRequest = useRef(0);
  const taskLinksRequest = useRef(0);
  const actionRequest = useRef(0);
  const sendRequest = useRef(0);
  const messageReadRequest = useRef(0);
  const createRequest = useRef(0);
  const activeChatId = useRef<string | null>(null);
  const drafts = useRef(new Map<string, { text: string; version: number }>());
  const draftGeneration = useRef(0);
  const invalidateSelection = useCallback(() => {
    selectionRequest.current += 1;
    actionRequest.current += 1;
    sendRequest.current += 1;
    messageReadRequest.current += 1;
    createRequest.current += 1;
    activeChatId.current = null;
  }, []);

  useEffect(() => setNewChatProjectId(projectId ?? ""), [projectId]);

  const loadMessages = useCallback(async (chat: Chat, request: number) => {
    const readRequest = ++messageReadRequest.current;
    const isCurrent = () => selectionRequest.current === request && messageReadRequest.current === readRequest;
    setMessagesLoading(true);
    setMessagesError(null);
    try {
      const nextMessages = await fetchChatMessages(chat.id);
      if (nextMessages.some((item) => item.chatId !== chat.id)) throw new Error("Response belongs to another Chat.");
      if (isCurrent()) setMessages(nextMessages);
    } catch (error) {
      if (isCurrent()) setMessagesError(error instanceof Error ? error.message : String(error));
    } finally {
      if (isCurrent()) setMessagesLoading(false);
    }
  }, []);

  const selectChat = useCallback(async (chat: Chat) => {
    const request = ++selectionRequest.current;
    activeChatId.current = chat.id;
    actionRequest.current += 1;
    sendRequest.current += 1;
    setSelected(chat);
    setMessages([]);
    setMessage(drafts.current.get(chat.id)?.text ?? "");
    setSendState("idle");
    setSendError(null);
    setLaunchRepo("");
    setActionProjectId(chat.projectId ?? "");
    setChainTaskLinkId(chat.taskLinkId ?? "");
    setActionError(null);
    setChatAction("idle");
    await loadMessages(chat, request);
  }, [loadMessages]);

  useEffect(() => {
    let cancelled = false;
    const request = ++listRequest.current;
    invalidateSelection();
    setLoading(true);
    setListError(null);
    setChats([]);
    setProjects([]);
    setSelected(null);
    setMessages([]);
    setTaskLinks([]);
    Promise.all([fetchChats(projectId), fetchProjects()])
      .then(([chatResponse, projectResponse]) => {
        if (cancelled || listRequest.current !== request) return;
        setChats(chatResponse.items);
        setProjects(projectResponse.items);
        if (chatId) {
          const deepLinked = chatResponse.items.find((chat) => chat.id === chatId);
          if (deepLinked) void selectChat(deepLinked);
          else toast.error("Chat not found in this Project.");
        }
      })
      .catch(() => {
        if (!cancelled && listRequest.current === request) {
          setChats([]);
          setProjects([]);
          setSelected(null);
          setMessages([]);
          setListError("Failed to load chats.");
          toast.error("Failed to load chats");
        }
      })
      .finally(() => {
        if (!cancelled && listRequest.current === request) setLoading(false);
      });
    return () => {
      cancelled = true;
      invalidateSelection();
    };
  }, [chatId, invalidateSelection, projectId, selectChat, reload]);

  useEffect(() => {
    const request = ++taskLinksRequest.current;
    setTaskLinks([]);
    setLaunchRepo("");
    if (!actionProjectId) {
      setChainTaskLinkId("");
      return;
    }
    void fetchProjectTaskLinks(actionProjectId)
      .then(({ items }) => {
        if (taskLinksRequest.current === request) setTaskLinks(items);
      })
      .catch((error) => {
        if (taskLinksRequest.current === request) {
          setTaskLinks([]);
          setChainTaskLinkId("");
          setActionError(error instanceof Error ? error.message : String(error));
        }
      });
  }, [actionProjectId]);

  function chatUrl(chat: Chat): string {
    return chat.projectId
      ? `/projects/id/${encodeURIComponent(chat.projectId)}/chats/${encodeURIComponent(chat.id)}`
      : `/chats/${encodeURIComponent(chat.id)}`;
  }

  async function startChat() {
    if (!title.trim()) return;
    const request = ++createRequest.current;
    const selection = selectionRequest.current;
    const targetProject = newChatProjectId;
    try {
      const chat = await createChat({
        title: title.trim(),
        projectId: targetProject || null,
      });
      if (createRequest.current !== request || selectionRequest.current !== selection) {
        toast.success(`Chat "${chat.title}" created. Your current selection was not changed.`);
        return;
      }
      if (chat.projectId !== (targetProject || null)) throw new Error("Chat response belongs to another Project.");
      setChats((items) => [chat, ...items]);
      setTitle("");
      navigate(chatUrl(chat));
      await selectChat(chat);
    } catch (error) {
      toast.error(String(error));
    }
  }

  async function sendMessage() {
    const content = message.trim();
    if (!selected || !content || messagesLoading || messagesError || sendState === "sending" || sendState === "awaiting-assistant") return;

    const optimisticId = `sending-${Date.now()}`;
    const request = selectionRequest.current;
    const operation = ++sendRequest.current;
    messageReadRequest.current += 1;
    const selectedChatId = selected.id;
    const draftVersion = drafts.current.get(selectedChatId)?.version;
    const isCurrent = () => selectionRequest.current === request && sendRequest.current === operation;
    const optimistic: ChatMessage = {
      id: optimisticId,
      chatId: selected.id,
      role: "user",
      content,
      createdAt: new Date().toISOString(),
    };
    setMessages((items) => [...items, optimistic]);
    setSendState("sending");
    setSendError(null);
    await Promise.resolve();
    if (!isCurrent()) return;
    setSendState("awaiting-assistant");

    try {
      const turn = await sendChatTurn(selectedChatId, content);
      if (turn.userMessage.chatId !== selectedChatId || (turn.assistantMessage && turn.assistantMessage.chatId !== selectedChatId)) {
        throw new Error("The response belongs to another Chat. Reload this conversation.");
      }
      const submittedDraft = drafts.current.get(selectedChatId);
      if (turn.state !== "error" && submittedDraft && submittedDraft.version === draftVersion) {
        drafts.current.delete(selectedChatId);
        setMessage((visible) => activeChatId.current === selectedChatId
          && !drafts.current.has(selectedChatId) && visible === submittedDraft.text ? "" : visible);
      }
      if (!isCurrent()) return;
      setMessages((items) => [
        ...items.filter((item) => item.id !== optimisticId),
        turn.userMessage,
        ...(turn.assistantMessage ? [turn.assistantMessage] : []),
      ]);
      if (turn.state === "error") {
        setSendState("error");
        setSendError(turn.error ?? "The assistant could not respond.");
        return;
      }

      setMessage((draft) => draft.trim() === content ? "" : draft);
      setSendState("assistant");
    } catch (error) {
      if (!isCurrent()) return;
      try {
        const persisted = await fetchChatMessages(selectedChatId);
        if (isCurrent()) setMessages(persisted);
      } catch {
        if (isCurrent()) {
          setMessages((items) => items.filter((item) => item.id !== optimisticId));
        }
      }
      if (!isCurrent()) return;
      setSendState("error");
      setSendError(error instanceof Error ? error.message : String(error));
    }
  }

  function updateSelectedChat(updated: Chat) {
    setSelected(updated);
    setChats((items) => items.map((item) => item.id === updated.id ? updated : item));
    setChainTaskLinkId(updated.taskLinkId ?? "");
    setActionProjectId(updated.projectId ?? "");
  }

  function invalidateAction() {
    actionRequest.current += 1;
    setChatAction("idle");
    setActionError(null);
  }

  async function launchJob() {
    if (!selected || !launchRepo) {
      setActionError("Select a repository before launching a Job.");
      return;
    }
    setChatAction("launching");
    setActionError(null);
    const operation = ++actionRequest.current;
    const selection = selectionRequest.current;
    const targetChatId = selected.id;
    const isCurrent = () => actionRequest.current === operation && selectionRequest.current === selection;
    try {
      const job = await launchJobFromChat(targetChatId, { repo: launchRepo });
      toast.success("Job launched from Chat");
      if (isCurrent()) navigate(`/jobs/${encodeURIComponent(job.id)}`);
    } catch (error) {
      if (isCurrent()) setActionError(error instanceof Error ? error.message : String(error));
      else toast.error(`Earlier Chat launch: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (isCurrent()) setChatAction("idle");
    }
  }

  async function attachChain() {
    if (!selected || !chainTaskLinkId) {
      setActionError("Select a Task Recipe chain to attach.");
      return;
    }
    setChatAction("attaching");
    setActionError(null);
    const operation = ++actionRequest.current;
    const selection = selectionRequest.current;
    const targetChatId = selected.id;
    const isCurrent = () => actionRequest.current === operation && selectionRequest.current === selection;
    try {
      const updated = await attachChatToChain(targetChatId, chainTaskLinkId);
      if (!isCurrent()) return;
      if (updated.id !== targetChatId) throw new Error("Attachment response belongs to another Chat.");
      updateSelectedChat(updated);
      toast.success("Chat attached to chain");
    } catch (error) {
      if (isCurrent()) setActionError(error instanceof Error ? error.message : String(error));
      else toast.error(`Earlier Chat attachment: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (isCurrent()) setChatAction("idle");
    }
  }

  async function detachChain() {
    if (!selected) return;
    setChatAction("detaching");
    setActionError(null);
    const operation = ++actionRequest.current;
    const selection = selectionRequest.current;
    const targetChatId = selected.id;
    const isCurrent = () => actionRequest.current === operation && selectionRequest.current === selection;
    try {
      const updated = await detachChatFromChain(targetChatId);
      if (!isCurrent()) return;
      if (updated.id !== targetChatId) throw new Error("Detachment response belongs to another Chat.");
      updateSelectedChat(updated);
      toast.success("Chat detached from chain");
    } catch (error) {
      if (isCurrent()) setActionError(error instanceof Error ? error.message : String(error));
      else toast.error(`Earlier Chat detachment: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (isCurrent()) setChatAction("idle");
    }
  }

  if (loading) return <div className="flex justify-center py-20"><Spinner size="lg" /></div>;
  if (listError) return (
    <div role="alert" className="space-y-3 rounded-lg border border-border bg-card p-4">
      <p>{listError}</p>
      <Button variant="secondary" onClick={() => setReload((value) => value + 1)}>Retry loading chats</Button>
    </div>
  );

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <div className="flex items-center gap-2">
        <MessageSquare size={18} />
        <h1 className="text-xl font-semibold">{projectId ? "Project Chats" : "Chats"}</h1>
      </div>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(10rem,14rem)_auto]">
        <Input
          value={title}
          onChange={(event) => { createRequest.current += 1; setTitle(event.target.value); }}
          placeholder="New chat title"
          onKeyDown={(event) => event.key === "Enter" && void startChat()}
        />
        <select
          value={newChatProjectId}
          onChange={(event) => { createRequest.current += 1; setNewChatProjectId(event.target.value); }}
          aria-label="Chat Project"
          className="h-9 rounded-md border border-border bg-background px-2 text-xs text-foreground"
        >
          <option value="">Unscoped</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>{project.name}</option>
          ))}
        </select>
        <Button onClick={() => void startChat()}>Start</Button>
      </div>
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <div className="rounded-lg border border-border bg-card p-2 space-y-1">
          {chats.length === 0 ? <p className="p-4 text-sm text-muted-foreground">No chats yet</p> : chats.map((chat) => (
            <Link
              key={chat.id}
              to={chatUrl(chat)}
              onClick={() => void selectChat(chat)}
              className={`block w-full text-left rounded-md px-3 py-2 text-sm ${selected?.id === chat.id ? "bg-accent" : "hover:bg-accent/50"}`}
            >
              <span className="block truncate">{chat.title}</span>
              <span className="text-xs text-muted-foreground">{new Date(chat.lastMessageAt).toLocaleString()}</span>
            </Link>
          ))}
        </div>
        <div className="rounded-lg border border-border bg-card p-4 min-h-40">
          {selected ? (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-semibold">{selected.title}</h2>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{projects.find((project) => project.id === selected.projectId)?.name ?? "Unscoped"}</span>
                  {selected.projectId && selected.taskLinkId && (
                    <Link
                      to={`/projects/id/${encodeURIComponent(selected.projectId)}/board/task/${encodeURIComponent(selected.taskLinkId)}`}
                      className="hover:text-foreground"
                    >
                      Supervising chain
                    </Link>
                  )}
                </div>
              </div>
              <div className="mt-4 grid gap-3 rounded-md border border-border bg-background p-3">
                  {!selected.projectId && (
                    <select
                      aria-label="Project for Chat action"
                      value={actionProjectId}
                      onChange={(event) => {
                        invalidateAction();
                        setActionProjectId(event.target.value);
                        setChainTaskLinkId("");
                        setActionError(null);
                      }}
                      className="h-9 rounded-md border border-border bg-background px-2 text-xs"
                    >
                      <option value="">Select Project for actions…</option>
                      {projects.map((project) => (
                        <option key={project.id} value={project.id}>{project.name}</option>
                      ))}
                    </select>
                  )}
                  <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                    <select
                      aria-label="Repository for launched Job"
                      value={launchRepo}
                      onChange={(event) => { invalidateAction(); setLaunchRepo(event.target.value); }}
                      className="h-9 rounded-md border border-border bg-background px-2 text-xs"
                    >
                      <option value="">Select repository for Job…</option>
                      {projects.find((project) => project.id === actionProjectId)?.repoPaths.map((repo) => (
                        <option key={repo} value={repo}>{repo}</option>
                      ))}
                    </select>
                    <Button
                      size="sm"
                      onClick={() => void launchJob()}
                      loading={chatAction === "launching"}
                      disabled={!launchRepo || chatAction !== "idle"}
                    >
                      <Play size={12} /> Launch Job
                    </Button>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                    <select
                      aria-label="Task Recipe chain"
                      value={chainTaskLinkId}
                      onChange={(event) => { invalidateAction(); setChainTaskLinkId(event.target.value); }}
                      className="h-9 rounded-md border border-border bg-background px-2 text-xs"
                    >
                      <option value="">Select Task Recipe chain…</option>
                      {taskLinks.map((link) => (
                        <option key={link.id} value={link.id}>
                          {link.storyNodeId ?? link.trackerTicketRef ?? link.id}
                        </option>
                      ))}
                    </select>
                    {selected.taskLinkId ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void detachChain()}
                        loading={chatAction === "detaching"}
                        disabled={chatAction !== "idle"}
                      >
                        <Unlink size={12} /> Detach chain
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void attachChain()}
                        loading={chatAction === "attaching"}
                        disabled={!chainTaskLinkId || chatAction !== "idle"}
                      >
                        <Link2 size={12} /> Attach chain
                      </Button>
                    )}
                  </div>
                  {actionError && (
                    <p role="alert" className="text-xs text-red-500">{actionError}</p>
                  )}
                </div>
              <div className="mt-4 space-y-2 max-h-80 overflow-y-auto" aria-live="polite">
                {messagesLoading ? (
                  <p role="status" className="text-sm text-muted-foreground">Loading messages...</p>
                ) : messagesError ? (
                  <div role="alert" className="space-y-2">
                    <p className="text-sm text-red-500">{messagesError}</p>
                    <Button size="sm" variant="secondary" onClick={() => void loadMessages(selected, selectionRequest.current)}>
                      Retry loading messages
                    </Button>
                  </div>
                ) : messages.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No messages yet.</p>
                ) : messages.map((item) => (
                  <div key={item.id} className={`rounded-md px-3 py-2 text-sm ${item.role === "user" ? "bg-primary/10 ml-6" : "bg-muted mr-6"}`}>
                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{item.role}</p>
                    <p className="whitespace-pre-wrap">{item.content}</p>
                  </div>
                ))}
              </div>
              <div className="flex gap-2 mt-6">
                <Input
                  value={message}
                  onChange={(event) => {
                    setMessage(event.target.value);
                    drafts.current.set(selected.id, { text: event.target.value, version: ++draftGeneration.current });
                  }}
                  placeholder="Write a message…"
                  disabled={messagesLoading || Boolean(messagesError) || sendState === "sending" || sendState === "awaiting-assistant"}
                  onKeyDown={(event) => event.key === "Enter" && void sendMessage()}
                />
                <Button
                  size="sm"
                  onClick={() => void sendMessage()}
                  disabled={messagesLoading || Boolean(messagesError) || sendState === "sending" || sendState === "awaiting-assistant"}
                  aria-label="Send message"
                >
                  <Send size={14} />
                </Button>
              </div>
              <div className="mt-2 min-h-5 text-xs" aria-live="polite">
                {sendState === "sending" && <span className="text-muted-foreground">Sending…</span>}
                {sendState === "awaiting-assistant" && <span className="text-muted-foreground">Waiting for assistant…</span>}
                {sendState === "assistant" && (
                  <span className="inline-flex items-center gap-1 text-green-500"><CheckCircle2 size={12} /> Assistant replied</span>
                )}
                {sendState === "error" && (
                  <span className="inline-flex items-center gap-1 text-red-500"><AlertCircle size={12} /> {sendError}</span>
                )}
              </div>
            </>
          ) : <p className="text-sm text-muted-foreground">Select a chat to view it.</p>}
        </div>
      </div>
    </div>
  );
}
