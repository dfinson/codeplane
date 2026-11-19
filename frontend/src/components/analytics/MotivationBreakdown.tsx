import { Brain } from "lucide-react";
import { type CostDriversData } from "../MetricsPanelTypes";
import { formatUsd } from "./helpers";
import { Tooltip } from "../ui/tooltip";

/**
 * Motivation-driven attribution breakdown (Item 17).
 * Horizontal stacked bar showing cost by agent motivation.
 */

const MOTIVATION_META: Record<string, { label: string; color: string; bg: string; description: string }> = {
  user_directed: {
    label: "User-Directed",
    color: "text-blue-600",
    bg: "bg-blue-500",
    description: "Work directly responding to the user's prompt",
  },
  agent_exploration: {
    label: "Agent Exploration",
    color: "text-purple-600",
    bg: "bg-purple-500",
    description: "Agent-initiated investigation or coding",
  },
  error_recovery: {
    label: "Error Recovery",
    color: "text-red-600",
    bg: "bg-red-500",
    description: "Fixing the agent's own mistakes",
  },
  test_driven_iteration: {
    label: "Test-Driven",
    color: "text-amber-600",
    bg: "bg-amber-500",
    description: "Changes driven by test failures",
  },
  context_gathering: {
    label: "Context Gathering",
    color: "text-cyan-600",
    bg: "bg-cyan-500",
    description: "Reading and searching to build understanding",
  },
  plan_execution: {
    label: "Plan Execution",
    color: "text-green-600",
    bg: "bg-green-500",
    description: "Executing a plan item the agent created",
  },
};

interface Props {
  data: CostDriversData | null;
}

export function MotivationBreakdown({ data }: Props) {
  // Extract motivation buckets from the cost drivers data
  const motivationBuckets = data?.motivation ?? data?.activity?.filter(
    (b) => b.dimension === "motivation",
  ) ?? [];

  if (!motivationBuckets.length) {
    return (
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
          <Brain size={14} />
          Motivation
        </div>
        <p className="text-muted-foreground text-sm mt-2">No motivation data yet.</p>
      </div>
    );
  }

  const total = motivationBuckets.reduce((s, b) => s + (b.costUsd ?? 0), 0);

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
        <Brain size={14} />
        Why the Agent Spent
      </div>

      {/* Stacked bar */}
      <div className="flex h-4 rounded overflow-hidden">
        {motivationBuckets
          .sort((a, b) => (b.costUsd ?? 0) - (a.costUsd ?? 0))
          .map((bucket) => {
            const cost = bucket.costUsd;
            const pct = cost == null ? null : total > 0 ? (cost / total) * 100 : 0;
            const meta = MOTIVATION_META[bucket.bucket] || { bg: "bg-gray-400", label: bucket.bucket };
            return pct != null && pct > 0 ? (
              <Tooltip key={bucket.bucket} content={`${meta.label}: ${formatUsd(cost)} (${pct.toFixed(1)}%)${"description" in meta ? ` — ${meta.description}` : ""}`}>
                <div
                  className={`${meta.bg} transition-all`}
                  style={{ width: `${pct}%` }}
                />
              </Tooltip>
            ) : null;
          })}
      </div>

      {/* Legend */}
      <div className="space-y-1">
        {motivationBuckets
          .sort((a, b) => (b.costUsd ?? 0) - (a.costUsd ?? 0))
          .map((bucket) => {
            const cost = bucket.costUsd;
            const pct = cost == null ? null : total > 0 ? (cost / total) * 100 : 0;
            const meta = MOTIVATION_META[bucket.bucket] || {
              label: bucket.bucket,
              color: "text-foreground",
              bg: "bg-gray-400",
              description: "",
            };
            return (
              <div key={bucket.bucket} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded ${meta.bg}`} />
                  <span className={meta.color}>{meta.label}</span>
                  {meta.description && (
                    <span className="text-muted-foreground text-[10px] hidden sm:inline">
                      — {meta.description}
                    </span>
                  )}
                </div>
                <span className="text-foreground">
                  {formatUsd(cost)} <span className="text-muted-foreground">({pct == null ? "unknown share" : `${pct.toFixed(1)}%`})</span>
                </span>
              </div>
            );
          })}
      </div>
    </div>
  );
}
