import { useState, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Clock } from "lucide-react";
import { fetchAnalyticsJobs, type AnalyticsJobs } from "../../api/client";
import { Spinner } from "../ui/spinner";
import { Badge } from "../ui/badge";
import { formatRelativeTime, formatUsd, STATUS_COLORS } from "./helpers";
import { pathBasename } from "../../lib/paths";

const PREVIEW_COUNT = 5;

export function RecentJobsPreview({ period }: { period: number }) {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<AnalyticsJobs["jobs"]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAnalyticsJobs({ period, sort: "completed_at", desc: true, limit: PREVIEW_COUNT })
      .then((data) => { if (!cancelled) setJobs(data.jobs); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [period]);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-foreground flex items-center gap-2">
          <Clock size={14} />
          Recent Jobs
        </h2>
        <Link to="/history" className="text-xs text-muted-foreground hover:text-foreground transition-colors">
          View all &rarr;
        </Link>
      </div>

      {loading ? (
        <div className="flex justify-center py-4"><Spinner size="sm" /></div>
      ) : jobs.length === 0 ? (
        <p className="text-xs text-muted-foreground">No jobs in this period.</p>
      ) : (
        <ul className="space-y-1">
          {jobs.map((j) => {
            const repoName = j.repo ? pathBasename(j.repo) : "—";
            const statusColor = STATUS_COLORS[j.status] || "#666";
            const when = j.completed_at || j.created_at;
            return (
              <li key={j.job_id}>
                <button
                  onClick={() => navigate(`/jobs/${j.job_id}`)}
                  className="w-full flex items-center gap-3 rounded-md px-2 py-1.5 text-xs hover:bg-accent/40 transition-colors text-left"
                >
                  <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: statusColor }} />
                  <span className="text-muted-foreground w-16 shrink-0 truncate">{j.status}</span>
                  <span className="truncate flex-1 font-medium">{repoName}</span>
                  <Badge variant="outline" className="text-[10px] shrink-0">{j.model || j.sdk}</Badge>
                  <span className="text-muted-foreground w-14 text-right shrink-0">{formatUsd(j.total_cost_usd)}</span>
                  <span className="text-muted-foreground w-16 text-right shrink-0">{when ? formatRelativeTime(when) : "—"}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
