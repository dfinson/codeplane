import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch } from "lucide-react";
import type { JobSummary } from "../store";
import { StateBadge } from "./StateBadge";

function elapsed(createdAt: string): string {
  const ms = Date.now() - new Date(createdAt).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export const JobCard = memo(function JobCard({ job }: { job: JobSummary }) {
  const navigate = useNavigate();
  const repoName = job.repo.split("/").pop() ?? job.repo;
  const displayName = job.title ?? job.id;

  return (
    <button
      className="w-full text-left rounded-lg border border-border bg-background p-3 cursor-pointer transition-colors hover:border-primary/60 hover:bg-accent"
      onClick={() => navigate(`/jobs/${job.id}`)}
    >
      <div className="flex justify-between items-center mb-1.5">
        <span className="text-sm font-semibold text-primary truncate" title={displayName}>{displayName}</span>
        <StateBadge state={job.state} />
      </div>

      <div className="flex items-center gap-1 mb-1">
        <GitBranch size={12} className="text-muted-foreground shrink-0" />
        <span className="text-xs text-muted-foreground truncate" title={job.branch ?? job.repo}>
          {job.branch ?? repoName}
        </span>
      </div>

      <p className="text-xs leading-snug line-clamp-2 text-foreground/70 mb-2">{job.prompt}</p>

      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{elapsed(job.createdAt)}</span>
        <span className="font-mono">{job.id}</span>
      </div>
    </button>
  );
});
