import { DollarSign, AlertTriangle } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { type ScorecardResponse } from "../../api/client";
import { Badge } from "../ui/badge";
import { formatUsd, formatDuration } from "./helpers";
import { sumKnown } from "../../lib/currency";
import { CostEvidence } from "../CostEvidence";

// ---------------------------------------------------------------------------
// Budget card — adapts per SDK
// ---------------------------------------------------------------------------

export function BudgetCard({ scorecard }: { scorecard: ScorecardResponse }) {
  const { budget, quotaJson, dailySpendLimitUsd, costTrend } = scorecard;
  const totalCost = sumKnown(budget.map(b => b.totalCostUsd));
  const totalJobs = budget.reduce((s, b) => s + b.jobCount, 0);
  const hasBudgetConfig = ((scorecard.monthlyBudgetUsd ?? -1) > 0) || (dailySpendLimitUsd ?? -1) > 0 || !!quotaJson;
  const periodLabel = scorecard.period === 1 ? "today" : `last ${scorecard.period} days`;

  // Today's spend from costTrend (last entry is today/most recent day)
  const todaysCost = costTrend[costTrend.length - 1]?.cost;
  const dailyLimit = dailySpendLimitUsd || 0;
  const dailyPct = dailyLimit > 0 && todaysCost != null ? (todaysCost / dailyLimit) * 100 : 0;
  const monthlyPct = scorecard.pctMonthlyBudgetUsed ?? 0;

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
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
          <DollarSign size={14} />
          {hasBudgetConfig ? "Spend & Budget" : "Spend"}
        </div>
        <span className="text-muted-foreground text-[11px]">{periodLabel}</span>
      </div>

      <div className="text-2xl font-semibold text-foreground">
        <Tooltip content={`Verified source USD across ${totalJobs} jobs. Native units, estimates and unverified historical values are excluded.`}>
          <span className="cursor-help">{formatUsd(totalCost)}</span>
        </Tooltip>
      </div>
      <CostEvidence usage={scorecard.costUsage} estimate={scorecard.estimatedCostUsd} />

      <div className="space-y-2">
        {budget.map((b) => (
          <div key={b.sdk} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-1.5">
              <Badge variant="outline" className="text-[10px]">{b.sdk}</Badge>
              <span className="text-muted-foreground">{b.jobCount} jobs</span>
            </div>
            <div className="flex items-center gap-3">
              {b.totalCostUsd != null ? (
                <Tooltip content={`${formatUsd(b.avgCostPerJob)} verified source USD per job · ${formatDuration(b.avgDurationMs)} avg duration. Unknown costs and estimates are excluded.`}>
                  <span className="cursor-help text-foreground">{formatUsd(b.totalCostUsd)}</span>
                </Tooltip>
              ) : (
                <span className="text-muted-foreground italic">No verified USD</span>
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

      {dailyLimit > 0 && (
        <div className="pt-2 border-t border-border">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-muted-foreground">Daily Limit</span>
            <span className={dailyPct > 80 ? "text-red-400 font-medium" : "text-foreground"}>
              {formatUsd(todaysCost)} / {formatUsd(dailyLimit)}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-border overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                dailyPct > 80 ? "bg-red-500" : dailyPct > 60 ? "bg-yellow-500" : "bg-green-500"
              }`}
              style={{ width: `${Math.min(dailyPct, 100)}%` }}
            />
          </div>
          {dailyPct > 80 && (
            <Tooltip content="Agent spend today is close to your configured daily cap. New jobs may be throttled once the limit is reached.">
            <div className="flex items-center gap-1 mt-1 text-[11px] text-red-400 cursor-help">
              <AlertTriangle size={11} />
              Approaching daily limit
            </div>
            </Tooltip>
          )}
        </div>
      )}

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
            <Tooltip content="Your Copilot API quota is nearly exhausted for this billing period. Jobs may be denied once 100% is reached.">
            <div className="flex items-center gap-1 mt-1 text-[11px] text-red-400 cursor-help">
              <AlertTriangle size={11} />
              Approaching quota limit
            </div>
            </Tooltip>
          )}
        </div>
      )}

      {(scorecard.monthlyBudgetUsd ?? -1) > 0 && (
        <div className="pt-2 border-t border-border">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-muted-foreground">Monthly Budget</span>
            <span className={monthlyPct > 0.8 ? "text-red-400 font-medium" : "text-foreground"}>
              {formatUsd(scorecard.monthSpendUsd)} / {formatUsd(scorecard.monthlyBudgetUsd)}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-border overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                monthlyPct > 0.8
                  ? "bg-red-500"
                  : monthlyPct > 0.6
                  ? "bg-yellow-500"
                  : "bg-green-500"
              }`}
              style={{ width: `${Math.min(monthlyPct * 100, 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[11px] text-muted-foreground mt-1">
            <span>Day {scorecard.daysElapsed} of {scorecard.daysInMonth}</span>
            <Tooltip content={`At ${formatUsd(scorecard.dailyAvgUsd)}/day average, projected month-end total`}>
              <span className="cursor-help">Proj: {formatUsd(scorecard.projectedMonthEndUsd)}</span>
            </Tooltip>
          </div>
          {monthlyPct > 0.8 && (
            <Tooltip content="Monthly agent spend is approaching your configured budget. Consider reducing job concurrency or pausing lower-priority work.">
            <div className="flex items-center gap-1 mt-1 text-[11px] text-red-400 cursor-help">
              <AlertTriangle size={11} />
              Approaching monthly budget
            </div>
            </Tooltip>
          )}
          {scorecard.projectedMonthEndUsd != null && scorecard.projectedMonthEndUsd > scorecard.monthlyBudgetUsd && (
            <Tooltip content="Based on your average daily spend so far, you're on track to exceed your monthly budget. Projection = daily average × days in month.">
            <div className="flex items-center gap-1 mt-1 text-[11px] text-red-400 cursor-help">
              <AlertTriangle size={11} />
              Projected month-end: {formatUsd(scorecard.projectedMonthEndUsd)} (exceeds {formatUsd(scorecard.monthlyBudgetUsd)} budget by {Math.round(((scorecard.projectedMonthEndUsd - scorecard.monthlyBudgetUsd) / scorecard.monthlyBudgetUsd) * 100)}%)
            </div>
            </Tooltip>
          )}
        </div>
      )}

      {/* Cost per line of code */}
      {scorecard.costPerDiffLine != null && scorecard.costPerDiffLine > 0 && (
        <div className="pt-2 border-t border-border">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">Cost per line</span>
            <span className="text-foreground font-medium">
              ${scorecard.costPerDiffLine.toFixed(4)} / line
              {scorecard.totalDiffLines != null && (
                <span className="text-muted-foreground font-normal ml-1">
                  ({scorecard.totalDiffLines.toLocaleString()} lines changed)
                </span>
              )}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
