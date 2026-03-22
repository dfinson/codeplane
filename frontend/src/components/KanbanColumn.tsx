import { memo } from "react";
import { PlayCircle, CheckCircle2 } from "lucide-react";
import type { JobSummary } from "../store";
import { JobCard } from "./JobCard";
import { Button } from "./ui/button";

interface KanbanColumnProps {
  title: string;
  jobs: JobSummary[];
  onLoadMore?: () => void;
  hasMore?: boolean;
}

export const KanbanColumn = memo(function KanbanColumn({
  title,
  jobs,
  onLoadMore,
  hasMore,
}: KanbanColumnProps) {
  return (
    <div className="flex flex-col overflow-hidden h-full rounded-lg border border-border bg-card" role="region" aria-label={title}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <span className="text-sm font-semibold text-muted-foreground">{title}</span>
        <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground">
          {jobs.length}
        </span>
      </div>

      <div className="flex flex-col gap-2 flex-1 overflow-y-auto p-2">
        {jobs.length === 0 ? (
          (() => {
            if (title === "In Progress") {
              return (
                <div className="flex flex-col items-center gap-3 px-4 py-6">
                  <div className="rounded-full bg-primary/10 p-3">
                    <PlayCircle className="h-6 w-6 text-primary" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-muted-foreground">No jobs running</p>
                    <p className="text-xs text-muted-foreground/70 mt-1">Create a new job to get started</p>
                  </div>
                </div>
              );
            }
            if (title === "Awaiting Input") {
              return (
                <div className="flex flex-col items-center gap-3 px-4 py-6">
                  <div className="rounded-full bg-emerald-500/10 p-3">
                    <CheckCircle2 className="h-6 w-6 text-emerald-500" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-muted-foreground">All caught up</p>
                    <p className="text-xs text-muted-foreground/70 mt-1">Nothing is waiting for your input</p>
                  </div>
                </div>
              );
            }
            if (title === "Failed") {
              return (
                <div className="flex flex-col items-center gap-3 px-4 py-6">
                  <div className="rounded-full bg-emerald-500/10 p-3">
                    <CheckCircle2 className="h-6 w-6 text-emerald-500" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-muted-foreground">All clear</p>
                    <p className="text-xs text-muted-foreground/70 mt-1">No failures or issues</p>
                  </div>
                </div>
              );
            }
            return (
              <p className="text-center text-sm text-muted-foreground px-4">
                No {title.toLowerCase()} jobs
              </p>
            );
          })()
        ) : (
          jobs.map((job) => <JobCard key={job.id} job={job} />)
        )}
        {hasMore && onLoadMore && (
          <Button variant="ghost" size="sm" className="w-full" onClick={onLoadMore}>
            Load more
          </Button>
        )}
      </div>
    </div>
  );
});
