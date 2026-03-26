import { useState, useEffect, useMemo } from "react";
import {
  BarChart3, DollarSign, Clock, Cpu, Wrench, TrendingUp,
  ArrowUpRight, GitBranch, ChevronUp, ChevronDown,
} from "lucide-react";
import { Tooltip } from "./ui/tooltip";
import {
  fetchAnalyticsOverview,
  fetchAnalyticsModels,
  fetchAnalyticsTools,
  fetchAnalyticsRepos,
  fetchAnalyticsJobs,
  fetchFleetCostDrivers,
  type AnalyticsOverview,
  type AnalyticsModels,
  type AnalyticsTools,
  type AnalyticsRepos,
  type AnalyticsJobs,
  type FleetCostDriversResponse,
} from "../api/client";
import { Badge } from "./ui/badge";
import { Spinner } from "./ui/spinner";
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
  type TooltipValueType,
} from "recharts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatUsd(n: number): string {
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function formatDuration(ms: number): string {
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
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
        <Icon size={14} />
        {label}
      </div>
      <div className="text-2xl font-semibold text-foreground">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

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
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: "#888" }}
          tickFormatter={(v: string) => v.slice(5)}
        />
        <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
          formatter={(v: TooltipValueType | undefined) => [formatUsd(Number(v ?? 0)), "Cost"]}
          labelFormatter={(l: unknown) => String(l)}
        />
        <Area type="monotone" dataKey="cost" stroke="#6366f1" fill="url(#costGrad)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function JobStatusPie({ overview }: { overview: AnalyticsOverview }) {
  const data = [
    { name: "In Review", value: overview.review, color: STATUS_COLORS.review },
    { name: "Completed", value: overview.completed, color: STATUS_COLORS.completed },
    { name: "Failed", value: overview.failed, color: STATUS_COLORS.failed },
    { name: "Cancelled", value: overview.cancelled, color: STATUS_COLORS.cancelled },
    { name: "Running", value: overview.running, color: STATUS_COLORS.running },
  ].filter((d) => d.value > 0);

  if (!data.length) return null;

  return (
    <ResponsiveContainer width="100%" height={180}>
      <PieChart>
        <Pie data={data} cx="50%" cy="50%" innerRadius={45} outerRadius={70} dataKey="value" paddingAngle={2}>
          {data.map((entry) => (
            <Cell key={entry.name} fill={entry.color} />
          ))}
        </Pie>
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

function ModelBreakdown({ models }: { models: AnalyticsModels["models"] }) {
  const [metric, setMetric] = useState<"cost" | "cost_per_job" | "cost_per_minute" | "cost_per_turn" | "cost_per_tool_call" | "cost_per_diff_line" | "cost_per_mtok">("cost");
  if (!models.length) return <p className="text-muted-foreground text-sm">No model data yet.</p>;

  const metricLabel: Record<string, string> = {
    cost: "Total Cost",
    cost_per_job: "Cost / Job",
    cost_per_minute: "Cost / Minute",
    cost_per_turn: "Cost / Turn",
    cost_per_tool_call: "Cost / Tool Call",
    cost_per_diff_line: "Cost / Diff Line",
    cost_per_mtok: "Cost / MTok",
  };

  const chartData = models.slice(0, 8).map((m) => ({
    name: m.model || "unknown",
    value: Number(m[metric] ?? m.total_cost_usd) || 0,
    jobs: m.job_count,
  }));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5 text-[11px]">
        {(Object.keys(metricLabel) as Array<typeof metric>).map((k) => (
          <button
            key={k}
            onClick={() => setMetric(k)}
            className={`px-2 py-0.5 rounded-full border transition-colors ${
              metric === k
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border text-muted-foreground hover:text-foreground hover:border-foreground/30"
            }`}
          >
            {metricLabel[k]}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
          <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#888" }} interval={0} angle={-20} textAnchor="end" height={50} />
          <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(4)}`} />
          <RTooltip
            contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
            formatter={(v: TooltipValueType | undefined) => [formatUsd(Number(v ?? 0)), metricLabel[metric]]}
          />
          <Bar dataKey="value" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left py-1.5 px-2 font-medium">Model</th>
              <th className="text-left py-1.5 px-2 font-medium">SDK</th>
              <th className="text-right py-1.5 px-2 font-medium">Jobs</th>
              <th className="text-right py-1.5 px-2 font-medium">Cost</th>
              <th className="text-right py-1.5 px-2 font-medium">$/Job</th>
              <th className="text-right py-1.5 px-2 font-medium">$/Min</th>
              <th className="text-right py-1.5 px-2 font-medium">$/Turn</th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Percentage of input tokens served from prompt cache — cached tokens are billed at a reduced rate.">
                  <span className="cursor-help border-b border-dotted border-muted-foreground/50">Cache</span>
                </Tooltip>
              </th>
            </tr>
          </thead>
          <tbody>
            {models.map((m, i) => {
              const cacheRate = m.cache_hit_rate != null ? (Number(m.cache_hit_rate) * 100) : (m.input_tokens ? ((m.cache_read_tokens / m.input_tokens) * 100) : 0);
              return (
                <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                  <td className="py-1.5 px-2 font-mono">{m.model || "—"}</td>
                  <td className="py-1.5 px-2">
                    <Badge variant="outline" className="text-[10px]">{m.sdk}</Badge>
                  </td>
                  <td className="text-right py-1.5 px-2">{m.job_count}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(Number(m.total_cost_usd) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(Number(m.cost_per_job) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(Number(m.cost_per_minute) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(Number(m.cost_per_turn) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{cacheRate.toFixed(0)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ToolHealth({ tools }: { tools: AnalyticsTools["tools"] }) {
  if (!tools.length) return <p className="text-muted-foreground text-sm">No tool data yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-1.5 px-2 font-medium">Tool</th>
            <th className="text-right py-1.5 px-2 font-medium">Calls</th>
            <th className="text-right py-1.5 px-2 font-medium">Failures</th>
            <th className="text-right py-1.5 px-2 font-medium">Success</th>
            <th className="text-right py-1.5 px-2 font-medium">Avg</th>
            <th className="text-right py-1.5 px-2 font-medium">Total</th>
          </tr>
        </thead>
        <tbody>
          {tools.map((t, i) => {
            const successRate = t.count > 0 ? ((t.count - (t.failure_count || 0)) / t.count * 100) : 100;
            return (
              <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2 font-mono">{t.name}</td>
                <td className="text-right py-1.5 px-2">{t.count}</td>
                <td className="text-right py-1.5 px-2">
                  {t.failure_count ? (
                    <span className="text-red-400">{t.failure_count}</span>
                  ) : (
                    <span className="text-muted-foreground">0</span>
                  )}
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

function FleetCostDriverInsights({ fleetDrivers }: { fleetDrivers: FleetCostDriversResponse }) {
  const [dimension, setDimension] = useState<"phase" | "tool_category" | "turn">("phase");
  const summary = useMemo(() => fleetDrivers.summary ?? [], [fleetDrivers.summary]);

  const topPhase = summary.filter((row) => row.dimension === "phase").sort((a, b) => b.cost_usd - a.cost_usd)[0];
  const topToolCategory = summary
    .filter((row) => row.dimension === "tool_category")
    .sort((a, b) => b.cost_usd - a.cost_usd)[0];
  const topTurn = summary.filter((row) => row.dimension === "turn").sort((a, b) => b.cost_usd - a.cost_usd)[0];

  const dimensionRows = useMemo(
    () => summary.filter((row) => row.dimension === dimension).sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 8),
    [summary, dimension],
  );

  const labelMap: Record<typeof dimension, string> = {
    phase: "Phase",
    tool_category: "Tool Category",
    turn: "Turn",
  };

  const metricCards = [
    {
      label: "Highest-Cost Phase",
      bucket: topPhase?.bucket?.replace(/_/g, " ") ?? "—",
      cost: topPhase?.cost_usd ?? 0,
      meta: topPhase ? `${topPhase.job_count ?? 0} jobs` : "No data",
    },
    {
      label: "Highest-Cost Tool Category",
      bucket: topToolCategory?.bucket ?? "—",
      cost: topToolCategory?.cost_usd ?? 0,
      meta: topToolCategory ? `${topToolCategory.call_count} calls` : "No data",
    },
    {
      label: "Peak Turn Bucket",
      bucket: topTurn ? `turn ${topTurn.bucket}` : "—",
      cost: topTurn?.cost_usd ?? 0,
      meta: topTurn ? `${topTurn.job_count ?? 0} jobs` : "No data",
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {metricCards.map((card) => (
          <div key={card.label} className="rounded-md border border-border bg-background p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{card.label}</div>
            <div className="mt-1 text-sm font-semibold text-foreground">{card.bucket}</div>
            <div className="mt-1 text-lg font-bold tabular-nums">{formatUsd(card.cost)}</div>
            <div className="text-xs text-muted-foreground">{card.meta}</div>
          </div>
        ))}
      </div>

      <div className="rounded-md border border-border bg-background p-3 space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="text-sm font-medium text-foreground">Cost Driver Drill-down</div>
            <div className="text-xs text-muted-foreground">Inspect the leading buckets for one dimension at a time.</div>
          </div>
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {(["phase", "tool_category", "turn"] as const).map((key) => (
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
        </div>

        {dimensionRows.length > 0 ? (
          <>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={dimensionRows.map((row) => ({ name: row.bucket, cost: row.cost_usd }))} margin={{ top: 5, right: 10, left: 0, bottom: 40 }}>
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
                    <th className="text-left py-1.5 px-2 font-medium">Bucket</th>
                    <th className="text-right py-1.5 px-2 font-medium">Cost</th>
                    <th className="text-right py-1.5 px-2 font-medium">Calls</th>
                    <th className="text-right py-1.5 px-2 font-medium">Jobs</th>
                    <th className="text-right py-1.5 px-2 font-medium">Avg/Job</th>
                  </tr>
                </thead>
                <tbody>
                  {dimensionRows.map((row, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                      <td className="py-1.5 px-2 font-mono">{row.bucket}</td>
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
          <p className="text-sm text-muted-foreground">No cost-driver data available for this dimension.</p>
        )}
      </div>
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
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
          <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#888" }} interval={0} angle={-20} textAnchor="end" height={50} />
          <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
          <RTooltip
            contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
            formatter={(v) => [formatUsd(Number(v)), "Cost"]}
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
              <th className="text-right py-1.5 px-2 font-medium">Pass</th>
              <th className="text-right py-1.5 px-2 font-medium">Fail</th>
              <th className="text-right py-1.5 px-2 font-medium">Cost</th>
              <th className="text-right py-1.5 px-2 font-medium">Tokens</th>
              <th className="text-right py-1.5 px-2 font-medium">Tool Calls</th>
              <th className="text-right py-1.5 px-2 font-medium">Avg Time</th>
            </tr>
          </thead>
          <tbody>
            {repos.map((r, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2 font-mono truncate max-w-[200px]" title={r.repo || "(none)"}>
                  {r.repo || <span className="text-muted-foreground italic">(none)</span>}
                </td>
                <td className="text-right py-1.5 px-2">{r.job_count}</td>
                <td className="text-right py-1.5 px-2 text-green-400">{r.succeeded}</td>
                <td className="text-right py-1.5 px-2">
                  {r.failed ? <span className="text-red-400">{r.failed}</span> : <span className="text-muted-foreground">0</span>}
                </td>
                <td className="text-right py-1.5 px-2">{formatUsd(Number(r.total_cost_usd) || 0)}</td>
                <td className="text-right py-1.5 px-2">{formatTokens(r.total_tokens)}</td>
                <td className="text-right py-1.5 px-2">{r.tool_calls}</td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(r.avg_duration_ms) || 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Jobs table
// ---------------------------------------------------------------------------

type SortField = "completed_at" | "total_cost_usd" | "duration_ms" | "input_tokens" | "created_at";

function SortHeader({
  label, field, current, desc, onSort,
}: {
  label: string; field: SortField; current: SortField; desc: boolean;
  onSort: (f: SortField) => void;
}) {
  const active = field === current;
  return (
    <th
      className="text-right py-1.5 px-2 font-medium cursor-pointer select-none hover:text-foreground"
      onClick={() => onSort(field)}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {active && (desc ? <ChevronDown size={12} /> : <ChevronUp size={12} />)}
      </span>
    </th>
  );
}

function JobsTable({ period }: { period: number }) {
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
    if (field === sortField) {
      setSortDesc(!sortDesc);
    } else {
      setSortField(field);
      setSortDesc(true);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner size="sm" />
      </div>
    );
  }

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
            <SortHeader label="Tokens" field="input_tokens" current={sortField} desc={sortDesc} onSort={handleSort} />
            <SortHeader label="Duration" field="duration_ms" current={sortField} desc={sortDesc} onSort={handleSort} />
            <SortHeader label="When" field="completed_at" current={sortField} desc={sortDesc} onSort={handleSort} />
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => {
            const shortId = j.job_id?.slice(0, 8) || "—";
            const repoName = j.repo ? j.repo.split("/").pop() : "—";
            const statusColor = STATUS_COLORS[j.status] || "#666";
            const totalTokens = (j.input_tokens || 0) + (j.output_tokens || 0);
            const when = j.completed_at || j.created_at;
            const relTime = when ? formatRelativeTime(when) : "—";
            return (
              <tr key={j.job_id} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2 font-mono text-muted-foreground" title={j.job_id}>{shortId}</td>
                <td className="py-1.5 px-2 truncate max-w-[120px]" title={j.repo}>{repoName}</td>
                <td className="py-1.5 px-2">
                  <Badge variant="outline" className="text-[10px]">{j.model || "—"}</Badge>
                </td>
                <td className="py-1.5 px-2">
                  <span className="inline-flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} />
                    {j.status}
                  </span>
                </td>
                <td className="text-right py-1.5 px-2">{formatUsd(Number(j.total_cost_usd) || 0)}</td>
                <td className="text-right py-1.5 px-2">{formatTokens(totalTokens)}</td>
                <td className="text-right py-1.5 px-2">{formatDuration(j.duration_ms || 0)}</td>
                <td className="text-right py-1.5 px-2 text-muted-foreground" title={when || undefined}>{relTime}</td>
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
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [models, setModels] = useState<AnalyticsModels | null>(null);
  const [tools, setTools] = useState<AnalyticsTools | null>(null);
  const [repos, setRepos] = useState<AnalyticsRepos | null>(null);
  const [fleetDrivers, setFleetDrivers] = useState<FleetCostDriversResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchAnalyticsOverview(period),
      fetchAnalyticsModels(period),
      fetchAnalyticsTools(Math.max(period, 30)),
      fetchAnalyticsRepos(period),
      fetchFleetCostDrivers(Math.max(period, 30)).catch(() => null),
    ])
      .then(([o, m, t, r, fd]) => {
        if (cancelled) return;
        setOverview(o);
        setModels(m);
        setTools(t);
        setRepos(r);
        setFleetDrivers(fd);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || "Failed to load analytics");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [period]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {error}
        </div>
      </div>
    );
  }

  if (!overview) return null;

  const successRate = overview.totalJobs
    ? (((overview.review + overview.completed) / overview.totalJobs) * 100).toFixed(0)
    : "—";

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground flex items-center gap-2">
            <BarChart3 size={20} />
            Analytics
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Fleet-wide telemetry across all agent runs
          </p>
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

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard icon={Cpu} label="Jobs" value={String(overview.totalJobs)} sub={`${successRate}% success`} />
        <StatCard icon={DollarSign} label="Cost" value={formatUsd(overview.totalCostUsd)} />
        <StatCard
          icon={Clock}
          label="Avg Duration"
          value={formatDuration(overview.avgDurationMs)}
        />
        <StatCard
          icon={ArrowUpRight}
          label="Tokens"
          value={formatTokens(overview.totalTokens)}
          sub={
            <Tooltip content="Cached tokens reuse previous context at a significantly lower rate. Higher cache percentages reduce total spend.">
              <span className="cursor-help border-b border-dotted border-muted-foreground/50">
                {overview.cacheHitRate}% cache
              </span>
            </Tooltip>
          }
        />
        <StatCard
          icon={Wrench}
          label="Tool Calls"
          value={String(overview.totalToolCalls)}
          sub={`${overview.toolSuccessRate}% success`}
        />
        <StatCard
          icon={TrendingUp}
          label="Premium Req"
          value={String(Math.round(overview.totalPremiumRequests))}
          sub={overview.totalPremiumRequests === 0 ? "* unlimited seats not metered" : undefined}
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-medium text-foreground mb-3">Cost Trend</h2>
          <CostTrendChart data={overview.costTrend} />
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-medium text-foreground mb-3">Job Outcomes</h2>
          <JobStatusPie overview={overview} />
          <div className="flex flex-wrap gap-3 justify-center mt-2">
            {[
              { label: "In Review", color: STATUS_COLORS.review, count: overview.review },
              { label: "Completed", color: STATUS_COLORS.completed, count: overview.completed },
              { label: "Failed", color: STATUS_COLORS.failed, count: overview.failed },
              { label: "Cancelled", color: STATUS_COLORS.cancelled, count: overview.cancelled },
            ]
              .filter((d) => d.count > 0)
              .map((d) => (
                <span key={d.label} className="flex items-center gap-1 text-xs text-muted-foreground">
                  <span className="inline-block w-2 h-2 rounded-full" style={{ background: d.color }} />
                  {d.label} ({d.count})
                </span>
              ))}
          </div>
        </div>
      </div>

      {/* Model breakdown */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-3">Model Usage</h2>
        {models && <ModelBreakdown models={models.models} />}
      </div>

      {/* Repo breakdown */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-3 flex items-center gap-2">
          <GitBranch size={14} />
          Repository Breakdown
        </h2>
        {repos && <RepoBreakdown repos={repos.repos} />}
      </div>

      {/* Jobs table */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-3">Recent Jobs</h2>
        <JobsTable period={period} />
      </div>

      {/* Tool health */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium text-foreground mb-3">Tool Health</h2>
        {tools && <ToolHealth tools={tools.tools} />}
      </div>

      {/* Fleet Cost Drivers */}
      {fleetDrivers?.summary && fleetDrivers.summary.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-medium text-foreground mb-3">Cost Drivers</h2>
          <FleetCostDriverInsights fleetDrivers={fleetDrivers} />
        </div>
      )}
    </div>
  );
}
