import { Cpu } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { type ScorecardResponse } from "../../api/client-analytics";
import { formatUsd } from "./helpers";

// Consistent palette for model segments
const SEGMENT_COLORS = [
  "bg-blue-500",
  "bg-emerald-500",
  "bg-amber-500",
];

function shortModelName(model: string): string {
  // Strip provider prefix (e.g. "anthropic/claude-sonnet-4-20250514" → "claude-sonnet-4")
  const base = model.includes("/") ? model.split("/").pop()! : model;
  // Drop date suffixes like -20250514
  return base.replace(/-\d{8}$/, "");
}

export function ModelCostCard({ scorecard }: { scorecard: ScorecardResponse }) {
  const { avgCostPerMtok, modelCostMix } = scorecard;
  const hasData = avgCostPerMtok != null && modelCostMix.length > 0;

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium uppercase tracking-wide">
          <Cpu size={14} />
          Cost / MTok
        </div>
        <span className="text-muted-foreground text-[11px]">
          {scorecard.period === 1 ? "today" : `last ${scorecard.period} days`}
        </span>
      </div>

      <div className="text-2xl font-semibold text-foreground">
        {hasData ? (
          <Tooltip content="Weighted average cost per million tokens across all models used in this period">
            <span className="cursor-help">{formatUsd(avgCostPerMtok)}</span>
          </Tooltip>
        ) : (
          <span className="text-muted-foreground">Unknown USD</span>
        )}
      </div>

      {hasData && (
        <>
          {/* Stacked bar showing token share */}
          <div className="flex h-2 rounded-full overflow-hidden bg-border">
            {modelCostMix.map((m, i) => (
              <Tooltip key={m.model} content={`${shortModelName(m.model)}: ${m.pctOfTokens.toFixed(1)}% of tokens`}>
                <div
                  className={`${SEGMENT_COLORS[i % SEGMENT_COLORS.length]} cursor-help transition-all`}
                  style={{ width: `${m.pctOfTokens}%` }}
                />
              </Tooltip>
            ))}
          </div>

          {/* Legend — top 3 models */}
          <div className="space-y-1.5">
            {modelCostMix.map((m, i) => (
              <div key={m.model} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${SEGMENT_COLORS[i % SEGMENT_COLORS.length]}`} />
                  <span className="text-foreground truncate">{shortModelName(m.model)}</span>
                </div>
                <Tooltip content={`${formatUsd(m.totalCostUsd)} total · ${(m.totalTokens / 1_000_000).toFixed(1)}M tokens`}>
                  <span className="cursor-help text-muted-foreground whitespace-nowrap ml-2">
                    {formatUsd(m.costPerMtok)}/MTok
                  </span>
                </Tooltip>
              </div>
            ))}
          </div>
        </>
      )}

      {!hasData && (
        <p className="text-xs text-muted-foreground">No verified USD per-token rate in this period</p>
      )}
    </div>
  );
}
