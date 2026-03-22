import { useMemo, useState } from "react";
import { PlayCircle, CheckCircle2 } from "lucide-react";
import { useStore, selectJobs, selectApprovals } from "../store";
import type { JobSummary } from "../store";
import { JobCard } from "./JobCard";
import { cn } from "../lib/utils";
import { KANBAN_COLUMNS } from "../constants/kanban";
import type { KanbanColumn } from "../constants/kanban";

const TABS = [
  KANBAN_COLUMNS.IN_PROGRESS,
  KANBAN_COLUMNS.AWAITING_INPUT,
  KANBAN_COLUMNS.FAILED,
] as const;

function filterForTab(jobs: Record<string, JobSummary>, tab: KanbanColumn): JobSummary[] {
  return Object.values(jobs)
    .filter((j) => {
      switch (tab) {
        case KANBAN_COLUMNS.IN_PROGRESS:
          return !j.archivedAt && (j.state === "queued" || j.state === "running");
        case KANBAN_COLUMNS.AWAITING_INPUT:
          return (
            !j.archivedAt &&
            (j.state === "waiting_for_approval" ||
              j.state === "succeeded" ||
              j.state === "canceled")
          );
        case KANBAN_COLUMNS.FAILED:
          return !j.archivedAt && j.state === "failed";
        default:
          return false;
      }
    })
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

export function MobileJobList() {
  const [tab, setTab] = useState<KanbanColumn>(KANBAN_COLUMNS.IN_PROGRESS);
  const jobs = useStore(selectJobs);
  const approvals = useStore(selectApprovals);
  const pendingCount = Object.values(approvals).filter((a) => !a.resolvedAt).length;

  const filtered = useMemo(() => filterForTab(jobs, tab), [jobs, tab]);

  return (
    <div className="sm:hidden">
      <div className="flex rounded-lg bg-muted p-1 mb-4 gap-0.5">
        {TABS.map((t) => {
          const label =
            t === KANBAN_COLUMNS.AWAITING_INPUT && pendingCount > 0
              ? `${KANBAN_COLUMNS.AWAITING_INPUT} (${pendingCount})`
              : t;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "flex-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                tab === t
                  ? "bg-background text-foreground shadow"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          );
        })}
      </div>
      <div className="flex flex-col gap-2">
        {filtered.length === 0 ? (
          (() => {
            if (tab === KANBAN_COLUMNS.IN_PROGRESS) {
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
            if (tab === KANBAN_COLUMNS.AWAITING_INPUT) {
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
            if (tab === KANBAN_COLUMNS.FAILED) {
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
            return null;
          })()
        ) : (
          filtered.map((job) => <JobCard key={job.id} job={job} />)
        )}
      </div>
    </div>
  );
}
