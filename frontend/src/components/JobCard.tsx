import { memo, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, GitMerge, GitPullRequest, Trash2, CheckCircle2, Archive, AlertTriangle, XCircle, ArrowDownCircle } from "lucide-react";
import { toast } from "sonner";
import { useStore, selectJobDiffs } from "../store";
import type { JobSummary } from "../store";
import { StateBadge } from "./StateBadge";
import { resolveJob } from "../api/client";
import { CompleteJobDialog } from "./CompleteJobDialog";
import { Button } from "./ui/button";

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
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [completeOpen, setCompleteOpen] = useState(false);
  const diffs = useStore(selectJobDiffs(job.id));
  const timeline = useStore((s) => s.timelines[job.id] ?? []);
  const hasChanges = diffs.length > 0;

  const needsResolution =
    job.state === "succeeded" &&
    (job.resolution === "unresolved" || job.resolution === "conflict");

  const isResolved =
    job.state === "succeeded" &&
    job.resolution != null &&
    job.resolution !== "unresolved" &&
    job.resolution !== "conflict";

  const isFailed = job.state === "failed";
  const isCanceled = job.state === "canceled";

  const handleResolve = useCallback(
    async (e: React.MouseEvent, action: "merge" | "smart_merge" | "create_pr" | "discard") => {
      e.stopPropagation();
      setLoading(action);
      setError(null);
      try {
        const res = await resolveJob(job.id, action);
        if (res.prUrl) {
          toast.success("PR created", {
            description: res.prUrl,
            action: { label: "Open", onClick: () => window.open(res.prUrl!, "_blank") },
          });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed");
      } finally {
        setLoading(null);
      }
    },
    [job.id],
  );

  const handleComplete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      setCompleteOpen(true);
    },
    [],
  );

  return (
    <>
    <button
      className="w-full text-left rounded-lg border border-border bg-background p-3 cursor-pointer transition-colors hover:border-primary/60 hover:bg-accent"
      onClick={() => navigate(`/jobs/${job.id}`)}
    >
      <div className="flex justify-between items-center mb-1.5">
        {job.title ? (
          <span className="text-sm font-semibold text-primary truncate" title={job.title}>{job.title}</span>
        ) : (job.state === "queued" || job.state === "running") && (Date.now() - new Date(job.createdAt).getTime() < 30_000) ? (
          <span className="h-4 w-36 bg-muted animate-pulse rounded shrink-0" />
        ) : (
          <span className="text-sm font-semibold text-primary truncate" title={job.id}>{job.id}</span>
        )}
        <div className="flex items-center gap-1">
          {job.resolution && <ResolutionBadge resolution={job.resolution} />}
          <StateBadge state={job.state} />
        </div>
      </div>

      <div className="flex items-center gap-2 mb-1">
        <div className="flex items-center gap-1 min-w-0">
          <GitBranch size={12} className="text-muted-foreground shrink-0" />
          <span className="text-xs text-muted-foreground truncate" title={job.branch ?? job.repo}>
            {job.branch ?? repoName}
          </span>
        </div>
        {job.worktreeName && (
          <span className="text-[10px] text-muted-foreground/70 font-mono truncate shrink-0" title={`Worktree: ${job.worktreeName}`}>
            {job.worktreeName}
          </span>
        )}
      </div>

      <p className="text-xs leading-snug line-clamp-2 text-foreground/70 mb-2">
        {job.state === "running" && timeline.length > 0 ? (
          <span className="flex flex-col gap-0.5">
            {timeline.slice(-4).map((entry, i) => (
              <span key={i} className={entry.active ? "italic text-primary/70" : "text-muted-foreground/60 line-through decoration-muted-foreground/20"}>
                {entry.active ? entry.headline : entry.headlinePast}
              </span>
            ))}
          </span>
        ) : job.progressHeadline ? (
          <span className="italic text-primary/70">{job.progressHeadline}</span>
        ) : (
          job.prompt
        )}
      </p>

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

      {error && (
        <p className="text-[11px] text-red-500 mb-1">{error}</p>
      )}

      {/* Action buttons for unresolved/conflict jobs */}
      {needsResolution && hasChanges && (
        <div className="flex gap-1.5 mt-1 mb-1" onClick={(e) => e.stopPropagation()}>
          {job.resolution !== "conflict" && (
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-[11px] px-2 gap-1"
              disabled={loading !== null}
              onClick={(e) => handleResolve(e, "merge")}
            >
              <GitMerge size={11} />
              {loading === "merge" ? "…" : "Merge"}
            </Button>
          )}
          {job.resolution !== "conflict" && (
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-[11px] px-2 gap-1"
              disabled={loading !== null}
              title="Cherry-pick commits onto the base branch"
              onClick={(e) => handleResolve(e, "smart_merge")}
            >
              <GitMerge size={11} />
              {loading === "smart_merge" ? "…" : "Smart"}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-[11px] px-2 gap-1"
            disabled={loading !== null}
            onClick={(e) => handleResolve(e, "create_pr")}
          >
            <GitPullRequest size={11} />
            {loading === "create_pr" ? "…" : "PR"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-[11px] px-2 gap-1 text-destructive hover:text-destructive"
            disabled={loading !== null}
            onClick={(e) => handleResolve(e, "discard")}
          >
            <Trash2 size={11} />
            {loading === "discard" ? "…" : "Discard"}
          </Button>
        </div>
      )}

      {/* No changes — just mark done */}
      {needsResolution && !hasChanges && (
        <div className="flex gap-1.5 mt-1 mb-1" onClick={(e) => e.stopPropagation()}>
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-[11px] px-2 gap-1"
            disabled={loading !== null}
            onClick={(e) => handleResolve(e, "discard")}
          >
            <CheckCircle2 size={11} />
            {loading === "discard" ? "…" : "Mark Done"}
          </Button>
        </div>
      )}

      {/* Complete button for resolved succeeded jobs */}
      {isResolved && (
        <div className="flex gap-1.5 mt-1 mb-1" onClick={(e) => e.stopPropagation()}>
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-[11px] px-2 gap-1 text-green-600 hover:text-green-600 border-green-500/30"
            onClick={handleComplete}
          >
            <CheckCircle2 size={11} />
            Complete
          </Button>
        </div>
      )}

      {/* Archive button for failed/canceled jobs */}
      {(isFailed || isCanceled) && (
        <div className="flex gap-1.5 mt-1 mb-1" onClick={(e) => e.stopPropagation()}>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-[11px] px-2 gap-1 text-muted-foreground"
            onClick={handleComplete}
          >
            <Archive size={11} />
            Archive
          </Button>
        </div>
      )}

      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{elapsed(job.createdAt)}</span>
        <span className="font-mono">{job.id}</span>
      </div>

    </button>
    {completeOpen && (
      <CompleteJobDialog job={job} open onClose={() => setCompleteOpen(false)} />
    )}
    </>
  );
});
