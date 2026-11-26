/**
 * Visualization templates for the custom metrics system.
 *
 * Each template is a pure function: (data, config) → JSX.
 * Uses Recharts for chart rendering, consistent with existing analytics.
 */

import {
  BarChart, Bar,
  LineChart, Line,
  PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer,
} from "recharts";

// Chart colour palette — indigo/purple family matching existing analytics
const COLORS = [
  "#6366f1", "#8b5cf6", "#a78bfa", "#c084fc",
  "#818cf8", "#60a5fa", "#34d399", "#fbbf24",
  "#f87171", "#fb923c",
];

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface VizConfig {
  title?: string;
  xKey?: string;
  yKey?: string;
  label?: string;
  keys?: string[];
  [k: string]: unknown;
}

// ---------------------------------------------------------------------------
// stat_card
// ---------------------------------------------------------------------------

function StatCard({ data, config }: { data: unknown[]; config: VizConfig }) {
  const item = (data[0] ?? {}) as Record<string, unknown>;
  const value = item.value ?? item[config.yKey ?? "value"] ?? "—";
  const label = item.label ?? config.label ?? config.title ?? "";

  return (
    <div className="flex flex-col items-center justify-center py-4">
      <div className="text-3xl font-bold text-foreground tabular-nums">
        {typeof value === "number" ? formatNum(value) : String(value)}
      </div>
      <div className="text-sm text-muted-foreground mt-1">{String(label)}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// bar_chart
// ---------------------------------------------------------------------------

function VizBarChart({ data, config }: { data: unknown[]; config: VizConfig }) {
  const xKey = config.xKey ?? "name";
  const yKey = config.yKey ?? "value";
  const keys = config.keys ?? [yKey];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data as Record<string, unknown>[]} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: "#888" }} />
        <YAxis tick={{ fontSize: 11, fill: "#888" }} />
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
        />
        {keys.map((key, i) => (
          <Bar key={key} dataKey={key} fill={COLORS[i % COLORS.length]} radius={[3, 3, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// line_chart
// ---------------------------------------------------------------------------

function VizLineChart({ data, config }: { data: unknown[]; config: VizConfig }) {
  const xKey = config.xKey ?? "date";
  const yKey = config.yKey ?? "value";
  const keys = config.keys ?? [yKey];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data as Record<string, unknown>[]} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: "#888" }} />
        <YAxis tick={{ fontSize: 11, fill: "#888" }} />
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
        />
        {keys.map((key, i) => (
          <Line key={key} type="monotone" dataKey={key} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// stacked_bar
// ---------------------------------------------------------------------------

function VizStackedBar({ data, config }: { data: unknown[]; config: VizConfig }) {
  const xKey = config.xKey ?? "name";
  // Infer series keys from the first data item
  const first = (data[0] ?? {}) as Record<string, unknown>;
  const keys = config.keys ?? Object.keys(first).filter((k) => k !== xKey && typeof first[k] === "number");

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data as Record<string, unknown>[]} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: "#888" }} />
        <YAxis tick={{ fontSize: 11, fill: "#888" }} />
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
        />
        {keys.map((key, i) => (
          <Bar key={key} dataKey={key} stackId="a" fill={COLORS[i % COLORS.length]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// donut
// ---------------------------------------------------------------------------

function VizDonut({ data, config }: { data: unknown[]; config: VizConfig }) {
  const nameKey = config.xKey ?? "name";
  const valueKey = config.yKey ?? "value";

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie
          data={data as Record<string, unknown>[]}
          dataKey={valueKey}
          nameKey={nameKey}
          cx="50%"
          cy="50%"
          innerRadius={50}
          outerRadius={80}
          paddingAngle={2}
          label={({ name, percent }: { name?: string; percent?: number }) =>
            `${name ?? ""} ${((percent ?? 0) * 100).toFixed(0)}%`
          }
          labelLine={{ stroke: "#666" }}
        >
          {(data as Record<string, unknown>[]).map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <RTooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// table
// ---------------------------------------------------------------------------

function VizTable({ data }: { data: unknown[] }) {
  if (!data.length) return <p className="text-sm text-muted-foreground">No data</p>;
  const columns = Object.keys(data[0] as Record<string, unknown>);

  return (
    <div className="overflow-auto max-h-[300px]">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {columns.map((col) => (
              <th key={col} className="text-left py-1.5 px-2 text-muted-foreground font-medium">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(data as Record<string, unknown>[]).map((row, i) => (
            <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
              {columns.map((col) => (
                <td key={col} className="py-1.5 px-2 text-foreground tabular-nums">
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// heatmap
// ---------------------------------------------------------------------------

function VizHeatmap({ data, config }: { data: unknown[]; config: VizConfig }) {
  const xKey = config.xKey ?? "x";
  const yKey = config.yKey ?? "y";
  const vKey = "value";

  const rows = data as Record<string, unknown>[];
  const xs = [...new Set(rows.map((r) => String(r[xKey])))];
  const ys = [...new Set(rows.map((r) => String(r[yKey])))];
  const values = rows.filter(r => r[vKey] != null).map((r) => Number(r[vKey]));
  const maxVal = Math.max(...values, 1);

  const lookup = new Map<string, number | null>();
  for (const r of rows) {
    lookup.set(`${r[xKey]}:${r[yKey]}`, r[vKey] == null ? null : Number(r[vKey]));
  }

  return (
    <div className="overflow-auto">
      <table className="text-xs">
        <thead>
          <tr>
            <th />
            {xs.map((x) => (
              <th key={x} className="px-1.5 py-1 text-muted-foreground font-normal text-center">{x}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ys.map((y) => (
            <tr key={y}>
              <td className="pr-2 py-0.5 text-muted-foreground text-right">{y}</td>
              {xs.map((x) => {
                const val = lookup.get(`${x}:${y}`);
                const opacity = maxVal > 0 && val != null ? (val / maxVal) * 0.8 + 0.1 : 0.1;
                return (
                  <td
                    key={x}
                    className="px-1.5 py-0.5 text-center tabular-nums"
                    style={{ background: `rgba(99, 102, 241, ${opacity})` }}
                    title={val == null ? "Unknown" : `${val}`}
                  >
                    {val == null ? "Unknown" : formatNum(val)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Template registry
// ---------------------------------------------------------------------------

const TEMPLATES: Record<string, React.ComponentType<{ data: unknown[]; config: VizConfig }>> = {
  stat_card: StatCard,
  bar_chart: VizBarChart,
  line_chart: VizLineChart,
  stacked_bar: VizStackedBar,
  donut: VizDonut,
  table: VizTable,
  heatmap: VizHeatmap,
};

/**
 * Render a visualization by template name.
 */
export function MetricViz({
  viz,
  data,
  config,
}: {
  viz: string;
  data: unknown[];
  config: VizConfig;
}) {
  const Template = TEMPLATES[viz] ?? VizTable;
  if (!data.length) {
    return <p className="text-sm text-muted-foreground py-4 text-center">No data</p>;
  }
  return <Template data={data} config={config} />;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNum(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toFixed(2);
}

function formatCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return formatNum(v);
  return String(v);
}
