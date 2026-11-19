import { Tooltip } from "../ui/tooltip";
import {
  BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, type TooltipValueType,
} from "recharts";
import { type AnalyticsRepos } from "../../api/client";
import { formatUsd, formatDuration } from "./helpers";
import { pathBasename } from "../../lib/paths";

// ---------------------------------------------------------------------------
// Repo breakdown
// ---------------------------------------------------------------------------

export function RepoBreakdown({ repos }: { repos: AnalyticsRepos["repos"] }) {
  if (!repos.length) return <p className="text-muted-foreground text-sm">No repo data yet.</p>;

  const chartData = repos.slice(0, 10).map((r) => ({
    name: r.repo ? pathBasename(r.repo) || r.repo : "(none)",
    cost: Number(r.totalCostUsd) || 0,
    jobs: r.jobCount,
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
            formatter={(v: TooltipValueType | undefined) => [formatUsd(v == null ? null : Number(v)), "Verified USD"]}
          />
          <Bar dataKey="cost" fill="#10b981" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left py-1.5 px-2 font-medium">Repository</th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Number of jobs run against this repository"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Jobs</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Total API-equivalent cost"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Cost</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="API-equivalent cost per job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">$/Job</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Average wall-clock time per job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Avg Time</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Total tool calls across all jobs"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Tool Calls</span></Tooltip>
              </th>
            </tr>
          </thead>
          <tbody>
            {repos.map((r, i) => {
              const costPerJob = r.jobCount > 0 ? (Number(r.totalCostUsd) || 0) / r.jobCount : 0;
              return (
                <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                  <td className="py-1.5 px-2 font-mono truncate max-w-[200px]" title={r.repo || "(none)"}>
                    {r.repo || <span className="text-muted-foreground italic">(none)</span>}
                  </td>
                  <td className="text-right py-1.5 px-2">{r.jobCount}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(r.totalCostUsd)}</td>
                  <td className="text-right py-1.5 px-2">{formatUsd(costPerJob)}</td>
                  <td className="text-right py-1.5 px-2">{formatDuration(Number(r.avgDurationMs) || 0)}</td>
                  <td className="text-right py-1.5 px-2">{r.toolCalls}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
