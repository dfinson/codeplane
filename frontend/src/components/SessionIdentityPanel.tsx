import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, GitFork, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { fetchPeerAgents, relayPeerMessage } from "../api/sessionCoordination";
import type { PeerMessageRequest, PeerMessageResponse, SessionAgentCard, SessionAgentDirectoryResponse } from "../api/types";
import type { JobSummary } from "../store";
import { useSessionCoordination } from "../store/sessionCoordination";
import { CopilotAccountInfo } from "./CopilotAccountInfo";

const actionClass = "rounded px-2 py-1 text-xs text-primary hover:bg-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50";

function CopyValue({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 break-all select-text">{value}</dd>
      <button
        type="button"
        className={actionClass}
        aria-label={`Copy ${label}`}
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(
            () => toast.success(`${label} copied`),
            () => toast.error(`Could not copy ${label}. Select and copy the value instead.`),
          );
          if (!navigator.clipboard) toast.error("Clipboard is unavailable. Select and copy the value instead.");
        }}
      >
        <Copy size={14} />
      </button>
    </div>
  );
}

export function SessionIdentityPanel({ job }: { job: JobSummary }) {
  return <SessionIdentityDetails key={job.id} job={job} />;
}

function SessionIdentityDetails({ job }: { job: JobSummary }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [recipient, setRecipient] = useState("");
  const [agents, setAgents] = useState<SessionAgentDirectoryResponse | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<SessionAgentCard | null>(null);
  const [agentCursor, setAgentCursor] = useState("");
  const [agentError, setAgentError] = useState<string | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [agentRefresh, setAgentRefresh] = useState(0);
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [receipt, setReceipt] = useState<PeerMessageResponse | null>(null);
  const [pendingRequest, setPendingRequest] = useState<{ key: string; request: PeerMessageRequest; revision: number } | null>(null);
  const draftRevision = useRef(0);
  const directory = useSessionCoordination((state) => state.directories[job.id]);
  const messages = useSessionCoordination((state) => state.messages[job.id]);
  const loading = useSessionCoordination((state) => state.loading[job.id]);
  const error = useSessionCoordination((state) => state.errors[job.id]);
  const load = useSessionCoordination((state) => state.load);

  useEffect(() => {
    if (open) void load(job.id, offset);
  }, [open, job.id, job.state, offset, load]);

  useEffect(() => {
    if (!open || !recipient) return;
    let active = true;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const update = async () => {
      setAgentsLoading(true);
      try {
        const result = await fetchPeerAgents(job.id, recipient, agentCursor);
        if (active) {
          setAgents(result);
          setAgentError(null);
        }
      } catch (failure) {
        if (active) setAgentError(failure instanceof Error ? failure.message : "Could not load agent cards");
      } finally {
        if (active) {
          setAgentsLoading(false);
          refresh = setTimeout(() => { void update(); }, 5000);
        }
      }
    };
    void update();
    return () => { active = false; clearTimeout(refresh); };
  }, [open, job.id, recipient, agentCursor, agentRefresh]);

  const selectedIsAvailable = !agentError && agents?.jobId === recipient && agents.items.some((agent) =>
    agent.agentId === selectedAgent?.agentId && agent.executionToken === selectedAgent.executionToken && agent.acceptsPeerMessages,
  );

  const send = async (checkPreviousReceipt = false) => {
    if (sending) return;
    let pending = pendingRequest;
    if (checkPreviousReceipt) {
      if (!pending) return;
    } else {
      if (!selectedAgent || !selectedIsAvailable) return;
      const key = JSON.stringify([recipient, selectedAgent.attemptId, selectedAgent.executionToken, content]);
      if (pending?.key !== key) {
        pending = {
          key, revision: draftRevision.current,
          request: {
            messageId: crypto.randomUUID(), recipientJobId: recipient, content,
            recipientAttemptId: selectedAgent.attemptId, recipientExecutionToken: selectedAgent.executionToken,
          },
        };
        setPendingRequest(pending);
      }
    }
    if (!pending) return;
    const submittedRevision = checkPreviousReceipt ? pending.revision : draftRevision.current;
    setSending(true);
    try {
      const result = await relayPeerMessage(job.id, pending.request);
      setReceipt(result);
      if (result.status !== "unknown") {
        if (draftRevision.current === submittedRevision) setContent("");
        setPendingRequest(null);
      }
      await load(job.id, offset);
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "Peer message outcome is unconfirmed");
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="mx-3 shrink-0 text-xs" aria-label="Session identity and communication">
      <button
        type="button"
        className={`${actionClass} flex items-center gap-2`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <GitFork size={14} /> Session details and peers
      </button>
      {open && (
        <div className="max-h-[55vh] overflow-y-auto rounded border border-border bg-card p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-semibold">Session identity</h2>
            <button type="button" className={actionClass} disabled={loading} onClick={() => void load(job.id, offset)} aria-label="Refresh session details">
              <RefreshCw size={14} />
            </button>
          </div>
          <dl className="space-y-1">
            <CopyValue label="Job ID" value={job.id} />
            <CopyValue label="Internal link" value={`${window.location.origin}/jobs/${encodeURIComponent(job.id)}`} />
            <CopyValue label="Branch" value={job.branch} />
            <CopyValue label="Worktree path" value={job.worktreePath} />
            <CopyValue label="Current SDK session ID" value={directory?.job.sdkSessionId} />
            <CopyValue label="External session ID" value={directory?.job.externalSessionId} />
          </dl>
          <p className="text-muted-foreground">Internal links require normal app access. They do not create a share token.</p>
          {loading && <p role="status">Loading session details...</p>}
          {error && <p role="alert" className="text-destructive">{error}. Use refresh to retry.</p>}
          {directory && (
            <>
              <p className="text-muted-foreground">
                {directory.job.sessionCount} execution(s). The SDK ID identifies the current or most recent session, not every execution.
              </p>
              <CopilotAccountInfo job={directory.job} />
              <nav aria-label="Follow-up lineage" className="space-y-1">
                <h3 className="font-semibold">Follow-up lineage</h3>
                {directory.parent
                  ? <p>Parent: <Link className={actionClass} to={`/jobs/${encodeURIComponent(directory.parent.id)}`}>{directory.parent.title || directory.parent.id}</Link></p>
                  : <p className="text-muted-foreground">No parent follow-up.</p>}
                {directory.children?.length
                  ? directory.children.map((child) => (
                    <p key={child.id}>Follow-up: <Link className={actionClass} to={`/jobs/${encodeURIComponent(child.id)}`}>{child.title || child.id}</Link> ({child.state})</p>
                  ))
                  : <p className="text-muted-foreground">No follow-ups on this page.</p>}
                <p className="text-muted-foreground">These links describe follow-ups, not ownership or cancellation groups.</p>
              </nav>
              {directory.job.source === "managed" && (
                <form className="space-y-2 border-t border-border pt-3" onSubmit={(event) => { event.preventDefault(); void send(); }}>
                  <h3 className="font-semibold">Relay a peer note</h3>
                  <p className="text-muted-foreground">Sent by you with this job as context. Agent-authored notes are labeled separately. Paused and inactive sessions are never resumed.</p>
                  <label className="block">
                    Recipient in this project
                    <select className="mt-1 w-full rounded border border-input bg-background p-2" value={recipient} onChange={(event) => {
                      draftRevision.current += 1;
                      setRecipient(event.target.value);
                      setSelectedAgent(null);
                      setAgents(null);
                      setAgentCursor("");
                      setAgentError(null);
                    }} required>
                      <option value="">Choose a job to discover its agents</option>
                      <option value={job.id}>This job's recipe agents</option>
                      {directory.peers?.map((peer) => <option key={peer.id} value={peer.id}>{peer.title || peer.id} ({peer.id})</option>)}
                    </select>
                  </label>
                  {recipient && (
                    <fieldset className="space-y-2" aria-busy={agentsLoading}>
                      <legend className="font-semibold">Choose the relevant agent</legend>
                      <p className="text-muted-foreground">Cards refresh every five seconds. A changed execution requires a new selection.</p>
                      {agentError && <p role="alert" className="text-destructive">{agentError}</p>}
                      {agentsLoading && !agents && <p role="status">Loading agent cards...</p>}
                      {agents?.items.map((agent) => (
                        <article key={agent.agentId} className="border-l-2 border-border py-2 pl-3 space-y-1">
                          <label className="flex items-start gap-2">
                            <input type="radio" name="peer-agent" className="mt-0.5" disabled={!agent.acceptsPeerMessages}
                              checked={selectedAgent?.agentId === agent.agentId && selectedAgent.executionToken === agent.executionToken}
                              onChange={() => { draftRevision.current += 1; setSelectedAgent(agent); }} />
                            <span className="font-semibold">{agent.name}</span>
                            <span className="ml-auto text-muted-foreground">{agent.state}</span>
                          </label>
                          <p className="text-muted-foreground">{agent.sdk}{agent.model ? ` / ${agent.model}` : ""}{agent.acceptsPeerMessages ? " / Available" : " / Unavailable"}</p>
                          {agent.description && <details><summary className="cursor-pointer">Agent purpose</summary><p className="whitespace-pre-wrap break-words">{agent.description}</p></details>}
                          {agent.detail && <p className="text-muted-foreground">{agent.detail}</p>}
                          <dl><CopyValue label="Agent SDK session ID" value={agent.sessionId} /></dl>
                        </article>
                      ))}
                      {agents && !agents.items.length && <p className="text-muted-foreground">No agents on this page.</p>}
                      {selectedAgent && !selectedIsAvailable && <p role="status">The selected execution is unavailable or changed. Choose an available agent before sending.</p>}
                      <div className="flex gap-2">
                        <button type="button" className={actionClass} onClick={() => setAgentRefresh((value) => value + 1)}>Refresh agent cards</button>
                        {agentCursor && <button type="button" className={actionClass} onClick={() => setAgentCursor("")}>First agents</button>}
                        {agents?.nextCursor && <button type="button" className={actionClass} onClick={() => setAgentCursor(agents.nextCursor ?? "")}>More agents</button>}
                      </div>
                    </fieldset>
                  )}
                  <label className="block">
                    Note
                    <textarea className="mt-1 w-full rounded border border-input bg-background p-2" rows={3} maxLength={10000} value={content} onChange={(event) => { draftRevision.current += 1; setContent(event.target.value); }} required />
                  </label>
                  <button type="submit" className={actionClass} disabled={sending || !recipient || !selectedIsAvailable || !content.trim()}>
                    {sending ? "Sending..." : "Send peer note"}
                  </button>
                  {pendingRequest && !sending && !selectedIsAvailable && (
                    <button type="button" className={actionClass} onClick={() => void send(true)}>Check previous delivery receipt</button>
                  )}
                  {receipt && <p role="status">{receipt.status}: {receipt.detail} (message {receipt.messageId})</p>}
                </form>
              )}
              <div className="space-y-2 border-t border-border pt-3">
                <h3 className="font-semibold">Peer receipts</h3>
                {!messages?.items?.length && <p className="text-muted-foreground">No peer messages on this page.</p>}
                {messages?.items?.map((message) => (
                  <article key={message.messageId} className="border-l-2 border-border pl-2">
                    <p>{message.origin === "agent" ? "Agent" : "Operator"}: {message.senderJobId} to {message.recipientJobId}</p>
                    {message.recipientAttemptId && <p className="break-all text-muted-foreground">Recipient attempt: {message.recipientAttemptId}</p>}
                    <p className="whitespace-pre-wrap break-words">{message.content}</p>
                    <p className="text-muted-foreground">{message.status}: {message.detail}</p>
                    <p className="break-all text-muted-foreground">Message {message.messageId}{message.replyTo ? `, reply to ${message.replyTo}` : ""}</p>
                  </article>
                ))}
              </div>
              <div className="flex gap-2">
                <button type="button" className={actionClass} disabled={offset === 0 || loading} onClick={() => setOffset((value) => Math.max(0, value - 50))}>Previous page</button>
                <button type="button" className={actionClass} disabled={loading || !(directory.childrenHasMore || directory.peersHasMore || messages?.hasMore)} onClick={() => setOffset((value) => value + 50)}>Next page</button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
