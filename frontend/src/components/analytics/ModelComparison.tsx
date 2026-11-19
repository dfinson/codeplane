import { Tooltip } from "../ui/tooltip";
import {
  type ModelComparisonResponse,
  type ModelComparisonRow,
  type AnalyticsRepos,
  type ModelEfficiencyResponse,
} from "../../api/client";
import { Badge } from "../ui/badge";
import { formatUsd, formatDuration, downloadCsv, CsvButton } from "./helpers";
import { pathBasename } from "../../lib/paths";

// ---------------------------------------------------------------------------
// Model comparison table
// ---------------------------------------------------------------------------

export function ModelComparison({
  data,
  repos,
  selectedRepo,
  onRepoChange,
  modelEfficiency,
}: {
  data: ModelComparisonResponse;
  repos: AnalyticsRepos | null;
  selectedRepo: string;
  onRepoChange: (repo: string) => void;
  modelEfficiency?: ModelEfficiencyResponse;
}) {
  const models = data.models;
  if (!models.length) return <p className="text-muted-foreground text-sm">No model data yet.</p>;

  // Build efficiency lookup by model name
  const efficiencyByModel = new Map(
    (modelEfficiency?.models ?? []).map((m) => [m.model, m]),
  );

  const exportModelsCsv = () => {
    downloadCsv(
      "codeplane-models.csv",
      ["Model", "SDK", "Jobs", "Avg Cost", "Avg Duration (ms)", "Total Cost", "Merged", "PR Created", "Discarded", "Failed", "Cache Hit %", "1-shot %", "Retry %", "Edit Turns", "$/Line"],
      models.map((m) => {
        const eff = efficiencyByModel.get(m.model);
        return [m.model, m.sdk, m.jobCount, m.avgCost, m.avgDurationMs, m.totalCostUsd, m.merged, m.prCreated, m.discarded, m.failed, m.cacheHitRate != null ? (m.cacheHitRate * 100).toFixed(1) : "0", eff ? (eff.oneShotRate * 100).toFixed(1) : "", eff ? (eff.retryRate * 100).toFixed(1) : "", eff ? eff.editTurns.toFixed(1) : "", m.costPerDiffLine != null ? m.costPerDiffLine.toFixed(4) : ""];
      }),
    );
  };

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
              <option key={r.repo} value={r.repo}>{r.repo ? pathBasename(r.repo) : "(none)"}</option>
            ))}
          </select>
        </div>
      )}

      <div className="flex justify-end mb-1">
        <CsvButton onClick={exportModelsCsv} />
      </div>
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
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Cache hit rate — % of input tokens served from cache"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Cache %</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="% of jobs completed in one edit turn"><span className="cursor-help border-b border-dotted border-muted-foreground/50">1-shot %</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="% of jobs that required retry/re-run"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Retry %</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="Average edit turns per job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Edit Turns</span></Tooltip>
              </th>
              <th className="text-right py-1.5 px-2 font-medium">
                <Tooltip content="API-equivalent cost per line of diff output"><span className="cursor-help border-b border-dotted border-muted-foreground/50">$/Line</span></Tooltip>
              </th>
            </tr>
          </thead>
          <tbody>
            {models.map((m: ModelComparisonRow, i: number) => {
              const cacheRate = m.cacheHitRate != null ? m.cacheHitRate * 100 : 0;
              const cacheColor = cacheRate >= 60 ? "text-green-400" : cacheRate >= 30 ? "text-yellow-400" : "text-red-400";
              const eff = efficiencyByModel.get(m.model);
              return (
              <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono">{m.model || "—"}</span>
                    <Badge variant="outline" className="text-[10px]">{m.sdk}</Badge>
                  </div>
                </td>
                <td className="text-right py-1.5 px-2">{m.jobCount}</td>
                <td className="text-right py-1.5 px-2">
                  {m.totalCostUsd != null || m.avgCost != null ? (
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
                <td className="text-right py-1.5 px-2"><span className={cacheColor}>{cacheRate.toFixed(0)}%</span></td>
                <td className="text-right py-1.5 px-2">{eff ? `${(eff.oneShotRate * 100).toFixed(0)}%` : <span className="text-muted-foreground">—</span>}</td>
                <td className="text-right py-1.5 px-2">{eff ? `${(eff.retryRate * 100).toFixed(0)}%` : <span className="text-muted-foreground">—</span>}</td>
                <td className="text-right py-1.5 px-2">{eff ? eff.editTurns.toFixed(1) : <span className="text-muted-foreground">—</span>}</td>
                <td className="text-right py-1.5 px-2">
                  {m.costPerDiffLine != null ? (
                    <Tooltip content={`${formatUsd(m.costPerDiffLine)} per diff line (added+removed)`}>
                      <span className="cursor-help">{formatUsd(m.costPerDiffLine)}</span>
                    </Tooltip>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
              </tr>
            );})}
          </tbody>
        </table>
      </div>
    </div>
  );
}
