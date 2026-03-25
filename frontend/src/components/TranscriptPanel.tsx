import { useRef, useEffect, useState, useCallback, memo, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Send, Bot, User, PauseCircle, ChevronDown, Brain, X,
  ShieldQuestion, CheckCircle2, XCircle as XCircleIcon,
  ArrowDown, Search,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useStore, selectJobTranscript, selectApprovals } from "../store";
import type { TranscriptEntry, ApprovalRequest } from "../store";
import { sendOperatorMessage, resumeJob, continueJob, pauseJob, resolveApproval } from "../api/client";
import { SdkIcon } from "./SdkBadge";
import { MicButton } from "./VoiceButton";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { Codicon } from "./ui/codicon";
import { cn } from "../lib/utils";
import { resolveToolIcon, type ToolIconDef } from "../lib/toolIcons";
import { useIsMobile } from "../hooks/useIsMobile";
import { ConfirmDialog } from "./ui/confirm-dialog";

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

    // agent | reasoning | tool_call | tool_running — group into turns
    let turnId = entry.turnId;

    if (!turnId) {
      // No explicit turnId — merge into the open turn if one exists,
      // otherwise start a new implicit turn.
      if (entry.role === "agent") {
        // An agent message closes the current open turn or stands alone.
        turnId = openTurnKey ?? `msg-${entry.seq}-${entry.timestamp}`;
        openTurnKey = null; // message closes the turn
      } else {
        // reasoning, tool_call, or tool_running without turnId — keep grouping
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
    else if (entry.role === "tool_call" || entry.role === "tool_running") turn.toolCalls.push(entry);
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
            <pre className="bg-background border border-border rounded-md p-3 my-2 overflow-x-auto max-w-full text-xs font-mono">
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
// Tool step list — vertical list with codicon glyphs (replaces chips)
// ---------------------------------------------------------------------------

function prettifyJson(raw: string | undefined): string {
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function TruncatedPayload({ content, maxLength = 500 }: { content: string; maxLength?: number }) {
  const [expanded, setExpanded] = useState(false);
  if (!content || content.length <= maxLength) return <pre className="text-xs whitespace-pre-wrap break-all">{content}</pre>;
  return (
    <div>
      <pre className="text-xs whitespace-pre-wrap break-all">
        {expanded ? content : content.slice(0, maxLength) + "…"}
      </pre>
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-primary hover:underline mt-1"
      >
        {expanded ? "Show less" : `Show all (${content.length.toLocaleString()} chars)`}
      </button>
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function stripMcpPrefix(name: string): string {
  return name.includes("/") ? name.split("/").pop()! : name;
}

function parseArgs(toolArgs?: string): Record<string, unknown> {
  if (!toolArgs) return {};
  try {
    const parsed = JSON.parse(toolArgs);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function countLines(text?: string): number | undefined {
  if (!text) return undefined;
  return text.split("\n").filter((l) => l.trim()).length;
}

function abbreviatePath(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  return parts.length <= 2 ? path : parts.slice(-2).join("/");
}

// Structured rendering per tool type
function StructuredToolContent({ entry }: { entry: TranscriptEntry }) {
  const toolName = stripMcpPrefix(entry.toolName ?? "");
  const args = parseArgs(entry.toolArgs);

  switch (toolName) {
    case "bash":
    case "run_in_terminal": {
      const command = (args.command as string) ?? "";
      return (
        <div className="font-mono text-xs">
          <div className={cn(
            "px-3 py-1.5 border-b border-border/30",
            entry.toolSuccess === false ? "bg-red-950/30" : "bg-zinc-950/50",
          )}>
            <span className="text-muted-foreground">$ </span>
            <span className="text-foreground/90">{command}</span>
          </div>
          {entry.toolResult && (
            <div className="px-3 py-1.5">
              <TruncatedPayload content={entry.toolResult} maxLength={600} />
            </div>
          )}
        </div>
      );
    }
    case "read_file": {
      const filePath = (args.filePath ?? args.file_path ?? "") as string;
      const startLine = (args.startLine ?? args.start_line) as number | undefined;
      const endLine = (args.endLine ?? args.end_line) as number | undefined;
      const lines = countLines(entry.toolResult);
      const shortPath = abbreviatePath(filePath);
      const range = startLine && endLine ? `lines ${startLine}–${endLine}` : null;
      return (
        <div className="px-3 py-1.5 flex items-center gap-2 text-xs">
          <Codicon name="file-code" size={11} className="text-blue-400/70 shrink-0" />
          <span className="font-mono text-foreground/80">{shortPath}</span>
          {range && <span className="text-muted-foreground">{range}</span>}
          {lines != null && <span className="text-muted-foreground/60">({lines} lines)</span>}
        </div>
      );
    }
    case "replace_string_in_file":
    case "multi_replace_string_in_file":
    case "str_replace_based_edit_tool": {
      const filePath = (args.filePath ?? args.file_path ?? args.path ?? "") as string;
      const shortPath = abbreviatePath(filePath);
      return (
        <div className="px-3 py-1.5 text-xs">
          <div className="flex items-center gap-2">
            <Codicon name="edit" size={11} className="text-amber-400/70 shrink-0" />
            <span className="font-mono text-foreground/80">{shortPath}</span>
            <span className="text-muted-foreground">
              {entry.toolSuccess !== false ? "→ applied" : "→ failed"}
            </span>
          </div>
          {typeof args.old_str === "string" && typeof args.new_str === "string" && (
            <div className="mt-1.5 font-mono text-[11px] leading-relaxed pl-5">
              <div className="text-red-400/80">- {args.old_str.slice(0, 80)}{args.old_str.length > 80 ? "…" : ""}</div>
              <div className="text-green-400/80">+ {args.new_str.slice(0, 80)}{args.new_str.length > 80 ? "…" : ""}</div>
            </div>
          )}
        </div>
      );
    }
    case "grep_search":
    case "semantic_search":
    case "file_search": {
      const query = (args.query ?? args.pattern ?? "") as string;
      const lines = countLines(entry.toolResult);
      return (
        <div className="px-3 py-1.5 flex items-center gap-2 text-xs">
          <Codicon name="search" size={11} className="text-blue-400/70 shrink-0" />
          <span className="font-mono text-foreground/80">&ldquo;{query}&rdquo;</span>
          {lines != null && <span className="text-muted-foreground">→ {lines} matches</span>}
        </div>
      );
    }
    case "create_file":
    case "write": {
      const filePath = (args.filePath ?? args.file_path ?? args.path ?? "") as string;
      return (
        <div className="px-3 py-1.5 flex items-center gap-2 text-xs">
          <Codicon name="edit" size={11} className="text-green-400/70 shrink-0" />
          <span className="font-mono text-foreground/80">{abbreviatePath(filePath)}</span>
          <span className="text-muted-foreground">→ {toolName === "write" ? "written" : "created"}</span>
        </div>
      );
    }
    case "view": {
      const path = (args.path as string) ?? "";
      const viewRange = args.view_range as [number, number] | undefined;
      const lines = countLines(entry.toolResult);
      const range = Array.isArray(viewRange) && viewRange.length >= 2
        ? `lines ${viewRange[0]}–${viewRange[1] === -1 ? "end" : viewRange[1]}`
        : null;
      return (
        <div className="px-3 py-1.5 flex items-center gap-2 text-xs">
          <Codicon name="file-code" size={11} className="text-blue-400/70 shrink-0" />
          <span className="font-mono text-foreground/80">{abbreviatePath(path)}</span>
          {range && <span className="text-muted-foreground">{range}</span>}
          {lines != null && <span className="text-muted-foreground/60">({lines} lines)</span>}
        </div>
      );
    }
    case "glob": {
      const pattern = (args.pattern as string) ?? "";
      const searchPath = (args.path as string) ?? "";
      const lines = countLines(entry.toolResult);
      return (
        <div className="px-3 py-1.5 flex items-center gap-2 text-xs">
          <Codicon name="search" size={11} className="text-blue-400/70 shrink-0" />
          <span className="font-mono text-foreground/80">{pattern}</span>
          {searchPath && <span className="text-muted-foreground/60">in {abbreviatePath(searchPath)}</span>}
          {lines != null && <span className="text-muted-foreground">→ {lines} files</span>}
        </div>
      );
    }
    case "grep": {
      const pattern = (args.pattern ?? args.query ?? "") as string;
      const searchPath = (args.path as string) ?? "";
      const globFilter = (args.glob as string) ?? "";
      const lines = countLines(entry.toolResult);
      return (
        <div className="px-3 py-1.5 flex items-center gap-2 text-xs">
          <Codicon name="search" size={11} className="text-blue-400/70 shrink-0" />
          <span className="font-mono text-foreground/80">&ldquo;{pattern}&rdquo;</span>
          {(globFilter || searchPath) && (
            <span className="text-muted-foreground/60">in {globFilter || abbreviatePath(searchPath)}</span>
          )}
          {lines != null && <span className="text-muted-foreground">→ {lines} matches</span>}
        </div>
      );
    }
    default:
      return null;
  }
}

function hasStructuredRenderer(toolName?: string): boolean {
  if (!toolName) return false;
  const name = stripMcpPrefix(toolName);
  return [
    "bash", "run_in_terminal", "read_file",
    "replace_string_in_file", "multi_replace_string_in_file", "str_replace_based_edit_tool",
    "grep_search", "semantic_search", "file_search",
    "create_file", "write",
    "view", "glob", "grep",
  ].includes(name);
}

function ToolDetail({ entry }: { entry: TranscriptEntry }) {
  return (
    <div className="ml-0 mt-1 mb-2 rounded border border-border/40 bg-muted/20 text-xs overflow-hidden">
      {entry.toolSuccess === false && entry.toolIssue && (
        <div className="px-3 py-1.5 bg-red-500/5 border-b border-border/30">
          <span className="text-red-400 font-medium">{entry.toolIssue}</span>
        </div>
      )}
      <StructuredToolContent entry={entry} />
      {!hasStructuredRenderer(entry.toolName) && (
        <>
          {entry.toolArgs && (
            <div className="px-3 py-1.5 border-b border-border/30">
              <span className="text-muted-foreground font-medium text-[10px] uppercase">Input</span>
              <pre className="mt-0.5 whitespace-pre-wrap break-all text-xs">{prettifyJson(entry.toolArgs)}</pre>
            </div>
          )}
          {entry.toolResult && (
            <div className="px-3 py-1.5">
              <span className="text-muted-foreground font-medium text-[10px] uppercase">Output</span>
              <TruncatedPayload content={entry.toolResult} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ToolIconGlyph({ icon, className }: { icon: ToolIconDef; className?: string }) {
  if (icon.kind === "codicon") {
    return <Codicon name={icon.name} size={11} className={className} />;
  }
  const Icon = icon.icon;
  return <Icon size={11} className={className} />;
}

function ToolStep({ entry, isActive }: {
  entry: TranscriptEntry;
  isActive: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const failed = entry.toolSuccess === false;
  const isRunning = entry.role === "tool_running";
  const label = entry.toolDisplay ?? entry.toolName ?? entry.content;
  const icon = resolveToolIcon(entry.toolName);

  return (
    <div className="relative pl-5">
      <div className={cn(
        "absolute left-0 top-[3px] w-[15px] h-[15px] flex items-center justify-center",
        (isActive || isRunning) && "animate-pulse",
      )}>
        <ToolIconGlyph icon={icon} className={cn(
          failed ? "text-red-400"
            : (isActive || isRunning) ? "text-blue-400"
            : "text-muted-foreground/50",
        )} />
      </div>
      <button
        onClick={() => !isRunning && setExpanded(!expanded)}
        className={cn("w-full text-left group", isRunning && "cursor-default")}
      >
        <div className="flex items-baseline gap-2 py-0.5">
          <span className={cn(
            "text-xs font-mono",
            failed ? "text-red-400"
              : (isActive || isRunning) ? "text-blue-400"
              : "text-foreground/80",
          )}>
            {label}{isRunning ? "…" : ""}
          </span>
          {entry.toolDurationMs != null && (
            <span className="text-[10px] text-muted-foreground/60">
              {formatDuration(entry.toolDurationMs)}
            </span>
          )}
          {failed && entry.toolIssue && (
            <span className="text-[10px] text-red-400 truncate max-w-[200px]">
              {entry.toolIssue}
            </span>
          )}
        </div>
      </button>
      {expanded && !isRunning && <ToolDetail entry={entry} />}
    </div>
  );
}

function ToolStepList({ calls, isActive }: { calls: TranscriptEntry[]; isActive: boolean }) {
  return (
    <div className="relative ml-1">
      <div className="absolute left-[7px] top-2 bottom-2 w-px border-l border-dotted border-border/60" />
      <div className="space-y-0.5">
        {calls.map((call, i) => (
          <ToolStep
            key={call.seq}
            entry={call}
            isActive={isActive && i === calls.length - 1}
          />
        ))}
      </div>
    </div>
  );
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

// ---------------------------------------------------------------------------
// Relative time display
// ---------------------------------------------------------------------------

function RelativeTime({ ts, className }: { ts: string; className?: string }) {
  const [, forceUpdate] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceUpdate((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);
  const diff = Date.now() - new Date(ts).getTime();
  const label = diff < 60_000 ? "just now"
    : diff < 3_600_000 ? `${Math.floor(diff / 60_000)}m ago`
    : new Date(ts).toLocaleTimeString();
  return <span className={className}>{label}</span>;
}

// ---------------------------------------------------------------------------
// Agent message block — plain text with progressive reveal
// ---------------------------------------------------------------------------

function AgentMessageBlock({ entry, isNew }: { entry: TranscriptEntry; isNew?: boolean }) {
  const content = entry.content.replace(/:$/, "");
  const isLong = content.length > 300;
  const shouldReveal = isNew && isLong;
  const [revealed, setRevealed] = useState(shouldReveal ? 0 : content.length);

  useEffect(() => {
    if (revealed >= content.length) return;
    const id = requestAnimationFrame(() => {
      setRevealed((prev) => Math.min(prev + 50, content.length));
    });
    return () => cancelAnimationFrame(id);
  }, [revealed, content.length]);

  const visibleContent = shouldReveal && revealed < content.length
    ? content.slice(0, revealed)
    : content;

  return (
    <div className="text-sm leading-relaxed">
      {entry.title && (
        <p className="text-xs text-muted-foreground font-medium mb-1.5 tracking-wide">
          {entry.title}
        </p>
      )}
      <AgentMarkdown content={visibleContent} />
      <RelativeTime ts={entry.timestamp} className="text-xs text-muted-foreground mt-1 block" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active indicator — shows what tool is running right now
// ---------------------------------------------------------------------------

function ActiveIndicator({ turn }: { turn: AgentTurnData }) {
  const runningTool = turn.toolCalls.find((c) => c.role === "tool_running");
  if (runningTool) return null; // tool_running entries are rendered inline in the step list
  return <div className="text-xs text-muted-foreground animate-pulse px-1">working…</div>;
}

// ---------------------------------------------------------------------------
// Agent turn (reasoning + tool steps + message)
// ---------------------------------------------------------------------------

function AgentTurn({
  turn,
  sdk,
  isLast,
}: {
  turn: AgentTurnData;
  sdk?: string;
  isLast?: boolean;
}) {
  const msg = turn.message;
  const intentLabel = extractReportIntent(turn.toolCalls);
  const isActive = !!isLast && !msg;

  return (
    <div className="flex gap-3 py-2">
      <div className="w-5 h-5 rounded-full bg-blue-900/50 flex items-center justify-center shrink-0 mt-0.5">
        <SdkIcon sdk={sdk} size={12} fallback={<Bot size={12} />} />
      </div>
      <div className="flex-1 min-w-0 space-y-1">
        {intentLabel && (
          <div className="text-xs font-medium text-muted-foreground">{intentLabel}</div>
        )}
        {turn.reasoning && <ReasoningBlock entry={turn.reasoning} />}
        {turn.toolCalls.length > 0 && (
          <ToolStepList calls={turn.toolCalls} isActive={isActive} />
        )}
        {msg && <AgentMessageBlock entry={msg} isNew={isLast} />}
        {isActive && (turn.reasoning || turn.toolCalls.length > 0) && (
          <ActiveIndicator turn={turn} />
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
  const [rejectOpen, setRejectOpen] = useState(false);

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
          onClick={() => setRejectOpen(true)}
        >
          Reject
        </Button>
      </div>
      <ConfirmDialog
        open={rejectOpen}
        onClose={() => setRejectOpen(false)}
        onConfirm={async () => {
          await handleResolve("rejected");
        }}
        title="Reject Approval?"
        description="The agent's proposed action will be denied. It may fail or take a different approach."
        confirmLabel="Reject"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main TranscriptPanel
// ---------------------------------------------------------------------------

export function TranscriptPanel({
  jobId,
  sdk,
  interactive,
  pausable,
  jobState,
  prompt,
  promptTimestamp,
}: {
  jobId: string;
  sdk?: string;
  interactive?: boolean;
  pausable?: boolean;
  jobState?: string;
  resolution?: string | null;
  archivedAt?: string | null;
  prompt?: string;
  promptTimestamp?: string;
}) {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const rawEntries = useStore(selectJobTranscript(jobId));
  const allApprovals = useStore(selectApprovals);
  const jobApprovals = Object.values(allApprovals).filter((a) => a.jobId === jobId);

  const entries = useMemo<TranscriptEntry[]>(() => [
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
  ], [rawEntries, jobId, prompt, promptTimestamp]);

  const viewportRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const waveformContainerRef = useRef<HTMLDivElement>(null);
  const [msg, setMsg] = useState("");
  const [sending, setSending] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [micState, setMicState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  const agentMessageCount = rawEntries.filter((e) => e.role === "agent").length;

  // Filter entries by search query
  const filteredEntries = useMemo(() => {
    if (!searchQuery.trim()) return entries;
    const q = searchQuery.toLowerCase();
    return entries.filter((e) =>
      e.content?.toLowerCase().includes(q) ||
      e.toolDisplay?.toLowerCase().includes(q) ||
      e.toolName?.toLowerCase().includes(q) ||
      e.toolArgs?.toLowerCase().includes(q),
    );
  }, [entries, searchQuery]);

  const displayItems = buildDisplayItems(filteredEntries, jobApprovals);

  const virtualizer = useVirtualizer({
    count: displayItems.length,
    getScrollElement: () => viewportRef.current,
    estimateSize: () => 120,
    overscan: 5,
  });

  useEffect(() => {
    if (stickRef.current && displayItems.length > 0) {
      virtualizer.scrollToIndex(displayItems.length - 1, { align: "end" });
    }
  }, [displayItems.length, virtualizer]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    stickRef.current = atBottom;
    setShowScrollBtn(!atBottom);
  };

  const scrollToBottom = useCallback(() => {
    if (displayItems.length > 0) {
      virtualizer.scrollToIndex(displayItems.length - 1, { align: "end", behavior: "smooth" });
      stickRef.current = true;
      setShowScrollBtn(false);
    }
  }, [displayItems.length, virtualizer]);

  const isTerminal = ["review", "completed", "failed", "canceled"].includes(jobState ?? "");

  const handleSend = useCallback(async () => {
    if (!msg.trim()) return;
    setSending(true);
    try {
      if (isTerminal) {
        try {
          await resumeJob(jobId, msg.trim());
        } catch {
          // Worktree gone / unrecoverable — fall back to follow-up job
          const nextJob = await continueJob(jobId, msg.trim());
          toast.success("Follow-up job created");
          navigate(`/jobs/${nextJob.id}`);
        }
      } else {
        await sendOperatorMessage(jobId, msg.trim());
      }
      setMsg("");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSending(false);
    }
  }, [jobId, msg, isTerminal, navigate]);

  const handlePause = useCallback(async () => {
    setPausing(true);
    try {
      await pauseJob(jobId);
      toast.info("Agent paused");
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
          {searchOpen ? (
            <div className="flex items-center gap-1">
              <Search size={12} className="text-muted-foreground" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search…"
                className="bg-transparent text-xs text-foreground placeholder:text-muted-foreground border-b border-border focus:border-primary outline-none w-32"
                autoFocus
              />
              <button
                onClick={() => { setSearchOpen(false); setSearchQuery(""); }}
                className="text-muted-foreground hover:text-foreground"
              >
                <X size={12} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setSearchOpen(true)}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Search transcript"
            >
              <Search size={14} />
            </button>
          )}
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
        className="h-full overflow-y-auto overflow-x-hidden overscroll-contain"
        style={{ contain: "strict" }}
        onScroll={handleScroll}
      >
        {displayItems.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">No messages yet</p>
        ) : (
          <div style={{ height: `${virtualizer.getTotalSize()}px`, width: "100%", position: "relative" }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const item = displayItems[virtualRow.index];
              if (!item) return null;
              return (
                <div
                  key={virtualRow.key}
                  data-index={virtualRow.index}
                  ref={virtualizer.measureElement}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  <div className="p-3">
                  {item.type === "divider" && (
                    <div className="flex items-center gap-3 py-1">
                      <div className="flex-1 h-px bg-border" />
                      <span className="text-xs text-muted-foreground font-medium px-1">
                        {item.entry.content} resumed
                        {item.entry.timestamp
                          ? ` · ${new Date(item.entry.timestamp).toLocaleTimeString()}`
                          : ""}
                      </span>
                      <div className="flex-1 h-px bg-border" />
                    </div>
                  )}

                  {item.type === "operator" && (
                    <div className="flex gap-2 flex-row-reverse">
                      <div className="w-5 h-5 rounded-full bg-green-900/50 flex items-center justify-center shrink-0 mt-1">
                        <User size={12} />
                      </div>
                      <div className="max-w-[80%] rounded-xl rounded-tr-sm px-3 py-2 text-sm leading-relaxed bg-blue-900/30">
                        <div className="whitespace-pre-wrap break-words">{item.entry.content}</div>
                        <RelativeTime ts={item.entry.timestamp} className="text-xs text-muted-foreground mt-1 block" />
                      </div>
                    </div>
                  )}

                  {item.type === "turn" && (
                    <AgentTurn sdk={sdk} turn={item.turn} isLast={virtualRow.index === displayItems.length - 1} />
                  )}

                  {item.type === "approval" && (
                    <InlineApprovalCard approval={item.approval} />
                  )}
                  </div>
                </div>
              );
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
                  e.currentTarget.style.height = Math.min(e.currentTarget.scrollHeight, isMobile ? 240 : 160) + "px";
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !isMobile && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                disabled={sending || micState !== "idle"}
                rows={1}
                aria-label="Chat input"
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-1.5 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-none pr-8 overflow-y-auto"
                style={{ maxHeight: isMobile ? 240 : 160 }}
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
              className="shrink-0 min-w-[44px] min-h-[44px]"
              aria-label="Send message"
            >
              <Send size={16} />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
