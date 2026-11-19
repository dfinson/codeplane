import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronUp, ChevronDown, Loader2 } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import {
  fetchAnalyticsJobs,
  type AnalyticsJobs,
} from "../../api/client";
import { Badge } from "../ui/badge";
import { Spinner } from "../ui/spinner";
import { formatRelativeTime, formatUsd, formatDuration, downloadCsv, STATUS_COLORS, CsvButton } from "./helpers";
import { pathBasename } from "../../lib/paths";

// ---------------------------------------------------------------------------
// Jobs table
// ---------------------------------------------------------------------------

type SortField = "completed_at" | "total_cost_usd" | "duration_ms" | "created_at";

export function SortHeader({ label, field, current, desc, onSort }: {
  label: string; field: SortField; current: SortField; desc: boolean; onSort: (f: SortField) => void;
}) {
  const active = field === current;
  return (
    <th
      className="text-right py-1.5 px-2 font-medium cursor-pointer select-none hover:text-foreground"
      onClick={() => onSort(field)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSort(field); } }}
      tabIndex={0}
      role="columnheader"
      aria-sort={active ? (desc ? "descending" : "ascending") : undefined}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {active && (desc ? <ChevronDown size={12} /> : <ChevronUp size={12} />)}
      </span>
    </th>
  );
}

export function JobsTable({ period }: { period: number }) {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<AnalyticsJobs["jobs"]>([]);
  const [sortField, setSortField] = useState<SortField>("completed_at");
  const [sortDesc, setSortDesc] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  const PAGE_SIZE = 100;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setHasMore(true);
    fetchAnalyticsJobs({ period, sort: sortField, desc: sortDesc, limit: PAGE_SIZE })
      .then((data) => { if (!cancelled) { setJobs(data.jobs); setHasMore(data.jobs.length >= PAGE_SIZE); } })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [period, sortField, sortDesc]);

  const loadMore = () => {
    setLoadingMore(true);
    fetchAnalyticsJobs({ period, sort: sortField, desc: sortDesc, limit: PAGE_SIZE, offset: jobs.length })
      .then((data) => {
        setJobs((prev) => [...prev, ...data.jobs]);
        setHasMore(data.jobs.length >= PAGE_SIZE);
      })
      .catch(() => {})
      .finally(() => setLoadingMore(false));
  };

  const handleSort = (field: SortField) => {
    if (field === sortField) setSortDesc(!sortDesc);
    else { setSortField(field); setSortDesc(true); }
  };

  if (loading) return <div className="flex justify-center py-8"><Spinner size="sm" /></div>;
  if (!jobs.length) return <p className="text-muted-foreground text-sm">No jobs in this period.</p>;

  const exportJobsCsv = () => {
    downloadCsv(
      "codeplane-jobs.csv",
      ["Job ID", "SDK", "Model", "Repo", "Status", "Cost (USD)", "Duration (ms)", "When"],
      jobs.map((j) => [j.job_id, j.sdk, j.model, j.repo, j.status, j.total_cost_usd, j.duration_ms, j.completed_at || j.created_at || ""]),
    );
  };

  return (
    <div className="overflow-x-auto">
      <div className="flex justify-end mb-2">
        <CsvButton onClick={exportJobsCsv} />
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-1.5 px-2 font-medium">
              <Tooltip content="Short ID of the job — click row for details"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Job</span></Tooltip>
            </th>
            <th className="text-left py-1.5 px-2 font-medium">Repo</th>
            <th className="text-left py-1.5 px-2 font-medium">
              <Tooltip content="Which coding agent SDK ran this job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Agent</span></Tooltip>
            </th>
            <th className="text-left py-1.5 px-2 font-medium">
              <Tooltip content="LLM model used for this job"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Model</span></Tooltip>
            </th>
            <th className="text-left py-1.5 px-2 font-medium">Status</th>
            <SortHeader label="Cost" field="total_cost_usd" current={sortField} desc={sortDesc} onSort={handleSort} />
            <SortHeader label="Duration" field="duration_ms" current={sortField} desc={sortDesc} onSort={handleSort} />
            <SortHeader label="When" field="completed_at" current={sortField} desc={sortDesc} onSort={handleSort} />
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => {
            const shortId = j.job_id?.slice(0, 8) || "—";
            const repoName = j.repo ? pathBasename(j.repo) : "—";
            const statusColor = STATUS_COLORS[j.status] || "#666";
            const when = j.completed_at || j.created_at;
            return (
              <tr key={j.job_id} className="border-b border-border/50 hover:bg-accent/30 cursor-pointer" onClick={() => navigate(`/jobs/${j.job_id}`)}>
                <td className="py-1.5 px-2 font-mono text-muted-foreground" title={j.job_id}>{shortId}</td>
                <td className="py-1.5 px-2 truncate max-w-[120px]" title={j.repo}>{repoName}</td>
                <td className="py-1.5 px-2"><Badge variant="outline" className="text-[10px]">{j.sdk}</Badge></td>
                <td className="py-1.5 px-2"><Badge variant="outline" className="text-[10px]">{j.model || "—"}</Badge></td>
                <td className="py-1.5 px-2">
                  <span className="inline-flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} />
                    {j.status}
                  </span>
                </td>
                <td className="text-right py-1.5 px-2">
                  <Tooltip content="Verified source USD; unknown is not zero"><span className="cursor-help">{formatUsd(j.total_cost_usd)}</span></Tooltip>
                </td>
                <td className="text-right py-1.5 px-2">{formatDuration(j.duration_ms || 0)}</td>
                <td className="text-right py-1.5 px-2 text-muted-foreground" title={when || undefined}>{when ? formatRelativeTime(when) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {hasMore && (
        <div className="flex justify-center pt-3">
          <button
            onClick={loadMore}
            disabled={loadingMore}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-4 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors disabled:opacity-50"
          >
            {loadingMore ? <Loader2 size={12} className="animate-spin" /> : null}
            Load more
          </button>
        </div>
      )}
    </div>
  );
}
