import { AlertTriangle, X, TrendingUp, DollarSign, Activity, type LucideIcon } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { type Observation } from "../../api/client";
import { Badge } from "../ui/badge";
import { formatUsd } from "./helpers";

// ---------------------------------------------------------------------------
// Category icons — renders alongside observation title
// ---------------------------------------------------------------------------

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  cost_spike: TrendingUp,
  budget: DollarSign,
  performance: Activity,
};

// ---------------------------------------------------------------------------
// Observations panel
// ---------------------------------------------------------------------------

export function ObservationsPanel({ observations, onDismiss }: { observations: Observation[]; onDismiss: (id: number) => void }) {
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
                {(() => {
                  const Icon = CATEGORY_ICONS[obs.category] ?? AlertTriangle;
                  return <Icon size={13} className={severityText[obs.severity] || "text-muted-foreground"} />;
                })()}
                <span className="text-sm font-medium text-foreground">{obs.title}</span>
                <Badge variant="outline" className="text-[10px]">{obs.category}</Badge>
              </div>
              <p className="text-xs text-muted-foreground">{obs.detail}</p>
              {(obs.totalWasteUsd ?? -1) > 0 && (
                <p className="text-xs mt-1">
                  <Tooltip content="Estimated excess spend attributable to this pattern">
                    <span className="cursor-help text-yellow-400">{formatUsd(obs.totalWasteUsd)} estimated waste</span>
                  </Tooltip>
                  {obs.jobCount > 0 && <span className="text-muted-foreground"> across {obs.jobCount} jobs</span>}
                </p>
              )}
            </div>
            <Tooltip content="Dismiss this observation">
              <button
                onClick={() => onDismiss(obs.id)}
                className="shrink-0 p-1 sm:p-1 min-h-[44px] sm:min-h-0 min-w-[44px] sm:min-w-0 rounded hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors flex items-center justify-center"
                aria-label="Dismiss observation"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </Tooltip>
          </div>
        </div>
      ))}
    </div>
  );
}
