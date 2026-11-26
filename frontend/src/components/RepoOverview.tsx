import { useEffect, useState } from "react";
import { sumKnown } from "../lib/currency";
import { useParams, Link, Navigate } from "react-router-dom";
import {
  Activity, DollarSign,
  Briefcase, LayoutGrid,
} from "lucide-react";
import { toast } from "sonner";
import { fetchRepoSummary, fetchProject } from "../api/client";
import type { RepoSummaryResponse } from "../api/client";
import type { ProjectResponse } from "../api/types";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { pathBasename } from "../lib/paths";

function StatBadge({ icon: Icon, label, value, className }: {
  icon: React.ComponentType<{ size?: string | number; className?: string }>;
  label: string;
  value: string | number;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1.5 text-xs text-muted-foreground", className)}>
      <Icon size={12} className="shrink-0" />
      <span>{label}:</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

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

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/** Stable Project overview aggregated across every member repository. */
export function RepoOverview() {
  const { projectId } = useParams<{ projectId: string }>();

  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<RepoSummaryResponse | null>(null);
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [error, setError] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [failedRepoCount, setFailedRepoCount] = useState(0);

  useEffect(() => {
    if (!projectId) return;
    let ignore = false;
    setLoading(true);
    setError(false);
    setNotFound(false);
    fetchProject(projectId)
      .then((proj) => {
        if (ignore) return;
        setProject(proj);
        if (proj.repoPaths.length === 0) {
          setSummary(null);
          setFailedRepoCount(0);
          setLoading(false);
          return;
        }
        return Promise.allSettled(proj.repoPaths.map((repoPath) => fetchRepoSummary(repoPath)))
          .then((results) => {
            if (ignore) return;
            const summaries = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
            const failures = results.length - summaries.length;
            setFailedRepoCount(failures);
            if (summaries.length === 0) {
              setError(true);
              toast.error("Failed to load repository summaries");
              return;
            }
            setSummary({
              path: proj.id,
              originUrl: null,
              baseBranch: null,
              currentBranch: null,
              platform: null,
              recentJobs: summaries
                .flatMap((item) => item.recentJobs)
                .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
                .slice(0, 10),
              activeJobCount: summaries.reduce((total, item) => total + item.activeJobCount, 0),
              cost: {
                totalCostUsd: sumKnown(summaries.map(item => item.cost.totalCostUsd)),
                totalJobs: summaries.reduce((total, item) => total + item.cost.totalJobs, 0),
                totalTokens: summaries.reduce((total, item) => total + item.cost.totalTokens, 0),
              },
            });
            if (failures > 0) toast.error(`${failures} repository summaries could not be loaded`);
          })
          .finally(() => { if (!ignore) setLoading(false); });
      })
      .catch((err: unknown) => {
        if (ignore) return;
        const status = (err as { status?: number } | null)?.status;
        if (status === 404) {
          setNotFound(true);
        } else {
          setError(true);
          toast.error("Failed to load Project");
        }
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [projectId]);

  if (notFound) {
    return <Navigate to="/projects" replace />;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        <p>{error ? "Failed to load Project" : "Project not found"}</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      {/* Header */}
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-xl font-semibold text-foreground">{project.name}</h1>
          <p className="text-xs text-muted-foreground">{project.repoPaths.length} member repositories</p>
          <Link
            to={`/projects/id/${encodeURIComponent(project.id)}/board`}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0"
          >
            <LayoutGrid size={12} />
            Board
          </Link>
        </div>
        {summary && (
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            {summary.activeJobCount > 0 && (
              <span className="flex items-center gap-1 text-blue-400">
                <Activity size={12} />
                {summary.activeJobCount} active
              </span>
            )}
            {failedRepoCount > 0 && <span className="text-amber-500">{failedRepoCount} unavailable</span>}
          </div>
        )}
        <div className="flex flex-wrap gap-1.5 pt-2">
          {project.repoPaths.map((path) => (
            <span key={path} className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-mono">{pathBasename(path) || path}</span>
          ))}
        </div>
      </div>

      {!summary ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted-foreground">
          <p>This project has no repositories. Add one in Project settings.</p>
          <Link
            to={`/projects/id/${encodeURIComponent(project.id)}/settings`}
            className="mt-3 inline-flex text-xs text-primary hover:underline"
          >
            Open Project settings
          </Link>
        </div>
      ) : (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Recent Jobs */}
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold flex items-center gap-2">
              <Briefcase size={14} className="text-muted-foreground" />
              Recent Jobs
            </span>
            <Link
              to={`/projects/id/${encodeURIComponent(project.id)}/board`}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              View all →
            </Link>
          </div>
          {summary.recentJobs.length === 0 ? (
            <p className="text-xs text-muted-foreground py-3 text-center">No jobs yet</p>
          ) : (
            <div className="space-y-1">
              {summary.recentJobs.map((job) => (
                <Link
                  key={job.id}
                  to={`/jobs/${job.id}`}
                  className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-accent transition-colors group"
                >
                  <span className={cn("w-1.5 h-1.5 rounded-full shrink-0", {
                    "bg-blue-400": job.state === "running" || job.state === "preparing",
                    "bg-green-400": job.state === "completed",
                    "bg-red-400": job.state === "failed" || job.state === "error",
                    "bg-yellow-400": job.state === "paused",
                    "bg-muted-foreground": !["running", "preparing", "completed", "failed", "error", "paused"].includes(job.state),
                  })} />
                  <span className="flex-1 text-xs truncate text-foreground/90">
                    {job.title || job.id.slice(0, 8)}
                  </span>
                  <span className={cn("text-[10px]", stateColor(job.state))}>
                    {job.state}
                  </span>
                  {job.totalCostUsd != null && (job.totalCostUsd ?? -1) > 0 && (
                    <span className="text-[10px] text-muted-foreground">
                      {formatCost(job.totalCostUsd)}
                    </span>
                  )}
                  <span className="text-[10px] text-muted-foreground/60">
                    {timeAgo(job.createdAt)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Cost */}
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold flex items-center gap-2">
              <DollarSign size={14} className="text-muted-foreground" />
              Cost (7d)
            </span>
            <span className="text-xs text-muted-foreground">Select a repository above for details</span>
          </div>
          <div className="space-y-2">
            <div className="text-2xl font-bold text-foreground">
              {formatCost(summary.cost.totalCostUsd)}
            </div>
            <div className="flex items-center gap-4">
              <StatBadge icon={Briefcase} label="Jobs" value={summary.cost.totalJobs} />
              <StatBadge icon={Activity} label="Tokens" value={summary.cost.totalTokens.toLocaleString()} />
            </div>
          </div>
        </div>

      </div>
      )}
    </div>
  );
}
