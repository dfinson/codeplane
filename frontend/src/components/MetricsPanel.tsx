import { useState, useEffect, useMemo } from "react";
import {
  Cpu, Clock, Wrench, MessageSquare, Brain, BarChart3,
  AlertTriangle, ArrowDownUp, ChevronDown, ChevronRight,
  BookOpen, CheckCircle, XCircle, Zap, TrendingUp,
} from "lucide-react";
import { fetchJobTelemetry, fetchArtifacts, fetchArtifactContent, fetchJobContext, type JobContextResponse } from "../api/client";
import { Badge } from "./ui/badge";
import { Progress } from "./ui/progress";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { useStore } from "../store";
import { Tooltip } from "./ui/tooltip";
import type {
  TelemetryData, LLMCall, SortField, SortDir, ToolAggregate,
  SessionCheckpoint, SessionSummaryJson,
} from "./MetricsPanelTypes";
import { formatDuration, formatTokens, formatUsd, formatActivityBucket, ACTIVITY_DESCRIPTIONS } from "./MetricsPanelTypes";
import {
  useModelPricing,
  CacheEfficiencyBar,
  CostSection,
  SDK_COST_CONFIG,
  DEFAULT_COST_CONFIG,
  SortHeader,
  FileAccessSection,
  SidecarSessionsSection,
  SidecarSessionJobMetrics,
  StatCard,
  CompactStat,
  SubAgentGroup,
} from "./MetricsPanelSections";
import { LatencyBreakdown } from "./analytics/LatencyBreakdown";
import { CostEvidence } from "./CostEvidence";
import { sumKnown } from "../lib/currency";

// ---------------------------------------------------------------------------
// Section group — wraps children in a visually distinct card with a header
// ---------------------------------------------------------------------------

function SectionGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-6 first:mt-0">
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest">
          {title}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>
      <div className="rounded-lg border border-border bg-card/50 p-4 space-y-4">
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component — single flat view, no tabs
// ---------------------------------------------------------------------------

export function MetricsPanel({ jobId, isRunning = false }: { jobId: string; isRunning?: boolean }) {
  const [toolsCollapsed, setToolsCollapsed] = useState(true);
  const [llmCollapsed, setLlmCollapsed] = useState(true);
  const [llmMainExpanded, setLlmMainExpanded] = useState(false);
  const [llmSubExpanded, setLlmSubExpanded] = useState(false);
  const [turnsCollapsed, setTurnsCollapsed] = useState(true);
  const [expandedActivities, setExpandedActivities] = useState<Set<string>>(new Set());
  const [data, setData] = useState<TelemetryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [toolSort, setToolSort] = useState<{ field: SortField; dir: SortDir }>({ field: "totalMs", dir: "desc" });
  const [checkpoints, setCheckpoints] = useState<SessionCheckpoint[]>([]);

  // Subscribe to the per-job telemetry version counter — bumped whenever a
  // telemetry_updated SSE event arrives (debounced every ~2 s by the backend).
  const telemetryVersion = useStore((s) => s.telemetryVersions[jobId] ?? 0);

  // Fetch telemetry on mount, when isRunning changes, or when a
  // telemetry_updated SSE event bumps telemetryVersion. No polling needed.
  useEffect(() => {
    let cancelled = false;
    fetchJobTelemetry(jobId)
      .then((d) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch(() => { if (!cancelled) { setData((prev) => prev ?? { available: false }); setLoading(false); } });
    return () => { cancelled = true; };
  }, [jobId, isRunning, telemetryVersion]);

  // Job context — comparison against repo average + noteworthy flags
  const [jobContext, setJobContext] = useState<JobContextResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchJobContext(jobId)
      .then((ctx) => { if (!cancelled) setJobContext(ctx); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [jobId, telemetryVersion]);

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

  const fails = (data?.toolCalls ?? []).filter((t) => t.success === false).length;
  const unknown = (data?.toolCalls ?? []).filter((t) => t.success === null).length;

  const toolAggs = useMemo(() => {
    const map = new Map<string, ToolAggregate>();
    for (const tc of data?.toolCalls ?? []) {
      const agg = map.get(tc.name) ?? { name: tc.name, count: 0, totalMs: 0, avgMs: 0, fails: 0, unknown: 0 };
      agg.count++;
      agg.totalMs += tc.durationMs;
      if (tc.success === false) agg.fails++;
      if (tc.success === null) agg.unknown++;
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
    const map = new Map<string, { model: string; count: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; cost: number | null; durationMs: number; calls: LLMCall[] }>();
    for (const c of subCalls) {
      const key = c.model || "unknown";
      const g = map.get(key) ?? { model: key, count: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cost: null, durationMs: 0, calls: [] };
      g.count += c.callCount ?? 1;
      g.inputTokens += c.inputTokens;
      g.outputTokens += c.outputTokens;
      g.cacheReadTokens += c.cacheReadTokens;
      g.cost = sumKnown([g.cost, c.cost]);
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
    cost: sumKnown(mainCalls.map(c => c.cost)),
    durationMs: mainCalls.reduce((s, c) => s + c.durationMs, 0),
  }), [mainCalls]);

  // Dynamic model pricing from backend
  const modelPricing = useModelPricing(data?.model ?? data?.mainModel);
  const activityBuckets = data?.costDrivers?.activity ?? [];
  const editEfficiencyBuckets = data?.costDrivers?.editEfficiency ?? [];
  const turnEconomics = data?.turnEconomics;
  const turnCurve = turnEconomics?.turnCurve ?? [];
  const showCacheEfficiency = (data?.inputTokens ?? 0) > 0 || (data?.cacheReadTokens ?? 0) > 0 || (data?.cacheWriteTokens ?? 0) > 0;
  const showTurnEconomics = !isRunning && (turnEconomics?.totalTurns ?? 0) > 0;

  // Rework stats from edit_efficiency dimension
  const reworkStats = useMemo(() => {
    const totalRetries = editEfficiencyBuckets.reduce((s, b) => s + b.outputTokens, 0);
    const totalEditTurns = editEfficiencyBuckets.reduce((s, b) => s + b.callCount, 0);
    // Approximate rework cost: retries / total-edit-turns * total-activity-cost
    const totalCost = sumKnown(activityBuckets.map(b => b.costUsd));
    const reworkFraction = totalEditTurns > 0 ? totalRetries / totalEditTurns : 0;
    const reworkCost = totalCost == null ? null : totalCost * reworkFraction;
    return { retries: totalRetries, editTurns: totalEditTurns, cost: reworkCost, totalCost, fraction: reworkFraction };
  }, [editEfficiencyBuckets, activityBuckets]);

  // Build per-activity tool breakdown from actual tool call data
  const toolsByActivity = useMemo(() => {
    const map: Record<string, { name: string; count: number }[]> = {};
    for (const tc of data?.toolCalls ?? []) {
      const activity = tc.activity ?? "overhead";
      const label = tc.displayLabel ?? tc.name;
      if (!map[activity]) map[activity] = [];
      const existing = map[activity].find((t) => t.name === label);
      if (existing) existing.count++;
      else map[activity].push({ name: label, count: 1 });
    }
    for (const tools of Object.values(map)) {
      tools.sort((a, b) => b.count - a.count);
    }
    return map;
  }, [data?.toolCalls]);

  return (
    <div className="md:h-full overflow-y-auto">
      <div className="space-y-3 p-4">
          {loading ? (
            <div className="flex justify-center py-8"><Spinner size="sm" /></div>
          ) : !data?.available ? (
            <p className="text-sm text-muted-foreground text-center py-8">No data available yet</p>
          ) : (
            <>
              <SectionGroup title="Overview">
              <CostEvidence usage={data.costUsage} estimate={data.estimatedCostUsd} />
              {/* Stat cards — hero numbers first */}
              {(() => {
                const sdkConf = SDK_COST_CONFIG[data.sdk ?? ""] ?? DEFAULT_COST_CONFIG;
                return (
              <div className={cn("grid grid-cols-2 gap-3", (data.totalCost ?? 0) > 0 ? "md:grid-cols-3 xl:grid-cols-5" : "md:grid-cols-2 xl:grid-cols-4")}>
                <Tooltip content="Wall-clock time from job start to completion">
                  <div><StatCard icon={<Clock size={14} />} label="Duration" value={formatDuration(data.durationMs ?? 0)} color="text-blue-400" /></div>
                </Tooltip>
                {(
                  <Tooltip content={sdkConf.costTooltip}>
                    <div>
                      <StatCard icon={<Zap size={14} />} label="Verified USD" value={formatUsd(data.totalCost)} color="text-green-400" />
                    </div>
                  </Tooltip>
                )}
                <Tooltip content="Total input and output tokens consumed by the LLM">
                  <div><StatCard icon={<Cpu size={14} />} label="Tokens" value={formatTokens(data.totalTokens ?? 0)} color="text-violet-400" /></div>
                </Tooltip>
                <Tooltip content={sdkConf.llmStatLabel === "Turns" ? "Conversational turns between the agent and LLM" : "Individual requests made to the large language model"}>
                  <div><StatCard icon={<Brain size={14} />} label={sdkConf.llmStatLabel} value={sdkConf.llmStatValue(data)} color="text-blue-400" /></div>
                </Tooltip>
                <Tooltip content="Shell commands, file edits, and other tool invocations by the agent">
                  <div><StatCard icon={<Wrench size={14} />} label="Tools" value={`${data.toolCallCount ?? 0}${fails ? ` (${fails} fail)` : ""}${unknown ? ` (${unknown} unknown)` : ""}`} color="text-yellow-400" /></div>
                </Tooltip>
              </div>
                );
              })()}

              {/* Session Info */}
              <div className="flex flex-wrap items-center gap-3 text-xs">
                {(data.mainModel || data.model) && (
                  <Tooltip content="The primary LLM model used by the agent for this job">
                    <Badge variant="secondary" className="cursor-help">
                      {data.mainModel || data.model}
                    </Badge>
                  </Tooltip>
                )}
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <MessageSquare size={12} />
                  {data.agentMessages ?? 0} agent{(data.operatorMessages ?? 0) > 0 ? ` / ${data.operatorMessages} operator` : ""}
                </span>
                {(data.approvalCount ?? 0) > 0 && (
                  <Tooltip content="Times the agent paused to ask for human approval before proceeding (e.g. destructive commands, sensitive operations). Wait time = how long the agent was blocked.">
                  <span className="flex items-center gap-1.5 text-muted-foreground cursor-help">
                    <AlertTriangle size={12} />
                    {data.approvalCount} approval{data.approvalCount !== 1 ? "s" : ""} ({formatDuration(data.totalApprovalWaitMs ?? 0)} wait)
                  </span>
                  </Tooltip>
                )}
              </div>

              {/* Job Context — how this job compares to repo average (only meaningful with enough data) */}
              {jobContext && jobContext.repoAvg && jobContext.repoAvg.jobCount >= 10 && (
                <div className="rounded-md bg-muted/30 p-3 space-y-2">
                  <h4 className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                    <TrendingUp size={12} /> vs. Repo Average
                  </h4>
                  <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div>
                      <p className="text-sm font-bold tabular-nums">{formatUsd(jobContext.job.cost)}</p>
                      <p className="text-muted-foreground">This Job</p>
                      {jobContext.repoAvg && (
                        <p className="text-[10px] text-muted-foreground/70">avg {formatUsd(jobContext.repoAvg.avgCost)}</p>
                      )}
                    </div>
                    <div>
                      <p className="text-sm font-bold tabular-nums">{jobContext.job.durationMs ? formatDuration(jobContext.job.durationMs) : "—"}</p>
                      <p className="text-muted-foreground">Duration</p>
                      {jobContext.repoAvg && jobContext.repoAvg.avgDurationMs > 0 && (
                        <p className="text-[10px] text-muted-foreground/70">avg {formatDuration(jobContext.repoAvg.avgDurationMs)}</p>
                      )}
                    </div>
                    {(jobContext.job.diffLinesAdded + jobContext.job.diffLinesRemoved > 0 || (jobContext.repoAvg && jobContext.repoAvg.avgDiffLines > 0)) && (
                    <div>
                      <p className="text-sm font-bold tabular-nums">{jobContext.job.diffLinesAdded + jobContext.job.diffLinesRemoved}</p>
                      <p className="text-muted-foreground">Diff Lines</p>
                      {jobContext.repoAvg && (
                        <p className="text-[10px] text-muted-foreground/70">avg {Math.round(jobContext.repoAvg.avgDiffLines)}</p>
                      )}
                    </div>
                    )}
                  </div>
                </div>
              )}

              {/* Job flags — per-job observations (useful regardless of N) */}
              {jobContext && jobContext.flags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {jobContext.flags.map((f, i) => {
                    const isWarning = f.type === "turn_escalation" || f.type === "high_rereads" || f.type === "tool_failures";
                    const label = f.type === "high_rereads" ? "High Re-reads"
                      : f.type === "turn_escalation" ? "Cost Escalation"
                      : f.type === "tool_failures" ? "Tool Failures"
                      : f.type;
                    return (
                      <Tooltip key={i} content={f.message}>
                        <span>
                          <Badge
                            variant="outline"
                            className={`text-[10px] cursor-help ${
                              isWarning ? "border-yellow-500/40 text-yellow-400" :
                              "border-blue-500/40 text-blue-400"
                            }`}
                          >
                            {label}
                          </Badge>
                        </span>
                      </Tooltip>
                    );
                  })}
                </div>
              )}
              </SectionGroup>

              {/* ─── Tokens & Context ─── */}
              <SectionGroup title="Tokens & Context">
                <div className="grid grid-cols-3 gap-2 text-center text-xs">
                  <div>
                    <p className="text-sm font-bold tabular-nums">{formatTokens(data.inputTokens ?? 0)}</p>
                    <p className="text-muted-foreground">Input</p>
                  </div>
                  <Tooltip content="Tokens served from prompt cache — reused context the model didn't re-read, billed at a reduced rate">
                    <div className="cursor-help">
                      <p className="text-sm font-bold tabular-nums">{formatTokens(data.cacheReadTokens ?? 0)}</p>
                      <p className="text-muted-foreground">Cache</p>
                    </div>
                  </Tooltip>
                  <div>
                    <p className="text-sm font-bold tabular-nums">{formatTokens(data.outputTokens ?? 0)}</p>
                    <p className="text-muted-foreground">Output</p>
                  </div>
                </div>

                {data.contextWindowSize ? (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-muted-foreground">Context Window</span>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {formatTokens(data.currentContextTokens ?? 0)} / {formatTokens(data.contextWindowSize)}
                      </span>
                    </div>
                    <Progress
                      value={Math.min(100, (data.contextUtilization ?? 0) * 100)}
                      color={(data.contextUtilization ?? 0) > 0.8 ? "red" : "blue"}
                    />
                    {(data.compactions ?? 0) > 0 && (
                      <Tooltip content="Compaction = the agent's conversation exceeded the model's context window so older messages were summarized and dropped. Tokens removed are no longer visible to the agent, which can cause repeated work.">
                      <p className="text-xs text-yellow-400 mt-1.5 flex items-center gap-1 cursor-help">
                        <ArrowDownUp size={10} />
                        {data.compactions} compaction{data.compactions !== 1 ? "s" : ""} ({formatTokens(data.tokensCompacted ?? 0)} removed)
                      </p>
                      </Tooltip>
                    )}
                  </div>
                ) : null}
              </SectionGroup>

              {/* ─── Cost & Efficiency ─── */}
              <SectionGroup title="Cost & Efficiency">
              <CostSection data={data} />

              {/* Integrated economics / efficiency — peer-level expandables */}
              {showCacheEfficiency && (
                    <div className="space-y-3 rounded-md border border-border bg-background p-3">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                        <Cpu size={12} className="text-violet-400" /> Cache Efficiency
                      </div>
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                        <CompactStat label="Cache Read" value={formatTokens(data.cacheReadTokens ?? 0)} />
                        <CompactStat label="Cache Write" value={formatTokens(data.cacheWriteTokens ?? 0)} />
                        <CompactStat label="Input Reuse" value={`${((data.inputTokens ?? 0) + (data.cacheReadTokens ?? 0)) > 0 ? (((data.cacheReadTokens ?? 0) / ((data.inputTokens ?? 0) + (data.cacheReadTokens ?? 0))) * 100).toFixed(0) : 0}%`} />
                      </div>
                      <CacheEfficiencyBar
                        inputTokens={data.inputTokens ?? 0}
                        cacheReadTokens={data.cacheReadTokens ?? 0}
                        outputTokens={data.outputTokens ?? 0}
                        pricing={modelPricing}
                        actualCost={data.totalCost}
                      />
                    </div>
              )}

              {activityBuckets.length > 0 && (
                    <div className="rounded-md border border-border bg-background p-3 space-y-2">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                        <BarChart3 size={12} className="text-yellow-400" /> Cost Breakdown
                      </div>

                      {/* Rework banner */}
                      {reworkStats.retries > 0 && (
                        <Tooltip content="Rework = turns where the agent re-edited the same file to fix errors from a previous edit. High rework means the agent is looping on mistakes, burning tokens without forward progress.">
                        <div className="flex items-center gap-2 rounded-md bg-amber-500/10 border border-amber-500/30 px-2.5 py-2 text-xs cursor-help">
                          <AlertTriangle size={12} className="text-amber-400 shrink-0" />
                          <span>
                            <span className="font-semibold text-amber-400">Rework: {formatUsd(reworkStats.cost)}</span>
                            <span className="text-muted-foreground"> ({(reworkStats.fraction * 100).toFixed(0)}%) — {reworkStats.retries} retry loop{reworkStats.retries !== 1 ? "s" : ""} across {reworkStats.editTurns} edit turn{reworkStats.editTurns !== 1 ? "s" : ""}</span>
                          </span>
                        </div>
                        </Tooltip>
                      )}

                      {/* Activity breakdown rows */}
                      {activityBuckets
                        .slice()
                        .sort((a, b) => (b.costUsd ?? -1) - (a.costUsd ?? -1))
                        .map((bucket) => {
                          const total = sumKnown(activityBuckets.map(entry => entry.costUsd));
                          const widthPct = total != null && total > 0 && bucket.costUsd != null ? (bucket.costUsd / total) * 100 : 0;
                          const pctLabel = total != null && bucket.costUsd != null ? `${(widthPct).toFixed(0)}%` : "Unknown";
                          const costPerTurn = bucket.callCount > 0 && bucket.costUsd != null ? bucket.costUsd / bucket.callCount : null;
                          const isExpanded = expandedActivities.has(bucket.bucket);
                          const toggleExpand = () => {
                            setExpandedActivities((prev) => {
                              const next = new Set(prev);
                              if (next.has(bucket.bucket)) next.delete(bucket.bucket);
                              else next.add(bucket.bucket);
                              return next;
                            });
                          };
                          return (
                            <div key={bucket.bucket} className="space-y-1">
                              <div
                                className={cn(
                                  "flex items-center justify-between gap-2 text-xs",
                                  "cursor-pointer hover:bg-accent/30 rounded -mx-1 px-1",
                                )}
                                onClick={toggleExpand}
                              >
                                <div className="min-w-0 flex items-center gap-1">
                                  {isExpanded
                                    ? <ChevronDown size={10} className="shrink-0 text-muted-foreground" />
                                    : <ChevronRight size={10} className="shrink-0 text-muted-foreground" />
                                  }
                                  <div>
                                    <Tooltip content={ACTIVITY_DESCRIPTIONS[bucket.bucket] ?? bucket.bucket}>
                                      <div className="truncate text-foreground cursor-help border-b border-dotted border-muted-foreground/30">{formatActivityBucket(bucket.bucket)}</div>
                                    </Tooltip>
                                    <div className="text-muted-foreground">{bucket.callCount} turn{bucket.callCount !== 1 ? "s" : ""} · {pctLabel} of total</div>
                                  </div>
                                </div>
                                <div className="text-right tabular-nums shrink-0">
                                  <div>{formatUsd(bucket.costUsd)}</div>
                                  <div className="text-muted-foreground">{formatTokens(bucket.inputTokens + bucket.outputTokens)}</div>
                                </div>
                              </div>
                              {/* Cost proportion bar */}
                              <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                                <div className="h-full rounded-full bg-amber-500" style={{ width: `${Math.max(widthPct, 4)}%` }} />
                              </div>
                              {/* Expanded detail */}
                              {isExpanded && (
                                <div className="ml-4 space-y-2 pb-1 border-l border-border/50 pl-3">
                                  {/* Description */}
                                  {ACTIVITY_DESCRIPTIONS[bucket.bucket] && (
                                    <div className="text-[10px] text-muted-foreground/80 italic">
                                      {ACTIVITY_DESCRIPTIONS[bucket.bucket]}
                                    </div>
                                  )}
                                  {/* Per-activity token & cost summary */}
                                  <div className="grid grid-cols-3 gap-1 text-[10px] text-muted-foreground">
                                    <div>
                                      <div className="text-muted-foreground/60">Input</div>
                                      <div className="tabular-nums">{formatTokens(bucket.inputTokens)}</div>
                                    </div>
                                    <div>
                                      <div className="text-muted-foreground/60">Output</div>
                                      <div className="tabular-nums">{formatTokens(bucket.outputTokens)}</div>
                                    </div>
                                    <div>
                                      <div className="text-muted-foreground/60">Cost/turn</div>
                                      <div className="tabular-nums">{formatUsd(costPerTurn)}</div>
                                    </div>
                                  </div>
                                  {/* Tool breakdown for this activity */}
                                  {(() => {
                                    const tools = toolsByActivity[bucket.bucket];
                                    if (tools && tools.length > 0) {
                                      return (
                                        <div className="space-y-1">
                                          <div className="text-[10px] text-muted-foreground/60 font-medium uppercase tracking-wider">Tools</div>
                                          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                                            {tools.map((t) => (
                                              <span key={t.name} className="text-[10px] text-muted-foreground">
                                                {t.name} <span className="text-muted-foreground/50">×{t.count}</span>
                                              </span>
                                            ))}
                                          </div>
                                        </div>
                                      );
                                    }
                                    return null;
                                  })()}
                                </div>
                              )}
                            </div>
                          );
                        })}
                    </div>
                  )}

                  {showTurnEconomics && turnEconomics && (
                    <div className="space-y-3 rounded-md border border-border bg-background p-3">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                        <TrendingUp size={12} className="text-blue-400" /> Turn Economics
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        <CompactStat label="Total Turns" value={String(turnEconomics.totalTurns)} />
                        <CompactStat label="Avg Cost/Turn" value={formatUsd(turnEconomics.avgTurnCostUsd)} />
                        <CompactStat label="Peak Turn" value={formatUsd(turnEconomics.peakTurnCostUsd)} />
                      </div>
                      {turnCurve.length > 1 && (
                        <div className="space-y-1">
                          <button
                            type="button"
                            onClick={() => setTurnsCollapsed((current) => !current)}
                            className="flex w-full items-center justify-between gap-2 rounded-md border border-border/80 px-2.5 py-2 text-left text-xs text-muted-foreground hover:bg-accent/30 transition-colors"
                          >
                            <span>Turn list</span>
                            <span>{turnsCollapsed ? `Show ${turnCurve.length} turns` : "Hide turns"}</span>
                          </button>
                          {!turnsCollapsed && turnCurve.map((bucket) => {
                            const maxCost = Math.max(...turnCurve.map((entry) => entry.costUsd ?? 0), 0);
                            const widthPct = maxCost > 0 && bucket.costUsd != null ? (bucket.costUsd / maxCost) * 100 : 0;
                            const actions = bucket.actions ?? [];
                            return (
                              <div key={bucket.bucket} className="space-y-0.5 py-1.5 border-b border-border/30 last:border-0">
                                <div className="flex items-center justify-between text-xs">
                                  <div className="flex items-center gap-2">
                                    <span className="text-muted-foreground font-mono w-5 text-right">{bucket.bucket}</span>
                                  </div>
                                  <span className="tabular-nums">{formatUsd(bucket.costUsd)}</span>
                                </div>
                                <div className="h-1 rounded-full bg-muted overflow-hidden ml-7">
                                  <div className="h-full rounded-full bg-blue-500" style={{ width: `${Math.max(widthPct, 3)}%` }} />
                                </div>
                                {(bucket.intent || actions.length > 0) && (
                                  <div className="ml-7 text-[11px] text-muted-foreground/80 space-y-0.5">
                                    {bucket.intent && <div className="italic">{bucket.intent}</div>}
                                    {actions.map((a, i) => (
                                      <div key={i} className="flex items-center gap-1.5 text-muted-foreground/60">
                                        <span className="text-[9px] px-1 py-px rounded bg-muted text-muted-foreground/50 shrink-0">{formatActivityBucket(a.activity)}</span>
                                        <span>• {a.text}</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}

              </SectionGroup>

              {/* ─── Latency Breakdown ─── */}
              {((data.latencyDrivers?.activity?.length ?? 0) > 0 || (data.latencyDrivers?.category?.length ?? 0) > 0) && (
                <SectionGroup title="Latency">
                  <LatencyBreakdown
                    activityBuckets={data.latencyDrivers!.activity ?? []}
                    categoryBuckets={data.latencyDrivers!.category ?? []}
                    totalDurationMs={data.durationMs ?? 0}
                    idleMs={data.idleMs ?? 0}
                    parallelismRatio={data.parallelismRatio ?? 0}
                  />
                </SectionGroup>
              )}

              {/* ─── Summary ─── */}
              {checkpoints.length > 0 && (
              <SectionGroup title="Summary">
                <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-3">
                  <BookOpen size={12} className="text-blue-400" /> Session Timeline
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
              </SectionGroup>
              )}

              {/* ─── Breakdowns ─── */}
              <SectionGroup title="Breakdowns">

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
                              <td className={cn("px-2 py-1.5 text-right tabular-nums", agg.fails > 0 && "text-red-400")}>
                                {agg.unknown < agg.count && agg.fails}
                                {agg.unknown > 0 && (
                                  <span className="text-muted-foreground">
                                    {agg.unknown === agg.count ? `Unknown (${agg.unknown})` : ` (${agg.unknown} unknown)`}
                                  </span>
                                )}
                              </td>
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
                    <Tooltip content="Individual requests made to the large language model">
                      <span className="cursor-help">LLM Calls</span>
                    </Tooltip>
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
                            <span>{mainCalls.reduce((s, c) => s + (c.callCount ?? 1), 0)} calls</span>
                            <span>{formatTokens(mainTotals.inputTokens)} in</span>
                            <span>{formatTokens(mainTotals.outputTokens)} out</span>
                            {mainTotals.cacheReadTokens > 0 && <span>{formatTokens(mainTotals.cacheReadTokens)} cache</span>}
                            <span className="text-green-400">{formatUsd(mainTotals.cost)}</span>
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
                                {(SDK_COST_CONFIG[data.sdk ?? ""]?.showTurnsColumn) && <th className="px-2 py-1.5 text-right font-medium">Turns</th>}
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
                                  {(SDK_COST_CONFIG[data.sdk ?? ""]?.showTurnsColumn) && <td className="px-2 py-1.5 text-right tabular-nums">{lc.callCount ?? 1}</td>}
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
                              <span className="text-green-400">{formatUsd(sumKnown(subAgentGroups.map(g => g.cost)))}</span>
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

              {/* WS3: File access visualization */}
              {data.fileAccess && data.fileAccess.stats.totalAccesses > 0 && (
                <FileAccessSection fileAccess={data.fileAccess} />
              )}

              {/* Sidecar session cost breakdown (preflight, memory, etc.) */}
              {data.sidecarSessions && data.sidecarSessions.length > 0 ? (
                <SidecarSessionsSection sessions={data.sidecarSessions} />
              ) : (
                <SidecarSessionJobMetrics jobId={jobId} />
              )}
              </SectionGroup>
            </>
          )}
        </div>
    </div>
  );
}
