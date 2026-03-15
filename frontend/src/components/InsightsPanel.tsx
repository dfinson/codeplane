import { useState, useEffect, useMemo } from "react";
import {
  Cpu, Clock, Wrench, MessageSquare, Brain,
  AlertTriangle, ArrowDownUp, ChevronDown, ChevronRight,
} from "lucide-react";
import { fetchJobTelemetry } from "../api/client";
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
  offsetSec?: number;
}

interface LLMCall {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  durationMs: number;
  offsetSec?: number;
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

function formatOffset(sec: number): string {
  if (sec < 60) return `T+${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `T+${m}m${s ? `${s}s` : ""}`;
}

// ---------------------------------------------------------------------------
// Activity timeline — built from tool calls + LLM calls with offsetSec
// ---------------------------------------------------------------------------

interface ActivityChunk {
  offsetSec: number;
  tools: { name: string; count: number; totalMs: number; fails: number }[];
  llmCalls: { model: string; inputTokens: number; outputTokens: number; durationMs: number }[];
}

function buildActivityChunks(data: TelemetryData): ActivityChunk[] {
  // Merge tool calls and LLM calls into a single sorted stream by offsetSec
  type Event = { type: "tool"; tc: ToolCall } | { type: "llm"; lc: LLMCall };
  const events: Event[] = [
    ...(data.toolCalls ?? []).map((tc) => ({ type: "tool" as const, tc })),
    ...(data.llmCalls ?? []).map((lc) => ({ type: "llm" as const, lc })),
  ];
  events.sort((a, b) => {
    const oa = a.type === "tool" ? (a.tc.offsetSec ?? 0) : (a.lc.offsetSec ?? 0);
    const ob = b.type === "tool" ? (b.tc.offsetSec ?? 0) : (b.lc.offsetSec ?? 0);
    return oa - ob;
  });

  if (events.length === 0) return [];

  // Group into chunks: events within 10s of each other form a chunk
  const chunks: ActivityChunk[] = [];
  let current: ActivityChunk | null = null;
  let chunkEnd = 0;

  for (const ev of events) {
    const offset = ev.type === "tool" ? (ev.tc.offsetSec ?? 0) : (ev.lc.offsetSec ?? 0);
    if (!current || offset > chunkEnd + 10) {
      current = { offsetSec: offset, tools: [], llmCalls: [] };
      chunks.push(current);
    }
    chunkEnd = offset;

    if (ev.type === "tool") {
      const existing = current.tools.find((t) => t.name === ev.tc.name);
      if (existing) {
        existing.count++;
        existing.totalMs += ev.tc.durationMs;
        if (!ev.tc.success) existing.fails++;
      } else {
        current.tools.push({
          name: ev.tc.name,
          count: 1,
          totalMs: ev.tc.durationMs,
          fails: ev.tc.success ? 0 : 1,
        });
      }
    } else {
      current.llmCalls.push({
        model: ev.lc.model,
        inputTokens: ev.lc.inputTokens,
        outputTokens: ev.lc.outputTokens,
        durationMs: ev.lc.durationMs,
      });
    }
  }

  return chunks;
}

// ---------------------------------------------------------------------------
// Sort header for tool breakdown table
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
// Main component — single flat view, no tabs
// ---------------------------------------------------------------------------

export function InsightsPanel({ jobId }: { jobId: string }) {
  const [collapsed, setCollapsed] = useState(true);
  const [data, setData] = useState<TelemetryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [toolSort, setToolSort] = useState<{ field: SortField; dir: SortDir }>({ field: "totalMs", dir: "desc" });

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

  const fails = (data?.toolCalls ?? []).filter((t) => !t.success).length;

  const activityChunks = useMemo(
    () => (data?.available ? buildActivityChunks(data) : []),
    [data],
  );

  const toolAggs = useMemo(() => {
    const map = new Map<string, ToolAggregate>();
    for (const tc of data?.toolCalls ?? []) {
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
  }, [data?.toolCalls, toolSort]);

  const toggleSort = (field: SortField) => {
    setToolSort((prev) =>
      prev.field === field ? { field, dir: prev.dir === "asc" ? "desc" : "asc" } : { field, dir: "desc" },
    );
  };

  const llmCalls = data?.llmCalls ?? [];

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
        <div className="space-y-4 p-4">
          {loading ? (
            <div className="flex justify-center py-8"><Spinner size="sm" /></div>
          ) : !data?.available ? (
            <p className="text-sm text-muted-foreground text-center py-8">No data available yet</p>
          ) : (
            <>
              {/* Session Info — on top */}
              <div className="flex flex-wrap items-center gap-3 text-xs">
                {data.model && <Badge variant="secondary">{data.model}</Badge>}
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <MessageSquare size={12} />
                  {data.agentMessages ?? 0} agent / {data.operatorMessages ?? 0} operator
                </span>
                {(data.approvalCount ?? 0) > 0 && (
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <AlertTriangle size={12} />
                    {data.approvalCount} approval{data.approvalCount !== 1 ? "s" : ""} ({formatDuration(data.totalApprovalWaitMs ?? 0)} wait)
                  </span>
                )}
              </div>

              {/* Stat cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <StatCard icon={<Clock size={14} />} label="Duration" value={formatDuration(data.durationMs ?? 0)} color="text-blue-400" />
                <StatCard icon={<Cpu size={14} />} label="Tokens" value={formatTokens(data.totalTokens ?? 0)} color="text-violet-400" />
                <StatCard icon={<Brain size={14} />} label="LLM Calls" value={String(data.llmCallCount ?? 0)} color="text-blue-400" />
                <StatCard icon={<Wrench size={14} />} label="Tools" value={`${data.toolCallCount ?? 0}${fails ? ` (${fails} fail)` : ""}`} color="text-yellow-400" />
              </div>

              {/* Token breakdown */}
              <div>
                <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
                  <Cpu size={12} className="text-violet-400" /> Token Breakdown
                </h4>
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
              </div>

              {/* Context window */}
              {data.contextWindowSize ? (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-xs font-semibold text-muted-foreground">Context Window</h4>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {formatTokens(data.currentContextTokens ?? 0)} / {formatTokens(data.contextWindowSize)}
                    </span>
                  </div>
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
                </div>
              ) : null}

              {/* Activity timeline */}
              {activityChunks.length > 0 && (
                <div>
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
                    <Clock size={12} className="text-blue-400" /> Activity
                  </h4>
                  <div className="relative pl-4 border-l-2 border-border space-y-3">
                    {activityChunks.map((chunk, i) => (
                      <div key={i} className="relative">
                        <div className="absolute -left-[calc(1rem+5px)] top-1 w-2 h-2 rounded-full bg-blue-500" />
                        <span className="text-[11px] font-mono text-muted-foreground tabular-nums">
                          {formatOffset(chunk.offsetSec)}
                        </span>
                        <div className="flex flex-wrap gap-1.5 mt-1">
                          {chunk.llmCalls.map((lc, j) => (
                            <span key={`llm-${j}`} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono border border-violet-500/30 bg-violet-500/5 text-violet-400">
                              <Brain size={10} />
                              {formatTokens(lc.inputTokens + lc.outputTokens)} · {formatDuration(lc.durationMs)}
                            </span>
                          ))}
                          {chunk.tools.map((t, j) => (
                            <span
                              key={`tool-${j}`}
                              className={cn(
                                "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono border",
                                t.fails > 0
                                  ? "border-red-500/30 bg-red-500/5 text-red-400"
                                  : "border-border bg-muted/30 text-foreground/70",
                              )}
                            >
                              {t.name}{t.count > 1 ? ` ×${t.count}` : ""}
                              <span className="text-muted-foreground">{formatDuration(t.totalMs)}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Tool breakdown table */}
              {toolAggs.length > 0 && (
                <div>
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
                    <Wrench size={12} className="text-yellow-400" />
                    Tool Breakdown
                    <span className="text-muted-foreground font-normal ml-1">
                      ({data.toolCallCount ?? 0} calls, {formatDuration(data.totalToolDurationMs ?? 0)})
                    </span>
                  </h4>
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
                </div>
              )}

              {/* LLM calls table */}
              {llmCalls.length > 0 && (
                <div>
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
                    <Brain size={12} className="text-violet-400" />
                    LLM Calls
                    <span className="text-muted-foreground font-normal ml-1">
                      ({data.llmCallCount ?? 0} calls, {formatDuration(data.totalLlmDurationMs ?? 0)})
                    </span>
                  </h4>
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
                </div>
              )}
            </>
          )}
        </div>
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
