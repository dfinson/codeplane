import { useState, useEffect, useRef, useMemo } from "react";
import {
  Cpu, Clock, Wrench, MessageSquare, Brain,
  AlertTriangle, ArrowDownUp, Search, ChevronDown, ChevronRight,
} from "lucide-react";
import { fetchJobTelemetry, fetchJobLogs } from "../api/client";
import { useTowerStore, selectJobLogs } from "../store";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";
import { Badge } from "./ui/badge";
import { Progress } from "./ui/progress";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ToolCall {
  name: string;
  durationMs: number;
  success: boolean;
}

interface LLMCall {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  durationMs: number;
}

interface TelemetryData {
  available: boolean;
  model?: string;
  durationMs?: number;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  totalCost?: number;
  contextWindowSize?: number;
  currentContextTokens?: number;
  contextUtilization?: number;
  compactions?: number;
  tokensCompacted?: number;
  toolCallCount?: number;
  totalToolDurationMs?: number;
  toolCalls?: ToolCall[];
  llmCallCount?: number;
  totalLlmDurationMs?: number;
  llmCalls?: LLMCall[];
  approvalCount?: number;
  totalApprovalWaitMs?: number;
  agentMessages?: number;
  operatorMessages?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

// ---------------------------------------------------------------------------
// Collapsible section helper
// ---------------------------------------------------------------------------

function Section({ title, icon, defaultOpen = true, badge, children }: {
  title: string;
  icon?: React.ReactNode;
  defaultOpen?: boolean;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-border bg-background overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left hover:bg-accent/30 transition-colors"
      >
        {open ? <ChevronDown size={12} className="text-muted-foreground shrink-0" /> : <ChevronRight size={12} className="text-muted-foreground shrink-0" />}
        {icon}
        <span className="text-xs font-semibold text-muted-foreground">{title}</span>
        {badge && <span className="ml-auto">{badge}</span>}
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 1: Summary
// ---------------------------------------------------------------------------

function SummaryTab({ data }: { data: TelemetryData }) {
  const fails = (data.toolCalls ?? []).filter((t) => !t.success).length;
  const slowest = [...(data.toolCalls ?? [])]
    .sort((a, b) => b.durationMs - a.durationMs)
    .slice(0, 3);

  return (
    <div className="space-y-4 p-4">
      {/* Top stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard icon={<Clock size={14} />} label="Duration" value={formatDuration(data.durationMs ?? 0)} color="text-blue-400" />
        <StatCard icon={<Cpu size={14} />} label="Tokens" value={formatTokens(data.totalTokens ?? 0)} color="text-violet-400" />
        <StatCard icon={<Brain size={14} />} label="LLM Calls" value={String(data.llmCallCount ?? 0)} color="text-blue-400" />
        <StatCard icon={<Wrench size={14} />} label="Tools" value={`${data.toolCallCount ?? 0}${fails ? ` (${fails} fail)` : ""}`} color="text-yellow-400" />
      </div>

      {/* Token breakdown */}
      <Section title="Token Breakdown" icon={<Cpu size={12} className="text-violet-400" />}>
        <div className="grid grid-cols-4 gap-2 text-center text-xs">
          <div>
            <p className="text-sm font-bold tabular-nums">{formatTokens(data.inputTokens ?? 0)}</p>
            <p className="text-muted-foreground">Input</p>
          </div>
          <div>
            <p className="text-sm font-bold tabular-nums">{formatTokens(data.outputTokens ?? 0)}</p>
            <p className="text-muted-foreground">Output</p>
          </div>
          <div>
            <p className="text-sm font-bold tabular-nums">{formatTokens(data.cacheReadTokens ?? 0)}</p>
            <p className="text-muted-foreground">Cache Read</p>
          </div>
          <div>
            <p className="text-sm font-bold tabular-nums">{formatTokens(data.cacheWriteTokens ?? 0)}</p>
            <p className="text-muted-foreground">Cache Write</p>
          </div>
        </div>
      </Section>

      {/* Context window + meta row */}
      <div className="flex flex-wrap gap-4">
        {data.contextWindowSize ? (
          <div className="flex-1 min-w-[200px]">
            <Section
              title="Context Window"
              badge={<span className="text-xs text-muted-foreground tabular-nums">{formatTokens(data.currentContextTokens ?? 0)} / {formatTokens(data.contextWindowSize)}</span>}
            >
              <Progress
                value={Math.min(100, (data.contextUtilization ?? 0) * 100)}
                color={(data.contextUtilization ?? 0) > 0.8 ? "red" : "blue"}
              />
              {(data.compactions ?? 0) > 0 && (
                <p className="text-[11px] text-yellow-400 mt-1.5 flex items-center gap-1">
                  <ArrowDownUp size={10} />
                  {data.compactions} compaction{data.compactions !== 1 ? "s" : ""} ({formatTokens(data.tokensCompacted ?? 0)} removed)
                </p>
              )}
            </Section>
          </div>
        ) : null}

        <Section title="Session Info" icon={<MessageSquare size={12} className="text-blue-400" />}>
          <div className="flex flex-col gap-2">
            {data.model && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Model</span>
                <Badge variant="secondary">{data.model}</Badge>
              </div>
            )}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <MessageSquare size={12} />
              {data.agentMessages ?? 0} agent / {data.operatorMessages ?? 0} operator
            </div>
            {(data.approvalCount ?? 0) > 0 && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <AlertTriangle size={12} />
                {data.approvalCount} approval{data.approvalCount !== 1 ? "s" : ""} ({formatDuration(data.totalApprovalWaitMs ?? 0)} wait)
              </div>
            )}
          </div>
        </Section>
      </div>

      {/* Slowest tools */}
      {slowest.length > 0 && (
        <Section title="Slowest Tools" icon={<Wrench size={12} className="text-yellow-400" />} defaultOpen={false}>
          <div className="space-y-1">
            {slowest.map((tc, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <div className={cn("w-1.5 h-1.5 rounded-full", tc.success ? "bg-green-500" : "bg-red-500")} />
                  <code className="font-mono text-foreground/80">{tc.name}</code>
                </div>
                <span className="text-muted-foreground tabular-nums">{formatDuration(tc.durationMs)}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: string; color: string }) {
  return (
    <div className="rounded-md border border-border bg-background p-3 text-center">
      <div className={cn("flex items-center justify-center gap-1.5 mb-1", color)}>
        {icon}
        <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
      </div>
      <p className="text-lg font-bold tabular-nums">{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 2: Timeline (intelligent clustered events)
// ---------------------------------------------------------------------------

interface TimelineNode {
  label: string;
  detail?: string;
  timestamp: string;
  kind: "state" | "tool_cluster" | "llm" | "compaction" | "error" | "approval";
  color: string;
  count?: number;
  durationMs?: number;
  success?: boolean;
}

function buildTimelineNodes(data: TelemetryData, logs: { timestamp: string; level: string; message: string }[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];

  // State transitions from logs
  for (const l of logs) {
    const lower = l.message.toLowerCase();
    if (l.level === "error") {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "error", color: "bg-red-500" });
    } else if (lower.includes("state") && (lower.includes("→") || lower.includes("->"))) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "state", color: "bg-blue-500" });
    } else if (lower.includes("started") && lower.includes("job")) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "state", color: "bg-blue-500" });
    } else if (lower.includes("succeeded") || lower.includes("completed") && lower.includes("job")) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "state", color: "bg-green-500" });
    } else if (lower.includes("failed") || lower.includes("canceled")) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "state", color: "bg-red-500" });
    } else if (lower.includes("compacted")) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "compaction", color: "bg-yellow-500" });
    } else if (lower.includes("approval")) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "approval", color: "bg-orange-500" });
    } else if (lower.includes("model changed")) {
      nodes.push({ label: l.message, timestamp: l.timestamp, kind: "state", color: "bg-violet-500" });
    }
  }

  // Cluster tool calls into groups of consecutive same-name calls
  const toolCalls = data.toolCalls ?? [];
  let i = 0;
  while (i < toolCalls.length) {
    const current = toolCalls[i]!;
    let count = 1;
    let totalMs = current.durationMs;
    let anyFail = !current.success;
    while (i + count < toolCalls.length) {
      const next = toolCalls[i + count]!;
      if (next.name !== current.name) break;
      totalMs += next.durationMs;
      if (!next.success) anyFail = true;
      count++;
    }
    const label = count > 1
      ? `${current.name} ×${count}`
      : current.name;
    nodes.push({
      label,
      detail: formatDuration(totalMs),
      timestamp: "", // tool calls don't have wall-clock timestamps from the API
      kind: "tool_cluster",
      color: anyFail ? "bg-red-500" : "bg-emerald-500",
      count,
      durationMs: totalMs,
      success: !anyFail,
    });
    i += count;
  }

  return nodes;
}

function TimelineTab({ data, jobId }: { data: TelemetryData; jobId: string }) {
  const allLogs = useTowerStore(selectJobLogs(jobId));
  const nodes = useMemo(
    () => buildTimelineNodes(data, allLogs),
    [data, allLogs],
  );

  // Separate state/system events (have timestamps) from tool clusters (no timestamps)
  const stateNodes = nodes.filter((n) => n.kind !== "tool_cluster");
  const toolNodes = nodes.filter((n) => n.kind === "tool_cluster");

  return (
    <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
      {/* Milestones */}
      {stateNodes.length > 0 && (
        <Section title="Milestones" badge={<span className="text-[11px] text-muted-foreground">{stateNodes.length}</span>}>
          <div className="space-y-1">
            {stateNodes.map((n, i) => (
              <div key={i} className="flex items-start gap-3 py-1 text-xs">
                <div className={cn("w-2 h-2 rounded-full mt-1.5 shrink-0", n.color)} />
                {n.timestamp && (
                  <span className="text-muted-foreground font-mono shrink-0 tabular-nums">
                    {new Date(n.timestamp).toLocaleTimeString()}
                  </span>
                )}
                <span className="text-foreground/80">{n.label}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Tool activity clusters */}
      {toolNodes.length > 0 && (
        <Section title="Tool Activity" icon={<Wrench size={12} className="text-yellow-400" />} badge={<span className="text-[11px] text-muted-foreground">{toolNodes.length}</span>}>
          <div className="flex flex-wrap gap-1.5">
            {toolNodes.map((n, i) => (
              <div
                key={i}
                className={cn(
                  "inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-mono border",
                  n.success === false
                    ? "border-red-500/30 bg-red-500/5 text-red-400"
                    : "border-border bg-muted/30 text-foreground/70",
                )}
              >
                <div className={cn("w-1.5 h-1.5 rounded-full shrink-0", n.color)} />
                <span>{n.label}</span>
                {n.detail && <span className="text-muted-foreground">{n.detail}</span>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {stateNodes.length === 0 && toolNodes.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-6">No timeline events yet</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3: Performance (tool + LLM tables)
// ---------------------------------------------------------------------------

type SortField = "name" | "count" | "avgMs" | "totalMs" | "fails";
type SortDir = "asc" | "desc";

interface ToolAggregate {
  name: string;
  count: number;
  totalMs: number;
  avgMs: number;
  fails: number;
}

function PerformanceTab({ data }: { data: TelemetryData }) {
  const [toolSort, setToolSort] = useState<{ field: SortField; dir: SortDir }>({ field: "totalMs", dir: "desc" });

  // Aggregate tools by name
  const toolAggs = useMemo(() => {
    const map = new Map<string, ToolAggregate>();
    for (const tc of data.toolCalls ?? []) {
      const agg = map.get(tc.name) ?? { name: tc.name, count: 0, totalMs: 0, avgMs: 0, fails: 0 };
      agg.count++;
      agg.totalMs += tc.durationMs;
      if (!tc.success) agg.fails++;
      map.set(tc.name, agg);
    }
    for (const agg of map.values()) {
      agg.avgMs = agg.totalMs / agg.count;
    }
    const list = Array.from(map.values());
    list.sort((a, b) => {
      const av = a[toolSort.field] as number;
      const bv = b[toolSort.field] as number;
      if (typeof av === "string") return toolSort.dir === "asc" ? (av as string).localeCompare(bv as unknown as string) : (bv as unknown as string).localeCompare(av);
      return toolSort.dir === "asc" ? av - bv : bv - av;
    });
    return list;
  }, [data.toolCalls, toolSort]);

  const llmCalls = data.llmCalls ?? [];

  const toggleSort = (field: SortField) => {
    setToolSort((prev) =>
      prev.field === field ? { field, dir: prev.dir === "asc" ? "desc" : "asc" } : { field, dir: "desc" },
    );
  };

  return (
    <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
      {/* Tool breakdown table */}
      {toolAggs.length > 0 && (
        <Section
          title={`Tool Breakdown (${data.toolCallCount ?? 0} calls, ${formatDuration(data.totalToolDurationMs ?? 0)} total)`}
          icon={<Wrench size={12} className="text-yellow-400" />}
        >
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/50 text-muted-foreground">
                  <SortHeader label="Tool" field="name" current={toolSort} onClick={toggleSort} />
                  <SortHeader label="Count" field="count" current={toolSort} onClick={toggleSort} align="right" />
                  <SortHeader label="Avg" field="avgMs" current={toolSort} onClick={toggleSort} align="right" />
                  <SortHeader label="Total" field="totalMs" current={toolSort} onClick={toggleSort} align="right" />
                  <SortHeader label="Fails" field="fails" current={toolSort} onClick={toggleSort} align="right" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {toolAggs.map((agg) => (
                  <tr key={agg.name} className="hover:bg-accent/30">
                    <td className="px-2 py-1.5 font-mono">{agg.name}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{agg.count}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">{formatDuration(agg.avgMs)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{formatDuration(agg.totalMs)}</td>
                    <td className={cn("px-2 py-1.5 text-right tabular-nums", agg.fails > 0 && "text-red-400")}>{agg.fails}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {/* LLM calls table */}
      {llmCalls.length > 0 && (
        <Section
          title={`LLM Calls (${data.llmCallCount ?? 0} calls, ${formatDuration(data.totalLlmDurationMs ?? 0)} total)`}
          icon={<Brain size={12} className="text-violet-400" />}
          defaultOpen={false}
        >
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/50 text-muted-foreground">
                  <th className="px-2 py-1.5 text-left font-medium">#</th>
                  <th className="px-2 py-1.5 text-left font-medium">Model</th>
                  <th className="px-2 py-1.5 text-right font-medium">In</th>
                  <th className="px-2 py-1.5 text-right font-medium">Out</th>
                  <th className="px-2 py-1.5 text-right font-medium">Cache</th>
                  <th className="px-2 py-1.5 text-right font-medium">Duration</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {llmCalls.map((lc, i) => (
                  <tr key={i} className="hover:bg-accent/30">
                    <td className="px-2 py-1.5 text-muted-foreground tabular-nums">{i + 1}</td>
                    <td className="px-2 py-1.5 font-mono">{lc.model || "—"}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{formatTokens(lc.inputTokens)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{formatTokens(lc.outputTokens)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                      {lc.cacheReadTokens > 0 ? formatTokens(lc.cacheReadTokens) : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{formatDuration(lc.durationMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {toolAggs.length === 0 && llmCalls.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-6">No performance data yet</p>
      )}
    </div>
  );
}

function SortHeader({
  label,
  field,
  current,
  onClick,
  align = "left",
}: {
  label: string;
  field: SortField;
  current: { field: SortField; dir: SortDir };
  onClick: (f: SortField) => void;
  align?: "left" | "right";
}) {
  const active = current.field === field;
  return (
    <th
      className={cn("px-2 py-1.5 font-medium cursor-pointer hover:text-foreground select-none", align === "right" && "text-right")}
      onClick={() => onClick(field)}
    >
      {label}
      {active && <span className="ml-0.5">{current.dir === "asc" ? "↑" : "↓"}</span>}
    </th>
  );
}

// ---------------------------------------------------------------------------
// Tab 4: Logs (debug console)
// ---------------------------------------------------------------------------

const LEVELS = ["debug", "info", "warn", "error"] as const;
type Level = (typeof LEVELS)[number];
const LEVEL_PRIORITY: Record<Level, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const LEVEL_CLASSES: Record<string, string> = {
  debug: "text-muted-foreground",
  info: "text-blue-400",
  warn: "text-yellow-400",
  error: "text-red-400",
};
const LEVEL_DOT: Record<string, string> = {
  debug: "bg-muted-foreground",
  info: "bg-blue-400",
  warn: "bg-yellow-400",
  error: "bg-red-400",
};

const CATEGORY_PATTERNS: [string, RegExp][] = [
  ["tool", /^tool\s/i],
  ["llm", /^llm\s|tokens?\)/i],
  ["git", /git|branch|commit|push|pull/i],
  ["state", /state|started|succeeded|failed|canceled|queued|running/i],
  ["ctx", /context|compact/i],
  ["model", /model\s+changed/i],
  ["auth", /approval|auth|permission/i],
];

function categorize(msg: string): string {
  for (const [cat, re] of CATEGORY_PATTERNS) {
    if (re.test(msg)) return cat;
  }
  return "sys";
}

const CATEGORY_COLORS: Record<string, string> = {
  tool: "text-yellow-400",
  llm: "text-violet-400",
  git: "text-orange-400",
  state: "text-blue-400",
  ctx: "text-cyan-400",
  model: "text-pink-400",
  auth: "text-orange-400",
  sys: "text-muted-foreground",
};

function LogsTab({ jobId }: { jobId: string }) {
  const allLogs = useTowerStore(selectJobLogs(jobId));
  const [minLevel, setMinLevel] = useState<Level>("info");
  const [filter, setFilter] = useState("");
  const viewportRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  const logs = useMemo(() => {
    let filtered = allLogs.filter(
      (l) => LEVEL_PRIORITY[l.level as Level] >= LEVEL_PRIORITY[minLevel],
    );
    if (filter) {
      const lower = filter.toLowerCase();
      filtered = filtered.filter((l) => l.message.toLowerCase().includes(lower));
    }
    return filtered;
  }, [allLogs, minLevel, filter]);

  useEffect(() => {
    fetchJobLogs(jobId, minLevel).then((fetched) => {
      useTowerStore.setState((s) => {
        const existing = s.logs[jobId] ?? [];
        const fetchedSeqs = new Set(fetched.map((l) => l.seq));
        const merged = [
          ...fetched,
          ...existing.filter((l) => !fetchedSeqs.has(l.seq)),
        ].sort((a, b) => a.seq - b.seq);
        return { logs: { ...s.logs, [jobId]: merged } };
      });
    }).catch(() => {});
  }, [jobId, minLevel]);

  useEffect(() => {
    if (stickRef.current && viewportRef.current) {
      viewportRef.current.scrollTo({ top: viewportRef.current.scrollHeight });
    }
  }, [logs.length]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <div className="flex flex-col overflow-hidden">
      {/* Controls */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border gap-2">
        <div className="flex items-center gap-1">
          {LEVELS.map((level) => {
            const active = LEVEL_PRIORITY[level] === LEVEL_PRIORITY[minLevel];
            const dimmed = LEVEL_PRIORITY[level] < LEVEL_PRIORITY[minLevel];
            return (
              <button
                key={level}
                type="button"
                onClick={() => { setMinLevel(level); stickRef.current = true; }}
                title={`Show ${level} and above`}
                className={cn(
                  "flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border transition-colors",
                  active
                    ? "border-transparent bg-muted text-foreground ring-1 ring-ring"
                    : dimmed
                    ? "border-transparent text-muted-foreground/40"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                <span className={cn("w-1.5 h-1.5 rounded-full", dimmed ? "bg-muted-foreground/30" : LEVEL_DOT[level])} />
                {level}
              </button>
            );
          })}
        </div>
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            placeholder="Filter…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-6 pl-6 pr-2 text-xs rounded border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring w-32"
          />
        </div>
      </div>

      {/* Log lines */}
      <div
        ref={viewportRef}
        className="h-64 min-h-0 overflow-y-auto overscroll-contain font-mono"
        onScroll={handleScroll}
      >
        {logs.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">No logs</p>
        ) : (
          <div className="p-2 space-y-px">
            {logs.map((l, i) => {
              const cat = categorize(l.message);
              return (
                <div key={i} className="flex items-start gap-2 text-xs py-0.5 hover:bg-accent/30 px-1 rounded">
                  <span className="text-muted-foreground shrink-0 tabular-nums">
                    {new Date(l.timestamp).toLocaleTimeString()}
                  </span>
                  <span className={cn("uppercase font-semibold w-10 shrink-0", LEVEL_CLASSES[l.level])}>
                    {l.level}
                  </span>
                  <span className={cn("w-10 shrink-0 font-medium", CATEGORY_COLORS[cat])}>
                    [{cat}]
                  </span>
                  <span className="text-foreground/80 break-words min-w-0">{l.message}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function InsightsPanel({ jobId }: { jobId: string }) {
  const [collapsed, setCollapsed] = useState(true);
  const [insightTab, setInsightTab] = useState("summary");
  const [data, setData] = useState<TelemetryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchJobTelemetry(jobId)
        .then((d) => { if (!cancelled) { setData(d); setLoading(false); } })
        .catch(() => { if (!cancelled) { setData({ available: false }); setLoading(false); } });
    };
    load();
    const interval = setInterval(load, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [jobId]);

  const headerStats = data?.available
    ? `${formatTokens(data.totalTokens ?? 0)} tokens · ${data.toolCallCount ?? 0} tools · ${formatDuration(data.durationMs ?? 0)}`
    : null;

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Collapsible header */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-2 px-4 py-2.5 border-b border-border hover:bg-accent/30 transition-colors text-left"
      >
        {collapsed ? <ChevronRight size={14} className="text-muted-foreground shrink-0" /> : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
        <span className="text-sm font-semibold text-muted-foreground">Insights</span>
        {headerStats && (
          <span className="text-[11px] text-muted-foreground ml-auto hidden sm:block">{headerStats}</span>
        )}
      </button>

      {!collapsed && (
        <>
          <div className="px-4 py-2 border-b border-border">
            <Tabs value={insightTab} onValueChange={setInsightTab}>
              <TabsList className="h-7">
                <TabsTrigger value="summary" className="text-xs px-2 py-0.5">Summary</TabsTrigger>
                <TabsTrigger value="timeline" className="text-xs px-2 py-0.5">Timeline</TabsTrigger>
                <TabsTrigger value="performance" className="text-xs px-2 py-0.5">Performance</TabsTrigger>
                <TabsTrigger value="logs" className="text-xs px-2 py-0.5">Logs</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {loading ? (
            <div className="flex justify-center py-8"><Spinner size="sm" /></div>
          ) : (
            <>
              {insightTab === "summary" && (
                data?.available ? <SummaryTab data={data} /> : <EmptyState />
              )}
              {insightTab === "timeline" && (
                data?.available ? <TimelineTab data={data} jobId={jobId} /> : <EmptyState />
              )}
              {insightTab === "performance" && (
                data?.available ? <PerformanceTab data={data} /> : <EmptyState />
              )}
              {insightTab === "logs" && (
                <LogsTab jobId={jobId} />
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function EmptyState() {
  return <p className="text-sm text-muted-foreground text-center py-8">No data available yet</p>;
}
