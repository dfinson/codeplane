import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, AlertTriangle, XCircle, ArrowDownCircle, BookOpen, CheckCircle2 } from "lucide-react";
import { useStore, selectJobTimeline } from "../store";
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

function ResolutionBadge({ resolution }: { resolution: string }) {
  const styles: Record<string, string> = {
    unresolved: "bg-amber-500/15 text-amber-600 border-amber-500/30",
    conflict: "bg-red-500/15 text-red-600 border-red-500/30",
    merged: "bg-green-500/15 text-green-600 border-green-500/30",
    pr_created: "bg-blue-500/15 text-blue-600 border-blue-500/30",
    discarded: "bg-muted text-muted-foreground border-border",
  };
  const labels: Record<string, string> = {
    unresolved: "Needs review",
    conflict: "Conflict",
    merged: "Merged",
    pr_created: "PR created",
    discarded: "Discarded",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${styles[resolution] ?? styles.unresolved}`}>
      {resolution === "conflict" && <AlertTriangle size={10} className="mr-0.5" />}
      {labels[resolution] ?? resolution}
    </span>
  );
}

export const JobCard = memo(function JobCard({ job }: { job: JobSummary }) {
  const navigate = useNavigate();
  const repoName = job.repo.split("/").pop() ?? job.repo;
  const timeline = useStore(selectJobTimeline(job.id));

  const isFailed = job.state === "failed";
  return (
    <button
      className="w-full text-left rounded-lg border border-border bg-background p-3 cursor-pointer transition-colors hover:border-primary/60 hover:bg-accent"
      onClick={() => navigate(`/jobs/${job.id}`)}
    >
      <div className="flex justify-between items-start gap-2 mb-1.5">
        {job.title ? (
          <span className="text-sm font-semibold text-primary min-w-0 break-words" title={job.title}>{job.title}</span>
        ) : (
          <span className="text-sm font-semibold text-primary min-w-0 break-words" title={job.id}>{job.id}</span>
        )}
        <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
          {job.resolution && <ResolutionBadge resolution={job.resolution} />}
          <StateBadge state={job.state} />
        </div>
      </div>

      <div className="flex items-center gap-1 mb-1">
        <BookOpen size={11} className="text-muted-foreground/60 shrink-0" />
        <span className="text-xs text-muted-foreground/80 font-mono truncate" title={job.repo}>{repoName}</span>
      </div>

      <div className="flex items-center gap-2 mb-1">
        <div className="flex items-center gap-1 min-w-0">
          <GitBranch size={12} className="text-muted-foreground shrink-0" />
          <span className="text-xs text-muted-foreground truncate" title={job.branch ?? job.repo}>
            {job.branch ?? repoName}
          </span>
        </div>
        {job.sdk && job.sdk !== "copilot" && (
          <span className="inline-flex items-center rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground shrink-0">
            {job.sdk}
          </span>
        )}
      </div>

      <div className="text-xs leading-snug text-foreground/70 mb-2">
        {job.state === "running" && timeline.length > 0 ? (
          (() => {
            const active = timeline.find((e) => e.active) ?? timeline[timeline.length - 1];
            return (
              <>
                <p className="italic text-primary/70 line-clamp-1">{active!.headline}</p>
                {active?.summary && (
                  <p className="text-[11px] text-muted-foreground/70 mt-0.5 line-clamp-2">
                    {active.summary}
                  </p>
                )}
              </>
            );
          })()
        ) : job.progressHeadline ? (
          <p className="italic text-primary/70 line-clamp-2">{job.progressHeadline}</p>
        ) : (
          <p className="line-clamp-2">{job.prompt}</p>
        )}
      </div>

      {/* Model downgrade warning */}
      {job.modelDowngraded && (
        <div className="flex items-start gap-1.5 text-[11px] text-amber-600 mb-2 rounded bg-amber-500/10 border border-amber-500/30 px-2 py-1.5">
          <ArrowDownCircle size={12} className="shrink-0 mt-0.5" />
          <span>Model downgraded: requested <span className="font-medium">{job.requestedModel}</span> but received <span className="font-medium">{job.actualModel}</span></span>
        </div>
      )}

      {/* Failure reason */}
      {isFailed && job.failureReason && (
        <div className="flex items-start gap-1.5 text-[11px] text-red-500 mb-2 rounded bg-red-500/10 px-2 py-1.5">
          <XCircle size={12} className="shrink-0 mt-0.5" />
          <span className="line-clamp-2">{job.failureReason}</span>
        </div>
      )}

      {/* Success outcome */}
      {job.state === "succeeded" && job.resolution && job.resolution !== "unresolved" && (
        <div className="flex items-start gap-1.5 text-[11px] text-green-600 mb-2 rounded bg-green-500/10 px-2 py-1.5">
          <CheckCircle2 size={12} className="shrink-0 mt-0.5" />
          <span>
            {job.resolution === "merged" && "Changes merged into base branch"}
            {job.resolution === "pr_created" && "Pull request created"}
            {job.resolution === "discarded" && "Changes discarded"}
            {job.resolution === "conflict" && "Merge conflict — needs manual resolution"}
          </span>
        </div>
      )}

      {/* Conflict file list */}
      {job.resolution === "conflict" && job.conflictFiles && job.conflictFiles.length > 0 && (
        <div className="text-[11px] text-red-500 mb-2">
          <span className="font-medium">Conflicts in {job.conflictFiles.length} file{job.conflictFiles.length > 1 ? "s" : ""}:</span>
          <span className="ml-1">{job.conflictFiles.slice(0, 3).join(", ")}{job.conflictFiles.length > 3 ? "…" : ""}</span>
        </div>
      )}

      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{elapsed(job.createdAt)}</span>
        <span className="font-mono">{job.id}</span>
      </div>

    </button>
  );
});
