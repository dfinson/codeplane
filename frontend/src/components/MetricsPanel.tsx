import { useState, useEffect, useMemo } from "react";
import {
  Cpu, Clock, Wrench, MessageSquare, Brain,
  AlertTriangle, ArrowDownUp, ChevronDown, ChevronRight,
  BookOpen, CheckCircle, XCircle, DollarSign, Zap,
} from "lucide-react";
import { fetchJobTelemetry, fetchArtifacts, fetchArtifactContent } from "../api/client";
import { Badge } from "./ui/badge";
import { Progress } from "./ui/progress";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { useStore } from "../store";

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
  isSubagent: boolean;
}

interface TelemetryData {
  available: boolean;
  sdk?: string;
  model?: string;
  mainModel?: string;
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
  // Copilot: premium requests consumed this session
  premiumRequests?: number;
  // Copilot: per-resource quota snapshots
  quotaSnapshots?: Record<string, QuotaSnapshotData>;
}

interface QuotaSnapshotData {
  usedRequests: number;
  entitlementRequests: number;
  remainingPercentage: number;
  overage: number;
  overageAllowed: boolean;
  isUnlimited: boolean;
  usageAllowedWithExhaustedQuota: boolean;
  resetDate: string;
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
// Cost / quota helpers
// ---------------------------------------------------------------------------

// Per-model USD rates ($/MTok) for Claude API — input / output
const CLAUDE_MODEL_RATES: Record<string, { input: number; output: number; label: string }> = {
  "claude-opus-4-6":    { input: 5,   output: 25,  label: "Claude Opus 4.6" },
  "claude-opus-4-5":    { input: 5,   output: 25,  label: "Claude Opus 4.5" },
  "claude-opus-4":      { input: 15,  output: 75,  label: "Claude Opus 4" },
  "claude-sonnet-4-6":  { input: 3,   output: 15,  label: "Claude Sonnet 4.6" },
  "claude-sonnet-4-5":  { input: 3,   output: 15,  label: "Claude Sonnet 4.5" },
  "claude-sonnet-4":    { input: 3,   output: 15,  label: "Claude Sonnet 4" },
  "claude-haiku-4-5":   { input: 1,   output: 5,   label: "Claude Haiku 4.5" },
  "claude-haiku-3-5":   { input: 0.8, output: 4,   label: "Claude Haiku 3.5" },
};

function normalizeModelKey(model: string): string {
  return model.toLowerCase().replace(/[^a-z0-9]/g, "-").replace(/-+/g, "-");
}

function lookupModelRate(model: string) {
  const key = normalizeModelKey(model);
  return CLAUDE_MODEL_RATES[key] ?? null;
}

function formatUsd(amount: number): string {
  if (amount < 0.001) return `$${amount.toFixed(6)}`;
  if (amount < 0.01)  return `$${amount.toFixed(4)}`;
  if (amount < 1)     return `$${amount.toFixed(3)}`;
  return `$${amount.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// CostSection component
// ---------------------------------------------------------------------------

function CostSection({ data }: { data: TelemetryData }) {
  const sdk = data.sdk ?? "";
  const isCopilot = sdk === "copilot";
  const isClaude = sdk === "claude";

  if (!isCopilot && !isClaude) {
    return null;
  }

  return (
    <div>
      <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-3">
        {isCopilot ? <Zap size={12} className="text-yellow-400" /> : <DollarSign size={12} className="text-green-400" />}
        {isCopilot ? "Premium Requests" : "Cost"}
      </h4>

      {isCopilot && <CopilotCostView data={data} />}
      {isClaude && <ClaudeCostView data={data} />}
    </div>
  );
}

function CopilotCostView({ data }: { data: TelemetryData }) {
  const snapshots = data.quotaSnapshots ?? {};
  const snapshotEntries = Object.entries(snapshots);

  return (
    <div className="space-y-3">
      {/* Premium requests consumed this session */}
      {(data.premiumRequests ?? 0) > 0 && (
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-muted-foreground">This session</span>
          <span className="font-semibold tabular-nums text-yellow-400">
            {data.premiumRequests} premium request{data.premiumRequests !== 1 ? "s" : ""}
          </span>
        </div>
      )}

      {/* Per-resource quota snapshots */}
      {snapshotEntries.map(([key, snap]) => {
        const label = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
        const pct = snap.remainingPercentage;
        const usedPct = Math.min(100, 100 - pct);
        const exhausted = !snap.isUnlimited && pct <= 0;
        const nearLimit = !snap.isUnlimited && pct < 20 && pct > 0;

        return (
          <div key={key} className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{label}</span>
              {snap.isUnlimited ? (
                <span className="text-green-400 text-xs">Unlimited</span>
              ) : (
                <span className={cn("tabular-nums text-xs", exhausted ? "text-red-400" : nearLimit ? "text-yellow-400" : "text-muted-foreground")}>
                  {snap.usedRequests.toFixed(1)} / {snap.entitlementRequests.toFixed(0)} used
                  {snap.overage > 0 && ` (+${snap.overage.toFixed(1)} overage)`}
                </span>
              )}
            </div>
            {!snap.isUnlimited && (
              <Progress
                value={usedPct}
                color={exhausted || nearLimit ? "red" : "blue"}
              />
            )}
            {snap.resetDate && (
              <p className="text-xs text-muted-foreground">
                Resets {new Date(snap.resetDate).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </p>
            )}
          </div>
        );
      })}

      {(data.premiumRequests ?? 0) === 0 && snapshotEntries.length === 0 && (
        <p className="text-xs text-muted-foreground italic">Premium request data available after session completes.</p>
      )}

      <p className="text-xs text-muted-foreground leading-snug">
        Premium requests are consumed based on model multipliers (e.g. Claude Sonnet 4.6 = 1×,
        Claude Opus 4.5 = 3×). Included models (GPT-5 mini, GPT-4.1, GPT-4o) cost 0 on paid plans.
      </p>
    </div>
  );
}

function ClaudeCostView({ data }: { data: TelemetryData }) {
  const totalCost = data.totalCost ?? 0;
  const rate = data.model ? lookupModelRate(data.model) : null;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">Total API cost</span>
        <span className={cn("font-semibold tabular-nums", totalCost > 5 ? "text-red-400" : totalCost > 1 ? "text-yellow-400" : "text-green-400")}>
          {formatUsd(totalCost)}
        </span>
      </div>

      {rate && (
        <p className="text-xs text-muted-foreground">
          {rate.label}: ${rate.input}/MTok input · ${rate.output}/MTok output
        </p>
      )}

      {totalCost === 0 && (
        <p className="text-xs text-muted-foreground italic">Cost data available after session completes.</p>
      )}

      <p className="text-xs text-muted-foreground leading-snug">
        Claude Max and enterprise (Bedrock/Vertex/Foundry) plans do not expose quota via the SDK.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Session summary timeline — powered by agent_summary artifacts
// ---------------------------------------------------------------------------

interface SummaryAccomplished {
  what: string;
  files_affected?: string[];
}

interface SummaryInProgress {
  description: string;
  file?: string;
}

interface SummaryVerification {
  tests_run: boolean;
  tests_passed: boolean | null;
  build_run: boolean;
  build_passed: boolean | null;
}

interface SessionSummaryJson {
  session_number?: number;
  accomplished?: SummaryAccomplished[];
  in_progress?: SummaryInProgress[] | null;
  resume_instructions?: string;
  verification_state?: SummaryVerification | null;
}

interface SessionCheckpoint {
  sessionNumber: number;
  artifactId: string;
  createdAt: string;
  summary: SessionSummaryJson | null;
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

export function MetricsPanel({ jobId, isRunning = false }: { jobId: string; isRunning?: boolean }) {
  const [collapsed, setCollapsed] = useState(true);
  const [toolsCollapsed, setToolsCollapsed] = useState(true);
  const [llmCollapsed, setLlmCollapsed] = useState(true);
  const [llmMainExpanded, setLlmMainExpanded] = useState(false);
  const [llmSubExpanded, setLlmSubExpanded] = useState(false);
  const [data, setData] = useState<TelemetryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [toolSort, setToolSort] = useState<{ field: SortField; dir: SortDir }>({ field: "totalMs", dir: "desc" });
  const [checkpoints, setCheckpoints] = useState<SessionCheckpoint[]>([]);

  // Subscribe to the per-job telemetry version counter — bumped whenever a
  // telemetry_updated SSE event arrives (e.g. when a session ends).
  const telemetryVersion = useStore((s) => s.telemetryVersions[jobId] ?? 0);

  // Fetch telemetry:
  //   • on mount
  //   • when the job stops running (isRunning flip)
  //   • when a telemetry_updated SSE event arrives (telemetryVersion bump)
  //   • every 5 s while running (for live duration / accumulating totals)
  useEffect(() => {
    let cancelled = false;
    const doFetch = () => {
      fetchJobTelemetry(jobId)
        .then((d) => { if (!cancelled) { setData(d); setLoading(false); } })
        .catch(() => { if (!cancelled) { setData((prev) => prev ?? { available: false }); setLoading(false); } });
    };
    doFetch();
    if (!isRunning) return () => { cancelled = true; };
    const interval = setInterval(doFetch, 5_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [jobId, isRunning, telemetryVersion]);

  // Load agent_summary artifacts once on mount and when job stops
  useEffect(() => {
    let cancelled = false;
    const loadCheckpoints = async () => {
      try {
        const { items } = await fetchArtifacts(jobId);
        const summaryItems = items
          .filter((a) => a.type === "agent_summary")
          .sort((a, b) => a.createdAt.localeCompare(b.createdAt));

        const resolved = await Promise.all(
          summaryItems.map(async (artifact) => {
            const m = artifact.name.match(/session-(\d+)-summary/);
            const sessionNumber = m ? parseInt(m[1] ?? "0", 10) : 0;
            let summary: SessionSummaryJson | null = null;
            try {
              summary = (await fetchArtifactContent(artifact.id)) as SessionSummaryJson;
            } catch {
              // leave summary null — still show the checkpoint without detail
            }
            return { sessionNumber, artifactId: artifact.id, createdAt: artifact.createdAt, summary };
          }),
        );

        if (!cancelled) setCheckpoints(resolved);
      } catch {
        // artifacts unavailable — leave checkpoints empty
      }
    };
    loadCheckpoints();
    return () => { cancelled = true; };
  }, [jobId]);

  const headerStats = data?.available
    ? `${formatTokens(data.totalTokens ?? 0)} tokens · ${data.toolCallCount ?? 0} tools · ${formatDuration(data.durationMs ?? 0)}`
    : null;

  const fails = (data?.toolCalls ?? []).filter((t) => !t.success).length;

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

  const allLlmCalls = data?.llmCalls ?? [];
  const mainCalls = allLlmCalls.filter((c) => !c.isSubagent);
  const subCalls = allLlmCalls.filter((c) => c.isSubagent);

  // Aggregate sub-agent calls by model
  const subAgentGroups = useMemo(() => {
    const map = new Map<string, { model: string; count: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; durationMs: number; calls: LLMCall[] }>();
    for (const c of subCalls) {
      const key = c.model || "unknown";
      const g = map.get(key) ?? { model: key, count: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, durationMs: 0, calls: [] };
      g.count++;
      g.inputTokens += c.inputTokens;
      g.outputTokens += c.outputTokens;
      g.cacheReadTokens += c.cacheReadTokens;
      g.durationMs += c.durationMs;
      g.calls.push(c);
      map.set(key, g);
    }
    return Array.from(map.values()).sort((a, b) => b.count - a.count);
  }, [subCalls]);

  // Main agent totals
  const mainTotals = useMemo(() => ({
    inputTokens: mainCalls.reduce((s, c) => s + c.inputTokens, 0),
    outputTokens: mainCalls.reduce((s, c) => s + c.outputTokens, 0),
    cacheReadTokens: mainCalls.reduce((s, c) => s + c.cacheReadTokens, 0),
    durationMs: mainCalls.reduce((s, c) => s + c.durationMs, 0),
  }), [mainCalls]);

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Collapsible header */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-2 px-4 py-2.5 border-b border-border hover:bg-accent/30 transition-colors text-left"
      >
        {collapsed ? <ChevronRight size={14} className="text-muted-foreground shrink-0" /> : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
        <DollarSign size={14} className="text-muted-foreground shrink-0" />
        <span className="text-sm font-semibold text-muted-foreground">Metrics</span>
        {headerStats && (
          <span className="text-xs text-muted-foreground ml-auto hidden sm:block">{headerStats}</span>
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
                {(data.mainModel || data.model) && (
                  <Badge variant="secondary" title="Main agent model">
                    {data.mainModel || data.model}
                  </Badge>
                )}
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
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center text-xs">
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
                    <p className="text-xs text-yellow-400 mt-1.5 flex items-center gap-1">
                      <ArrowDownUp size={10} />
                      {data.compactions} compaction{data.compactions !== 1 ? "s" : ""} ({formatTokens(data.tokensCompacted ?? 0)} removed)
                    </p>
                  )}
                </div>
              ) : null}

              {/* Cost / quota — shown for copilot and claude sdk jobs */}
              <CostSection data={data} />

              {/* Session summary — only shown when summaries exist */}
              {checkpoints.length > 0 && (
              <div>
                <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-3">
                  <BookOpen size={12} className="text-blue-400" /> Summary
                </h4>
                  <div className="relative pl-5">
                    {/* Vertical rail */}
                    <div className="absolute left-[7px] top-2 bottom-2 w-px bg-border" />
                    <div className="space-y-4">
                      {checkpoints.map((cp) => {
                        const { summary } = cp;
                        const accomplished = summary?.accomplished ?? [];
                        const inProgress = summary?.in_progress ?? [];
                        const ver = summary?.verification_state;
                        const verBadge = ver
                          ? ver.tests_passed === true
                            ? <span className="flex items-center gap-0.5 text-green-400"><CheckCircle size={10} /> tests passed</span>
                            : ver.tests_passed === false
                              ? <span className="flex items-center gap-0.5 text-red-400"><XCircle size={10} /> tests failed</span>
                              : ver.build_passed === true
                                ? <span className="flex items-center gap-0.5 text-green-400"><CheckCircle size={10} /> build passed</span>
                                : null
                          : null;

                        return (
                          <div key={cp.artifactId} className="relative">
                            {/* Dot on the rail */}
                            <div className="absolute -left-5 top-[3px] w-3 h-3 rounded-full border-2 border-blue-400 bg-background" />
                            <div className="space-y-1">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-xs font-semibold text-foreground">Session {cp.sessionNumber}</span>
                                <span className="text-xs text-muted-foreground tabular-nums">
                                  {new Date(cp.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                                </span>
                                {verBadge && <span className="text-xs">{verBadge}</span>}
                              </div>
                              {accomplished.length > 0 && (
                                <ul className="space-y-0.5">
                                  {accomplished.slice(0, 4).map((item, i) => (
                                    <li key={i} className="text-xs text-muted-foreground flex gap-1.5">
                                      <span className="text-muted-foreground/50 shrink-0">·</span>
                                      <span>{item.what}</span>
                                    </li>
                                  ))}
                                  {accomplished.length > 4 && (
                                    <li className="text-xs text-muted-foreground/60 pl-3">
                                      and {accomplished.length - 4} more
                                    </li>
                                  )}
                                </ul>
                              )}
                              {inProgress.length > 0 && inProgress[0] && (
                                <p className="text-xs text-yellow-400/80">
                                  In progress: {inProgress[0].description}
                                </p>
                              )}
                              {summary?.resume_instructions && (
                                <p className="text-xs text-muted-foreground/70 italic">
                                  Next: {summary.resume_instructions}
                                </p>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
              </div>
              )}

              {/* Tool breakdown table */}
              {toolAggs.length > 0 && (
                <div>
                  <button
                    className="flex w-full items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2 hover:text-foreground transition-colors"
                    onClick={() => setToolsCollapsed((c) => !c)}
                  >
                    {toolsCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                    <Wrench size={12} className="text-yellow-400" />
                    Tool Breakdown
                    <span className="text-muted-foreground font-normal ml-1">
                      ({data.toolCallCount ?? 0} calls, {formatDuration(data.totalToolDurationMs ?? 0)})
                    </span>
                  </button>
                  {!toolsCollapsed && (
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
                  )}
                </div>
              )}

              {/* LLM calls — two-tier: Main Agent + Sub-agents */}
              {allLlmCalls.length > 0 && (
                <div>
                  <button
                    className="flex w-full items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2 hover:text-foreground transition-colors"
                    onClick={() => setLlmCollapsed((c) => !c)}
                  >
                    {llmCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                    <Brain size={12} className="text-violet-400" />
                    LLM Calls
                    <span className="text-muted-foreground font-normal ml-1">
                      ({data.llmCallCount ?? 0} calls, {formatDuration(data.totalLlmDurationMs ?? 0)})
                    </span>
                  </button>

                  {!llmCollapsed && (
                    <div className="space-y-2">

                      {/* ── Main agent tier ── */}
                      <div className="rounded-md border border-border overflow-hidden">
                        <button
                          className="flex w-full items-center gap-2 px-3 py-2 bg-muted/30 hover:bg-muted/50 transition-colors text-left"
                          onClick={() => setLlmMainExpanded((c) => !c)}
                        >
                          {llmMainExpanded ? <ChevronDown size={11} className="text-muted-foreground shrink-0" /> : <ChevronRight size={11} className="text-muted-foreground shrink-0" />}
                          <span className="text-xs font-medium text-foreground">Main agent</span>
                          {(data.mainModel || data.model) && (
                            <span className="font-mono text-xs text-muted-foreground">{data.mainModel || data.model}</span>
                          )}
                          <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
                            <span>{mainCalls.length} calls</span>
                            <span>{formatTokens(mainTotals.inputTokens)} in</span>
                            <span>{formatTokens(mainTotals.outputTokens)} out</span>
                            {mainTotals.cacheReadTokens > 0 && <span>{formatTokens(mainTotals.cacheReadTokens)} cache</span>}
                            <span>{formatDuration(mainTotals.durationMs)}</span>
                          </span>
                        </button>
                        {llmMainExpanded && mainCalls.length > 0 && (
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="bg-muted/20 text-muted-foreground">
                                <th className="px-2 py-1.5 text-left font-medium w-8">#</th>
                                <th className="px-2 py-1.5 text-right font-medium">In</th>
                                <th className="px-2 py-1.5 text-right font-medium">Out</th>
                                <th className="px-2 py-1.5 text-right font-medium">Cache</th>
                                <th className="px-2 py-1.5 text-right font-medium">Duration</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-border/50">
                              {mainCalls.map((lc, i) => (
                                <tr key={i} className="hover:bg-accent/30">
                                  <td className="px-2 py-1.5 text-muted-foreground tabular-nums">{i + 1}</td>
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
                        )}
                        {llmMainExpanded && mainCalls.length === 0 && (
                          <p className="px-3 py-2 text-xs text-muted-foreground">No main-agent calls recorded yet.</p>
                        )}
                      </div>

                      {/* ── Sub-agents tier (only shown if any) ── */}
                      {subCalls.length > 0 && (
                        <div className="rounded-md border border-border overflow-hidden">
                          <button
                            className="flex w-full items-center gap-2 px-3 py-2 bg-muted/30 hover:bg-muted/50 transition-colors text-left"
                            onClick={() => setLlmSubExpanded((c) => !c)}
                          >
                            {llmSubExpanded ? <ChevronDown size={11} className="text-muted-foreground shrink-0" /> : <ChevronRight size={11} className="text-muted-foreground shrink-0" />}
                            <span className="text-xs font-medium text-foreground">Sub-agents</span>
                            <span className="text-xs text-muted-foreground">
                              {subAgentGroups.length} model{subAgentGroups.length !== 1 ? "s" : ""}
                            </span>
                            <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
                              <span>{subCalls.length} calls</span>
                              <span>{formatTokens(subCalls.reduce((s, c) => s + c.inputTokens, 0))} in</span>
                              <span>{formatTokens(subCalls.reduce((s, c) => s + c.outputTokens, 0))} out</span>
                              <span>{formatDuration(subCalls.reduce((s, c) => s + c.durationMs, 0))}</span>
                            </span>
                          </button>
                          {llmSubExpanded && (
                            <div className="divide-y divide-border/50">
                              {subAgentGroups.map((grp) => (
                                <SubAgentGroup key={grp.model} group={grp} />
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                    </div>
                  )}
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
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
      </div>
      <p className="text-lg font-bold tabular-nums">{value}</p>
    </div>
  );
}

function SubAgentGroup({ group }: {
  group: { model: string; count: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; durationMs: number; calls: LLMCall[] };
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div>
      <button
        className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent/30 transition-colors text-left"
        onClick={() => setExpanded((c) => !c)}
      >
        {expanded ? <ChevronDown size={10} className="text-muted-foreground shrink-0" /> : <ChevronRight size={10} className="text-muted-foreground shrink-0" />}
        <span className="font-mono text-xs text-foreground">{group.model || "unknown"}</span>
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
          <span>{group.count} calls</span>
          <span>{formatTokens(group.inputTokens)} in</span>
          <span>{formatTokens(group.outputTokens)} out</span>
          {group.cacheReadTokens > 0 && <span>{formatTokens(group.cacheReadTokens)} cache</span>}
          <span>{formatDuration(group.durationMs)}</span>
        </span>
      </button>
      {expanded && (
        <table className="w-full text-xs bg-background/50">
          <thead>
            <tr className="bg-muted/20 text-muted-foreground">
              <th className="px-2 py-1 text-left font-medium w-8">#</th>
              <th className="px-2 py-1 text-right font-medium">In</th>
              <th className="px-2 py-1 text-right font-medium">Out</th>
              <th className="px-2 py-1 text-right font-medium">Cache</th>
              <th className="px-2 py-1 text-right font-medium">Duration</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {group.calls.map((lc, i) => (
              <tr key={i} className="hover:bg-accent/30">
                <td className="px-2 py-1 text-muted-foreground tabular-nums">{i + 1}</td>
                <td className="px-2 py-1 text-right tabular-nums">{formatTokens(lc.inputTokens)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{formatTokens(lc.outputTokens)}</td>
                <td className="px-2 py-1 text-right tabular-nums text-muted-foreground">
                  {lc.cacheReadTokens > 0 ? formatTokens(lc.cacheReadTokens) : "—"}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">{formatDuration(lc.durationMs)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
