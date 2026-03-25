import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { useStore, enrichJob } from "../store";
import type { JobSummary } from "../store";
import { fetchJobs } from "../api/client";
import { KanbanBoard } from "./KanbanBoard";
import { MobileJobList } from "./MobileJobList";
import { Button } from "./ui/button";
import { KanbanSkeleton } from "./KanbanSkeleton";

export function DashboardScreen() {
  const navigate = useNavigate();
  const hasJobs = useStore((state) => Object.keys(state.jobs).length > 0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchJobs({ limit: 100, archived: false })
      .then((result) => {
        useStore.setState((state) => {
          const updated = { ...state.jobs };
          for (const job of result.items) updated[job.id] = enrichJob(job as JobSummary);
          return { jobs: updated };
        });
      })
      .catch((err) => console.error("Failed to fetch jobs", err))
      .finally(() => {
        setLoading(false);
      });
  }, []);

  if (loading && !hasJobs) return <KanbanSkeleton />;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-foreground">Jobs</h3>
        <Button size="sm" className="hidden sm:inline-flex" onClick={() => navigate("/jobs/new")}>
          <Plus size={16} />
          New Job
          <kbd className="ml-1 rounded border border-white/20 px-1 py-px font-mono text-[10px] leading-none opacity-70">Alt+N</kbd>
        </Button>
      </div>
      <KanbanBoard />
      <MobileJobList />
      {/* Mobile FAB — thumb-zone primary action, hidden on tablet/desktop */}
      <Button
        size="icon"
        className="sm:hidden fixed bottom-6 right-6 z-50 h-14 w-14 rounded-full shadow-lg"
        onClick={() => navigate("/jobs/new")}
        aria-label="New Job"
      >
        <Plus size={22} />
      </Button>
    </div>
  );
}
