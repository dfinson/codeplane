import type { UsageAmount } from "../api/types";
import { formatUsd } from "../lib/currency";

export function CostEvidence({ usage, estimate }: {
  usage?: UsageAmount[] | null;
  estimate?: number | null;
}) {
  const grouped = new Map<string, number>();
  for (const item of usage ?? []) {
    const usd = item.unit === "USD" && item.verified === true && item.currency === "USD";
    if (!usd && item.unit !== "copilot_cost_unit" && item.unit !== "premium_requests") continue;
    const label = `${item.producer} ${item.role} ${usd ? "verified USD" : item.unit === "copilot_cost_unit" ? "cost units" : "premium requests"}`;
    grouped.set(label, (grouped.get(label) ?? 0) + Number(item.amount));
  }
  return <div className="text-xs text-muted-foreground space-y-1">
    <p>USD totals include only verified source currency. Unknown and historical unverified values are excluded, not converted.</p>
    {[...grouped].map(([label, amount]) => <p key={label}>{label}: {label.endsWith("verified USD") ? formatUsd(amount) : amount.toLocaleString(undefined, { maximumFractionDigits: 6 })}</p>)}
    {estimate != null && <p>Estimated API-equivalent cost: {formatUsd(estimate)} (estimate only, excluded from verified USD)</p>}
  </div>;
}
