import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  BarChart3, DollarSign, Clock, Wrench, GitBranch,
  ChevronUp, ChevronDown, ChevronRight, AlertTriangle,
  Activity, Zap, X, Loader2,
} from "lucide-react";
import { Tooltip } from "./ui/tooltip";
import {
  fetchScorecard,
  fetchModelComparison,
  fetchAnalyticsTools,
  fetchAnalyticsRepos,
  fetchAnalyticsJobs,
  fetchFleetCostDrivers,
  fetchObservations,
  dismissObservation,
  type ScorecardResponse,
  type ModelComparisonResponse,
  type ModelComparisonRow,
  type AnalyticsTools,
  type AnalyticsRepos,
  type AnalyticsJobs,
  type FleetCostDriversResponse,
  type Observation,
} from "../api/client";
import { Badge } from "./ui/badge";
import { Spinner } from "./ui/spinner";
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, type TooltipValueType,
} from "recharts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatUsd(n: number): string {
  if (n == null || isNaN(n)) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function formatDuration(ms: number): string {
  if (ms == null || isNaN(ms)) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const STATUS_COLORS: Record<string, string> = {
  review: "#06b6d4",
  completed: "#22c55e",
  failed: "#ef4444",
  cancelled: "#f59e0b",
  running: "#3b82f6",
};

// ---------------------------------------------------------------------------
// Collapsible section wrapper
// ---------------------------------------------------------------------------

function CollapsibleSection({
  title,
  icon: Icon,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon?: React.ElementType;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between p-4 text-left hover:bg-accent/30 transition-colors"
      >
        <h2 className="text-sm font-medium text-foreground flex items-center gap-2">
          {Icon && <Icon size={14} />}
          {title}
        </h2>
        <ChevronRight
          size={16}
          className={`text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Budget card — adapts per SDK
// ---------------------------------------------------------------------------

function BudgetCard({ scorecard }: { scorecard: ScorecardResponse }) {
  const { budget, quotaJson } = scorecard;
  const totalCost = budget.reduce((s, b) => s + b.totalCostUsd, 0);
  const totalJobs = budget.reduce((s, b) => s + b.jobCount, 0);

  let quotaInfo: { pct: number } | null = null;
  if (quotaJson) {
    try {
      const q = JSON.parse(quotaJson);
      const snapshots = Array.isArray(q) ? q : q?.snapshots ?? [q];
      const latest = snapshots[snapshots.length - 1];
      if (latest && typeof latest.percentage_used === "number") {
        quotaInfo = { pct: latest.percentage_used };
      } else if (latest && latest.used != null && latest.total != null && latest.total > 0) {
        quotaInfo = { pct: (latest.used / latest.total) * 100 };
      }
    } catch { /* ignore malformed quota */ }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
        <DollarSign size={14} />
        Budget
      </div>

      <div className="text-2xl font-semibold text-foreground">
        <Tooltip content={`API-equivalent cost across ${totalJobs} jobs. For subscription plans (Claude Max, Copilot Pro), this reflects what the same usage would cost at API rates — not your subscription charge.`}>
          <span className="cursor-help">{formatUsd(totalCost)}</span>
        </Tooltip>
      </div>

      <div className="space-y-2">
        {budget.map((b) => (
          <div key={b.sdk} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-1.5">
              <Badge variant="outline" className="text-[10px]">{b.sdk}</Badge>
              <span className="text-muted-foreground">{b.jobCount} jobs</span>
            </div>
            <div className="flex items-center gap-3">
              {b.totalCostUsd > 0 || b.avgCostPerJob > 0 ? (
                <Tooltip content={`API-equivalent cost: ${formatUsd(b.avgCostPerJob)} avg per job, ${formatDuration(b.avgDurationMs)} avg duration. For subscriptions this reflects usage value, not your actual charge.`}>
                  <span className="cursor-help text-foreground">{formatUsd(b.totalCostUsd)}</span>
                </Tooltip>
              ) : (
                <span className="text-muted-foreground italic">No usage data</span>
              )}
              {b.premiumRequests > 0 && (
                <Tooltip content="Premium requests consumed from your Copilot entitlement this period">
                  <span className="cursor-help text-muted-foreground">{b.premiumRequests} reqs</span>
                </Tooltip>
              )}
            </div>
          </div>
        ))}
      </div>

      {quotaInfo && (
        <div className="pt-2 border-t border-border">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-muted-foreground">Copilot Quota</span>
            <span className={(quotaInfo.pct ?? 0) > 80 ? "text-red-400 font-medium" : "text-foreground"}>
              {(quotaInfo.pct ?? 0).toFixed(0)}% used
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-border overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                quotaInfo.pct > 80 ? "bg-red-500" : quotaInfo.pct > 60 ? "bg-yellow-500" : "bg-green-500"
              }`}
              style={{ width: `${Math.min(quotaInfo.pct, 100)}%` }}
            />
          </div>
          {quotaInfo.pct > 80 && (
            <div className="flex items-center gap-1 mt-1 text-[11px] text-red-400">
              <AlertTriangle size={11} />
              Approaching quota limit
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity card — raw resolution counts + animated running indicator
// ---------------------------------------------------------------------------

function ActivityCard({ scorecard }: { scorecard: ScorecardResponse }) {
  const a = scorecard.activity;
  const outcomes = [
    { label: "Running", count: a.running, color: "#3b82f6", spinning: true },
    { label: "In Review", count: a.inReview, color: "#8b5cf6" },
    { label: "Merged", count: a.merged, color: "#22c55e" },
    { label: "PR Created", count: a.prCreated, color: "#06b6d4" },
    { label: "Discarded", count: a.discarded, color: "#f59e0b" },
    { label: "Failed", count: a.failed, color: "#ef4444" },
    { label: "Cancelled", count: a.cancelled, color: "#6b7280" },
  ].filter((o) => o.count > 0);

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
        <Activity size={14} />
        Activity
      </div>

      <div className="text-2xl font-semibold text-foreground">
        {a.totalJobs} <span className="text-sm font-normal text-muted-foreground">jobs</span>
      </div>

      <div className="space-y-1.5">
        {outcomes.map((o) => (
          <div key={o.label} className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5">
              {"spinning" in o && o.spinning ? (
                <Loader2 size={10} className="animate-spin" style={{ color: o.color }} />
              ) : (
                <span className="w-2 h-2 rounded-full" style={{ background: o.color }} />
              )}
              {o.label}
            </span>
            <span className="text-foreground font-medium">{o.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cost trend chart
// ---------------------------------------------------------------------------

function CostTrendChart({ data }: { data: { date: string; cost: number; jobs: number }[] }) {
  if (!data.length) return <p className="text-muted-foreground text-sm">No data yet.</p>;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: string) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
          formatter={(v: TooltipValueType | undefined) => [formatUsd(Number(v ?? 0)), "API-equivalent cost"]}
          labelFormatter={(l: unknown) => String(l)}
        />
        <Area type="monotone" dataKey="cost" stroke="#6366f1" fill="url(#costGrad)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// Model comparison table
// ---------------------------------------------------------------------------

function ModelComparison({
  data,
  repos,
  selectedRepo,
  onRepoChange,
}: {
  data: ModelComparisonResponse;
  repos: AnalyticsRepos | null;
  selectedRepo: string;
  onRepoChange: (repo: string) => void;
}) {
  const models = data.models;
  if (!models.length) return <p className="text-muted-foreground text-sm">No model data yet.</p>;

  return (
    <div className="space-y-3">
      {repos && repos.repos.length > 1 && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Filter by repo:</span>
          <select
            value={selectedRepo}
            onChange={(e) => onRepoChange(e.target.value)}
            className="rounded border border-border bg-background px-2 py-0.5 text-xs text-foreground"
          >
            <option value="">All repos</option>
            {repos.repos.map((r) => (
              <option key={r.repo} value={r.repo}>{r.repo ? r.repo.split("/").pop() : "(none)"}</option>
            ))}
          </select>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left py-1.5 px-2 font-medium">Model</th>
              <th className="text-right py-1.5 px-2 font-medium">Jobs</th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="API-equivalent average cost per job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Avg Cost</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">Avg Time</th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Jobs whose changes were merged"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Merged</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Jobs where a PR was created"><span className="cursor-help border-b border-dotted border-muted-foreground/50">PR'd</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Jobs whose output was discarded"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Discarded</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">Failed</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m: ModelComparisonRow, i: number) => (
              <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono">{m.model || "—"}</span>
                    <Badge variant="outline" className="text-[10px]">{m.sdk}</Badge>
                  </div>
                </td>
                <td className="text-right py-1.5 px-2">{m.jobCount}</td>
                <td className="text-right py-1.5 px-2">
                  {m.totalCostUsd > 0 || m.avgCost > 0 ? (
                    <Tooltip content={`Total: ${formatUsd(m.totalCostUsd)} · ${formatUsd(m.costPerMinute)}/min · ${formatUsd(m.costPerTurn)}/turn`}>
                      <span className="cursor-help">{formatUsd(m.avgCost)}</span>
                    </Tooltip>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
                <td className="text-right py-1.5 px-2">{formatDuration(m.avgDurationMs)}</td>
                <td className="text-right py-1.5 px-2">{m.merged > 0 ? <span className="text-green-400">{m.merged}</span> : <span className="text-muted-foreground">0</span>}</td>
                <td className="text-right py-1.5 px-2">{m.prCreated > 0 ? <span className="text-cyan-400">{m.prCreated}</span> : <span className="text-muted-foreground">0</span>}</td>
                <td className="text-right py-1.5 px-2">{m.discarded > 0 ? <span className="text-yellow-400">{m.discarded}</span> : <span className="text-muted-foreground">0</span>}</td>
                <td className="text-right py-1.5 px-2">{m.failed > 0 ? <span className="text-red-400">{m.failed}</span> : <span className="text-muted-foreground">0</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Observations panel
// ---------------------------------------------------------------------------

function ObservationsPanel({ observations, onDismiss }: { observations: Observation[]; onDismiss: (id: number) => void }) {
  if (!observations.length) return null;

  const severityColor: Record<string, string> = {
    critical: "border-red-500/40 bg-red-500/10",
    warning: "border-yellow-500/40 bg-yellow-500/10",
    info: "border-blue-500/40 bg-blue-500/10",
  };
  const severityText: Record<string, string> = {
    critical: "text-red-400",
    warning: "text-yellow-400",
    info: "text-blue-400",
  };

  return (
    <div className="space-y-2">
      {observations.map((obs) => (
        <div key={obs.id} className={`rounded-lg border px-4 py-3 ${severityColor[obs.severity] || "border-border bg-card"}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <AlertTriangle size={13} className={severityText[obs.severity] || "text-muted-foreground"} />
                <span className="text-sm font-medium text-foreground">{obs.title}</span>
                <Badge variant="outline" className="text-[10px]">{obs.category}</Badge>
              </div>
              <p className="text-xs text-muted-foreground">{obs.detail}</p>
              {obs.total_waste_usd > 0 && (
                <p className="text-xs mt-1">
                  <Tooltip content="Estimated excess spend attributable to this pattern">
                    <span className="cursor-help text-yellow-400">{formatUsd(obs.total_waste_usd)} estimated waste</span>
                  </Tooltip>
                  {obs.job_count > 0 && <span className="text-muted-foreground"> across {obs.job_count} jobs</span>}
                </p>
              )}
            </div>
            <button
              onClick={() => onDismiss(obs.id)}
              className="shrink-0 p-1 rounded hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors"
              title="Dismiss"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Repo breakdown
// ---------------------------------------------------------------------------

function RepoBreakdown({ repos }: { repos: AnalyticsRepos["repos"] }) {
  if (!repos.length) return <p className="text-muted-foreground text-sm">No repo data yet.</p>;

  const chartData = repos.slice(0, 10).map((r) => ({
    name: r.repo ? r.repo.split("/").pop() || r.repo : "(none)",
    cost: Number(r.total_cost_usd) || 0,
    jobs: r.job_count,
  }));

  return (
    <div className="space-y-3">
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
          <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#888" }} interval={0} angle={-20} textAnchor="end" height={50} />
          <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
          <RTooltip
            contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
            formatter={(v: TooltipValueType | undefined) => [formatUsd(Number(v ?? 0)), "API-equivalent cost"]}
          />
          <Bar dataKey="cost" fill="#10b981" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left py-1.5 px-2 font-medium">Repository</th>
              <th className="text-right py-1.5 px-2 font-medium">Jobs</th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Total API-equivalent cost"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Cost</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="API-equivalent cost per job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">$/Job</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">Avg Time</th>
              <th className="text-right py-1.5 px-2 font-medium">Tool Calls</th>
            </tr>
          </thead>
          <tbody>
            {repos.map((r, i) => {
              const costPerJob = r.job_count > 0 ? (Number(r.total_cost_usd) || 0) / r.job_count : 0;
              return (
                <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                  <td className="py-1.5 px-2 font-mono truncate max-w-[200px]" title={r.repo || "(none)"}>
                    {r.repo || <span className="text-muted-foreground italic">(none)</span>}
                  </td>
                  <td className="text-right py-1.5 px-2">{r.job_count}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(Number(r.total_cost_usd) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(costPerJob)}</td>
                  <td className="text-right py-1.5 px-2">{formatDuration(Number(r.avg_duration_ms) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{r.tool_calls}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool Health
// ---------------------------------------------------------------------------

function ToolHealth({ tools }: { tools: AnalyticsTools["tools"] }) {
  if (!tools.length) return <p className="text-muted-foreground text-sm">No tool data yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-1.5 px-2 font-medium">Tool</th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Total number of times this tool was called"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Calls</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Agent called the tool with invalid arguments or in an incorrect context"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Agent Err</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="The tool itself returned an error or timed out"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Tool Err</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Percentage of calls that completed without errors"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Success</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Average time per call"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Avg Time</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Total cumulative time spent in this tool"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Total Time</span></Tooltip>
            </th>
          </tr>
        </thead>
        <tbody>
          {tools.map((t, i) => {
            const successRate = t.count > 0 ? ((t.count - (t.failure_count || 0)) / t.count * 100) : 100;
            const agentErrors = (t as Record<string, unknown>).agent_error_count as number | undefined ?? 0;
            const toolErrors = (t as Record<string, unknown>).tool_error_count as number | undefined ?? 0;
            return (
              <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2 font-mono">{t.name}</td>
                <td className="text-right py-1.5 px-2">{t.count}</td>
                <td className="text-right py-1.5 px-2">
                  {agentErrors ? <span className="text-yellow-400">{agentErrors}</span> : <span className="text-muted-foreground">0</span>}
                </td>
                <td className="text-right py-1.5 px-2">
                  {toolErrors ? <span className="text-red-400">{toolErrors}</span> : <span className="text-muted-foreground">0</span>}
                </td>
                <td className="text-right py-1.5 px-2">
                  <span className={successRate >= 95 ? "text-green-400" : successRate >= 80 ? "text-yellow-400" : "text-red-400"}>
                    {successRate.toFixed(0)}%
                  </span>
                </td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(t.avg_duration_ms) || 0)}</td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(t.total_duration_ms) || 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fleet cost driver insights
// ---------------------------------------------------------------------------

function FleetCostDriverInsights({ fleetDrivers }: { fleetDrivers: FleetCostDriversResponse }) {
  const [dimension, setDimension] = useState<"phase" | "tool_category">("phase");
  const summary = useMemo(() => fleetDrivers.summary ?? [], [fleetDrivers.summary]);

  const phaseLabels: Record<string, string> = {
    agent_reasoning: "Reasoning",
    verification: "Verification",
    environment_setup: "Setup",
    finalization: "Wrap-up",
    post_completion: "Post-completion",
    unknown: "Unattributed",
  };
  const toolCategoryLabels: Record<string, string> = {
    file_read: "File Reading",
    file_write: "File Writing",
    file_search: "File Search",
    shell: "Shell Commands",
    agent: "Sub-agents",
    other: "Other",
  };
  const bucketLabels = dimension === "phase" ? phaseLabels : toolCategoryLabels;

  const dimensionRows = useMemo(
    () => summary.filter((row) => row.dimension === dimension).sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 8),
    [summary, dimension],
  );
  const labelMap: Record<typeof dimension, string> = { phase: "Phase", tool_category: "Tool Category" };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5 text-[11px]">
        {(["phase", "tool_category"] as const).map((key) => (
          <button
            key={key}
            onClick={() => setDimension(key)}
            className={`px-2 py-0.5 rounded-full border transition-colors ${
              dimension === key
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border text-muted-foreground hover:text-foreground hover:border-foreground/30"
            }`}
          >
            {labelMap[key]}
          </button>
        ))}
      </div>
      {dimensionRows.length > 0 ? (
        <>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={dimensionRows.map((row) => ({ name: bucketLabels[row.bucket] || row.bucket, cost: row.cost_usd }))} margin={{ top: 5, right: 10, left: 0, bottom: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#888" }} interval={0} angle={-20} textAnchor="end" height={55} />
              <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
              <RTooltip
                contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
                formatter={(v: TooltipValueType | undefined) => [formatUsd(Number(v ?? 0)), "Cost"]}
              />
              <Bar dataKey="cost" fill="#0ea5e9" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-1.5 px-2 font-medium">{labelMap[dimension]}</th>
                  <th className="text-right py-1.5 px-2 font-medium">Cost</th>
                  <th className="text-right py-1.5 px-2 font-medium">Calls</th>
                  <th className="text-right py-1.5 px-2 font-medium">Jobs</th>
                  <th className="text-right py-1.5 px-2 font-medium">Avg/Job</th>
                </tr>
              </thead>
              <tbody>
                {dimensionRows.map((row, i) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                    <td className="py-1.5 px-2">{bucketLabels[row.bucket] || row.bucket}</td>
                    <td className="text-right py-1.5 px-2">{formatUsd(Number(row.cost_usd) || 0)}</td>
                    <td className="text-right py-1.5 px-2">{row.call_count}</td>
                    <td className="text-right py-1.5 px-2">{row.job_count ?? "—"}</td>
                    <td className="text-right py-1.5 px-2">{formatUsd(Number(row.avg_cost_per_job) || 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">No cost-driver data for this dimension.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Jobs table
// ---------------------------------------------------------------------------

type SortField = "completed_at" | "total_cost_usd" | "duration_ms" | "created_at";

function SortHeader({ label, field, current, desc, onSort }: {
  label: string; field: SortField; current: SortField; desc: boolean; onSort: (f: SortField) => void;
}) {
  const active = field === current;
  return (
    <th className="text-right py-1.5 px-2 font-medium cursor-pointer select-none hover:text-foreground" onClick={() => onSort(field)}>
      <span className="inline-flex items-center gap-0.5">
        {label}
        {active && (desc ? <ChevronDown size={12} /> : <ChevronUp size={12} />)}
      </span>
    </th>
  );
}

function JobsTable({ period }: { period: number }) {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<AnalyticsJobs["jobs"]>([]);
  const [sortField, setSortField] = useState<SortField>("completed_at");
  const [sortDesc, setSortDesc] = useState(true);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAnalyticsJobs({ period, sort: sortField, desc: sortDesc, limit: 100 })
      .then((data) => { if (!cancelled) setJobs(data.jobs); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [period, sortField, sortDesc]);

  const handleSort = (field: SortField) => {
    if (field === sortField) setSortDesc(!sortDesc);
    else { setSortField(field); setSortDesc(true); }
  };

  if (loading) return <div className="flex justify-center py-8"><Spinner size="sm" /></div>;
  if (!jobs.length) return <p className="text-muted-foreground text-sm">No jobs in this period.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-1.5 px-2 font-medium">Job</th>
            <th className="text-left py-1.5 px-2 font-medium">Repo</th>
            <th className="text-left py-1.5 px-2 font-medium">Model</th>
            <th className="text-left py-1.5 px-2 font-medium">Status</th>
            <SortHeader label="Cost" field="total_cost_usd" current={sortField} desc={sortDesc} onSort={handleSort} />
            <SortHeader label="Duration" field="duration_ms" current={sortField} desc={sortDesc} onSort={handleSort} />
            <SortHeader label="When" field="completed_at" current={sortField} desc={sortDesc} onSort={handleSort} />
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => {
            const shortId = j.job_id?.slice(0, 8) || "—";
            const repoName = j.repo ? j.repo.split("/").pop() : "—";
            const statusColor = STATUS_COLORS[j.status] || "#666";
            const when = j.completed_at || j.created_at;
            return (
              <tr key={j.job_id} className="border-b border-border/50 hover:bg-accent/30 cursor-pointer" onClick={() => navigate(`/jobs/${j.job_id}`)}>
                <td className="py-1.5 px-2 font-mono text-muted-foreground" title={j.job_id}>{shortId}</td>
                <td className="py-1.5 px-2 truncate max-w-[120px]" title={j.repo}>{repoName}</td>
                <td className="py-1.5 px-2"><Badge variant="outline" className="text-[10px]">{j.model || "—"}</Badge></td>
                <td className="py-1.5 px-2">
                  <span className="inline-flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} />
                    {j.status}
                  </span>
                </td>
                <td className="text-right py-1.5 px-2">
                  <Tooltip content="API-equivalent cost"><span className="cursor-help">{formatUsd(Number(j.total_cost_usd) || 0)}</span></Tooltip>
                </td>
                <td className="text-right py-1.5 px-2">{formatDuration(j.duration_ms || 0)}</td>
                <td className="text-right py-1.5 px-2 text-muted-foreground" title={when || undefined}>{when ? formatRelativeTime(when) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function AnalyticsScreen() {
  const [period, setPeriod] = useState(7);
  const [selectedRepo, setSelectedRepo] = useState("");
  const [scorecard, setScorecard] = useState<ScorecardResponse | null>(null);
  const [modelComparison, setModelComparison] = useState<ModelComparisonResponse | null>(null);
  const [tools, setTools] = useState<AnalyticsTools | null>(null);
  const [repos, setRepos] = useState<AnalyticsRepos | null>(null);
  const [fleetDrivers, setFleetDrivers] = useState<FleetCostDriversResponse | null>(null);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchScorecard(period),
      fetchModelComparison(Math.max(period, 30), selectedRepo || undefined),
      fetchAnalyticsTools(Math.max(period, 30)),
      fetchAnalyticsRepos(period),
      fetchFleetCostDrivers(Math.max(period, 30)).catch(() => null),
      fetchObservations().catch(() => ({ observations: [] })),
    ])
      .then(([sc, mc, t, r, fd, obs]) => {
        if (cancelled) return;
        setScorecard(sc);
        setModelComparison(mc);
        setTools(t);
        setRepos(r);
        setFleetDrivers(fd);
        setObservations(obs?.observations ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || "Failed to load analytics");
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [period, selectedRepo]);

  const handleDismissObservation = async (id: number) => {
    try {
      await dismissObservation(id);
      setObservations((prev) => prev.filter((o) => o.id !== id));
    } catch { /* ignore */ }
  };

  if (loading) return <div className="flex items-center justify-center py-20"><Spinner size="lg" /></div>;
  if (error) return <div className="max-w-4xl mx-auto p-6"><div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">{error}</div></div>;
  if (!scorecard) return null;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground flex items-center gap-2">
            <BarChart3 size={20} />
            Analytics
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">Budget, activity, and model effectiveness</p>
        </div>
        <select
          value={period}
          onChange={(e) => setPeriod(Number(e.target.value))}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground"
        >
          <option value={1}>Last 24h</option>
          <option value={7}>Last 7 days</option>
          <option value={14}>Last 14 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      {/* Observations — alerts at the top */}
      {observations.length > 0 && (
        <ObservationsPanel observations={observations} onDismiss={handleDismissObservation} />
      )}

      {/* Top row: Budget + Activity */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <BudgetCard scorecard={scorecard} />
        <ActivityCard scorecard={scorecard} />
      </div>

      {/* Cost trend */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-1">Cost Trend</h2>
        <p className="text-xs text-muted-foreground mb-3">Daily API-equivalent spend — for subscriptions this reflects usage value, not billing</p>
        <CostTrendChart data={scorecard.costTrend} />
      </div>

      {/* Model Comparison */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-1 flex items-center gap-2">
          <Zap size={14} />
          Model Comparison
        </h2>
        <p className="text-xs text-muted-foreground mb-3">Cost, speed, and outcomes per model — use this to pick models for future jobs</p>
        {modelComparison && <ModelComparison data={modelComparison} repos={repos} selectedRepo={selectedRepo} onRepoChange={setSelectedRepo} />}
      </div>

      {/* Repo breakdown */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-3 flex items-center gap-2">
          <GitBranch size={14} />
          Repository Breakdown
        </h2>
        {repos && <RepoBreakdown repos={repos.repos} />}
      </div>

      {/* Collapsed detail sections */}
      <CollapsibleSection title="Recent Jobs" icon={Clock}>
        <JobsTable period={period} />
      </CollapsibleSection>

      <CollapsibleSection title="Tool Health" icon={Wrench}>
        {tools && <ToolHealth tools={tools.tools} />}
      </CollapsibleSection>

      {fleetDrivers?.summary && fleetDrivers.summary.length > 0 && (
        <CollapsibleSection title="Cost Drivers" icon={DollarSign}>
          <FleetCostDriverInsights fleetDrivers={fleetDrivers} />
        </CollapsibleSection>
      )}
    </div>
  );
}
