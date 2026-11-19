import { TrendingUp } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { type YieldResponse } from "../../api/client-analytics";
import { formatUsd } from "./helpers";

const CATEGORY_COLORS: Record<string, string> = {
  productive: "bg-green-500",
  abandoned: "bg-amber-500",
  failed: "bg-red-500",
  cancelled: "bg-gray-500",
};

const CATEGORY_LABELS: Record<string, string> = {
  productive: "Productive (merged/PR'd)",
  abandoned: "Abandoned",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function YieldCard({ data }: { data: YieldResponse }) {
  const { categories, costPerMergeUsd, totalCostUsd, totalJobs } = data;
  if (totalJobs === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
          <TrendingUp size={14} />
          Yield / ROI
        </div>
        <p className="text-muted-foreground text-sm mt-2">No completed jobs yet.</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
        <TrendingUp size={14} />
        Yield / ROI
      </div>

      <div className="flex items-baseline gap-4">
        {(() => {
          const mergedCount = categories.find((c) => c.category === "productive")?.jobCount ?? 0;
          if (mergedCount === 0) {
            return (
              <div>
                <span className="text-2xl font-semibold text-muted-foreground">No merges</span>
                <span className="text-xs text-muted-foreground ml-1">in period</span>
              </div>
            );
          }
          return (
            <div>
              <Tooltip content={`Average cost across ${mergedCount} merged/PR'd job${mergedCount !== 1 ? "s" : ""}`}>
                <span className="text-2xl font-semibold text-foreground cursor-help">
                  {formatUsd(costPerMergeUsd)}
                </span>
              </Tooltip>
              <span className="text-xs text-muted-foreground ml-1">per merge</span>
            </div>
          );
        })()}
        <div className="text-xs text-muted-foreground">
          {totalJobs} jobs · {formatUsd(totalCostUsd)} total
        </div>
      </div>

      {/* Stacked bar */}
      <div className="h-3 rounded-full bg-border overflow-hidden flex">
        {categories.map((c) => (
          <Tooltip
            key={c.category}
            content={`${CATEGORY_LABELS[c.category] ?? c.category}: ${c.jobCount} jobs, ${formatUsd(c.totalCostUsd)} (${c.pctOfTotal == null ? "unknown share" : `${(c.pctOfTotal * 100).toFixed(0)}%`})`}
          >
            <div
              className={`h-full ${CATEGORY_COLORS[c.category] ?? "bg-gray-400"} cursor-help`}
              style={{ width: `${(c.pctOfTotal ?? 0) * 100}%` }}
            />
          </Tooltip>
        ))}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        {categories.map((c) => (
          <div key={c.category} className="flex items-center gap-1">
            <div className={`w-2 h-2 rounded-full ${CATEGORY_COLORS[c.category] ?? "bg-gray-400"}`} />
            <span className="text-muted-foreground">
              {CATEGORY_LABELS[c.category] ?? c.category}: {c.jobCount} ({formatUsd(c.avgCostUsd)} avg)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
