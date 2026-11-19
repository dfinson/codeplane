import {
  AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, type TooltipValueType,
} from "recharts";
import { formatUsd } from "./helpers";

// ---------------------------------------------------------------------------
// Cost trend chart
// ---------------------------------------------------------------------------

export function CostTrendChart({ data }: { data: import("../../api/types").AnalyticsOverviewResponse["costTrend"] }) {
  if (!data.length) return <p className="text-muted-foreground text-sm">No data yet.</p>;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: string) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11, fill: "#888" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
          formatter={(v: TooltipValueType | undefined) => [formatUsd(v == null ? null : Number(v)), "Verified USD"]}
          labelFormatter={(l: unknown) => String(l)}
        />
        <Area type="monotone" dataKey="cost" stroke="#6366f1" fill="url(#costGrad)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
