import { Download } from "lucide-react";
import { Tooltip } from "../ui/tooltip";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export { formatUsd } from "../../lib/currency";

export function formatDuration(ms: number): string {
  if (ms == null || isNaN(ms)) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export const STATUS_COLORS: Record<string, string> = {
  review: "#06b6d4",
  completed: "#22c55e",
  failed: "#ef4444",
  cancelled: "#f59e0b",
  running: "#3b82f6",
};

export function downloadCsv(filename: string, headers: string[], rows: (string | number | null | undefined)[][]) {
  const escape = (v: string | number | null | undefined) => {
    const s = String(v ?? "");
    return s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r") ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [headers.map(escape).join(","), ...rows.map((r) => r.map(escape).join(","))].join("\r\n");
  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function CsvButton({ onClick }: { onClick: () => void }) {
  return (
    <Tooltip content="Download this data as a CSV file">
      <button
        onClick={onClick}
        className="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-1 text-[10px] text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
      >
        <Download size={10} />
        CSV
      </button>
    </Tooltip>
  );
}
