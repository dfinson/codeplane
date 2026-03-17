import { useRef, useEffect, useState, useCallback, memo } from "react";
import {
  Send, Bot, User, PauseCircle, ChevronDown, Brain, X,
  ShieldQuestion, CheckCircle2, XCircle as XCircleIcon,
  ArrowDown, Wrench,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { useStore, selectJobTranscript, selectApprovals } from "../store";
import type { TranscriptEntry, ApprovalRequest } from "../store";
import { sendOperatorMessage, resumeJob, pauseJob, resolveApproval } from "../api/client";
import { MicButton } from "./VoiceButton";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { useIsMobile } from "../hooks/useIsMobile";

// ---------------------------------------------------------------------------
// Turn grouping
// ---------------------------------------------------------------------------

interface AgentTurnData {
  key: string;
  reasoning: TranscriptEntry | null;
  toolCalls: TranscriptEntry[];
  message: TranscriptEntry | null;
  firstTimestamp: string;
}

type DisplayItem =
  | { type: "operator"; entry: TranscriptEntry }
  | { type: "turn"; turn: AgentTurnData }
  | { type: "divider"; entry: TranscriptEntry }
  | { type: "approval"; approval: ApprovalRequest };

function buildDisplayItems(
  entries: TranscriptEntry[],
  approvals: ApprovalRequest[],
): DisplayItem[] {
  const items: DisplayItem[] = [];
  const turns = new Map<string, AgentTurnData>();
  // Track the "open" turn — consecutive reasoning/tool_call entries without an
  // explicit turnId are merged into the same turn until an agent message or
  // operator message closes it.
  let openTurnKey: string | null = null;

  for (const entry of entries) {
    if (entry.role === "divider") {
      openTurnKey = null; // break the run
      items.push({ type: "divider", entry });
      continue;
    }
    if (entry.role === "operator") {
      openTurnKey = null; // break the run
      items.push({ type: "operator", entry });
      continue;
    }

    // agent | reasoning | tool_call — group into turns
    let turnId = entry.turnId;

    if (!turnId) {
      // No explicit turnId — merge into the open turn if one exists,
      // otherwise start a new implicit turn.
      if (entry.role === "agent") {
        // An agent message closes the current open turn or stands alone.
        turnId = openTurnKey ?? `msg-${entry.seq}-${entry.timestamp}`;
        openTurnKey = null; // message closes the turn
      } else {
        // reasoning or tool_call without turnId — keep grouping
        if (!openTurnKey) {
          openTurnKey = `auto-${entry.seq}-${entry.timestamp}`;
        }
        turnId = openTurnKey;
      }
    } else {
      // Explicit turnId — use it and track as open
      if (entry.role !== "agent") {
        openTurnKey = turnId;
      } else {
        openTurnKey = null; // message closes the turn
      }
    }

    if (!turns.has(turnId)) {
      const turn: AgentTurnData = {
        key: turnId,
        reasoning: null,
        toolCalls: [],
        message: null,
        firstTimestamp: entry.timestamp,
      };
      turns.set(turnId, turn);
      items.push({ type: "turn", turn });
    }
    const turn = turns.get(turnId)!;
    if (entry.role === "reasoning") turn.reasoning = entry;
    else if (entry.role === "tool_call") turn.toolCalls.push(entry);
    else if (entry.role === "agent") turn.message = entry;
  }

  // Interleave approvals at their chronological position
  for (const approval of approvals) {
    items.push({ type: "approval", approval });
  }

  // Stable sort: entries list is already ordered; approvals get inserted by time
  items.sort((a, b) => {
    const tsA = (() => {
      if (a.type === "operator" || a.type === "divider") return a.entry.timestamp;
      if (a.type === "turn") return a.turn.firstTimestamp;
      if (a.type === "approval") return a.approval.requestedAt;
      return "";
    })();
    const tsB = (() => {
      if (b.type === "operator" || b.type === "divider") return b.entry.timestamp;
      if (b.type === "turn") return b.turn.firstTimestamp;
      if (b.type === "approval") return b.approval.requestedAt;
      return "";
    })();
    return new Date(tsA).getTime() - new Date(tsB).getTime();
  });

  return items;
}

// ---------------------------------------------------------------------------
// Markdown renderer for agent messages
// ---------------------------------------------------------------------------

const AgentMarkdown = memo(function AgentMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="mb-2 pl-4 list-disc space-y-0.5">{children}</ul>,
        ol: ({ children }) => <ol className="mb-2 pl-4 list-decimal space-y-0.5">{children}</ol>,
        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
        h1: ({ children }) => <h1 className="text-base font-semibold mb-1 mt-2 first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="text-sm font-semibold mb-1 mt-2 first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-medium mb-1 mt-1 first:mt-0">{children}</h3>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-muted-foreground/40 pl-3 text-muted-foreground italic my-2">
            {children}
          </blockquote>
        ),
        code: ({ className, children }) => {
          const isBlock = className?.startsWith("language-");
          return isBlock ? (
            <pre className="bg-background border border-border rounded-md p-3 my-2 overflow-x-auto text-xs font-mono">
              <code>{children}</code>
            </pre>
          ) : (
            <code className="bg-background border border-border rounded px-1 py-0.5 text-xs font-mono">
              {children}
            </code>
          );
        },
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2 hover:opacity-80">
            {children}
          </a>
        ),
        hr: () => <hr className="border-border my-2" />,
        table: ({ children }) => (
          <div className="overflow-x-auto my-2">
            <table className="text-xs border-collapse w-full">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-border px-2 py-1 bg-muted font-semibold text-left">{children}</th>
        ),
        td: ({ children }) => (
          <td className="border border-border px-2 py-1">{children}</td>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
});

// ---------------------------------------------------------------------------
// Collapsible reasoning block
// ---------------------------------------------------------------------------

function ReasoningBlock({ entry }: { entry: TranscriptEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border/50 bg-muted/30 overflow-hidden mb-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
      >
        <Brain size={11} className="shrink-0 text-violet-400/80" />
        <span className="font-medium">Reasoning</span>
        <ChevronDown
          size={11}
          className={cn("ml-auto shrink-0 transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <div className="px-3 pb-2 text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed border-t border-border/50 pt-2 font-mono">
          {entry.content}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool call chips + detail panel
// ---------------------------------------------------------------------------

function prettifyJson(raw: string | undefined): string {
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

/** Group sequential identical tool names into counted chips. */
function chipify(calls: TranscriptEntry[]): { name: string; display: string; count: number; entries: TranscriptEntry[] }[] {
  const chips: { name: string; display: string; count: number; entries: TranscriptEntry[] }[] = [];
  for (const tc of calls) {
    const name = tc.toolName ?? tc.content;
    const display = tc.toolDisplay ?? name;
    const last = chips[chips.length - 1];
    if (last && last.name === name) {
      last.count++;
      last.entries.push(tc);
    } else {
      chips.push({ name, display, count: 1, entries: [tc] });
    }
  }
  return chips;
}

function ToolChips({ calls }: { calls: TranscriptEntry[] }) {
  const [expanded, setExpanded] = useState<TranscriptEntry | null>(null);
  const chips = chipify(calls);
  const anyFailed = calls.some((c) => c.toolSuccess === false);

  return (
    <div className="space-y-1.5 mb-1">
      <div className="flex flex-wrap gap-1">
        {chips.map((chip, i) => {
          const hasFail = chip.entries.some((e) => e.toolSuccess === false);
          return (
            <button
              key={i}
              onClick={() => {
                const target = chip.entries[0] ?? null;
                setExpanded((prev) => prev === target ? null : target);
              }}
              className={cn(
                "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono transition-colors",
                "border border-border/60 hover:border-border hover:bg-muted/50",
                hasFail
                  ? "text-red-400 border-red-500/30 bg-red-500/5"
                  : "text-muted-foreground bg-muted/20",
              )}
            >
              <span>{chip.display}</span>
              {chip.count > 1 && (
                <span className="text-[10px] opacity-60">×{chip.count}</span>
              )}
            </button>
          );
        })}
        {anyFailed && (
          <span className="text-[10px] text-red-400 self-center ml-0.5">has errors</span>
        )}
      </div>

      {/* Expanded detail panel */}
      {expanded && (
        <div className="rounded-md border border-border/60 bg-background overflow-hidden text-xs">
          <div className="flex items-center justify-between px-2.5 py-1.5 bg-muted/30 border-b border-border/40">
            <span className="font-mono font-medium text-foreground/80">
              {expanded.toolName ?? expanded.content}
            </span>
            <div className="flex items-center gap-2">
              <span className={cn(
                "text-[10px] font-medium",
                expanded.toolSuccess !== false ? "text-green-500" : "text-red-400",
              )}>
                {expanded.toolSuccess !== false ? "ok" : "failed"}
              </span>
              {/* Navigation within the chip group */}
              {calls.length > 1 && (
                <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <button
                    onClick={() => {
                      const idx = calls.indexOf(expanded);
                      if (idx > 0) setExpanded(calls[idx - 1] ?? null);
                    }}
                    disabled={calls.indexOf(expanded) === 0}
                    className="hover:text-foreground disabled:opacity-30"
                  >
                    ‹
                  </button>
                  <span>{calls.indexOf(expanded) + 1}/{calls.length}</span>
                  <button
                    onClick={() => {
                      const idx = calls.indexOf(expanded);
                      if (idx < calls.length - 1) setExpanded(calls[idx + 1] ?? null);
                    }}
                    disabled={calls.indexOf(expanded) === calls.length - 1}
                    className="hover:text-foreground disabled:opacity-30"
                  >
                    ›
                  </button>
                </div>
              )}
              <button onClick={() => setExpanded(null)} className="text-muted-foreground hover:text-foreground">
                <X size={11} />
              </button>
            </div>
          </div>
          <div className="divide-y divide-border/30">
            {expanded.toolArgs && (
              <div className="px-2.5 py-2">
                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide mb-1">Input</p>
                <pre className="font-mono text-foreground/70 whitespace-pre-wrap overflow-x-auto max-h-32 overflow-y-auto">
                  {prettifyJson(expanded.toolArgs)}
                </pre>
              </div>
            )}
            {expanded.toolResult && (
              <div className="px-2.5 py-2">
                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide mb-1">Output</p>
                <pre className="font-mono text-foreground/70 whitespace-pre-wrap overflow-x-auto max-h-40 overflow-y-auto">
                  {expanded.toolResult}
                </pre>
              </div>
            )}
            {!expanded.toolArgs && !expanded.toolResult && (
              <div className="px-2.5 py-2 text-muted-foreground italic">No input/output recorded</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Collapsible tool group section (wraps ToolChips with a summary header)
// ---------------------------------------------------------------------------

function truncateLabel(label: string, maxWords = 8): string {
  const words = label.trim().split(/\s+/);
  if (words.length <= maxWords) return label.trim();
  return words.slice(0, maxWords).join(" ") + "…";
}

function deriveToolGroupLabel(calls: TranscriptEntry[]): string {
  // 1. SDK-provided intent string (deterministic, human-authored by the SDK/MCP manifest)
  const withIntent = calls.find((c) => c.toolIntent);
  if (withIntent?.toolIntent) return truncateLabel(withIntent.toolIntent);

  // 2. SDK-provided display title
  const withTitle = calls.find((c) => c.toolTitle);
  if (withTitle?.toolTitle) {
    const chips = chipify(calls);
    const counts = chips.map((c) => c.count > 1 ? `${c.name} ×${c.count}` : c.name).join(", ");
    return truncateLabel(`${withTitle.toolTitle}: ${counts}`);
  }

  // 3. Deterministic per-tool display labels
  const withDisplay = calls.filter((c) => c.toolDisplay);
  if (withDisplay.length > 0) {
    // Show up to 3 unique display labels
    const unique = [...new Set(withDisplay.map((c) => c.toolDisplay!))];
    const shown = unique.slice(0, 3).join(", ");
    const suffix = unique.length > 3 ? "…" : "";
    return truncateLabel(`${shown}${suffix}`);
  }

  // 4. Fallback: per-tool counts from chipify
  const chips = chipify(calls);
  return chips.map((c) => c.count > 1 ? `${c.name} ×${c.count}` : c.name).join(", ");
}

/** Extract the intent string from a leading report_intent tool call, if present. */
function extractReportIntent(calls: TranscriptEntry[]): string | null {
  const first = calls[0];
  if (first?.toolName !== "report_intent" || !first.toolArgs) return null;
  try {
    const args = JSON.parse(first.toolArgs) as Record<string, unknown>;
    return typeof args.intent === "string" ? args.intent : null;
  } catch {
    return null;
  }
}

function ToolGroupSection({ calls }: { calls: TranscriptEntry[] }) {
  const [open, setOpen] = useState(false);
  const anyFailed = calls.some((c) => c.toolSuccess === false);
  const intentLabel = extractReportIntent(calls);
  const label = intentLabel ?? deriveToolGroupLabel(calls);

  return (
    <div className={cn(
      "rounded-lg border overflow-hidden mb-1",
      anyFailed ? "border-red-500/30 bg-red-500/5" : "border-border/50 bg-muted/30",
    )}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted/50 transition-colors"
      >
        <Wrench size={11} className={cn("shrink-0", anyFailed ? "text-red-400" : "text-blue-400/70")} />
        <span className={cn("font-medium truncate", anyFailed ? "text-red-400" : "text-muted-foreground")}>
          {label}
        </span>
        <ChevronDown
          size={11}
          className={cn("ml-auto shrink-0 transition-transform text-muted-foreground", open && "rotate-180")}
        />
      </button>
      {open && (
        <div className="px-2 pb-2 pt-1.5 border-t border-border/50">
          <ToolChips calls={calls} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent turn (reasoning + tool chips + message)
// ---------------------------------------------------------------------------

function AgentTurn({ turn, isLast }: { turn: AgentTurnData; isLast?: boolean }) {
  const msg = turn.message;
  const ts = msg?.timestamp ?? turn.firstTimestamp;
  return (
    <div className="flex gap-2">
      <div className="w-6 h-6 rounded-full bg-blue-900/50 flex items-center justify-center shrink-0 mt-1">
        <Bot size={14} />
      </div>
      <div className="flex-1 min-w-0 space-y-0.5">
        {turn.reasoning && <ReasoningBlock entry={turn.reasoning} />}
        {turn.toolCalls.length > 0 && (
          <ToolGroupSection calls={turn.toolCalls} />
        )}
        {msg && (
          <div className="bg-muted rounded-xl rounded-tl-sm px-3 py-2 text-sm leading-relaxed">
            {msg.title && (
              <p className="text-[11px] text-muted-foreground font-medium mb-1.5 tracking-wide">
                {msg.title}
              </p>
            )}
            <AgentMarkdown content={msg.content.replace(/:$/, "")} />
            <span className="text-xs text-muted-foreground mt-1 block">
              {new Date(ts).toLocaleTimeString()}
            </span>
          </div>
        )}
        {/* Only the last turn shows "working…" when it has no message yet */}
        {isLast && !msg && (turn.reasoning || turn.toolCalls.length > 0) && (
          <div className="text-xs text-muted-foreground animate-pulse px-1">working…</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline approval card
// ---------------------------------------------------------------------------

function InlineApprovalCard({ approval }: { approval: ApprovalRequest }) {
  const [loading, setLoading] = useState<string | null>(null);

  const handleResolve = useCallback(async (resolution: "approved" | "rejected") => {
    setLoading(resolution);
    try {
      await resolveApproval(approval.id, resolution);
      toast.success(`Approval ${resolution}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoading(null);
    }
  }, [approval.id]);

  if (approval.resolvedAt) {
    const approved = approval.resolution === "approved";
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border/50 bg-muted/20 text-xs text-muted-foreground">
        {approved
          ? <CheckCircle2 size={12} className="text-green-500 shrink-0" />
          : <XCircleIcon size={12} className="text-red-400 shrink-0" />}
        <span className="truncate">{approval.description}</span>
        <span className={cn("ml-auto shrink-0 font-medium", approved ? "text-green-500" : "text-red-400")}>
          {approval.resolution}
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-orange-500/40 bg-orange-500/10 p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <ShieldQuestion size={14} className="text-orange-400 shrink-0" />
        <span className="text-sm font-semibold text-orange-300">Approval Required</span>
      </div>
      <p className="text-sm text-foreground mb-2">{approval.description}</p>
      {approval.proposedAction && (
        <pre className="text-xs bg-background border border-border rounded p-2 mb-2.5 overflow-x-auto font-mono">
          {approval.proposedAction}
        </pre>
      )}
      <div className="flex gap-2">
        <Button
          size="sm"
          className="bg-green-600 hover:bg-green-700 text-white h-7 px-3 text-xs"
          loading={loading === "approved"}
          disabled={!!loading}
          onClick={() => handleResolve("approved")}
        >
          Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="border-red-500/40 text-red-400 hover:bg-red-500/10 h-7 px-3 text-xs"
          loading={loading === "rejected"}
          disabled={!!loading}
          onClick={() => handleResolve("rejected")}
        >
          Reject
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main TranscriptPanel
// ---------------------------------------------------------------------------

export function TranscriptPanel({
  jobId,
  interactive,
  pausable,
  jobState,
  prompt,
  promptTimestamp,
}: {
  jobId: string;
  interactive?: boolean;
  pausable?: boolean;
  jobState?: string;
  prompt?: string;
  promptTimestamp?: string;
}) {
  const isMobile = useIsMobile();
  const rawEntries = useStore(selectJobTranscript(jobId));
  const allApprovals = useStore(selectApprovals);
  const jobApprovals = Object.values(allApprovals).filter((a) => a.jobId === jobId);

  const entries: TranscriptEntry[] = [
    ...(prompt
      ? [{ jobId, seq: -1, timestamp: promptTimestamp ?? "", role: "operator", content: prompt }]
      : []),
    // Dedup: if the synthetic prompt entry is present, suppress any SSE
    // operator entry with the same content (SDK echo of the initial prompt).
    ...rawEntries.filter((e) => {
      if (!e.content?.trim()) return false;
      if (prompt && e.role === "operator" && e.content === prompt) return false;
      return true;
    }),
  ];

  const displayItems = buildDisplayItems(entries, jobApprovals);

  const viewportRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const waveformContainerRef = useRef<HTMLDivElement>(null);
  const [msg, setMsg] = useState("");
  const [sending, setSending] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [micState, setMicState] = useState<"idle" | "recording" | "transcribing">("idle");

  const agentMessageCount = rawEntries.filter((e) => e.role === "agent").length;

  useEffect(() => {
    if (stickRef.current && viewportRef.current) {
      viewportRef.current.scrollTo({ top: viewportRef.current.scrollHeight });
    }
  }, [displayItems.length]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    stickRef.current = atBottom;
    setShowScrollBtn(!atBottom);
  };

  const scrollToBottom = useCallback(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTo({ top: viewportRef.current.scrollHeight, behavior: "smooth" });
      stickRef.current = true;
      setShowScrollBtn(false);
    }
  }, []);

  const isTerminal = ["succeeded", "failed", "canceled"].includes(jobState ?? "");

  const handleSend = useCallback(async () => {
    if (!msg.trim()) return;
    setSending(true);
    try {
      if (isTerminal) {
        await resumeJob(jobId, msg.trim());
      } else {
        await sendOperatorMessage(jobId, msg.trim());
      }
      setMsg("");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSending(false);
    }
  }, [jobId, msg, isTerminal]);

  const handlePause = useCallback(async () => {
    setPausing(true);
    try {
      await pauseJob(jobId);
      toast.info("Pause instruction sent — agent will stop when ready");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setPausing(false);
    }
  }, [jobId]);

  return (
    <div className="flex flex-col h-full overflow-hidden rounded-lg border border-border bg-card">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
        <span className="text-sm font-semibold text-muted-foreground">Transcript</span>
        <div className="flex items-center gap-2">
          {pausable && (
            <Button
              size="sm"
              variant="outline"
              onClick={handlePause}
              disabled={pausing}
              loading={pausing}
              className="h-7 px-2 text-xs gap-1"
            >
              <PauseCircle size={12} />
              Pause
            </Button>
          )}
          <span className="text-xs text-muted-foreground">{agentMessageCount} messages</span>
        </div>
      </div>

      {/* Message list */}
      <div className="relative flex-1 min-h-0">
      <div
        ref={viewportRef}
        className="h-full overflow-y-auto overscroll-contain"
        onScroll={handleScroll}
      >
        {displayItems.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">No messages yet</p>
        ) : (
          <div className="p-3 space-y-3">
            {displayItems.map((item, i) => {
              if (item.type === "divider") {
                return (
                  <div key={i} className="flex items-center gap-3 py-1">
                    <div className="flex-1 h-px bg-border" />
                    <span className="text-xs text-muted-foreground font-medium px-1">
                      {item.entry.content} resumed
                      {item.entry.timestamp
                        ? ` · ${new Date(item.entry.timestamp).toLocaleTimeString()}`
                        : ""}
                    </span>
                    <div className="flex-1 h-px bg-border" />
                  </div>
                );
              }

              if (item.type === "operator") {
                return (
                  <div key={i} className="flex gap-2 flex-row-reverse">
                    <div className="w-6 h-6 rounded-full bg-green-900/50 flex items-center justify-center shrink-0 mt-1">
                      <User size={14} />
                    </div>
                    <div className="max-w-[80%] rounded-xl rounded-tr-sm px-3 py-2 text-sm leading-relaxed bg-blue-900/30">
                      <div className="whitespace-pre-wrap">{item.entry.content}</div>
                      <span className="text-xs text-muted-foreground mt-1 block">
                        {new Date(item.entry.timestamp).toLocaleTimeString()}
                      </span>
                    </div>
                  </div>
                );
              }

              if (item.type === "turn") {
                return <AgentTurn key={item.turn.key} turn={item.turn} isLast={i === displayItems.length - 1} />;
              }

              if (item.type === "approval") {
                return <InlineApprovalCard key={item.approval.id} approval={item.approval} />;
              }

              return null;
            })}
          </div>
        )}
      </div>

      {/* Scroll to bottom button */}
      {showScrollBtn && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-primary text-primary-foreground text-xs font-medium shadow-lg hover:bg-primary/90 transition-opacity animate-in fade-in duration-200"
        >
          <ArrowDown size={12} />
          Jump to bottom
        </button>
      )}
      </div>

      {/* Input */}
      {interactive && (
        <div className="p-2 border-t border-border shrink-0 flex flex-col gap-1.5">
          {/* Waveform strip — always mounted for WaveSurfer stability, shown only during recording */}
          <div className={cn(
            "rounded border border-blue-600/50 bg-card px-3 py-1",
            micState === "recording" ? "block" : "hidden",
          )}>
            <div ref={waveformContainerRef} />
          </div>

          {/* Transcribing indicator */}
          {micState === "transcribing" && (
            <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
              <Spinner size="sm" />
              <span>Transcribing…</span>
            </div>
          )}

          <div className="flex items-end gap-2">
            <div className="relative flex-1">
              <textarea
                placeholder={isTerminal ? "Send a message to resume this job…" : "Send instruction to agent…"}
                value={msg}
                onChange={(e) => {
                  setMsg(e.currentTarget.value);
                  e.currentTarget.style.height = "auto";
                  e.currentTarget.style.height = Math.min(e.currentTarget.scrollHeight, 160) + "px";
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !isMobile && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                disabled={sending || micState !== "idle"}
                rows={1}
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-1.5 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-none pr-8 overflow-y-auto"
                style={{ maxHeight: 160 }}
              />
              <div className="absolute right-2 bottom-1.5">
                <MicButton
                  onTranscript={(t) => setMsg((prev) => (prev ? prev + " " : "") + t)}
                  onStateChange={setMicState}
                  waveformContainerRef={waveformContainerRef}
                />
              </div>
            </div>
            <Button
              size="icon"
              onClick={handleSend}
              disabled={sending || !msg.trim() || micState !== "idle"}
              loading={sending}
              className="shrink-0"
            >
              <Send size={16} />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
