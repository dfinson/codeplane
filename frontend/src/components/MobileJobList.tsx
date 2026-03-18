import { useMemo, useState } from "react";
import { useStore, selectJobs, selectApprovals } from "../store";
import type { JobSummary } from "../store";
import { JobCard } from "./JobCard";
import { cn } from "../lib/utils";
import { KANBAN_COLUMNS } from "../constants/kanban";
import type { KanbanColumn } from "../constants/kanban";

const TABS = [
  KANBAN_COLUMNS.IN_PROGRESS,
  KANBAN_COLUMNS.NEEDS_REVIEW,
  KANBAN_COLUMNS.NEEDS_ATTENTION,
] as const;

function filterForTab(jobs: Record<string, JobSummary>, tab: KanbanColumn): JobSummary[] {
  return Object.values(jobs)
    .filter((j) => {
      switch (tab) {
        case KANBAN_COLUMNS.IN_PROGRESS:
          return !j.archivedAt && (j.state === "queued" || j.state === "running");
        case KANBAN_COLUMNS.NEEDS_REVIEW:
          return (
            !j.archivedAt &&
            (j.state === "waiting_for_approval" ||
              j.state === "succeeded" ||
              j.state === "canceled")
          );
        case KANBAN_COLUMNS.NEEDS_ATTENTION:
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
            t === KANBAN_COLUMNS.NEEDS_REVIEW && pendingCount > 0
              ? `${KANBAN_COLUMNS.NEEDS_REVIEW} (${pendingCount})`
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
          <p className="text-sm text-muted-foreground text-center py-8">
            No {tab.toLowerCase()} jobs
          </p>
        ) : (
          filtered.map((job) => <JobCard key={job.id} job={job} />)
        )}
      </div>
    </div>
  );
}
