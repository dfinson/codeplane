import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, DollarSign, BarChart3 } from "lucide-react";
import { toast } from "sonner";
import {
  fetchModelComparison, fetchYield,
} from "../api/client";
import type { ModelComparisonRow, YieldCategoryRow } from "../api/client-analytics";
import { Spinner } from "./ui/spinner";
import { Tooltip } from "./ui/tooltip";
import { CostEvidence } from "./CostEvidence";
import { pathBasename } from "../lib/paths";
import { sumKnown } from "../lib/currency";

function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "Unknown USD";
  if (usd === 0) return "$0";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

export function RepoCost() {
  const { projectId, repoPath } = useParams<{ projectId: string; repoPath: string }>();
  const decoded = repoPath ?? "";
  const repoName = pathBasename(decoded) || decoded;

  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState(30);
  const [models, setModels] = useState<ModelComparisonRow[]>([]);
  const [yieldData, setYieldData] = useState<YieldCategoryRow[]>([]);
  const [totalCost, setTotalCost] = useState<number | null>(null);
  const [totalJobs, setTotalJobs] = useState(0);

  useEffect(() => {
    if (!decoded) return;
    let ignore = false;
    setLoading(true);
    Promise.all([
      fetchModelComparison(period, decoded),
      fetchYield(period, decoded),
    ])
      .then(([modelRes, yieldRes]) => {
        if (ignore) return;
        setModels(modelRes.models);
        setYieldData(yieldRes.categories);
        setTotalCost(sumKnown(modelRes.models.map(m => m.totalCostUsd)));
        setTotalJobs(modelRes.models.reduce((s, m) => s + (m.jobCount || 0), 0));
      })
      .catch(() => { if (!ignore) toast.error("Failed to load cost data"); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [decoded, period]);

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div className="flex items-center gap-3">
        <Link
          to={`/projects/id/${encodeURIComponent(projectId ?? "")}`}
          className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Back to overview"
        >
          <ArrowLeft size={18} />
        </Link>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold flex items-center gap-2">
            <DollarSign size={16} className="text-muted-foreground" />
            Cost Analytics
          </h1>
          <p className="text-sm text-muted-foreground truncate">{repoName}</p>
        </div>
        <select
          value={period}
          onChange={(e) => setPeriod(Number(e.target.value))}
          className="text-xs bg-muted border border-border rounded-md px-2 py-1 text-foreground"
          aria-label="Time period"
        >
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
          <option value={365}>1 year</option>
        </select>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : (
        <div className="space-y-5">
          {/* Summary cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-foreground">{formatCost(totalCost)}</p>
              <p className="text-xs text-muted-foreground">Verified USD subtotal ({period}d)</p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-foreground">{totalJobs}</p>
              <p className="text-xs text-muted-foreground">Total Jobs</p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-foreground">
                {totalJobs > 0 ? formatCost(totalCost == null ? null : totalCost / totalJobs) : "—"}
              </p>
              <Tooltip content="Verified USD subtotal divided by all jobs, including jobs without USD evidence.">
                <p className="text-xs text-muted-foreground cursor-help">Verified USD per job</p>
              </Tooltip>
            </div>
          </div>
          <CostEvidence />

          {/* Model comparison */}
          {models.length > 0 && (
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <span className="text-sm font-semibold flex items-center gap-2">
                  <BarChart3 size={14} className="text-muted-foreground" />
                  Model Comparison
                </span>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="text-left px-4 py-2 font-medium">Model</th>
                    <th className="text-right px-4 py-2 font-medium">Jobs</th>
                    <th className="text-right px-4 py-2 font-medium">Verified USD subtotal</th>
                    <th className="text-right px-4 py-2 font-medium">
                      <Tooltip content="Average over jobs with verified USD evidence; jobs with unknown USD are excluded.">
                        <span className="cursor-help">Average verified USD</span>
                      </Tooltip>
                    </th>
                    <th className="text-right px-4 py-2 font-medium">Requests</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={m.model} className="border-b border-border/50">
                      <td className="px-4 py-2 text-foreground font-medium truncate max-w-[12rem]">{m.model}</td>
                      <td className="px-4 py-2 text-right text-muted-foreground">{m.jobCount}</td>
                      <td className="px-4 py-2 text-right text-muted-foreground">{formatCost(m.totalCostUsd)}</td>
                      <td className="px-4 py-2 text-right text-muted-foreground">{formatCost(m.avgCost)}</td>
                      <td className="px-4 py-2 text-right text-muted-foreground">{m.premiumRequests.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Yield breakdown */}
          {yieldData.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-4 space-y-3">
              <span className="text-sm font-semibold flex items-center gap-2">
                <DollarSign size={14} className="text-muted-foreground" />
                Cost Yield Breakdown
              </span>
              <div className="space-y-2">
                {yieldData.map((cat) => (
                  <div key={cat.category} className="flex items-center gap-3">
                    <span className="text-xs text-foreground w-24 truncate">{cat.category}</span>
                    <div className="flex-1 bg-muted rounded-full h-2 overflow-hidden">
                      <div
                        className="bg-primary h-full rounded-full transition-all"
                        style={{ width: `${Math.min(100, cat.pctOfTotal ?? 0)}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground w-16 text-right">
                      {formatCost(cat.totalCostUsd)}
                    </span>
                    <span className="text-xs text-muted-foreground w-10 text-right">
                      {cat.jobCount}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
