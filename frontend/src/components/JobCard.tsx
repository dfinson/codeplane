import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, AlertTriangle, XCircle, ArrowDownCircle, FolderGit2, CheckCircle2, Coins } from "lucide-react";
import { useStore, selectJobTranscript } from "../store";
import type { JobSummary } from "../store";
import { StateBadge } from "./StateBadge";
import { SdkBadge } from "./SdkBadge";
import { Tooltip } from "./ui/tooltip";
import { useViewStateStore } from "../store/viewStateStore";
import { pathBasename } from "../lib/paths";
import { jobCostWithTokens } from "../lib/jobCost";

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
    conflict: "bg-orange-500/15 text-orange-600 border-orange-500/30",
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
  const tips: Record<string, string> = {
    unresolved: "Changes need your review before merging",
    conflict: "Merge conflict detected — manual resolution required",
    merged: "Changes have been merged into the target branch",
    pr_created: "A pull request was created for these changes",
    discarded: "Changes were discarded and not applied",
  };
  return (
    <Tooltip content={tips[resolution] ?? `Resolution: ${resolution}`}>
      <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-xs font-medium ${styles[resolution] ?? styles.unresolved}`}>
        {resolution === "conflict" && <AlertTriangle size={10} className="mr-0.5" />}
        {labels[resolution] ?? resolution}
      </span>
    </Tooltip>
  );
}

export const JobCard = memo(function JobCard({ job }: { job: JobSummary }) {
  const navigate = useNavigate();
  const repoName = pathBasename(job.repo) || job.repo || "External observation";
  const transcript = useStore(selectJobTranscript(job.id));
  const lastSeenSeq = useViewStateStore((s) => s.lastSeenSeq[job.id]);
  const hasUnread = lastSeenSeq != null && transcript.some((e) => (e.sequence ?? 0) > lastSeenSeq);
  const previewHeadline = job.progressHeadline ?? null;
  const previewSummary = job.progressSummary ?? "";

  const isFailed = job.state === "failed";
  const costLabel = jobCostWithTokens(job);
  return (
    <button
      className="w-full shrink-0 text-left rounded-lg border border-border bg-background p-3 cursor-pointer transition-colors hover:border-primary/60 hover:bg-accent overflow-hidden"
      onClick={() => navigate(`/jobs/${job.id}`)}
      aria-label={`Job: ${job.title || job.id} — ${job.state}`}
    >
      <div className="flex justify-between items-start gap-2 mb-1.5">
        {job.title ? (
          <span className="text-sm font-semibold text-primary flex-1 min-w-0 break-words line-clamp-2" title={job.title}>{job.title}</span>
        ) : (
          <span className="text-sm font-semibold text-primary flex-1 min-w-0 break-words line-clamp-2" title={job.id}>{job.id}</span>
        )}
        <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
          {hasUnread && (
            <Tooltip content="New activity since your last visit">
              <span className="inline-block w-2 h-2 rounded-full bg-blue-500 shrink-0" />
            </Tooltip>
          )}
          {job.resolution && !(job.state === "review" && job.resolution === "unresolved") && <ResolutionBadge resolution={job.resolution} />}
          <StateBadge state={job.observationOnly ? "observing" : job.state} />
        </div>
      </div>

      <div className="flex items-center gap-1 mb-1">
        <FolderGit2 size={11} className="text-muted-foreground/60 shrink-0" />
        <span className="text-xs text-muted-foreground/80 font-mono truncate" title={job.repo ?? undefined}>{repoName}</span>
      </div>

      <div className="flex items-center gap-2 mb-1">
        {!job.observationOnly && <div className="flex items-center gap-1 min-w-0">
          <GitBranch size={12} className="text-muted-foreground shrink-0" />
          <span className="text-xs text-muted-foreground truncate" title={job.branch ?? job.repo ?? undefined}>
            {job.branch ?? repoName}
          </span>
        </div>}
        <SdkBadge sdk={job.sdk} size="sm" />
      </div>

      {previewHeadline ? (
        <div className="text-xs leading-snug text-foreground/70 mb-2">
          <p className="italic text-primary/70 line-clamp-1">{previewHeadline}</p>
          {previewSummary && (
            <p className="text-xs text-muted-foreground/70 mt-0.5 line-clamp-2">
              {previewSummary}
            </p>
          )}
        </div>
      ) : null}

      {/* Model downgrade warning */}
      {job.modelDowngraded && (
        <div className="flex items-start gap-1.5 text-xs text-amber-600 mb-2 rounded bg-amber-500/10 border border-amber-500/30 px-2 py-1.5">
          <ArrowDownCircle size={12} className="shrink-0 mt-0.5" />
          <span>Model downgraded: requested <span className="font-medium">{job.requestedModel}</span> but received <span className="font-medium">{job.actualModel}</span></span>
        </div>
      )}

      {/* Failure reason */}
      {isFailed && job.failureReason && (
        <div className="flex items-start gap-1.5 text-xs text-red-500 mb-2 rounded bg-red-500/10 px-2 py-1.5">
          <XCircle size={12} className="shrink-0 mt-0.5" />
          <span className="line-clamp-2">{job.failureReason}</span>
        </div>
      )}

      {/* Success outcome */}
      {(job.state === "review" || job.state === "completed") && job.resolution && job.resolution !== "unresolved" && (
        <div className={`flex items-start gap-1.5 text-xs mb-2 rounded px-2 py-1.5 ${job.resolution === "conflict" ? "text-orange-600 bg-orange-500/10" : "text-green-600 bg-green-500/10"}`}>
          {job.resolution === "conflict"
            ? <AlertTriangle size={12} className="shrink-0 mt-0.5" />
            : <CheckCircle2 size={12} className="shrink-0 mt-0.5" />}
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
        <div className="text-xs text-orange-600 mb-2">
          <span className="font-medium">Conflicts in {job.conflictFiles.length} file{job.conflictFiles.length > 1 ? "s" : ""}:</span>
          <span className="ml-1">{job.conflictFiles.slice(0, 3).join(", ")}{job.conflictFiles.length > 3 ? "…" : ""}</span>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{elapsed(job.createdAt)}</span>
        {costLabel != null && (
          <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 bg-emerald-500/15 text-emerald-400 font-medium">
            <Coins size={10} />
            {costLabel}
          </span>
        )}
      </div>

    </button>
  );
});
