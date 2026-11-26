import { useState, useEffect, useMemo } from "react";
import {
  ChevronDown, ChevronRight, Brain, BarChart3,
  BookOpen, Zap,
} from "lucide-react";
import { fetchModelPricing, fetchSidecarSessionMetrics, type ModelPricing, type SidecarSessionMetrics } from "../api/client";
import { Progress } from "./ui/progress";
import { Tooltip } from "./ui/tooltip";
import { cn } from "../lib/utils";
import { useStore } from "../store";
import { sumKnown } from "../lib/currency";
import type {
  TelemetryData, FileAccessData, LLMCall, SortField, SortDir, SidecarSession,
} from "./MetricsPanelTypes";
import {
  formatDuration, formatTokens, formatUsd, estimateCostWithoutCache,
} from "./MetricsPanelTypes";

// ---------------------------------------------------------------------------
// useModelPricing hook — shared pricing cache
// ---------------------------------------------------------------------------

const _pricingCache = new Map<string, ModelPricing | null>();
const _pricingInflight = new Map<string, Promise<ModelPricing | null>>();

export function useModelPricing(model: string | undefined): ModelPricing | null {
  const [pricing, setPricing] = useState<ModelPricing | null>(null);

  useEffect(() => {
    if (!model) return;

    if (_pricingCache.has(model)) {
      setPricing(_pricingCache.get(model) ?? null);
      return;
    }

    let promise = _pricingInflight.get(model);
    if (!promise) {
      promise = fetchModelPricing([model])
        .then((res) => {
          const entry = res[model] ?? null;
          _pricingCache.set(model, entry);
          return entry;
        })
        .catch(() => {
          _pricingCache.set(model, null);
          return null;
        })
        .finally(() => _pricingInflight.delete(model));
      _pricingInflight.set(model, promise);
    }

    let cancelled = false;
    promise.then((entry) => { if (!cancelled) setPricing(entry); });
    return () => { cancelled = true; };
  }, [model]);

  return pricing;
}

// ---------------------------------------------------------------------------
// CacheEfficiencyBar
// ---------------------------------------------------------------------------

export function CacheEfficiencyBar({ inputTokens, cacheReadTokens, pricing, outputTokens, actualCost }: {
  inputTokens: number;
  cacheReadTokens: number;
  pricing?: ModelPricing | null;
  outputTokens?: number;
  actualCost?: number | null;
}) {
  const totalInput = inputTokens + cacheReadTokens;
  const rate = totalInput > 0 ? (cacheReadTokens / totalInput) * 100 : 0;
  const color = rate >= 60 ? "text-green-400" : rate >= 30 ? "text-yellow-400" : "text-red-400";
  const barColor = rate >= 60 ? "bg-green-500" : rate >= 30 ? "bg-yellow-500" : "bg-red-500";

  let savingsText: string | undefined;
  if (pricing && cacheReadTokens > 0) {
    const fullCost = estimateCostWithoutCache(pricing, inputTokens, outputTokens ?? 0, cacheReadTokens);
    if (actualCost != null && (actualCost ?? -1) > 0) {
      const saved = fullCost - actualCost;
      if (saved > 0) {
        const pct = (saved / fullCost) * 100;
        savingsText = `Caching saved est. ${formatUsd(saved)} (${pct.toFixed(0)}% off). Without cache this session would cost ~${formatUsd(fullCost)}.`;
      }
    } else {
      const cacheDiscount = pricing.input > 0
        ? ((1 - pricing.cache_read / pricing.input) * 100).toFixed(0)
        : null;
      savingsText = cacheDiscount
        ? `Cached tokens are billed at ${cacheDiscount}% less than regular input for this model ($${pricing.cache_read}/MTok vs $${pricing.input}/MTok).`
        : undefined;
    }
  }

  const tooltipContent = savingsText
    ?? "Higher cache efficiency = lower cost. Cached tokens are reused from previous turns at a reduced rate.";

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between text-xs mb-1">
        <Tooltip content={tooltipContent}>
          <span className="text-muted-foreground cursor-help">Cache Efficiency</span>
        </Tooltip>
        <span className={cn("font-semibold tabular-nums", color)}>{rate.toFixed(0)}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all duration-300", barColor)}
          style={{ width: `${Math.min(100, rate)}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SDK-specific cost rendering config
// ---------------------------------------------------------------------------

interface SdkCostConfig {
  label: string;
  icon: React.ReactNode;
  CostView: React.ComponentType<{ data: TelemetryData }>;
  llmStatLabel: string;
  llmStatValue: (data: TelemetryData) => string;
  costTooltip: string;
  showTurnsColumn: boolean;
}

export const SDK_COST_CONFIG: Record<string, SdkCostConfig> = {
  copilot: {
    label: "Premium Requests",
    icon: <Zap size={12} className="text-yellow-400" />,
    CostView: CopilotCostView,
    llmStatLabel: "LLM Calls",
    llmStatValue: (data) => String(data.llmCallCount ?? 0),
    costTooltip: "Verified USD only. Copilot native cost units are reported separately and are not converted.",
    showTurnsColumn: false,
  },
  claude: {
    label: "Cost",
    icon: <BarChart3 size={12} className="text-green-400" />,
    CostView: ClaudeCostView,
    llmStatLabel: "Turns",
    llmStatValue: (data) => String(data.agentMessages ?? 0),
    costTooltip: "Source-verified USD, not an inferred subscription charge. Estimates are reported separately.",
    showTurnsColumn: true,
  },
};

export const DEFAULT_COST_CONFIG: Omit<SdkCostConfig, "CostView"> = {
  label: "Cost",
  icon: <BarChart3 size={12} className="text-green-400" />,
  llmStatLabel: "LLM Calls",
  llmStatValue: (data) => String(data.llmCallCount ?? 0),
  costTooltip: "Source-verified USD for this job; unknown and estimated costs are excluded.",
  showTurnsColumn: false,
};

// ---------------------------------------------------------------------------
// CostSection
// ---------------------------------------------------------------------------

export function CostSection({ data }: { data: TelemetryData }) {
  const sdk = data.sdk ?? "";
  const config = SDK_COST_CONFIG[sdk];

  if (!config) {
    return null;
  }

  const { CostView } = config;

  return (
    <div>
      <h4 className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-3">
        {config.icon}
        {config.label}
      </h4>
      <CostView data={data} />
    </div>
  );
}

function CopilotCostView({ data }: { data: TelemetryData }) {
  const snapshots = data.quotaSnapshots ?? {};
  const snapshotEntries = Object.entries(snapshots);

  // Separate metered (non-unlimited) from unlimited snapshots
  const meteredEntries = snapshotEntries.filter(([, snap]) => !snap.isUnlimited);
  const allUnlimited = snapshotEntries.length > 0 && meteredEntries.length === 0;

  return (
    <div className="space-y-3">
      {(data.premiumRequests ?? 0) > 0 && (
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-muted-foreground">This session</span>
          <span className="font-semibold tabular-nums text-yellow-400">
            {data.premiumRequests} premium request{data.premiumRequests !== 1 ? "s" : ""}
          </span>
        </div>
      )}

      {/* When all quotas are unlimited, show a single compact line */}
      {allUnlimited && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Quota</span>
          <span className="text-green-400">Unlimited</span>
        </div>
      )}

      {/* Only render individual rows for metered (non-unlimited) quotas */}
      {meteredEntries.map(([key, snap]) => {
        const label = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
        const pct = snap.remainingPercentage;
        const usedPct = Math.min(100, 100 - pct);
        const exhausted = pct <= 0;
        const nearLimit = pct < 20 && pct > 0;

        return (
          <div key={key} className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{label}</span>
              <span className={cn("tabular-nums text-xs", exhausted ? "text-red-400" : nearLimit ? "text-yellow-400" : "text-muted-foreground")}>
                {snap.usedRequests.toFixed(1)} / {snap.entitlementRequests.toFixed(0)} used
                {snap.overage > 0 && ` (+${snap.overage.toFixed(1)} overage)`}
              </span>
            </div>
            <Progress
              value={usedPct}
              color={exhausted || nearLimit ? "red" : "blue"}
            />
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
    </div>
  );
}

function ClaudeCostView({ data }: { data: TelemetryData }) {
  const totalCost = data.totalCost;
  const pricing = useModelPricing(data.model);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">Total API cost</span>
        <span className={cn("font-semibold tabular-nums", (totalCost ?? -1) > 5 ? "text-red-400" : (totalCost ?? -1) > 1 ? "text-yellow-400" : "text-green-400")}>
          {formatUsd(totalCost)}
        </span>
      </div>

      {pricing && (
        <p className="text-xs text-muted-foreground">
          ${pricing.input}/MTok input · ${pricing.output}/MTok output
          {pricing.cache_read > 0 && ` · $${pricing.cache_read}/MTok cache read`}
        </p>
      )}

      {totalCost == null && (
        <p className="text-xs text-muted-foreground italic">Cost data available after session completes.</p>
      )}

      <p className="text-xs text-muted-foreground leading-snug">
        Claude Max and enterprise (Bedrock/Vertex/Foundry) plans do not expose quota via the SDK.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SortHeader
// ---------------------------------------------------------------------------

export function SortHeader({
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
// FileAccessSection
// ---------------------------------------------------------------------------

export function FileAccessSection({ fileAccess }: { fileAccess: FileAccessData }) {
  const [expanded, setExpanded] = useState(false);
  const [sortField, setSortField] = useState<"accessCount" | "readCount" | "writeCount">("accessCount");
  const sorted = useMemo(
    () => [...fileAccess.topFiles].sort((a, b) => b[sortField] - a[sortField]),
    [fileAccess.topFiles, sortField],
  );
  const { stats } = fileAccess;
  return (
    <div className="rounded-md border border-border/50 overflow-hidden">
      <button
        className="flex items-center gap-2 w-full px-3 py-2 hover:bg-accent/30 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown size={12} className="text-muted-foreground shrink-0" /> : <ChevronRight size={12} className="text-muted-foreground shrink-0" />}
        <BookOpen size={12} className="text-muted-foreground shrink-0" />
        <span className="text-xs font-medium text-foreground flex-1 text-left">File Access</span>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {stats.uniqueFiles} files · {stats.totalReads}R / {stats.totalWrites}W
          {stats.rereadCount > 0 && <span className="text-yellow-400 ml-1">({stats.rereadCount} re-reads)</span>}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-border/50">
          <div className="grid grid-cols-4 gap-2 p-3 text-center text-xs">
            <CompactStat label="Total" value={String(stats.totalAccesses)} />
            <CompactStat label="Unique Files" value={String(stats.uniqueFiles)} />
            <CompactStat label="Reads" value={String(stats.totalReads)} />
            <CompactStat label="Re-reads" value={String(stats.rereadCount)} warn={stats.rereadCount > 20} />
          </div>
          {sorted.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/20 text-muted-foreground">
                  <th className="px-2 py-1 text-left font-medium">File</th>
                  <th className="px-2 py-1 text-right font-medium cursor-pointer hover:text-foreground" onClick={() => setSortField("accessCount")}>
                    <Tooltip content="Total reads + writes for this file"><span className="cursor-help">Total{sortField === "accessCount" ? " ↓" : ""}</span></Tooltip>
                  </th>
                  <th className="px-2 py-1 text-right font-medium cursor-pointer hover:text-foreground" onClick={() => setSortField("readCount")}>
                    <Tooltip content="How many times the agent read this file"><span className="cursor-help">Reads{sortField === "readCount" ? " ↓" : ""}</span></Tooltip>
                  </th>
                  <th className="px-2 py-1 text-right font-medium cursor-pointer hover:text-foreground" onClick={() => setSortField("writeCount")}>
                    <Tooltip content="How many times the agent wrote to this file"><span className="cursor-help">Writes{sortField === "writeCount" ? " ↓" : ""}</span></Tooltip>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {sorted.map((f, i) => (
                  <tr key={i} className="hover:bg-accent/30">
                    <td className="px-2 py-1 font-mono truncate max-w-[200px]" title={f.filePath}>
                      {f.filePath.split("/").pop()}
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums">{f.accessCount}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{f.readCount}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{f.writeCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SidecarSessionsSection — per-session-kind cost breakdown from telemetry
// ---------------------------------------------------------------------------

const _SESSION_KIND_LABELS: Record<string, string> = {
  preflight: "Preflight Curator",
  memory_extraction: "Memory Extraction",
  memory_compaction: "Memory Compaction",
  narrator: "Narrator",
  sidecar: "Sidecar Session",
};

export function SidecarSessionsSection({ sessions }: { sessions: SidecarSession[] }) {
  const [expanded, setExpanded] = useState(false);

  if (!sessions || sessions.length === 0) return null;

  const totalCost = sumKnown(sessions.map(r => r.totalCostUsd));
  const totalCalls = sessions.reduce((s, r) => s + r.llmCallCount, 0);
  const totalTokens = sessions.reduce((s, r) => s + r.inputTokens + r.outputTokens, 0);

  return (
    <div className="rounded-md border border-border overflow-hidden">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 bg-muted/30 hover:bg-muted/50 transition-colors text-left"
        onClick={() => setExpanded((c) => !c)}
      >
        {expanded ? <ChevronDown size={11} className="text-muted-foreground shrink-0" /> : <ChevronRight size={11} className="text-muted-foreground shrink-0" />}
        <Zap size={12} className="text-amber-400 shrink-0" />
        <span className="text-xs font-medium text-foreground">Sidecar Sessions</span>
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
          <span>{sessions.length} types</span>
          <span>{totalCalls} calls</span>
          {totalTokens > 0 && <span>{formatTokens(totalTokens)} tok</span>}
          <span className="text-green-400">{formatUsd(totalCost)}</span>
        </span>
      </button>
      {expanded && (
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/20 text-muted-foreground">
              <th className="px-3 py-1.5 text-left font-medium">Session</th>
              <th className="px-3 py-1.5 text-right font-medium">LLM Calls</th>
              <th className="px-3 py-1.5 text-right font-medium">Tool Calls</th>
              <th className="px-3 py-1.5 text-right font-medium">Input</th>
              <th className="px-3 py-1.5 text-right font-medium">Output</th>
              <th className="px-3 py-1.5 text-right font-medium">Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {sessions.map((s) => (
              <tr key={s.sessionKind} className="hover:bg-accent/30">
                <td className="px-3 py-1.5 font-medium">
                  {_SESSION_KIND_LABELS[s.sessionKind] ?? s.sessionKind}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{s.llmCallCount}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{s.toolCallCount}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatTokens(s.inputTokens)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatTokens(s.outputTokens)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-green-400">
                  {formatUsd(s.totalCostUsd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SidecarSessionJobMetrics (legacy — kept for backward compat, delegates to SidecarSessionsSection)
// ---------------------------------------------------------------------------

export function SidecarSessionJobMetrics({ jobId }: { jobId: string }) {
  const [metrics, setMetrics] = useState<SidecarSessionMetrics | null>(null);
  const [expanded, setExpanded] = useState(false);
  const telemetryVersion = useStore((s) => s.telemetryVersions[jobId] ?? 0);
  useEffect(() => {
    fetchSidecarSessionMetrics()
      .then(setMetrics)
      .catch(() => {});
  }, [jobId, telemetryVersion]);

  const jobMetrics = metrics?.jobs?.[jobId];
  if (!jobMetrics || jobMetrics.callCount === 0) return null;

  const latencyColor = jobMetrics.avgLatencyMs > 5000
    ? "text-red-400"
    : jobMetrics.avgLatencyMs > 2000
      ? "text-yellow-400"
      : "text-green-400";

  const hasTokens = jobMetrics.inputTokens > 0 || jobMetrics.outputTokens > 0;

  return (
    <div className="rounded-md border border-border overflow-hidden">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 bg-muted/30 hover:bg-muted/50 transition-colors text-left"
        onClick={() => setExpanded((c) => !c)}
      >
        {expanded ? <ChevronDown size={11} className="text-muted-foreground shrink-0" /> : <ChevronRight size={11} className="text-muted-foreground shrink-0" />}
        <Brain size={12} className="text-purple-400 shrink-0" />
        <span className="text-xs font-medium text-foreground">Sidecar Session</span>
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
          <span>{jobMetrics.callCount} calls</span>
          {hasTokens && <span>{formatTokens(jobMetrics.inputTokens + jobMetrics.outputTokens)} tok</span>}
          {(jobMetrics.costUsd ?? -1) > 0 && <span className="text-green-400">{formatUsd(jobMetrics.costUsd)}</span>}
          <span className={latencyColor}>{formatDuration(jobMetrics.avgLatencyMs)} avg</span>
          <span>{formatDuration(jobMetrics.totalLatencyMs)}</span>
        </span>
      </button>
      {expanded && (
        <div className="px-3 py-2 grid grid-cols-3 gap-2">
          <CompactStat label="Calls" value={String(jobMetrics.callCount)} />
          <CompactStat label="Avg Latency" value={formatDuration(jobMetrics.avgLatencyMs)} warn={jobMetrics.avgLatencyMs > 5000} />
          <CompactStat label="Total Time" value={formatDuration(jobMetrics.totalLatencyMs)} />
          {hasTokens && <CompactStat label="Input" value={formatTokens(jobMetrics.inputTokens)} />}
          {hasTokens && <CompactStat label="Output" value={formatTokens(jobMetrics.outputTokens)} />}
          {(jobMetrics.costUsd ?? -1) > 0 && <CompactStat label="Cost" value={formatUsd(jobMetrics.costUsd)} />}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatCard & CompactStat
// ---------------------------------------------------------------------------

export function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: string; color: string }) {
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

export function CompactStat({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={cn("rounded-md border border-border bg-card px-3 py-2", warn && "border-yellow-500/50")}>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("text-sm font-medium font-mono", warn && "text-yellow-500")}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SubAgentGroup
// ---------------------------------------------------------------------------

export function SubAgentGroup({ group }: {
  group: { model: string; count: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; cost: number | null; durationMs: number; calls: LLMCall[] };
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
          <span className="text-green-400">{formatUsd(group.cost)}</span>
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
