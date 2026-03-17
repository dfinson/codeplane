import { useState, useCallback } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, GitBranch, FolderOpen, Trash2 } from "lucide-react";
import { useStore } from "../store";
import type { JobSummary } from "../store";
import { archiveJob } from "../api/client";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
  DialogFooter,
} from "./ui/dialog";

interface CompleteJobDialogProps {
  job: JobSummary;
  open: boolean;
  onClose: () => void;
  onArchived?: () => void;
}

export function CompleteJobDialog({ job, open, onClose, onArchived }: CompleteJobDialogProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isFailed = job.state === "failed";
  const isCanceled = job.state === "canceled";
  const title = isFailed ? "Archive Failed Job" : isCanceled ? "Archive Canceled Job" : "Complete Job";
  const actionLabel = isFailed || isCanceled ? "Archive & Clean Up" : "Complete & Clean Up";

  const merged = job.resolution === "merged";
  const prCreated = job.resolution === "pr_created";
  const discarded = job.resolution === "discarded";
  const hasUnmergedChanges = !merged && !prCreated && !discarded && job.state === "succeeded";

  const handleConfirm = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await archiveJob(job.id);
      useStore.setState((s) => {
        const existing = s.jobs[job.id];
        if (!existing) return s;
        return {
          jobs: { ...s.jobs, [job.id]: { ...existing, archivedAt: new Date().toISOString() } },
        };
      });
      onClose();
      onArchived?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive job");
    } finally {
      setLoading(false);
    }
  }, [job.id, onClose, onArchived]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{job.title ?? job.id}</DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-3">
          {/* Merge / resolution status */}
          {job.state === "succeeded" && (
            <div className="space-y-2">
              {merged && (
                <StatusRow icon={<CheckCircle2 size={16} className="text-green-500" />} text="Changes merged to base branch" />
              )}
              {prCreated && (
                <StatusRow
                  icon={<ExternalLink size={16} className="text-blue-500" />}
                  text={
                    <span>
                      Pull request created
                      {job.prUrl && (
                        <a
                          href={job.prUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-1.5 text-primary hover:underline text-xs"
                        >
                          View PR
                        </a>
                      )}
                    </span>
                  }
                />
              )}
              {discarded && (
                <StatusRow icon={<Trash2 size={16} className="text-muted-foreground" />} text="Changes were discarded" />
              )}
              {hasUnmergedChanges && (
                <StatusRow
                  icon={<AlertTriangle size={16} className="text-amber-500" />}
                  text="Changes have NOT been merged or PR'd — they will be lost"
                  warn
                />
              )}
            </div>
          )}

          {(isFailed || isCanceled) && (
            <>
              <StatusRow
                icon={<AlertTriangle size={16} className="text-muted-foreground" />}
                text={`Job ${isFailed ? "failed" : "was canceled"} — archiving will remove it from the board`}
              />
              {isFailed && job.failureReason && (
                <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3">
                  <p className="text-xs font-medium text-red-500">Reason</p>
                  <p className="text-sm text-red-400 mt-0.5">{job.failureReason}</p>
                </div>
              )}
            </>
          )}

          {/* Worktree info */}
          {job.worktreePath && (
            <div className="rounded-md border border-border bg-background p-3 space-y-1.5">
              <div className="flex items-center gap-2 text-sm">
                <FolderOpen size={14} className="text-muted-foreground shrink-0" />
                <span className="text-muted-foreground">Worktree:</span>
                <code className="text-xs break-all">{job.worktreePath}</code>
              </div>
              {job.branch && (
                <div className="flex items-center gap-2 text-sm">
                  <GitBranch size={14} className="text-muted-foreground shrink-0" />
                  <span className="text-muted-foreground">Branch:</span>
                  <code className="text-xs">{job.branch}</code>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-1">
                Worktree and local branch will be removed on completion.
              </p>
            </div>
          )}

          {error && <p className="text-sm text-red-500">{error}</p>}
        </DialogBody>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            variant={hasUnmergedChanges ? "destructive" : "default"}
            onClick={handleConfirm}
            loading={loading}
          >
            {actionLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StatusRow({ icon, text, warn }: { icon: React.ReactNode; text: React.ReactNode; warn?: boolean }) {
  return (
    <div className={`flex items-start gap-2 text-sm ${warn ? "text-amber-600 font-medium" : "text-foreground"}`}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>{text}</span>
    </div>
  );
}
