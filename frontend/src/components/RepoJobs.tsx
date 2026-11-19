import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Briefcase } from "lucide-react";
import { toast } from "sonner";
import { fetchAnalyticsJobs } from "../api/client";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { pathBasename } from "../lib/paths";

function stateColor(state: string): string {
  switch (state) {
    case "running":
    case "preparing":
      return "text-blue-400";
    case "completed":
      return "text-green-400";
    case "failed":
    case "error":
      return "text-red-400";
    case "paused":
      return "text-yellow-400";
    default:
      return "text-muted-foreground";
  }
}

function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "Unknown USD";
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface JobRow {
  job_id: string;
  title?: string;
  status: string;
  model?: string;
  total_cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  duration_ms?: number;
  completed_at?: string;
  created_at?: string;
}

export function RepoJobs() {
  const { projectId, repoPath } = useParams<{ projectId: string; repoPath: string }>();
  const decoded = repoPath ?? "";
  const repoName = pathBasename(decoded) || decoded;

  const [loading, setLoading] = useState(true);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [period, setPeriod] = useState(30);

  useEffect(() => {
    if (!decoded) return;
    let ignore = false;
    setLoading(true);
    fetchAnalyticsJobs({ period, repo: decoded, limit: 100 })
      .then((res) => { if (!ignore) setJobs(res.jobs as unknown as JobRow[]); })
      .catch(() => { if (!ignore) toast.error("Failed to load jobs"); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [decoded, period]);

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div className="flex items-center gap-3">
        <Link
          to={`/projects/id/${encodeURIComponent(projectId ?? "")}`}
          className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Back to overview"
        >
          <ArrowLeft size={18} />
        </Link>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold flex items-center gap-2">
            <Briefcase size={16} className="text-muted-foreground" />
            Jobs
          </h1>
          <p className="text-sm text-muted-foreground truncate">{repoName}</p>
        </div>
        <select
          value={period}
          onChange={(e) => setPeriod(Number(e.target.value))}
          className="text-xs bg-muted border border-border rounded-md px-2 py-1 text-foreground"
          aria-label="Time period"
        >
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
          <option value={365}>1 year</option>
        </select>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-12">
          No jobs found for this repository in the last {period} days.
        </p>
      ) : (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted-foreground">
                <th className="text-left px-4 py-2.5 font-medium">Job</th>
                <th className="text-left px-4 py-2.5 font-medium">State</th>
                <th className="text-left px-4 py-2.5 font-medium">Model</th>
                <th className="text-right px-4 py-2.5 font-medium">Cost</th>
                <th className="text-right px-4 py-2.5 font-medium">Tokens</th>
                <th className="text-right px-4 py-2.5 font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.job_id} className="border-b border-border/50 hover:bg-accent/30 transition-colors">
                  <td className="px-4 py-2.5">
                    <Link
                      to={`/jobs/${job.job_id}`}
                      className="text-foreground/90 hover:text-foreground font-medium truncate block max-w-[16rem]"
                    >
                      {job.title || job.job_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className={cn("text-xs font-medium", stateColor(job.status))}>
                      {job.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground truncate max-w-[8rem]">
                    {job.model || "—"}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-right text-muted-foreground">
                    {formatCost(job.total_cost_usd)}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-right text-muted-foreground">
                    {(job.input_tokens || 0) + (job.output_tokens || 0) > 0
                      ? ((job.input_tokens || 0) + (job.output_tokens || 0)).toLocaleString()
                      : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-right text-muted-foreground">
                    {job.completed_at ? formatDate(job.completed_at) : job.created_at ? formatDate(job.created_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
