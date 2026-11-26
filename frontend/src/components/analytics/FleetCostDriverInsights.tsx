import { useMemo, useState } from "react";
import { Tooltip } from "../ui/tooltip";
import { ChevronDown, ChevronRight } from "lucide-react";
import { type FleetCostDriversResponse, fetchRepoCostDrivers, type RepoCostBreakdown } from "../../api/client";
import { formatUsd } from "./helpers";
import { formatTokens, formatActivityBucket, ACTIVITY_DESCRIPTIONS } from "../MetricsPanelTypes";
import { pathBasename } from "../../lib/paths";
import { sumKnown } from "../../lib/currency";
import type { CurrencyValue } from "../../api/types";

// ---------------------------------------------------------------------------
// Fleet Cost Breakdown — mirrors per-job expandable card design
// ---------------------------------------------------------------------------

interface ActivityRow {
  bucket: string;
  costUsd: CurrencyValue;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  callCount: number;
  jobCount: number;
  avgCostPerJob: CurrencyValue;
}

export function FleetCostDriverInsights({ fleetDrivers, period }: { fleetDrivers: FleetCostDriversResponse; period?: number }) {
  const [groupBy, setGroupBy] = useState<"none" | "repo">("none");
  const [repoData, setRepoData] = useState<RepoCostBreakdown[] | null>(null);
  const [repoLoading, setRepoLoading] = useState(false);
  const [expandedRepos, setExpandedRepos] = useState<Set<string>>(new Set());

  const switchGroupBy = (mode: "none" | "repo") => {
    setGroupBy(mode);
    if (mode === "repo" && !repoData) {
      setRepoLoading(true);
      fetchRepoCostDrivers(period ?? 30)
        .then((r) => setRepoData(r.repos))
        .catch(() => {})
        .finally(() => setRepoLoading(false));
    }
  };

  const toggleRepo = (repo: string) => {
    setExpandedRepos((prev) => {
      const next = new Set(prev);
      if (next.has(repo)) next.delete(repo);
      else next.add(repo);
      return next;
    });
  };

  const activityRows = useMemo<ActivityRow[]>(() => {
    const summary = fleetDrivers.summary ?? [];
    return summary
      .filter((row) => row.dimension === "activity")
      .map((row) => ({
        bucket: row.bucket,
        costUsd: row.costUsd,
        inputTokens: row.inputTokens ?? 0,
        outputTokens: row.outputTokens ?? 0,
        cacheReadTokens: row.cacheReadTokens ?? 0,
        cacheWriteTokens: row.cacheWriteTokens ?? 0,
        callCount: row.callCount ?? 0,
        jobCount: row.jobCount ?? 0,
        avgCostPerJob: row.avgCostPerJob,
      }))
      .sort((a, b) => (b.costUsd ?? -1) - (a.costUsd ?? -1));
  }, [fleetDrivers.summary]);

  const totalCost = useMemo(() => sumKnown(activityRows.map(r => r.costUsd)), [activityRows]);
  const totalTurns = useMemo(() => activityRows.reduce((s, r) => s + r.callCount, 0), [activityRows]);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (bucket: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(bucket)) next.delete(bucket);
      else next.add(bucket);
      return next;
    });
  };

  if (activityRows.length === 0) {
    return <p className="text-sm text-muted-foreground">No cost attribution data yet — complete a job to see breakdown.</p>;
  }

  return (
    <div className="space-y-2">
      {/* Group-by toggle */}
      <div className="flex items-center justify-end gap-2 text-[11px]">
        <span className="text-muted-foreground">Group by:</span>
        <div className="flex rounded-md border border-border overflow-hidden">
          {(["none", "repo"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => switchGroupBy(mode)}
              className={`px-2 py-0.5 capitalize transition-colors ${
                groupBy === mode
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/50"
              }`}
            >
              {mode === "none" ? "None" : "Repository"}
            </button>
          ))}
        </div>
      </div>

      {groupBy === "repo" ? (
        repoLoading ? (
          <div className="h-20 animate-pulse bg-muted rounded" />
        ) : repoData && repoData.length > 0 ? (
          <div className="space-y-2">
            {repoData.map((repo) => {
              const repoName = repo.repo || "(no repo)";
              const isOpen = expandedRepos.has(repoName);
              return (
                <div key={repoName} className="border border-border/50 rounded-lg overflow-hidden">
                  <div
                    className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-accent/30 transition-colors"
                    onClick={() => toggleRepo(repoName)}
                  >
                    <div className="flex items-center gap-2">
                      {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      <span className="text-xs font-medium text-foreground">{pathBasename(repoName)}</span>
                    </div>
                    <span className="text-xs tabular-nums">{formatUsd(repo.totalCostUsd)}</span>
                  </div>
                  {isOpen && (
                    <div className="px-3 pb-2 space-y-1">
                      {repo.buckets
                        .filter((b) => b.dimension === "activity")
                        .sort((a, b) => (b.costUsd ?? 0) - (a.costUsd ?? 0))
                        .map((b) => (
                          <div key={b.bucket} className="flex items-center justify-between text-[10px]">
                            <span className="text-muted-foreground">{formatActivityBucket(b.bucket)}</span>
                            <span className="tabular-nums">{formatUsd(b.costUsd)}</span>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No per-repo data available.</p>
        )
      ) : (
        <>
      {activityRows.map((row) => {
        const maxCost = activityRows[0]?.costUsd || 1;
        const widthPct = row.costUsd == null ? 0 : (row.costUsd / maxCost) * 100;
        const pct = totalCost != null && totalCost > 0 && row.costUsd != null ? ((row.costUsd / totalCost) * 100).toFixed(0) : "Unknown";
        const isExpanded = expanded.has(row.bucket);
        const costPerTurn = row.callCount > 0 && row.costUsd != null ? row.costUsd / row.callCount : null;

        return (
          <div key={row.bucket} className="space-y-1">
            {/* Header row — clickable */}
            <div
              className="flex items-center gap-2 cursor-pointer rounded px-1 py-0.5 hover:bg-accent/30 transition-colors"
              onClick={() => toggle(row.bucket)}
            >
              {isExpanded
                ? <ChevronDown size={12} className="shrink-0 text-muted-foreground" />
                : <ChevronRight size={12} className="shrink-0 text-muted-foreground" />
              }
              <div className="flex-1 min-w-0">
                <Tooltip content={ACTIVITY_DESCRIPTIONS[row.bucket] ?? row.bucket}>
                  <div className="truncate text-foreground text-xs font-medium cursor-help border-b border-dotted border-muted-foreground/30 inline">
                    {formatActivityBucket(row.bucket)}
                  </div>
                </Tooltip>
                <div className="text-[10px] text-muted-foreground">
                  {row.callCount} turn{row.callCount !== 1 ? "s" : ""} · {pct}% of total · {row.jobCount} job{row.jobCount !== 1 ? "s" : ""}
                </div>
              </div>
              <div className="text-right tabular-nums shrink-0">
                <div className="text-xs">{formatUsd(row.costUsd)}</div>
                <div className="text-[10px] text-muted-foreground">{formatTokens(row.inputTokens + row.outputTokens)}</div>
              </div>
            </div>

            {/* Cost proportion bar */}
            <div className="h-1.5 rounded-full bg-muted overflow-hidden ml-5">
              <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.max(widthPct, 4)}%` }} />
            </div>

            {/* Expanded detail */}
            {isExpanded && (
              <div className="ml-7 space-y-2 pb-1 border-l border-border/50 pl-3">
                {ACTIVITY_DESCRIPTIONS[row.bucket] && (
                  <div className="text-[10px] text-muted-foreground/80 italic">
                    {ACTIVITY_DESCRIPTIONS[row.bucket]}
                  </div>
                )}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-1 text-[10px] text-muted-foreground">
                  <div>
                    <div className="text-muted-foreground/60">Input</div>
                    <div className="tabular-nums">{formatTokens(row.inputTokens)}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground/60">Output</div>
                    <div className="tabular-nums">{formatTokens(row.outputTokens)}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground/60">Cost/turn</div>
                    <div className="tabular-nums">{formatUsd(costPerTurn)}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground/60">Avg/job</div>
                    <div className="tabular-nums">{formatUsd(row.avgCostPerJob)}</div>
                  </div>
                </div>
                {(row.cacheReadTokens > 0 || row.cacheWriteTokens > 0) && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-1 text-[10px] text-muted-foreground">
                    <div>
                      <div className="text-muted-foreground/60">Cache read</div>
                      <div className="tabular-nums">{formatTokens(row.cacheReadTokens)}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground/60">Cache write</div>
                      <div className="tabular-nums">{formatTokens(row.cacheWriteTokens)}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground/60">Cache hit %</div>
                      <div className="tabular-nums">
                        {row.inputTokens > 0 ? ((row.cacheReadTokens / row.inputTokens) * 100).toFixed(1) : "0"}%
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Footer summary */}
      <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-2 border-t border-border/50">
        <span>{totalTurns} total turns across {activityRows.reduce((s, r) => Math.max(s, r.jobCount), 0)} jobs</span>
        <span className="tabular-nums font-medium">{formatUsd(totalCost)} total</span>
      </div>
        </>
      )}
    </div>
  );
}
