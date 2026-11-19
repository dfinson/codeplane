import type { Job } from "../api/types";

type CostJob = Pick<Job, "totalCostUsd" | "totalTokens" | "costUsage">;

/** Legacy numeric totals have no currency contract; only typed evidence can name USD. */
export function jobCostLabel(job: CostJob): string | null {
  const amounts = new Map<string, number>();
  for (const usage of job.costUsage ?? []) {
    if (usage.role !== "primary") continue;
    const amount = Number(usage.amount);
    if (!Number.isFinite(amount) || amount < 0) continue;
    const unit = usage.unit === "USD" && usage.currency === "USD" && usage.verified
      ? "USD"
      : usage.unit === "copilot_cost_unit" && usage.currency == null
        ? "Copilot cost units"
        : usage.unit === "premium_requests" && usage.currency == null
          ? "premium requests"
          : null;
    if (unit) amounts.set(unit, (amounts.get(unit) ?? 0) + amount);
  }
  if (amounts.size) {
    return [...amounts].map(([unit, amount]) =>
      unit === "USD" ? `$${formatAmount(amount)} USD` : `${formatAmount(amount)} ${unit}`,
    ).join(" + ");
  }
  return job.totalCostUsd != null && job.totalCostUsd > 0
    ? `${formatAmount(job.totalCostUsd)} legacy units (unverified)`
    : null;
}

export function jobCostWithTokens(job: CostJob): string | null {
  const cost = jobCostLabel(job);
  if (!cost || !job.totalTokens) return cost;
  const tokens = job.totalTokens >= 1_000_000
    ? `${(job.totalTokens / 1_000_000).toFixed(1)}M`
    : job.totalTokens >= 1_000 ? `${(job.totalTokens / 1_000).toFixed(0)}k` : String(job.totalTokens);
  return `${cost} · ${tokens} tok`;
}

function formatAmount(amount: number): string {
  return amount > 0 && amount < 0.01 ? amount.toFixed(4) : amount.toFixed(2);
}
